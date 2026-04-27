# Copyright (c) 2025 MiroMind
# This source code is licensed under the Apache 2.0 License.

import asyncio
import atexit
import json
import logging
import os
import re
import socket
import time
import xml.etree.ElementTree as ET
from io import BytesIO, StringIO
from ipaddress import ip_address, ip_network
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
from mcp.server.fastmcp import FastMCP
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tencentcloud.common import credential
from tencentcloud.common.common_client import CommonClient
from tencentcloud.common.exception.tencent_cloud_sdk_exception import (
    TencentCloudSDKException,
)
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile

from ..mcp_servers.utils.url_unquote import decode_http_urls_in_dict
from .providers.base import SearchParams
from .providers.registry import ProviderRegistry
from .providers.searxng import SearXNGProvider, SearxngPrecheckError
from .providers.serpapi import SerpAPIProvider
from .providers.serper import SerperProvider
from .providers.tavily import TavilyProvider

# Configure logging
logger = logging.getLogger("miroflow")

# ---------------------------------------------------------------------------
# Provider 注册中心（模块加载时自动初始化）
# ---------------------------------------------------------------------------
_registry = ProviderRegistry()
_registry.register(SerperProvider())
_registry.register(SerpAPIProvider())
_registry.register(SearXNGProvider())
_registry.register(TavilyProvider())
DEFAULT_SEARCH_PROVIDER_ORDER = "searxng,serpapi,serper,tavily"
SEARCH_PROVIDER_ORDER = os.getenv(
    "SEARCH_PROVIDER_ORDER", DEFAULT_SEARCH_PROVIDER_ORDER
)
DEFAULT_SEARCH_PROVIDER_MODE = "fallback"
SEARCH_PROVIDER_MODE = os.getenv(
    "SEARCH_PROVIDER_MODE", DEFAULT_SEARCH_PROVIDER_MODE
).strip()
VALID_SEARCH_PROVIDER_MODES = {
    "fallback",
    "merge",
    "parallel",
    "parallel_conf_fallback",
}
DEFAULT_SEARCH_PROVIDER_TRUSTED_ORDER = "serpapi,tavily,searxng,serper"
SEARCH_PROVIDER_TRUSTED_ORDER = os.getenv(
    "SEARCH_PROVIDER_TRUSTED_ORDER", DEFAULT_SEARCH_PROVIDER_TRUSTED_ORDER
).strip()
DEFAULT_SEARCH_PROVIDER_PARALLEL_MAX_WAIT_MS = 4500
DEFAULT_SEARCH_PROVIDER_PARALLEL_MIN_SUCCESS = 1
DEFAULT_SEARCH_PROVIDER_FALLBACK_MAX_STEPS = 3
DEFAULT_SEARCH_RESULT_NUM = 10
DEFAULT_SEARCH_RESULT_NUM_MAX = 50

DEFAULT_SEARCH_CONFIDENCE_ENABLED = True
DEFAULT_SEARCH_CONFIDENCE_SCORE_THRESHOLD = 0.62
DEFAULT_SEARCH_CONFIDENCE_MIN_RESULTS = 8
DEFAULT_SEARCH_CONFIDENCE_MIN_UNIQUE_DOMAINS = 5
DEFAULT_SEARCH_CONFIDENCE_MIN_PROVIDER_COVERAGE = 2
DEFAULT_SEARCH_CONFIDENCE_MIN_HIGH_CONF_HITS = 2
DEFAULT_SEARCH_CONFIDENCE_HIGH_CONF_DOMAINS = (
    "reuters.com,apnews.com,bbc.com,aljazeera.com,state.gov,un.org,iaea.org,who.int"
)

DEFAULT_SEARCH_SEARXNG_ONLY_ALLOW_DOWNGRADE = False
DEFAULT_SEARCH_SEARXNG_ONLY_DOWNGRADE_ORDER = "serpapi,tavily,serper"

TENCENTCLOUD_SECRET_ID = os.getenv("TENCENTCLOUD_SECRET_ID", "")
TENCENTCLOUD_SECRET_KEY = os.getenv("TENCENTCLOUD_SECRET_KEY", "")

# Initialize FastMCP server
mcp = FastMCP("search_and_scrape_webpage")


def _read_env_int(name: str, default: int, min_value: int = 0) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return max(min_value, int(raw_value))
    except ValueError:
        return default


def _read_env_float(name: str, default: float, min_value: float = 0.0) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return max(min_value, float(raw_value))
    except ValueError:
        return default


def _read_env_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _read_env_ip_networks(name: str) -> Tuple[Any, ...]:
    networks = []
    for raw_value in os.getenv(name, "").split(","):
        value = raw_value.strip()
        if not value:
            continue
        try:
            networks.append(ip_network(value, strict=False))
        except ValueError:
            continue
    return tuple(networks)


SEARCH_PROVIDER_PARALLEL_MAX_WAIT_MS = _read_env_int(
    "SEARCH_PROVIDER_PARALLEL_MAX_WAIT_MS",
    DEFAULT_SEARCH_PROVIDER_PARALLEL_MAX_WAIT_MS,
    min_value=500,
)
SEARCH_PROVIDER_PARALLEL_MIN_SUCCESS = _read_env_int(
    "SEARCH_PROVIDER_PARALLEL_MIN_SUCCESS",
    DEFAULT_SEARCH_PROVIDER_PARALLEL_MIN_SUCCESS,
    min_value=1,
)
SEARCH_PROVIDER_FALLBACK_MAX_STEPS = _read_env_int(
    "SEARCH_PROVIDER_FALLBACK_MAX_STEPS",
    DEFAULT_SEARCH_PROVIDER_FALLBACK_MAX_STEPS,
    min_value=1,
)
SEARCH_RESULT_NUM = _read_env_int(
    "SEARCH_RESULT_NUM",
    DEFAULT_SEARCH_RESULT_NUM,
    min_value=1,
)
SEARCH_RESULT_NUM_MAX = _read_env_int(
    "SEARCH_RESULT_NUM_MAX",
    DEFAULT_SEARCH_RESULT_NUM_MAX,
    min_value=1,
)

SEARCH_CONFIDENCE_ENABLED = _read_env_bool(
    "SEARCH_CONFIDENCE_ENABLED",
    DEFAULT_SEARCH_CONFIDENCE_ENABLED,
)
SEARCH_CONFIDENCE_SCORE_THRESHOLD = _read_env_float(
    "SEARCH_CONFIDENCE_SCORE_THRESHOLD",
    DEFAULT_SEARCH_CONFIDENCE_SCORE_THRESHOLD,
    min_value=0.0,
)
SEARCH_CONFIDENCE_MIN_RESULTS = _read_env_int(
    "SEARCH_CONFIDENCE_MIN_RESULTS",
    DEFAULT_SEARCH_CONFIDENCE_MIN_RESULTS,
    min_value=1,
)
SEARCH_CONFIDENCE_MIN_UNIQUE_DOMAINS = _read_env_int(
    "SEARCH_CONFIDENCE_MIN_UNIQUE_DOMAINS",
    DEFAULT_SEARCH_CONFIDENCE_MIN_UNIQUE_DOMAINS,
    min_value=1,
)
SEARCH_CONFIDENCE_MIN_PROVIDER_COVERAGE = _read_env_int(
    "SEARCH_CONFIDENCE_MIN_PROVIDER_COVERAGE",
    DEFAULT_SEARCH_CONFIDENCE_MIN_PROVIDER_COVERAGE,
    min_value=1,
)
SEARCH_CONFIDENCE_MIN_HIGH_CONF_HITS = _read_env_int(
    "SEARCH_CONFIDENCE_MIN_HIGH_CONF_HITS",
    DEFAULT_SEARCH_CONFIDENCE_MIN_HIGH_CONF_HITS,
    min_value=1,
)
SEARCH_CONFIDENCE_HIGH_CONF_DOMAINS = {
    domain.strip().lower()
    for domain in os.getenv(
        "SEARCH_CONFIDENCE_HIGH_CONF_DOMAINS",
        DEFAULT_SEARCH_CONFIDENCE_HIGH_CONF_DOMAINS,
    ).split(",")
    if domain.strip()
}

