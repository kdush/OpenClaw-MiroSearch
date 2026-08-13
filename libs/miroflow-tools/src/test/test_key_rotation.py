"""key_rotation 执行器单元测试：验证 429 真的会触发 Key 轮转与冷却。

这是回归测试——修复前各 provider 只读 current_key()、从不调用 KeyPool 的轮转
方法，多 Key 配置形同虚设。
"""

from unittest.mock import AsyncMock

import httpx
import pytest

from miroflow_tools.dev_mcp_servers.providers.key_rotation import (
    AllKeysRateLimitedError,
    parse_retry_after,
    request_with_rotation,
)
from miroflow_tools.mcp_servers.utils.key_pool import KeyPool


def _resp(status: int, key: str = "") -> httpx.Response:
    req = httpx.Request("GET", "https://example.com")
    return httpx.Response(status_code=status, json={"key": key}, request=req)


class TestParseRetryAfter:
    def test_delta_seconds(self):
        assert parse_retry_after("30") == 30.0

    def test_http_date_falls_back(self):
        assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT", default=12) == 12

    def test_none_falls_back(self):
        assert parse_retry_after(None, default=7) == 7


@pytest.mark.asyncio
async def test_429_rotates_to_next_key_and_succeeds():
    """第一个 key 返回 429 时应切换到第二个 key 并最终成功。"""
    pool = KeyPool(["k1", "k2"])
    used_keys = []

    async def send(active_key: str) -> httpx.Response:
        used_keys.append(active_key)
        if active_key == "k1":
            return _resp(429, active_key)
        return _resp(200, active_key)

    resp = await request_with_rotation(
        send=send, key_pool=pool, fallback_key="k1", provider_name="t"
    )
    assert resp.status_code == 200
    # 用过 k1（被限速）后切到 k2
    assert used_keys[0] == "k1"
    assert "k2" in used_keys
    # k1 应被标记冷却（mark_rate_limited 被真实调用过）：至少有一个 key 不可用
    statuses = pool.get_status()
    assert any(not is_available for _, is_available, _ in statuses)


@pytest.mark.asyncio
async def test_all_keys_429_raises_exhausted():
    """所有 key 都 429 时应抛 AllKeysRateLimitedError，而非死循环或静默失败。"""
    pool = KeyPool(["k1", "k2"])

    async def send(active_key: str) -> httpx.Response:
        return _resp(429, active_key)

    with pytest.raises(AllKeysRateLimitedError):
        await request_with_rotation(
            send=send, key_pool=pool, fallback_key="k1", provider_name="t"
        )


@pytest.mark.asyncio
async def test_single_key_pool_429_retries_with_bounded_wait(monkeypatch):
    """默认单 Key 也会被包装成 KeyPool，429 后仍应有限重试。"""
    pool = KeyPool(["k1"])
    responses = [_resp(429, "k1"), _resp(200, "k1")]
    sleep_mock = AsyncMock()
    monkeypatch.setattr("asyncio.sleep", sleep_mock)

    async def send(active_key: str) -> httpx.Response:
        assert active_key == "k1"
        return responses.pop(0)

    resp = await request_with_rotation(
        send=send,
        key_pool=pool,
        fallback_key="k1",
        provider_name="t",
    )

    assert resp.status_code == 200
    sleep_mock.assert_awaited_once_with(8.0)
    assert all(is_available for _, is_available, _ in pool.get_status())


@pytest.mark.asyncio
async def test_non_429_4xx_not_retried():
    """非 429 的 4xx（如 401）不应重试，直接抛出。"""
    pool = KeyPool(["k1", "k2"])
    calls = []

    async def send(active_key: str) -> httpx.Response:
        calls.append(active_key)
        return _resp(401, active_key)

    with pytest.raises(httpx.HTTPStatusError):
        await request_with_rotation(
            send=send, key_pool=pool, fallback_key="k1", provider_name="t"
        )
    assert len(calls) == 1  # 只调用一次，不重试


@pytest.mark.asyncio
async def test_success_first_try_no_rotation():
    """首次成功时不应轮转 key。"""
    pool = KeyPool(["k1", "k2"])
    calls = []

    async def send(active_key: str) -> httpx.Response:
        calls.append(active_key)
        return _resp(200, active_key)

    resp = await request_with_rotation(
        send=send, key_pool=pool, fallback_key="k1", provider_name="t"
    )
    assert resp.status_code == 200
    assert calls == ["k1"]
