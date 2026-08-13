"""搜索源的 Key 轮转 + 429 退避执行器。

各 provider（serper/serpapi/tavily）的请求只在"key 注入位置"上不同（header /
query / body）。本模块把"带 KeyPool 轮转与 429 冷却的重试"统一抽出来，避免每个
provider 各写一份裸 tenacity 重试却从不调用 KeyPool（导致多 Key 轮转形同虚设）。

用法::

    async def _send(key: str) -> httpx.Response:
        client = await get_shared_client()
        return await client.post(url, json={**body, "api_key": key})

    resp = await request_with_rotation(
        send=_send, key_pool=self._key_pool,
        fallback_key=self._api_key, provider_name="tavily",
    )
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

import httpx

from ...mcp_servers.utils.key_pool import KeyPool

logger = logging.getLogger("miroflow")

# 单个 key 的瞬时错误（连接/超时/5xx）退避序列（秒）
_TRANSIENT_BACKOFF = (2.0, 4.0, 8.0)
# 单 key（无 KeyPool）遇 429 时的最大等待时间，避免阻塞过久
_SINGLE_KEY_429_MAX_SLEEP = 8.0


class AllKeysRateLimitedError(Exception):
    """所有 API Key 均处于 429 冷却期。"""

    def __init__(self, provider: str, retry_after: float):
        self.provider = provider
        self.retry_after = retry_after
        super().__init__(
            f"{provider}: 所有 API Key 均被限速，约 {retry_after:.0f}s 后可重试"
        )


def parse_retry_after(value: Optional[str], default: float = 30.0) -> float:
    """解析 Retry-After 头。仅支持 delta-seconds；HTTP-date 回退到默认值。"""
    if not value:
        return default
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return default


def _backoff(attempt_idx: int) -> float:
    return _TRANSIENT_BACKOFF[min(attempt_idx, len(_TRANSIENT_BACKOFF) - 1)]


async def request_with_rotation(
    *,
    send: Callable[[str], Awaitable[httpx.Response]],
    key_pool: Optional[KeyPool],
    fallback_key: str,
    provider_name: str,
) -> httpx.Response:
    """用给定 key 发请求，按需轮转 Key 并对瞬时错误退避重试。

    - 成功：返回已通过 raise_for_status 的 response。
    - 429：读取 Retry-After；多 KeyPool 标记当前 Key 冷却并立即切换到下一可用
      Key，全部冷却时抛 AllKeysRateLimitedError；单 Key（无论是否包装为 KeyPool）
      则有限退避后重试。
    - 5xx / 连接 / 超时：对当前 Key 指数退避重试。
    - 其它 4xx：不可重试，直接抛出。
    """
    key_count = key_pool.size if key_pool else 1
    # 预算：足够把所有 Key 各试一遍，外加几次瞬时重试
    max_attempts = key_count + 3
    last_exc: Optional[Exception] = None

    for attempt in range(max_attempts):
        active_key = key_pool.current_key() if key_pool else fallback_key
        if not active_key:
            raise ValueError(f"{provider_name}: 无可用 API Key")

        try:
            response = await send(active_key)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code == 429:
                retry_after = parse_retry_after(exc.response.headers.get("Retry-After"))
                last_exc = exc
                if key_pool is not None and key_pool.size > 1:
                    key_pool.mark_rate_limited(active_key, retry_after)
                    next_key = key_pool.next_available_key()
                    if next_key is None:
                        raise AllKeysRateLimitedError(
                            provider_name, key_pool.min_cooldown_remaining()
                        ) from exc
                    # 已切到新 Key，立即重试
                    continue
                # 单 Key：不写入 KeyPool 冷却状态，避免成功重试后仍被误报为不可用。
                await asyncio.sleep(min(retry_after, _SINGLE_KEY_429_MAX_SLEEP))
                continue
            if 500 <= status_code < 600:
                last_exc = exc
                await asyncio.sleep(_backoff(attempt))
                continue
            # 其它 4xx 不可重试
            raise
        except (
            httpx.ConnectError,
            httpx.TimeoutException,
            httpx.RequestError,
        ) as exc:
            last_exc = exc
            await asyncio.sleep(_backoff(attempt))
            continue

    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"{provider_name}: 重试 {max_attempts} 次后仍失败")