SEARCH_SEARXNG_ONLY_ALLOW_DOWNGRADE = _read_env_bool(
    "SEARCH_SEARXNG_ONLY_ALLOW_DOWNGRADE",
    DEFAULT_SEARCH_SEARXNG_ONLY_ALLOW_DOWNGRADE,
)
SEARCH_SEARXNG_ONLY_DOWNGRADE_ORDER = os.getenv(
    "SEARCH_SEARXNG_ONLY_DOWNGRADE_ORDER",
    DEFAULT_SEARCH_SEARXNG_ONLY_DOWNGRADE_ORDER,
).strip()

def _build_searxng_only_downgrade_providers(
    providers: list[str],
) -> tuple[list[str], bool, list[str]]:
    """
    当且仅当处于 searxng-only 且开启降级开关时，自动追加可用兜底搜索源。
    """
    if not SEARCH_SEARXNG_ONLY_ALLOW_DOWNGRADE:
        return providers, False, []
    if providers != ["searxng"]:
        return providers, False, []

    added: list[str] = []
    configured = [
        p.strip().lower()
        for p in SEARCH_SEARXNG_ONLY_DOWNGRADE_ORDER.split(",")
        if p.strip()
    ]
    for name in configured:
        if name == "searxng":
            continue
        p = _registry.get(name)
        if p and p.is_available() and name not in providers:
            providers.append(name)
            added.append(name)
    return providers, bool(added), added


def _format_provider_error(provider: str, exc: Exception) -> str:
    """
    统一错误格式，便于上层区分「实例配置问题」与「暂时性网络问题」。
    """
    import httpx

    if provider == "searxng":
        if isinstance(exc, SearxngPrecheckError):
            return f"{provider}: precheck_failed::{str(exc)}"
        if isinstance(exc, httpx.HTTPStatusError):
            status_code = exc.response.status_code if exc.response else 0
            if status_code == 403:
                return (
                    f"{provider}: http_403_json_forbidden::"
                    "请在 SearXNG settings.yml 的 search.formats 启用 json"
                )
            return f"{provider}: http_{status_code}::{str(exc)}"
        if isinstance(exc, httpx.TimeoutException):
            return f"{provider}: timeout::{str(exc)}"
    return f"{provider}: {str(exc)}"


def _normalize_domain(url: str) -> str:
    if not url:
        return ""
    try:
        domain = urlparse(url).netloc.strip().lower()
    except Exception:
        return ""
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def _merge_provider_results(
    ordered_providers: list[str], provider_results: dict[str, list[dict]], limit: int
) -> list[dict]:
    merged: list[dict] = []
    seen_keys: set[str] = set()
    for provider in ordered_providers:
        for item in provider_results.get(provider, []):
            link = str(item.get("link", "")).strip()
            title = str(item.get("title", "")).strip()
            dedupe_key = link or title
            if not dedupe_key or dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            merged.append(item)
            if len(merged) >= limit:
                return merged
    return merged


def _evaluate_confidence(
    organic_results: list[dict],
    providers_with_results: set[str],
) -> dict[str, Any]:
    unique_domains = {
        _normalize_domain(str(item.get("link", "")).strip())
        for item in organic_results
        if str(item.get("link", "")).strip()
    }
    unique_domains.discard("")

    high_conf_domains_hit = {
        domain
        for domain in unique_domains
        if any(
            domain == trusted or domain.endswith(f".{trusted}")
            for trusted in SEARCH_CONFIDENCE_HIGH_CONF_DOMAINS
        )
    }

    result_ratio = min(
        len(organic_results) / max(1, SEARCH_CONFIDENCE_MIN_RESULTS),
        1.0,
    )
    domain_ratio = min(
        len(unique_domains) / max(1, SEARCH_CONFIDENCE_MIN_UNIQUE_DOMAINS),
        1.0,
    )
    provider_ratio = min(
        len(providers_with_results) / max(1, SEARCH_CONFIDENCE_MIN_PROVIDER_COVERAGE),
        1.0,
    )
    high_conf_ratio = min(
        len(high_conf_domains_hit) / max(1, SEARCH_CONFIDENCE_MIN_HIGH_CONF_HITS),
        1.0,
    )

    score = (
        0.35 * result_ratio
        + 0.25 * domain_ratio
        + 0.2 * provider_ratio
        + 0.2 * high_conf_ratio
    )

    hard_constraints_passed = (
        len(organic_results) >= SEARCH_CONFIDENCE_MIN_RESULTS
        and len(unique_domains) >= SEARCH_CONFIDENCE_MIN_UNIQUE_DOMAINS
        and len(providers_with_results) >= SEARCH_CONFIDENCE_MIN_PROVIDER_COVERAGE
        and len(high_conf_domains_hit) >= SEARCH_CONFIDENCE_MIN_HIGH_CONF_HITS
    )
    passed = hard_constraints_passed and score >= SEARCH_CONFIDENCE_SCORE_THRESHOLD

    return {
        "enabled": SEARCH_CONFIDENCE_ENABLED,
        "score": round(score, 4),
        "threshold": SEARCH_CONFIDENCE_SCORE_THRESHOLD,
        "passed": passed,
        "metrics": {
            "results": len(organic_results),
            "unique_domains": len(unique_domains),
            "provider_coverage": len(providers_with_results),
            "high_conf_domain_hits": len(high_conf_domains_hit),
        },
        "constraints": {
            "min_results": SEARCH_CONFIDENCE_MIN_RESULTS,
            "min_unique_domains": SEARCH_CONFIDENCE_MIN_UNIQUE_DOMAINS,
            "min_provider_coverage": SEARCH_CONFIDENCE_MIN_PROVIDER_COVERAGE,
            "min_high_conf_hits": SEARCH_CONFIDENCE_MIN_HIGH_CONF_HITS,
        },
        "high_conf_domains_hit": sorted(high_conf_domains_hit),
    }


