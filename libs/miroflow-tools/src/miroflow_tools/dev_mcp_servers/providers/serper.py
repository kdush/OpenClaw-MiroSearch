"""Serper API 搜索源实现。"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ...mcp_servers.utils.key_pool import KeyPool
from .base import SearchParams, SearchResult
from .http_client import get_shared_client, is_banned_url

logger = logging.getLogger("miroflow")


class SerperProvider:
    """Serper API 搜索源。"""

    def __init__(
        self,
        api_key: str = "",
        key_pool: Optional[KeyPool] = None,
        base_url: str = "",
    ):
        self._api_key = api_key or os.getenv("SERPER_API_KEY", "")
        self._base_url = base_url or os.getenv(
            "SERPER_BASE_URL", "https://google.serper.dev"
        )
        if key_pool is not None:
            self._key_pool = key_pool
        else:
            try:
                self._key_pool = KeyPool.from_env(
                    "SERPER_API_KEYS", fallback_key=self._api_key or None
                )
            except ValueError:
                self._key_pool = None

    @property
    def name(self) -> str:
        return "serper"

    def is_available(self) -> bool:
        return bool(self._key_pool or self._api_key)

    async def search(
        self, params: SearchParams
    ) -> tuple[list[SearchResult], dict[str, Any]]:
        """调用 Serper API 执行搜索。"""
        payload: Dict[str, Any] = {
            "q": params.query.strip(),
            "gl": params.gl,
            "hl": params.hl,
            "num": params.num,
        }
        if params.location:
            payload["location"] = params.location
        if params.tbs:
            payload["tbs"] = params.tbs
        if params.page is not None and params.page > 1:
            payload["page"] = params.page
        if params.autocorrect is not None:
            payload["autocorrect"] = params.autocorrect

        active_key = (
            self._key_pool.current_key() if self._key_pool else self._api_key
        )
        headers = {
            "X-API-KEY": active_key,
            "Content-Type": "application/json",
        }

        response = await self._make_request(payload, headers)
        data = response.json()

        results: list[SearchResult] = []
        for idx, item in enumerate(data.get("organic", []), start=1):
            link = item.get("link", "")
            if is_banned_url(link):
                continue
            results.append(
                SearchResult(
                    position=item.get("position", idx),
                    title=item.get("title", ""),
                    link=link,
                    snippet=item.get("snippet", ""),
                )
            )

        search_params = data.get("searchParameters", {})
        search_params["provider"] = "serper"
        return results, search_params

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        retry=retry_if_exception_type(
            (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError)
        ),
    )
    async def _make_request(
        self, payload: Dict[str, Any], headers: Dict[str, str]
    ) -> httpx.Response:
        """向 Serper API 发送请求，带重试。"""
        client = await get_shared_client()
        response = await client.post(
            f"{self._base_url}/search",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        return response
