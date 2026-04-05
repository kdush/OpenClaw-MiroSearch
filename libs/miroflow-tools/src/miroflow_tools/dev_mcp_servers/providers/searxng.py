"""SearXNG 搜索源实现。"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .base import SearchParams, SearchResult
from .http_client import get_shared_client, is_banned_url

logger = logging.getLogger("miroflow")

# tbs 到 SearXNG time_range 映射
_TBS_TO_TIME_RANGE: dict[str, str] = {
    "qdr:h": "hour",
    "qdr:d": "day",
    "qdr:w": "week",
    "qdr:m": "month",
    "qdr:y": "year",
}


def _read_env_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _read_env_float(name: str, default: float, min_value: float = 0.0) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return max(min_value, float(raw_value))
    except ValueError:
        return default


def _read_env_int(name: str, default: int, min_value: int = 0) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return max(min_value, int(raw_value))
    except ValueError:
        return default


class SearxngPrecheckError(RuntimeError):
    """SearXNG 预检失败。"""


class SearXNGProvider:
    """SearXNG 搜索源。"""

    def __init__(self, base_url: str = ""):
        self._base_url = base_url or os.getenv("SEARXNG_BASE_URL", "")
        self._precheck_enabled = _read_env_bool("SEARXNG_PRECHECK_ENABLED", True)
        self._precheck_timeout = _read_env_float(
            "SEARXNG_PRECHECK_TIMEOUT_SECONDS", 6.0, min_value=1.0
        )
        self._precheck_ttl = _read_env_int(
            "SEARXNG_PRECHECK_TTL_SECONDS", 600, min_value=10
        )
        # 预检状态
        self._precheck_state: dict[str, Any] = {
            "checked_at": 0.0,
            "ok": False,
            "reason": "not_checked",
        }
        self._precheck_lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return "searxng"

    def is_available(self) -> bool:
        return bool(self._base_url)

    async def search(
        self, params: SearchParams
    ) -> tuple[list[SearchResult], dict[str, Any]]:
        """调用 SearXNG 执行搜索。"""
        searxng_time_range = _TBS_TO_TIME_RANGE.get(params.tbs) if params.tbs else None

        request_params: Dict[str, Any] = {
            "q": params.query.strip(),
            "format": "json",
            "language": params.hl,
            "pageno": params.page,
        }
        if searxng_time_range:
            request_params["time_range"] = searxng_time_range

        # 预检
        precheck_t0 = time.perf_counter()
        await self._ensure_json_ready()
        precheck_elapsed = int((time.perf_counter() - precheck_t0) * 1000)

        # 搜索
        search_t0 = time.perf_counter()
        response = await self._make_request(request_params)
        search_elapsed = int((time.perf_counter() - search_t0) * 1000)

        logger.info(
            "SearXNG 搜索耗时 | precheck_ms=%d | search_ms=%d | total_ms=%d | query=%s",
            precheck_elapsed,
            search_elapsed,
            precheck_elapsed + search_elapsed,
            params.query.strip()[:60],
        )

        data = response.json()
        results: list[SearchResult] = []
        for index, item in enumerate(data.get("results", []), start=1):
            link = item.get("url", "")
            if is_banned_url(link):
                continue
            results.append(
                SearchResult(
                    position=index,
                    title=item.get("title", ""),
                    link=link,
                    snippet=item.get("content", ""),
                )
            )
        results = results[: params.num]

        search_meta: dict[str, Any] = {
            "q": params.query.strip(),
            "hl": params.hl,
            "gl": params.gl,
            "num": params.num,
            "page": params.page,
            "provider": "searxng",
        }
        return results, search_meta

    # ------------------------------------------------------------------
    # 预检逻辑
    # ------------------------------------------------------------------

    async def _ensure_json_ready(self) -> None:
        """预检 SearXNG JSON 接口可用性。"""
        if not self._base_url or not self._precheck_enabled:
            return

        now = time.monotonic()
        checked_at = float(self._precheck_state.get("checked_at", 0.0))
        if self._precheck_state.get("ok") and (now - checked_at) < self._precheck_ttl:
            return

        async with self._precheck_lock:
            now = time.monotonic()
            checked_at = float(self._precheck_state.get("checked_at", 0.0))
            if (
                self._precheck_state.get("ok")
                and (now - checked_at) < self._precheck_ttl
            ):
                return

            precheck_start = time.perf_counter()
            has_passed_before = self._precheck_state.get("_ever_passed", False)
            try:
                client = await get_shared_client()
                if has_passed_before:
                    response = await client.get(
                        f"{self._base_url.rstrip('/')}/healthz",
                        timeout=3.0,
                    )
                    if response.status_code == 404:
                        params = {"q": "1", "format": "json", "pageno": 1}
                        response = await client.get(
                            f"{self._base_url.rstrip('/')}/search",
                            params=params,
                            timeout=self._precheck_timeout,
                        )
                else:
                    params = {
                        "q": "healthcheck",
                        "format": "json",
                        "language": "auto",
                        "categories": "general",
                        "pageno": 1,
                    }
                    response = await client.get(
                        f"{self._base_url.rstrip('/')}/search",
                        params=params,
                        timeout=self._precheck_timeout,
                    )

                if response.status_code == 403:
                    raise SearxngPrecheckError(
                        "SearXNG 拒绝 JSON 请求（403），请在 search.formats 开启 json。"
                    )
                response.raise_for_status()

                if not has_passed_before:
                    payload = response.json()
                    if not isinstance(payload, dict) or "results" not in payload:
                        raise SearxngPrecheckError(
                            "SearXNG JSON 响应结构异常，缺少 results 字段。"
                        )

                precheck_ms = int(
                    (time.perf_counter() - precheck_start) * 1000
                )
                self._precheck_state.update(
                    {
                        "checked_at": time.monotonic(),
                        "ok": True,
                        "reason": "ok",
                        "_ever_passed": True,
                    }
                )
                logger.info(
                    "SearXNG 预检通过 | mode=%s | duration_ms=%d",
                    "lightweight" if has_passed_before else "full",
                    precheck_ms,
                )
            except Exception as exc:
                precheck_ms = int(
                    (time.perf_counter() - precheck_start) * 1000
                )
                self._precheck_state.update(
                    {
                        "checked_at": time.monotonic(),
                        "ok": False,
                        "reason": str(exc),
                    }
                )
                logger.warning(
                    "SearXNG 预检失败 | duration_ms=%d | error=%s",
                    precheck_ms,
                    str(exc),
                )
                if isinstance(exc, SearxngPrecheckError):
                    raise
                raise SearxngPrecheckError(
                    f"SearXNG 预检失败：{str(exc)}"
                ) from exc

    # ------------------------------------------------------------------
    # HTTP 请求
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=6),
        retry=retry_if_exception_type(
            (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError)
        ),
    )
    async def _make_request(self, params: Dict[str, Any]) -> httpx.Response:
        """向 SearXNG 发送搜索请求，带重试。"""
        client = await get_shared_client()
        response = await client.get(
            f"{self._base_url.rstrip('/')}/search",
            params=params,
        )
        response.raise_for_status()
        return response