@mcp.tool()
async def google_search(
    q: str,
    gl: str = "us",
    hl: str = "en",
    location: str = None,
    num: int = None,
    tbs: str = None,
    page: int = None,
    autocorrect: bool = None,
):
    """
    Tool to perform web searches and retrieve rich results.

    Search provider strategy:
    - `SEARCH_PROVIDER_MODE=fallback`: 按 `SEARCH_PROVIDER_ORDER` 依次尝试，命中即返回。
    - `SEARCH_PROVIDER_MODE=merge`: 串行聚合多路结果并去重后返回。
    - `SEARCH_PROVIDER_MODE=parallel`: 多路并发检索并聚合去重后返回。
    - `SEARCH_PROVIDER_MODE=parallel_conf_fallback`: 先并发检索并评分，若置信度不足则按 `SEARCH_PROVIDER_TRUSTED_ORDER` 串行补检。

    It is able to retrieve organic search results, people also ask,
    related searches, and knowledge graph.

    Args:
        q: Search query string
        gl: Optional region code for search results in ISO 3166-1 alpha-2 format (e.g., 'us')
        hl: Optional language code for search results in ISO 639-1 format (e.g., 'en')
        location: Optional location for search results (e.g., 'SoHo, New York, United States', 'California, United States')
        num: Number of results to return (default: 10)
        tbs: Time-based search filter ('qdr:h' for past hour, 'qdr:d' for past day, 'qdr:w' for past week, 'qdr:m' for past month, 'qdr:y' for past year)
        page: Page number of results to return (default: 1)
        autocorrect: Whether to autocorrect spelling in query

    Returns:
        Dictionary containing search results and metadata.
    """
    # Validate required parameter
    if not q or not q.strip():
        return json.dumps(
            {
                "success": False,
                "error": "Search query 'q' is required and cannot be empty",
                "results": [],
            },
            ensure_ascii=False,
        )

    try:
        search_provider = ""

        async def execute_provider_search(
            provider_name: str, search_query: str, result_num: int, result_page: int
        ) -> tuple[list, dict]:
            """通过 Provider 协议执行搜索，返回 (organic_dicts, search_params)。"""
            provider = _registry.get(provider_name)
            if not provider:
                raise ValueError(f"未注册的搜索源: {provider_name}")
            params = SearchParams(
                query=search_query,
                num=result_num,
                page=result_page,
                hl=hl,
                gl=gl,
                location=location,
                tbs=tbs,
                autocorrect=autocorrect,
            )
            results, meta = await provider.search(params)
            organic_dicts = [r.to_dict() for r in results]
            return organic_dicts, meta

        # Helper function to perform a single search
        async def perform_search(search_query: str) -> tuple[list, dict, list[str]]:
            """执行搜索并返回结果，支持串行回退、并发聚合和置信度不足串行补检。"""
            nonlocal search_provider
            configured_mode = SEARCH_PROVIDER_MODE.strip().lower()
            if configured_mode not in VALID_SEARCH_PROVIDER_MODES:
                configured_mode = DEFAULT_SEARCH_PROVIDER_MODE

            providers = _registry.resolve_order(SEARCH_PROVIDER_ORDER)
            (
                providers,
                searxng_only_downgraded,
                searxng_only_downgrade_added,
            ) = _build_searxng_only_downgrade_providers(
                providers,
            )
            if searxng_only_downgraded:
                logger.warning(
                    "searxng-only 自动降级已启用，追加兜底搜索源: %s",
                    ",".join(searxng_only_downgrade_added),
                )
            if not providers:
                raise ValueError(
                    "No search provider configured. Set SERPER_API_KEY or SERPAPI_API_KEY or SEARXNG_BASE_URL."
                )

            requested_result_num = num if num is not None else SEARCH_RESULT_NUM
            try:
                requested_result_num = int(requested_result_num)
            except (TypeError, ValueError):
                requested_result_num = SEARCH_RESULT_NUM
            result_num = min(
                SEARCH_RESULT_NUM_MAX,
                max(1, int(requested_result_num)),
            )
            result_page = page if page is not None else 1
            provider_errors: list[str] = []
            route_trace: list[dict[str, Any]] = []

            if configured_mode in {"parallel", "parallel_conf_fallback"}:
                provider_results_map: dict[str, list[dict]] = {}
                providers_with_results: set[str] = set()
                provider_tasks = {
                    provider: asyncio.create_task(
                        execute_provider_search(provider, search_query, result_num, result_page)
                    )
                    for provider in providers
                }
                done, pending = await asyncio.wait(
                    provider_tasks.values(),
                    timeout=SEARCH_PROVIDER_PARALLEL_MAX_WAIT_MS / 1000.0,
                )

                for provider, task in provider_tasks.items():
                    search_provider = provider
                    if task in pending:
                        task.cancel()
                        provider_errors.append(
                            f"{provider}: timeout>{SEARCH_PROVIDER_PARALLEL_MAX_WAIT_MS}ms"
                        )
                        route_trace.append(
                            {"phase": "parallel", "provider": provider, "status": "timeout"}
                        )
                        continue
                    try:
                        provider_results, _ = task.result()
                        if provider_results:
                            provider_results_map[provider] = provider_results
                            providers_with_results.add(provider)
                            route_trace.append(
                                {
                                    "phase": "parallel",
                                    "provider": provider,
                                    "status": "ok",
                                    "result_count": len(provider_results),
                                }
                            )
                        else:
                            provider_errors.append(f"{provider}: empty organic results")
                            route_trace.append(
                                {"phase": "parallel", "provider": provider, "status": "empty"}
                            )
                    except Exception as exc:
                        provider_errors.append(_format_provider_error(provider, exc))
                        route_trace.append(
                            {
                                "phase": "parallel",
                                "provider": provider,
                                "status": "error",
                                "error": str(exc),
                            }
                        )
                        logger.warning(
                            "Search provider failed in parallel mode | provider=%s | err=%s",
                            provider,
                            str(exc),
                        )

                merged_results = _merge_provider_results(
                    providers, provider_results_map, result_num
                )
                confidence = _evaluate_confidence(merged_results, providers_with_results)
                parallel_min_success_passed = (
                    len(providers_with_results) >= SEARCH_PROVIDER_PARALLEL_MIN_SUCCESS
                )

                search_params = {
                    "q": search_query.strip(),
                    "hl": hl,
                    "gl": gl,
                    "num": result_num,
                    "page": result_page,
                    "provider": "multi-route",
                    "provider_mode": configured_mode,
                    "provider_order": providers,
                    "searxng_only_downgraded": searxng_only_downgraded,
                    "searxng_only_downgrade_added": searxng_only_downgrade_added,
                    "providers_with_results": sorted(providers_with_results),
                    "parallel_min_success": SEARCH_PROVIDER_PARALLEL_MIN_SUCCESS,
                    "parallel_min_success_passed": parallel_min_success_passed,
                    "confidence": confidence,
                    "route_trace": route_trace,
                }

                if configured_mode == "parallel":
                    return merged_results, search_params, provider_errors

                confidence_passed = (not SEARCH_CONFIDENCE_ENABLED) or confidence.get(
                    "passed", False
                )
                if confidence_passed and parallel_min_success_passed:
                    return merged_results, search_params, provider_errors

                trusted_order = _registry.resolve_order(
                    SEARCH_PROVIDER_TRUSTED_ORDER
                )
                fallback_steps = 0
                for provider in trusted_order:
                    if fallback_steps >= SEARCH_PROVIDER_FALLBACK_MAX_STEPS:
                        break
                    if provider in providers_with_results:
                        continue
                    search_provider = provider
                    fallback_steps += 1
                    try:
                        provider_results, _ = await execute_provider_search(
                            provider, search_query, result_num, result_page
                        )
                        if provider_results:
                            provider_results_map[provider] = provider_results
                            providers_with_results.add(provider)
                            route_trace.append(
                                {
                                    "phase": "trusted_fallback",
                                    "provider": provider,
                                    "status": "ok",
                                    "result_count": len(provider_results),
                                }
                            )
                        else:
                            provider_errors.append(f"{provider}: empty organic results")
                            route_trace.append(
                                {
                                    "phase": "trusted_fallback",
                                    "provider": provider,
                                    "status": "empty",
                                }
                            )
                    except Exception as exc:
                        provider_errors.append(_format_provider_error(provider, exc))
                        route_trace.append(
                            {
                                "phase": "trusted_fallback",
                                "provider": provider,
                                "status": "error",
                                "error": str(exc),
                            }
                        )
                        logger.warning(
                            "Trusted fallback provider failed | provider=%s | err=%s",
                            provider,
                            str(exc),
                        )

                    merged_results = _merge_provider_results(
                        providers, provider_results_map, result_num
                    )
                    confidence = _evaluate_confidence(
                        merged_results, providers_with_results
                    )
                    confidence_passed = (not SEARCH_CONFIDENCE_ENABLED) or confidence.get(
                        "passed", False
                    )
                    if confidence_passed and (
                        len(providers_with_results) >= SEARCH_PROVIDER_PARALLEL_MIN_SUCCESS
                    ):
                        break

                search_params["providers_with_results"] = sorted(providers_with_results)
                search_params["confidence"] = confidence
                search_params["route_trace"] = route_trace
                search_params["trusted_fallback_order"] = trusted_order
                search_params["trusted_fallback_steps"] = fallback_steps
                search_params["trusted_fallback_max_steps"] = (
                    SEARCH_PROVIDER_FALLBACK_MAX_STEPS
                )
                return merged_results, search_params, provider_errors

            if configured_mode == "merge":
                merged_results: list[dict] = []
                seen_links: set[str] = set()
                for provider in providers:
                    search_provider = provider
                    try:
                        provider_results, _ = await execute_provider_search(
                            provider, search_query, result_num, result_page
                        )
                        if not provider_results:
                            provider_errors.append(f"{provider}: empty organic results")
                            continue

                        for item in provider_results:
                            link = str(item.get("link", "")).strip()
                            title = str(item.get("title", "")).strip()
                            dedupe_key = link or title
                            if not dedupe_key or dedupe_key in seen_links:
                                continue
                            seen_links.add(dedupe_key)
                            merged_results.append(item)
                            if len(merged_results) >= result_num:
                                break

                        if len(merged_results) >= result_num:
                            break
                    except Exception as exc:
                        provider_errors.append(_format_provider_error(provider, exc))
                        logger.warning(
                            "Search provider failed in merge mode | provider=%s | err=%s",
                            provider,
                            str(exc),
                        )

                return (
                    merged_results[:result_num],
                    {
                        "q": search_query.strip(),
                        "hl": hl,
                        "gl": gl,
                        "num": result_num,
                        "page": result_page,
                        "provider": "multi-route",
                        "provider_mode": "merge",
                        "provider_order": providers,
                        "searxng_only_downgraded": searxng_only_downgraded,
                        "searxng_only_downgrade_added": searxng_only_downgrade_added,
                    },
                    provider_errors,
                )

            for provider in providers:
                search_provider = provider
                try:
                    organic_results, search_params = await execute_provider_search(
                        provider, search_query, result_num, result_page
                    )
                    if organic_results:
                        search_params["provider_mode"] = "fallback"
                        search_params["provider_order"] = providers
                        search_params["searxng_only_downgraded"] = (
                            searxng_only_downgraded
                        )
                        search_params["searxng_only_downgrade_added"] = (
                            searxng_only_downgrade_added
                        )
                        return organic_results, search_params, provider_errors

                    provider_errors.append(f"{provider}: empty organic results")

                except Exception as exc:
                    provider_errors.append(_format_provider_error(provider, exc))
                    logger.warning(
                        "Search provider failed, fallback to next provider | provider=%s | err=%s",
                        provider,
                        str(exc),
                    )

            return (
                [],
                {
                    "q": search_query.strip(),
                    "hl": hl,
                    "gl": gl,
                    "num": result_num,
                    "page": result_page,
                    "provider": search_provider,
                    "provider_mode": "fallback",
                    "provider_order": providers,
                    "searxng_only_downgraded": searxng_only_downgraded,
                    "searxng_only_downgrade_added": searxng_only_downgrade_added,
                    "fallback_errors": provider_errors,
                },
                provider_errors,
            )

        # Perform initial search
        original_query = q.strip()
        organic_results, search_params, provider_errors = await perform_search(
            original_query
        )

        # If no results and query contains quotes, retry without quotes
        if not organic_results and '"' in original_query:
            # Remove all types of quotes
            query_without_quotes = original_query.replace('"', "").strip()
            if query_without_quotes:  # Make sure we still have a valid query
                organic_results, search_params, provider_errors = await perform_search(
                    query_without_quotes
                )

        # Build comprehensive response
        response_provider = search_params.get("provider", search_provider)
        response_data = {
            "organic": organic_results,
            "searchParameters": search_params,
            "provider": response_provider,
        }
        confidence_info = search_params.get("confidence")
        if confidence_info is not None:
            response_data["confidence"] = confidence_info
        route_trace = search_params.get("route_trace")
        if route_trace is not None:
            response_data["route_trace"] = route_trace
        if provider_errors:
            response_data["provider_fallback"] = provider_errors
        response_data = decode_http_urls_in_dict(response_data)

        return json.dumps(response_data, ensure_ascii=False)

    except Exception as e:
        return json.dumps(
            {
                "success": False,
                "error": f"Unexpected error: {str(e)}",
                "results": [],
            },
            ensure_ascii=False,
        )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    retry=retry_if_exception_type(TencentCloudSDKException),
)
async def make_sogou_request(query: str, cnt: int) -> Dict[str, Any]:
    """Make request to Tencent Cloud SearchPro API with retry logic."""
    cred = credential.Credential(TENCENTCLOUD_SECRET_ID, TENCENTCLOUD_SECRET_KEY)
    httpProfile = HttpProfile()
    httpProfile.endpoint = "wsa.tencentcloudapi.com"
    clientProfile = ClientProfile()
    clientProfile.httpProfile = httpProfile

    params = f'{{"Query":"{query}","Mode":0, "Cnt":{cnt}}}'
    common_client = CommonClient("wsa", "2025-05-08", cred, "", profile=clientProfile)
    result = common_client.call_json("SearchPro", json.loads(params))["Response"]
    return result


