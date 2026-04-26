"""Tavily 搜索源实现。

Tavily 是专为 LLM/Agent 优化的搜索 API：
- 单次调用返回结构化结果（url/title/content/score）
- 内置 LLM-friendly 摘要，content 比 google snippet 更长
- 价格友好（dev key 1000 次/月免费），适合作为 SerpAPI / Serper 之外的第三路冗余

API 文档: https://docs.tavily.com/docs/rest-api/search
"""

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

DEFAULT_TAVILY_ENDPOINT = "https://api.tavily.com/search"
# Tavily search_depth: "basic"（快、1 credit）或 "advanced"（更准、2 credits）
DEFAULT_TAVILY_SEARCH_DEPTH = "basic"
# Tavily 单次返回结果上限
TAVILY_MAX_RESULTS_LIMIT = 20


class TavilyProvider:
    """Tavily 搜索源。"""

    def __init__(
        self,
        api_key: str = "",
        key_pool: Optional[KeyPool] = None,
        endpoint: str = "",
        search_depth: str = "",
    ):
        self._api_key = api_key or os.getenv("TAVILY_API_KEY", "")
        self._endpoint = endpoint or os.getenv(
            "TAVILY_API_URL", DEFAULT_TAVILY_ENDPOINT
        )
        self._search_depth = (
            search_depth
            or os.getenv("TAVILY_SEARCH_DEPTH", DEFAULT_TAVILY_SEARCH_DEPTH)
        ).strip().lower()
        if self._search_depth not in {"basic", "advanced"}:
            self._search_depth = DEFAULT_TAVILY_SEARCH_DEPTH

        if key_pool is not None:
            self._key_pool = key_pool
        else:
            try:
                self._key_pool = KeyPool.from_env(
                    "TAVILY_API_KEYS", fallback_key=self._api_key or None
                )
            except ValueError:
                self._key_pool = None

    @property
    def name(self) -> str:
        return "tavily"

    def is_available(self) -> bool:
        return bool(self._key_pool or self._api_key)

    async def search(
        self, params: SearchParams
    ) -> tuple[list[SearchResult], dict[str, Any]]:
        """调用 Tavily API 执行搜索。"""
        active_key = (
            self._key_pool.current_key() if self._key_pool else self._api_key
        )

        # 控制 max_results 上界，避免触发 Tavily 报错
        requested_num = max(1, min(int(params.num or 10), TAVILY_MAX_RESULTS_LIMIT))

        request_body: Dict[str, Any] = {
            "api_key": active_key,
            "query": params.query.strip(),
            "max_results": requested_num,
            "search_depth": self._search_depth,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
        }
        # SearchParams.location 复用为 country 提示（Tavily 支持 ISO 国家代码）
        if params.location:
            request_body["country"] = params.location

        response = await self._make_request(request_body)
        data = response.json()

        results: list[SearchResult] = []
        for index, item in enumerate(data.get("results") or [], start=1):
            link = item.get("url") or ""
            if not link or is_banned_url(link):
                continue
            extra: dict[str, Any] = {}
            score = item.get("score")
            if score is not None:
                extra["score"] = score
            published = item.get("published_date")
            if published:
                extra["published_date"] = published
            results.append(
                SearchResult(
                    position=index,
                    title=item.get("title", ""),
                    link=link,
                    snippet=item.get("content", ""),
                    source="tavily",
                    extra=extra,
                )
            )

        search_meta: dict[str, Any] = {
            "q": params.query.strip(),
            "num": requested_num,
            "page": params.page,
            "provider": "tavily",
            "search_depth": self._search_depth,
        }
        # Tavily 可能返回上游回答摘要，保留下来供 verification 阶段参考
        answer = data.get("answer")
        if answer:
            search_meta["answer"] = answer
        return results, search_meta

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        retry=retry_if_exception_type(
            (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError)
        ),
    )
    async def _make_request(self, body: Dict[str, Any]) -> httpx.Response:
        """向 Tavily 发送请求，带指数退避重试。"""
        client = await get_shared_client()
        response = await client.post(self._endpoint, json=body)
        response.raise_for_status()
        return response
