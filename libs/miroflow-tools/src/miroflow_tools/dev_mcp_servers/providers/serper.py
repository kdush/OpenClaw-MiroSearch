"""Serper API 搜索源实现。"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import httpx

from ...mcp_servers.utils.key_pool import KeyPool
from .base import SearchParams, SearchResult
from .http_client import get_shared_client, is_banned_url
from .key_rotation import request_with_rotation

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

        async def _send(active_key: str) -> httpx.Response:
            client = await get_shared_client()
            return await client.post(
                f"{self._base_url}/search",
                json=payload,
                headers={
                    "X-API-KEY": active_key,
                    "Content-Type": "application/json",
                },
            )

        response = await request_with_rotation(
            send=_send,
            key_pool=self._key_pool,
            fallback_key=self._api_key,
            provider_name="serper",
        )
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