@mcp.tool()
async def sogou_search(
    q: str,
    num: int = 10,
) -> str:
    """
    Tool to perform web searches via Tencent Cloud SearchPro API (Sogou search engine).

    Sogou search offers superior results for Chinese-language queries compared to Google.

    Args:
        q: Search query string (Required)
        num: Number of search results to return (Can only be 10/20/30/40/50, default: 10)

    Returns:
        JSON string containing search results with the following fields:
        - Query: The original search query
        - Pages: Array of search results, each containing title, url, passage, date, and site
    """
    # Check for API credentials
    if not TENCENTCLOUD_SECRET_ID or not TENCENTCLOUD_SECRET_KEY:
        return json.dumps(
            {
                "success": False,
                "error": "TENCENTCLOUD_SECRET_ID or TENCENTCLOUD_SECRET_KEY environment variable not set",
                "results": [],
            },
            ensure_ascii=False,
        )

    # Validate required parameter
    if not q or not q.strip():
        return json.dumps(
            {
                "success": False,
                "error": "Search query 'q' is required and cannot be empty",
                "results": [],
            },
            ensure_ascii=False,
        )

    # Validate num parameter
    if num not in [10, 20, 30, 40, 50]:
        return json.dumps(
            {
                "success": False,
                "error": f"Invalid num value: {num}. Must be one of 10, 20, 30, 40, 50",
                "results": [],
            },
            ensure_ascii=False,
        )

    try:
        # Make the API request
        result = await make_sogou_request(q.strip(), num)

        # Remove RequestId from response
        if "RequestId" in result:
            del result["RequestId"]

        # Process and simplify the Pages field
        pages = []
        if "Pages" in result:
            for page in result["Pages"]:
                page_json = json.loads(page)
                new_page = {
                    "title": page_json.get("title", ""),
                    "url": page_json.get("url", ""),
                    "passage": page_json.get("passage", ""),
                    "date": page_json.get("date", ""),
                    "site": page_json.get("site", ""),
                }
                pages.append(new_page)
            result["Pages"] = pages

        # Decode URLs in the response
        result = decode_http_urls_in_dict(result)

        return json.dumps(result, ensure_ascii=False)

    except TencentCloudSDKException as e:
        return json.dumps(
            {
                "success": False,
                "error": f"Tencent Cloud API error: {str(e)}",
                "results": [],
            },
            ensure_ascii=False,
        )

    except Exception as e:
        return json.dumps(
            {
                "success": False,
                "error": f"Unexpected error: {str(e)}",
                "results": [],
            },
            ensure_ascii=False,
        )


