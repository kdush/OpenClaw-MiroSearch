"""Provider 共享 httpx 连接池。"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

logger = logging.getLogger("miroflow")

_shared_http_client: Optional[httpx.AsyncClient] = None
_shared_http_client_lock = asyncio.Lock()


async def get_shared_client() -> httpx.AsyncClient:
    """获取全局共享的 httpx 连接池客户端。"""
    global _shared_http_client
    if _shared_http_client is not None and not _shared_http_client.is_closed:
        return _shared_http_client
    async with _shared_http_client_lock:
        if _shared_http_client is not None and not _shared_http_client.is_closed:
            return _shared_http_client
        _shared_http_client = httpx.AsyncClient(
            timeout=30.0,
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=120,
            ),
        )
        logger.info("已创建全局 httpx 连接池")
        return _shared_http_client


def is_banned_url(url: str) -> bool:
    """检查 URL 是否在禁止列表中。"""
    banned_list = [
        "unifuncs",
        "huggingface.co/datasets",
        "huggingface.co/spaces",
    ]
    if not url:
        return False
    return any(banned in url for banned in banned_list)
