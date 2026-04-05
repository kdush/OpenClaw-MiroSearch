# Copyright (c) 2025 MiroMind
# This source code is licensed under the Apache 2.0 License.

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

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

# Configure logging
logger = logging.getLogger("miroflow")

# ---------------------------------------------------------------------------
# Provider 注册中心（模块加载时自动初始化）
# ---------------------------------------------------------------------------
_registry = ProviderRegistry()
_registry.register(SerperProvider())
_registry.register(SerpAPIProvider())
_registry.register(SearXNGProvider())
DEFAULT_SEARCH_PROVIDER_ORDER = "searxng,serpapi,serper"
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
DEFAULT_SEARCH_PROVIDER_TRUSTED_ORDER = "serpapi,searxng,serper"
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
DEFAULT_SEARCH_SEARXNG_ONLY_DOWNGRADE_ORDER = "serpapi,serper"

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


if __name__ == "__main__":
    mcp.run()