# ---------------------------------------------------------------------------
# 轻量级网页抓取工具：基于 httpx + BeautifulSoup，零外部 API 依赖。
# 用于 google_search 命中相关 URL 但 snippet 不足以给出条例原文/官方公告全文时，
# 让 LLM 主动 "打开页面看正文"。
# ---------------------------------------------------------------------------

DEFAULT_SCRAPE_TIMEOUT_SECONDS = _read_env_float("SCRAPE_TIMEOUT_SECONDS", 25.0, 1.0)
DEFAULT_SCRAPE_MAX_CHARS = _read_env_int("SCRAPE_MAX_CHARS", 10000, 500)
DEFAULT_SCRAPE_MAX_BODY_BYTES = 20 * 1024 * 1024
SCRAPE_HARD_CAP_CHARS = _read_env_int("SCRAPE_HARD_CAP_CHARS", 30000, 1000)
SCRAPE_MAX_BODY_BYTES = _read_env_int(
    "SCRAPE_MAX_BODY_BYTES", DEFAULT_SCRAPE_MAX_BODY_BYTES, 1024
)
SCRAPE_MAX_REDIRECT_HOPS = _read_env_int("SCRAPE_MAX_REDIRECT_HOPS", 5, 0)
SCRAPE_ENABLE_PDF = _read_env_bool("SCRAPE_ENABLE_PDF", True)
SCRAPE_FEED_MAX_ENTRIES = _read_env_int("SCRAPE_FEED_MAX_ENTRIES", 50, 1)
SCRAPE_USER_AGENT = os.getenv(
    "SCRAPE_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) MiroflowResearch/1.0 Chrome/126.0.0.0 Safari/537.36",
)
SCRAPE_PROXY_FAKE_IP_CIDRS = _read_env_ip_networks("SCRAPE_PROXY_FAKE_IP_CIDRS")
ALLOWED_SCRAPE_CONTENT_PREFIXES = (
    "text/html",
    "application/xhtml",
    "text/plain",
    "application/pdf",
    "application/json",
    "text/json",
    "application/rss+xml",
    "application/atom+xml",
    "application/xml",
    "text/xml",
)

# 共享 client 由 _get_scrape_client() lazy 初始化，进程结束时由 atexit 关闭。
# 引入共享 client 是为了让 LLM 在一轮研究中连续 scrape 多个 URL 时复用 TCP/TLS,
# 把第二条以后的请求 RTT 从「全新握手」降到「连接池命中」，明显降低尾延迟。
_SCRAPE_CLIENT: Optional[httpx.AsyncClient] = None
_SCRAPE_CLIENT_LOCK: Optional[asyncio.Lock] = None
_SCRAPE_ATEXIT_REGISTERED = False


def _parse_ip_literal(host: str) -> Optional[Any]:
    try:
        return ip_address(host.strip("[]"))
    except ValueError:
        return None


def _is_blocked_scrape_ip(ip: Any) -> bool:
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _is_configured_fake_ip(ip: Any) -> bool:
    return any(ip in network for network in SCRAPE_PROXY_FAKE_IP_CIDRS)


def _is_private_or_loopback_host(host: str) -> bool:
    """阻止 LLM 通过 scrape_url 访问内网/loopback，简单 SSRF 防护。"""
    if not host:
        return True
    ip_literal = _parse_ip_literal(host)
    if ip_literal is not None:
        return _is_blocked_scrape_ip(ip_literal)
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        # DNS 解析失败按拒绝处理，避免绕过
        return True
    for info in infos:
        try:
            ip = ip_address(info[4][0])
        except Exception:
            continue
        if _is_blocked_scrape_ip(ip) and not _is_configured_fake_ip(ip):
            return True
    return False


# ---------------------------------------------------------------------------
# 共享 AsyncClient（T1）
# ---------------------------------------------------------------------------


def _close_scrape_client_at_exit() -> None:
    """进程退出时尝试关闭共享 client，吃掉所有异常以免污染退出码。"""
    global _SCRAPE_CLIENT
    client = _SCRAPE_CLIENT
    if client is None:
        return
    _SCRAPE_CLIENT = None
    try:
        # 尝试在新事件循环里 aclose
        try:
            asyncio.run(client.aclose())
            return
        except RuntimeError:
            # 已有 loop 在运行（罕见），改走底层 transport.close
            pass
        transport = getattr(client, "_transport", None)
        if transport is not None and hasattr(transport, "close"):
            transport.close()
    except Exception:
        # atexit 钩子绝不抛异常
        pass


