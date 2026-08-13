"""SerpAPI 搜索源实现。"""

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

# SerpAPI 中文语言代码映射
_SERPAPI_HL_MAP: dict[str, str] = {
    "zh": "zh-cn",
    "zh_cn": "zh-cn",
    "zh-hans": "zh-cn",
    "zh_tw": "zh-tw",
    "zh-hant": "zh-tw",
}


class SerpAPIProvider:
    """SerpAPI 搜索源。"""

    def __init__(
        self,
        api_key: str = "",
        key_pool: Optional[KeyPool] = None,
    ):
        self._api_key = api_key or os.getenv("SERPAPI_API_KEY", "")
        if key_pool is not None:
            self._key_pool = key_pool
        else:
            try:
                self._key_pool = KeyPool.from_env(
                    "SERPAPI_API_KEYS", fallback_key=self._api_key or None
                )
            except ValueError:
                self._key_pool = None

    @property
    def name(self) -> str:
        return "serpapi"

    def is_available(self) -> bool:
        return bool(self._key_pool or self._api_key)

    async def search(
        self, params: SearchParams
    ) -> tuple[list[SearchResult], dict[str, Any]]:
        """调用 SerpAPI 执行搜索。"""
        # 中文 hl 参数映射
        normalized_hl = (params.hl or "").strip().lower()
        serpapi_hl = _SERPAPI_HL_MAP.get(normalized_hl, params.hl)

        start = max(params.page - 1, 0) * params.num

        request_params: Dict[str, Any] = {
            "engine": "google",
            "q": params.query.strip(),
            "hl": serpapi_hl,
            "gl": params.gl,
            "num": params.num,
            "start": start,
        }
        if params.location:
            request_params["location"] = params.location
        if params.tbs:
            request_params["tbs"] = params.tbs

        async def _send(active_key: str) -> httpx.Response:
            client = await get_shared_client()
            return await client.get(
                "https://serpapi.com/search.json",
                params={**request_params, "api_key": active_key},
            )

        response = await request_with_rotation(
            send=_send,
            key_pool=self._key_pool,
            fallback_key=self._api_key,
            provider_name="serpapi",
        )
        data = response.json()

        results: list[SearchResult] = []
        for index, item in enumerate(data.get("organic_results", []), start=1):
            link = item.get("link", "")
            if is_banned_url(link):
                continue
            results.append(
                SearchResult(
                    position=item.get("position", index),
                    title=item.get("title", ""),
                    link=link,
                    snippet=item.get("snippet", ""),
                    source=item.get("source", ""),
                )
            )

        search_meta: dict[str, Any] = {
            "q": params.query.strip(),
            "hl": serpapi_hl,
            "gl": params.gl,
            "num": params.num,
            "page": params.page,
            "provider": "serpapi",
        }
        return results, search_meta
