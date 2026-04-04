"""请求限流中间件：基于内存滑动窗口，不依赖外部存储。"""

import os
import time
import threading
from collections import defaultdict
from typing import Optional

from fastapi import HTTPException, Request, status

# 限流配置（环境变量可覆盖）
RATE_LIMIT_RPM = int(os.getenv("RATE_LIMIT_RPM", "30"))  # 每分钟最大请求数
RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "1").strip() in ("1", "true", "yes")


class SlidingWindowCounter:
    """线程安全的滑动窗口计数器。

    每个 key（IP 或 Token）维护一个时间戳列表，
    在窗口期（60 秒）内计数，超过阈值则拒绝。
    """

    def __init__(self, max_requests: int = 30, window_seconds: int = 60):
        self._max = max(1, max_requests)
        self._window = max(1, window_seconds)
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def is_allowed(self, key: str) -> bool:
        """检查 key 是否允许通过。允许则记录本次请求并返回 True。"""
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            timestamps = self._buckets[key]
            # 清理过期时间戳
            self._buckets[key] = [t for t in timestamps if t > cutoff]
            if len(self._buckets[key]) >= self._max:
                return False
            self._buckets[key].append(now)
            return True

    def remaining(self, key: str) -> int:
        """返回 key 在当前窗口内的剩余配额。"""
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            timestamps = self._buckets.get(key, [])
            active = [t for t in timestamps if t > cutoff]
            return max(0, self._max - len(active))

    def cleanup(self) -> int:
        """清理所有过期数据，返回清理的 key 数量。"""
        now = time.monotonic()
        cutoff = now - self._window
        removed = 0
        with self._lock:
            expired_keys = [
                k for k, v in self._buckets.items()
                if not any(t > cutoff for t in v)
            ]
            for k in expired_keys:
                del self._buckets[k]
                removed += 1
        return removed


# 全局限流器实例
_limiter = SlidingWindowCounter(max_requests=RATE_LIMIT_RPM, window_seconds=60)


def _extract_client_key(request: Request) -> str:
    """从请求中提取客户端标识（优先 Bearer Token，其次 IP）。"""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer ") and len(auth) > 10:
        return f"token:{auth[7:23]}"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return f"ip:{forwarded.split(',')[0].strip()}"
    client = request.client
    return f"ip:{client.host}" if client else "ip:unknown"


def cleanup_rate_limit_buckets() -> int:
    """清理限流器中的过期数据，返回清理的 key 数量。"""
    return _limiter.cleanup()


async def check_rate_limit(request: Request) -> Optional[str]:
    """限流检查依赖。超出限额时抛出 429。

    - 未启用限流时直接通过
    - /health 和 /docs 等路径跳过限流
    """
    if not RATE_LIMIT_ENABLED:
        return None

    # 跳过非业务路径
    path = request.url.path
    if path in ("/health", "/docs", "/redoc", "/openapi.json"):
        return None

    key = _extract_client_key(request)
    if not _limiter.is_allowed(key):
        remaining = _limiter.remaining(key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Retry later.",
            headers={"Retry-After": "60", "X-RateLimit-Remaining": str(remaining)},
        )
    return key