async def _get_scrape_client() -> httpx.AsyncClient:
    """返回模块级共享的 httpx.AsyncClient，懒初始化 + 单例 + atexit 关闭。"""
    global _SCRAPE_CLIENT, _SCRAPE_CLIENT_LOCK, _SCRAPE_ATEXIT_REGISTERED
    if _SCRAPE_CLIENT_LOCK is None:
        _SCRAPE_CLIENT_LOCK = asyncio.Lock()
    async with _SCRAPE_CLIENT_LOCK:
        if _SCRAPE_CLIENT is None:
            # follow_redirects=False：T2 接管重定向链以便每跳做 SSRF 校验
            _SCRAPE_CLIENT = httpx.AsyncClient(
                follow_redirects=False,
                timeout=DEFAULT_SCRAPE_TIMEOUT_SECONDS,
                headers={
                    "User-Agent": SCRAPE_USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
            )
            if not _SCRAPE_ATEXIT_REGISTERED:
                atexit.register(_close_scrape_client_at_exit)
                _SCRAPE_ATEXIT_REGISTERED = True
    return _SCRAPE_CLIENT


async def _reset_scrape_client_for_tests() -> None:
    """测试钩子：强制丢弃当前共享 client，让下一次 _get_scrape_client 重建。"""
    global _SCRAPE_CLIENT
    client = _SCRAPE_CLIENT
    _SCRAPE_CLIENT = None
    if client is not None:
        try:
            await client.aclose()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 编码识别（T4）
# ---------------------------------------------------------------------------

_META_CHARSET_RE = re.compile(
    rb'<meta[^>]+charset\s*=\s*["\']?([\w\-:]+)', re.IGNORECASE
)
_META_HTTP_EQUIV_CHARSET_RE = re.compile(
    rb'<meta[^>]+http-equiv\s*=\s*["\']?content-type["\']?'
    rb'[^>]*content\s*=\s*["\'][^"\';]*charset\s*=\s*([\w\-:]+)',
    re.IGNORECASE,
)
_XML_DECL_CHARSET_RE = re.compile(
    rb'<\?xml[^>]+encoding\s*=\s*["\']([\w\-:]+)["\']', re.IGNORECASE
)


def _extract_header_charset(content_type: str) -> Optional[str]:
    """从 Content-Type 头提取 charset，例如 'text/html; charset=GBK'。"""
    if not content_type or "charset=" not in content_type.lower():
        return None
    for part in content_type.split(";"):
        part = part.strip()
        if part.lower().startswith("charset="):
            value = part[len("charset="):].strip().strip('"\'').strip()
            return value or None
    return None


def _extract_xml_decl_charset(head_bytes: bytes) -> Optional[str]:
    if not head_bytes:
        return None
    match = _XML_DECL_CHARSET_RE.search(head_bytes)
    if not match:
        return None
    try:
        return match.group(1).decode("ascii", errors="ignore").strip() or None
    except Exception:
        return None


def _extract_meta_charset(head_bytes: bytes) -> Optional[str]:
    """从 HTML 头部前若干 KB 字节中识别 <meta charset> / <meta http-equiv>。"""
    if not head_bytes:
        return None
    match = _META_CHARSET_RE.search(head_bytes)
    if match:
        try:
            return match.group(1).decode("ascii", errors="ignore").strip()
        except Exception:
            return None
    match = _META_HTTP_EQUIV_CHARSET_RE.search(head_bytes)
    if match:
        try:
            return match.group(1).decode("ascii", errors="ignore").strip()
        except Exception:
            return None
    return None


def _decode_response_bytes(
    content: bytes, header_content_type: str
) -> Tuple[str, str]:
    """按 header → meta → charset_normalizer → utf-8(replace) 顺序解码字节体。

    返回 (text, encoding_used)。encoding_used 落在 metrics / 调试字段里，便于
    回溯 LLM 抓到的中文乱码到底是哪个步骤兜底失败。
    """
    if not content:
        return "", "utf-8"

    # 1. header charset
    charset = _extract_header_charset(header_content_type)
    if charset:
        try:
            return content.decode(charset, errors="replace"), charset.lower()
        except (LookupError, UnicodeDecodeError):
            pass

    # 2. meta charset（只看前 4KB，足够覆盖 head 区）
    meta_charset = _extract_meta_charset(content[:4096])
    if meta_charset:
        try:
            return content.decode(meta_charset, errors="replace"), meta_charset.lower()
        except (LookupError, UnicodeDecodeError):
            pass

    xml_charset = _extract_xml_decl_charset(content[:512])
    if xml_charset:
        try:
            return content.decode(xml_charset, errors="replace"), xml_charset.lower()
        except (LookupError, UnicodeDecodeError):
            pass

    # 3. charset_normalizer 自动识别（httpx 已传递依赖）
    try:
        from charset_normalizer import from_bytes  # 延迟导入

        result = from_bytes(content).best()
        if result is not None:
            encoding = (result.encoding or "utf-8").lower()
            return str(result), encoding
    except Exception:
        pass

    # 4. utf-8 replace 兜底
    return content.decode("utf-8", errors="replace"), "utf-8"


def _normalize_extracted_text(text: str) -> str:
    if not text:
        return ""
    normalized = (
        text.replace("\x00", "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\f", "\n")
    )
    normalized = re.sub(r"(?<=\w)-\n(?=\w)", "", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return "\n".join(line.strip() for line in normalized.splitlines() if line.strip())


def _detect_content_kind(content_type: str) -> str:
    if not content_type:
        return "html"
    if content_type.startswith("text/html") or content_type.startswith(
        "application/xhtml"
    ):
        return "html"
    if content_type.startswith("text/plain"):
        return "text"
    if content_type.startswith("application/pdf"):
        return "pdf"
    if content_type.startswith("application/json") or content_type.startswith(
        "text/json"
    ):
        return "json"
    if content_type.startswith("application/rss+xml"):
        return "rss"
    if content_type.startswith("application/atom+xml"):
        return "atom"
    if content_type.startswith("application/xml") or content_type.startswith(
        "text/xml"
    ):
        return "xml"
    return "unknown"


class _BodyTooLarge(Exception):
    def __init__(self, bytes_read: int, max_bytes: int):
        super().__init__(f"body too large ({bytes_read} > {max_bytes} bytes)")
        self.bytes_read = bytes_read
        self.max_bytes = max_bytes


async def _read_response_body_with_limit(
    response: httpx.Response, max_bytes: int
) -> Tuple[bytes, int]:
    declared_length = response.headers.get("content-length")
    if declared_length:
        try:
            declared_bytes = int(declared_length)
        except ValueError:
            declared_bytes = 0
        if declared_bytes > max_bytes:
            raise _BodyTooLarge(declared_bytes, max_bytes)

    content = bytearray()
    async for chunk in response.aiter_bytes():
        if not chunk:
            continue
        content.extend(chunk)
        if len(content) > max_bytes:
            raise _BodyTooLarge(len(content), max_bytes)
    return bytes(content), len(content)


def _json_type_name(value: Any) -> str:
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, bool):
        return "boolean"
    if value is None:
        return "null"
    return "number"


def _build_json_payload(decoded_text: str) -> Dict[str, Any]:
    data = json.loads(decoded_text)
    payload: Dict[str, Any] = {
        "content_kind": "json",
        "content": decoded_text.strip(),
        "json_type": _json_type_name(data),
    }
    if isinstance(data, dict):
        payload["json_keys"] = list(data.keys())[:50]
    elif isinstance(data, list):
        payload["json_length"] = len(data)
        if data and isinstance(data[0], dict):
            payload["json_item_keys"] = list(data[0].keys())[:50]
    return payload


def _xml_local_name(tag: str) -> str:
    if not tag:
        return ""
    if "}" in tag:
        return tag.rsplit("}", 1)[1].lower()
    if ":" in tag:
        return tag.split(":", 1)[1].lower()
    return tag.lower()


def _find_child(node: ET.Element, *names: str) -> Optional[ET.Element]:
    wanted = {name.lower() for name in names}
    for child in list(node):
        if _xml_local_name(child.tag) in wanted:
            return child
    return None


def _find_children(node: ET.Element, name: str) -> List[ET.Element]:
    wanted = name.lower()
    return [child for child in list(node) if _xml_local_name(child.tag) == wanted]


def _node_text(node: Optional[ET.Element]) -> str:
    if node is None:
        return ""
    return _normalize_extracted_text("\n".join(part for part in node.itertext()))


def _find_atom_link(entry: ET.Element) -> str:
    fallback = ""
    for link in _find_children(entry, "link"):
        href = (link.attrib.get("href") or "").strip()
        rel = (link.attrib.get("rel") or "alternate").strip().lower()
        if href and rel in {"", "alternate"}:
            return href
        if href and not fallback:
            fallback = href
    return fallback


def _summarize_feed_entries(feed_title: str, entries: List[Dict[str, str]]) -> str:
    lines = [feed_title] if feed_title else []
    for entry in entries:
        parts = [entry.get("title", "").strip(), entry.get("published", "").strip()]
        line = " | ".join(part for part in parts if part)
        if entry.get("link"):
            line = f"{line} | {entry['link']}" if line else entry["link"]
        if entry.get("summary"):
            line = f"{line} | {entry['summary']}" if line else entry["summary"]
        if line:
            lines.append(line)
    return _normalize_extracted_text("\n".join(lines))


def _build_xml_payload(decoded_text: str) -> Dict[str, Any]:
    root = ET.fromstring(decoded_text)
    root_name = _xml_local_name(root.tag)
    if root_name == "rss":
        channel = _find_child(root, "channel")
        if channel is None:
            channel = root
        feed_title = _node_text(_find_child(channel, "title"))
        entries = []
        for item in _find_children(channel, "item")[:SCRAPE_FEED_MAX_ENTRIES]:
            entries.append(
                {
                    "title": _node_text(_find_child(item, "title")),
                    "link": _node_text(_find_child(item, "link")),
                    "published": _node_text(
                        _find_child(item, "pubDate", "published", "updated")
                    ),
                    "summary": _node_text(
                        _find_child(item, "description", "summary", "content")
                    ),
                }
            )
        return {
            "content_kind": "rss",
            "feed_title": feed_title,
            "entries": entries,
            "content": _summarize_feed_entries(feed_title, entries),
        }
    if root_name == "feed":
        feed_title = _node_text(_find_child(root, "title"))
        entries = []
        for entry in _find_children(root, "entry")[:SCRAPE_FEED_MAX_ENTRIES]:
            entries.append(
                {
                    "title": _node_text(_find_child(entry, "title")),
                    "link": _find_atom_link(entry),
                    "published": _node_text(
                        _find_child(entry, "published", "updated")
                    ),
                    "summary": _node_text(
                        _find_child(entry, "summary", "content")
                    ),
                }
            )
        return {
            "content_kind": "atom",
            "feed_title": feed_title,
            "entries": entries,
            "content": _summarize_feed_entries(feed_title, entries),
        }
    return {
        "content_kind": "xml",
        "xml_root": root_name,
        "content": _normalize_extracted_text("\n".join(part for part in root.itertext())),
    }


def _build_pdf_payload(content: bytes) -> Dict[str, Any]:
    if not SCRAPE_ENABLE_PDF:
        raise ValueError("pdf scraping is disabled")
    from pdfminer.converter import TextConverter
    from pdfminer.layout import LAParams
    from pdfminer.pdfdocument import PDFDocument
    from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
    from pdfminer.pdfpage import PDFPage
    from pdfminer.pdfparser import PDFParser

    input_buffer = BytesIO(content)
    output_buffer = StringIO()
    pages = 0
    try:
        parser = PDFParser(input_buffer)
        document = PDFDocument(parser)
        resource_manager = PDFResourceManager()
        device = TextConverter(resource_manager, output_buffer, laparams=LAParams())
        interpreter = PDFPageInterpreter(resource_manager, device)
        try:
            for page in PDFPage.create_pages(document):
                pages += 1
                interpreter.process_page(page)
        finally:
            device.close()
        text = _normalize_extracted_text(output_buffer.getvalue())
        return {
            "content_kind": "pdf",
            "content": text,
            "pages": pages,
            "text_quality": "empty" if not text else "ok",
        }
    finally:
        input_buffer.close()
        output_buffer.close()


# ---------------------------------------------------------------------------
# 手动重定向（T2）
# ---------------------------------------------------------------------------

class _RedirectBlocked(Exception):
    """重定向链被 SSRF / 非 http(s) / 跳数上限阻断时抛出。"""

    def __init__(self, reason: str, chain: List[str]):
        super().__init__(reason)
        self.reason = reason
        self.chain = chain


async def _fetch_with_manual_redirects(
    client: httpx.AsyncClient,
    initial_url: str,
    max_hops: int,
) -> Tuple[httpx.Response, List[str]]:
    """手动跟随 30x 重定向，每跳都做 SSRF + scheme 校验。

    返回 (最终响应, 中间重定向 URL 列表，不含 initial_url)。
    """
    chain: List[str] = []
    current_url = initial_url
    for _hop in range(max_hops + 1):
        request = client.build_request("GET", current_url)
        response = await client.send(request, stream=True)
        if not (300 <= response.status_code < 400):
            return response, chain
        location = response.headers.get("location") or response.headers.get("Location")
        if not location:
            # 30x 但没 Location：当作终态返回
            return response, chain
        try:
            next_url = str(httpx.URL(current_url).join(location))
        except Exception as exc:
            await response.aclose()
            raise _RedirectBlocked(
                f"invalid redirect target: {exc}", chain + [location]
            ) from exc
        parsed = urlparse(next_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            await response.aclose()
            raise _RedirectBlocked(
                "redirect target must be absolute http(s)",
                chain + [next_url],
            )
        if _is_private_or_loopback_host(parsed.hostname or ""):
            await response.aclose()
            raise _RedirectBlocked(
                "redirect target is private/loopback/multicast host",
                chain + [next_url],
            )
        await response.aclose()
        chain.append(next_url)
        current_url = next_url
    raise _RedirectBlocked(
        f"too many redirects (hops > {max_hops})",
        chain,
    )


def _extract_main_text(html: str) -> tuple[str, str]:
    """从 HTML 抽正文与标题。优先 main/article/role=main，否则整页 text。"""
    try:
        from bs4 import BeautifulSoup  # 延迟导入，避免无 bs4 环境直接 import 失败
    except Exception as exc:
        return "", f"BeautifulSoup unavailable: {exc}"

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    for tag in soup(
        [
            "script",
            "style",
            "noscript",
            "iframe",
            "svg",
            "form",
            "header",
            "footer",
            "nav",
            "aside",
            "template",
        ]
    ):
        tag.decompose()

    candidates = []
    for selector in ("article", "main", "[role=main]", "#content", ".article", ".content"):
        for node in soup.select(selector):
            text = node.get_text(separator="\n", strip=True)
            if text and len(text) > 200:
                candidates.append(text)

    if candidates:
        text_content = max(candidates, key=len)
    else:
        text_content = soup.get_text(separator="\n", strip=True)

    text_content = _normalize_extracted_text(text_content)

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()[:300]

    return text_content, title


@mcp.tool()
async def scrape_url(url: str, max_chars: int = DEFAULT_SCRAPE_MAX_CHARS) -> str:
    """
    Fetch a webpage and return its main textual content.

    Use this tool when google_search snippets are insufficient and you need the full
    article / regulation / official announcement / report text. Best to call this
    immediately after google_search returns a relevant URL whose snippet hints at,
    but does not contain, the specific detail you need (条例原文、公告全文、统计数字、
    完整法规名称等). Pass the EXACT absolute URL from the search result.

    Args:
        url: Absolute http(s) URL to scrape (required, e.g. https://example.com/page)
        max_chars: Maximum characters of textual content to return (default 10000,
                   hard cap 30000). Use a smaller value when you only need a quick
                   confirmation; use a larger value when you need full text.

    Returns:
        JSON string with fields:
        - success: bool
        - url, final_url, http_status, title, content, content_type, encoding
        - content_length, truncated
        - redirect_chain (list[str], only present when redirects happened)
        - metrics: {t_request_ms, t_parse_ms, t_extract_ms, redirect_hops}
        - error (only present when success=False)
    """
    metrics: Dict[str, Any] = {
        "t_request_ms": 0,
        "t_parse_ms": 0,
        "t_extract_ms": 0,
        "redirect_hops": 0,
    }

    if not url or not isinstance(url, str):
        return json.dumps(
            {
                "success": False,
                "error": "url is required and must be a string",
                "url": url,
                "metrics": metrics,
            },
            ensure_ascii=False,
        )

    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return json.dumps(
            {
                "success": False,
                "error": "only absolute http(s) URLs are supported",
                "url": url,
                "metrics": metrics,
            },
            ensure_ascii=False,
        )

    if _is_private_or_loopback_host(parsed.hostname or ""):
        return json.dumps(
            {
                "success": False,
                "error": "private/loopback/multicast hosts are blocked",
                "url": url,
                "metrics": metrics,
            },
            ensure_ascii=False,
        )

    try:
        cap_chars = int(max_chars or DEFAULT_SCRAPE_MAX_CHARS)
    except (TypeError, ValueError):
        cap_chars = DEFAULT_SCRAPE_MAX_CHARS
    cap_chars = max(500, min(cap_chars, SCRAPE_HARD_CAP_CHARS))

    redirect_chain: List[str] = []
    response: Optional[httpx.Response] = None
    response_content = b""
    final_url = url
    raw_content_type = ""
    content_type = ""
    content_kind = "html"
    bytes_read = 0
    request_started = time.perf_counter()
    try:
        client = await _get_scrape_client()
        response, redirect_chain = await _fetch_with_manual_redirects(
            client, url, SCRAPE_MAX_REDIRECT_HOPS
        )
        metrics["redirect_hops"] = len(redirect_chain)
        final_url = str(response.url)
        raw_content_type = response.headers.get("content-type") or ""
        content_type = raw_content_type.split(";")[0].strip().lower()
        content_kind = _detect_content_kind(content_type)

        if content_type and not any(
            content_type.startswith(prefix) for prefix in ALLOWED_SCRAPE_CONTENT_PREFIXES
        ):
            metrics["t_request_ms"] = int((time.perf_counter() - request_started) * 1000)
            payload: Dict[str, Any] = {
                "success": False,
                "error": f"unsupported content_type {content_type!r}; only HTML/text/PDF/JSON/XML are extracted",
                "url": url,
                "final_url": final_url,
                "http_status": response.status_code,
                "content_type": content_type,
                "content_kind": content_kind,
                "bytes_read": bytes_read,
                "metrics": metrics,
            }
            if redirect_chain:
                payload["redirect_chain"] = redirect_chain
            return json.dumps(payload, ensure_ascii=False)

        if response.status_code >= 400:
            metrics["t_request_ms"] = int((time.perf_counter() - request_started) * 1000)
            payload = {
                "success": False,
                "error": f"http_status={response.status_code}",
                "url": url,
                "final_url": final_url,
                "http_status": response.status_code,
                "content_type": content_type,
                "content_kind": content_kind,
                "bytes_read": bytes_read,
                "metrics": metrics,
            }
            if redirect_chain:
                payload["redirect_chain"] = redirect_chain
            return json.dumps(payload, ensure_ascii=False)

        response_content, bytes_read = await _read_response_body_with_limit(
            response, SCRAPE_MAX_BODY_BYTES
        )
    except _RedirectBlocked as exc:
        metrics["t_request_ms"] = int((time.perf_counter() - request_started) * 1000)
        metrics["redirect_hops"] = len(exc.chain)
        return json.dumps(
            {
                "success": False,
                "error": f"redirect_blocked: {exc.reason}",
                "url": url,
                "redirect_chain": exc.chain,
                "metrics": metrics,
            },
            ensure_ascii=False,
        )
    except _BodyTooLarge as exc:
        metrics["t_request_ms"] = int((time.perf_counter() - request_started) * 1000)
        return json.dumps(
            {
                "success": False,
                "error": f"body too large: {exc.bytes_read} > {exc.max_bytes} bytes",
                "url": url,
                "final_url": final_url,
                "http_status": response.status_code if response is not None else None,
                "content_type": content_type,
                "content_kind": content_kind,
                "bytes_read": exc.bytes_read,
                "metrics": metrics,
            },
            ensure_ascii=False,
        )
    except httpx.TimeoutException:
        metrics["t_request_ms"] = int((time.perf_counter() - request_started) * 1000)
        return json.dumps(
            {
                "success": False,
                "error": "request timed out",
                "url": url,
                "metrics": metrics,
            },
            ensure_ascii=False,
        )
    except httpx.HTTPError as exc:
        metrics["t_request_ms"] = int((time.perf_counter() - request_started) * 1000)
        return json.dumps(
            {
                "success": False,
                "error": f"http error: {type(exc).__name__}: {exc}",
                "url": url,
                "metrics": metrics,
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        metrics["t_request_ms"] = int((time.perf_counter() - request_started) * 1000)
        return json.dumps(
            {
                "success": False,
                "error": f"unexpected error: {type(exc).__name__}: {exc}",
                "url": url,
                "metrics": metrics,
            },
            ensure_ascii=False,
        )
    finally:
        if response is not None:
            try:
                await response.aclose()
            except Exception:
                pass

    metrics["t_request_ms"] = int((time.perf_counter() - request_started) * 1000)

    structured_payload: Dict[str, Any] = {}
    title = ""
    text_content = ""
    encoding_used = "utf-8"

    try:
        if content_kind == "pdf":
            extract_started = time.perf_counter()
            structured_payload = _build_pdf_payload(response_content)
            metrics["t_extract_ms"] = int((time.perf_counter() - extract_started) * 1000)
            text_content = structured_payload.pop("content", "")
            encoding_used = "binary/pdf"
        else:
            parse_started = time.perf_counter()
            decoded_text, encoding_used = _decode_response_bytes(
                response_content, raw_content_type
            )
            metrics["t_parse_ms"] = int((time.perf_counter() - parse_started) * 1000)

            extract_started = time.perf_counter()
            if content_kind == "text":
                text_content = _normalize_extracted_text(decoded_text)
            elif content_kind == "html":
                text_content, title = _extract_main_text(decoded_text)
            elif content_kind == "json":
                structured_payload = _build_json_payload(decoded_text)
                text_content = structured_payload.pop("content", "")
            else:
                structured_payload = _build_xml_payload(decoded_text)
                content_kind = structured_payload.get("content_kind", content_kind)
                title = structured_payload.get("feed_title", "")
                text_content = structured_payload.pop("content", "")
            metrics["t_extract_ms"] = int((time.perf_counter() - extract_started) * 1000)
    except Exception as exc:
        payload = {
            "success": False,
            "error": f"extract_failed: {type(exc).__name__}: {exc}",
            "url": url,
            "final_url": final_url,
            "http_status": response.status_code if response is not None else None,
            "content_type": content_type,
            "content_kind": content_kind,
            "bytes_read": bytes_read,
            "metrics": metrics,
        }
        if redirect_chain:
            payload["redirect_chain"] = redirect_chain
        return json.dumps(payload, ensure_ascii=False)

    truncated = len(text_content) > cap_chars
    if truncated:
        text_content = text_content[:cap_chars]

    payload = {
        "success": True,
        "url": url,
        "final_url": final_url,
        "http_status": response.status_code,
        "title": title,
        "content": text_content,
        "content_type": content_type or "text/html",
        "content_kind": content_kind,
        "encoding": encoding_used,
        "content_length": len(text_content),
        "truncated": truncated,
        "bytes_read": bytes_read,
        "metrics": metrics,
    }
    payload.update(structured_payload)
    if redirect_chain:
        payload["redirect_chain"] = redirect_chain
    return json.dumps(payload, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
