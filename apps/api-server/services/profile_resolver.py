"""检索任务策略解析器。

将 demo 投递的五个字段（mode / search_profile / search_result_num /
verification_min_search_rounds / output_detail_level）转换为：

1. 一组进程环境变量（注入给检索 MCP 子进程）；
2. 一组 hydra overrides（控制 agent yaml、模型名、最大轮次、token 上限等）。

本模块独立于 gradio-demo，所有可调常量均通过 ``os.getenv`` 读取，便于 worker
与 demo 在共用配置文件下保持行为一致。新增 mode/profile 时请同步在此处与
``apps/gradio-demo/main.py`` 内的相关定义一并更新（见 ``MODE_OVERRIDE_MAP`` /
``SEARCH_PROFILE_ENV_MAP``）。
"""

from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("api-server.profile_resolver")


# ---- 基础工具 ----------------------------------------------------------
def _env_int(name: str, default_value: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default_value
    try:
        return int(raw)
    except ValueError:
        return default_value


# ---- 默认值（每次调用读取，便于 worker 启动后热更新 env） ----------------
def _default_research_mode() -> str:
    return os.getenv("DEFAULT_RESEARCH_MODE", "balanced")


def _default_search_profile() -> str:
    return os.getenv("DEFAULT_SEARCH_PROFILE", "searxng-first")


SEARCH_RESULT_NUM_CHOICES = [10, 20, 30]


def _default_search_result_num() -> int:
    return _env_int("DEFAULT_SEARCH_RESULT_NUM", SEARCH_RESULT_NUM_CHOICES[1])


def _default_verification_min_search_rounds() -> int:
    return max(1, _env_int("DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS", 3))


MAX_VERIFICATION_MIN_SEARCH_ROUNDS = 8

OUTPUT_DETAIL_LEVEL_CHOICES = ["compact", "balanced", "detailed"]


def _default_output_detail_level() -> str:
    val = os.getenv("DEFAULT_OUTPUT_DETAIL_LEVEL", "detailed")
    if val not in OUTPUT_DETAIL_LEVEL_CHOICES:
        return "detailed"
    return val


# ---- 模型名读取（与 demo 完全一致） --------------------------------------
def _default_model_name() -> str:
    return os.getenv("DEFAULT_MODEL_NAME", "gpt-4o-mini")


def _default_model_tool_name() -> str:
    return os.getenv("MODEL_TOOL_NAME", _default_model_name())


def _default_model_fast_name() -> str:
    return os.getenv("MODEL_FAST_NAME", _default_model_name())


def _default_model_thinking_name() -> str:
    return os.getenv("MODEL_THINKING_NAME", _default_model_name())


def _default_model_summary_name() -> str:
    return os.getenv("MODEL_SUMMARY_NAME", _default_model_fast_name())


# ---- 检索源策略 → 进程环境变量映射 --------------------------------------
SEARCH_PROFILE_ENV_MAP: Dict[str, Dict[str, str]] = {
    "searxng-first": {
        "SEARCH_PROVIDER_ORDER": "searxng,serpapi,tavily,serper",
        "SEARCH_PROVIDER_MODE": "fallback",
    },
    "serp-first": {
        "SEARCH_PROVIDER_ORDER": "serpapi,tavily,searxng,serper",
        "SEARCH_PROVIDER_MODE": "fallback",
    },
    "multi-route": {
        "SEARCH_PROVIDER_ORDER": "serpapi,tavily,searxng,serper",
        "SEARCH_PROVIDER_MODE": "merge",
    },
    "parallel": {
        "SEARCH_PROVIDER_ORDER": "serpapi,tavily,searxng,serper",
        "SEARCH_PROVIDER_MODE": "parallel",
        "SEARCH_PROVIDER_PARALLEL_MAX_WAIT_MS": "4500",
        "SEARCH_PROVIDER_PARALLEL_MIN_SUCCESS": "1",
    },
    "parallel-trusted": {
        "SEARCH_PROVIDER_ORDER": "serpapi,tavily,searxng,serper",
        "SEARCH_PROVIDER_MODE": "parallel_conf_fallback",
        "SEARCH_PROVIDER_TRUSTED_ORDER": "serpapi,tavily,searxng,serper",
        "SEARCH_PROVIDER_PARALLEL_MAX_WAIT_MS": "4500",
        "SEARCH_PROVIDER_PARALLEL_MIN_SUCCESS": "1",
        "SEARCH_PROVIDER_FALLBACK_MAX_STEPS": "3",
        "SEARCH_CONFIDENCE_ENABLED": "1",
        "SEARCH_CONFIDENCE_SCORE_THRESHOLD": "0.62",
        "SEARCH_CONFIDENCE_MIN_RESULTS": "8",
        "SEARCH_CONFIDENCE_MIN_UNIQUE_DOMAINS": "5",
        "SEARCH_CONFIDENCE_MIN_PROVIDER_COVERAGE": "2",
        "SEARCH_CONFIDENCE_MIN_HIGH_CONF_HITS": "2",
        "SEARCH_CONFIDENCE_HIGH_CONF_DOMAINS": (
            "reuters.com,apnews.com,bbc.com,aljazeera.com,"
            "state.gov,un.org,iaea.org,who.int"
        ),
    },
    "searxng-only": {
        "SEARCH_PROVIDER_ORDER": "searxng",
        "SEARCH_PROVIDER_MODE": "fallback",
    },
}

# 已知 mode 集合（与 _build_mode_overrides 内的分支一一对应）
MODE_CHOICES = frozenset(
    {"production-web", "verified", "research", "balanced", "quota", "thinking"}
)


# ---- normalize 函数 -----------------------------------------------------
def normalize_research_mode(mode: Optional[str]) -> str:
    if not mode:
        mode = _default_research_mode()
    normalized = str(mode).strip().lower()
    if normalized not in MODE_CHOICES:
        logger.warning("未知检索模式 %s，回退到 balanced", mode)
        return "balanced"
    return normalized


def normalize_search_profile(profile: Optional[str]) -> str:
    if not profile:
        profile = _default_search_profile()
    normalized = str(profile).strip().lower()
    if normalized not in SEARCH_PROFILE_ENV_MAP:
        logger.warning("未知检索源策略 %s，回退到 searxng-first", profile)
        return "searxng-first"
    return normalized


def normalize_search_result_num(value: Optional[int]) -> int:
    if value is None:
        value = _default_search_result_num()
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = _default_search_result_num()
    if parsed in SEARCH_RESULT_NUM_CHOICES:
        return parsed
    return SEARCH_RESULT_NUM_CHOICES[0]


def normalize_verification_min_search_rounds(value: Optional[int]) -> int:
    if value is None:
        value = _default_verification_min_search_rounds()
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = _default_verification_min_search_rounds()
    return max(1, min(MAX_VERIFICATION_MIN_SEARCH_ROUNDS, parsed))


def resolve_effective_min_search_rounds(
    mode: Optional[str], rounds: Optional[int]
) -> int:
    """仅在 verified 模式启用最少检索轮次，其它模式固定为默认值。"""
    if normalize_research_mode(mode) != "verified":
        return normalize_verification_min_search_rounds(
            _default_verification_min_search_rounds()
        )
    return normalize_verification_min_search_rounds(rounds)


def normalize_output_detail_level(level: Optional[str]) -> str:
    resolved_default = _default_output_detail_level()
    if level is None:
        return resolved_default
    normalized = str(level).strip().lower()
    if normalized in OUTPUT_DETAIL_LEVEL_CHOICES:
        return normalized
    logger.warning("未知输出篇幅档位 %s，回退到 %s", level, resolved_default)
    return resolved_default


# ---- 输出篇幅档位 → hydra overrides ------------------------------------
def get_mode_overrides_for_output_detail(level: Optional[str]) -> List[str]:
    resolved = normalize_output_detail_level(level)
    if resolved == "compact":
        max_tokens = max(1024, _env_int("DETAIL_COMPACT_MAX_TOKENS", 2400))
        tool_chars = max(2000, _env_int("DETAIL_COMPACT_TOOL_RESULT_MAX_CHARS", 2600))
        summary_tokens = max(1024, _env_int("DETAIL_COMPACT_SUMMARY_MAX_TOKENS", 2048))
        verification_tokens = max(
            1024, _env_int("DETAIL_COMPACT_VERIFICATION_MAX_TOKENS", 1536)
        )
        keep_tool = max(-1, _env_int("DETAIL_COMPACT_KEEP_TOOL_RESULT", 1))
        compress = max(0, _env_int("DETAIL_COMPACT_CONTEXT_COMPRESS_LIMIT", 1))
        max_turns = max(1, _env_int("DETAIL_COMPACT_MAIN_AGENT_MAX_TURNS", 8))
        return [
            "++agent.output_detail_level=compact",
            "++agent.research_report_mode=true",
            f"agent.main_agent.max_turns={max_turns}",
            f"agent.keep_tool_result={keep_tool}",
            f"agent.context_compress_limit={compress}",
            f"llm.max_tokens={max_tokens}",
            f"llm.tool_result_max_chars={tool_chars}",
            f"llm.summary_max_tokens={summary_tokens}",
            f"llm.verification_max_tokens={verification_tokens}",
        ]
    if resolved == "balanced":
        max_tokens = max(1024, _env_int("DETAIL_BALANCED_MAX_TOKENS", 4096))
        tool_chars = max(
            2000, _env_int("DETAIL_BALANCED_TOOL_RESULT_MAX_CHARS", 5000)
        )
        summary_tokens = max(
            1024, _env_int("DETAIL_BALANCED_SUMMARY_MAX_TOKENS", 4096)
        )
        verification_tokens = max(
            1024, _env_int("DETAIL_BALANCED_VERIFICATION_MAX_TOKENS", 3072)
        )
        keep_tool = max(-1, _env_int("DETAIL_BALANCED_KEEP_TOOL_RESULT", 2))
        compress = max(0, _env_int("DETAIL_BALANCED_CONTEXT_COMPRESS_LIMIT", 2))
        max_turns = max(1, _env_int("DETAIL_BALANCED_MAIN_AGENT_MAX_TURNS", 10))
        return [
            "++agent.output_detail_level=balanced",
            "++agent.research_report_mode=true",
            f"agent.main_agent.max_turns={max_turns}",
            f"agent.keep_tool_result={keep_tool}",
            f"agent.context_compress_limit={compress}",
            f"llm.max_tokens={max_tokens}",
            f"llm.tool_result_max_chars={tool_chars}",
            f"llm.summary_max_tokens={summary_tokens}",
            f"llm.verification_max_tokens={verification_tokens}",
        ]
    # detailed
    max_tokens = max(1024, _env_int("DETAIL_DETAILED_MAX_TOKENS", 16384))
    tool_chars = max(2000, _env_int("DETAIL_DETAILED_TOOL_RESULT_MAX_CHARS", 20000))
    summary_tokens = max(1024, _env_int("DETAIL_DETAILED_SUMMARY_MAX_TOKENS", 16384))
    verification_tokens = max(
        1024, _env_int("DETAIL_DETAILED_VERIFICATION_MAX_TOKENS", 12288)
    )
    keep_tool = max(-1, _env_int("DETAIL_DETAILED_KEEP_TOOL_RESULT", -1))
    compress = max(0, _env_int("DETAIL_DETAILED_CONTEXT_COMPRESS_LIMIT", 0))
    max_turns = max(1, _env_int("DETAIL_DETAILED_MAIN_AGENT_MAX_TURNS", 20))
    return [
        "++agent.output_detail_level=detailed",
        "++agent.research_report_mode=true",
        f"agent.main_agent.max_turns={max_turns}",
        f"agent.keep_tool_result={keep_tool}",
        f"agent.context_compress_limit={compress}",
        f"llm.max_tokens={max_tokens}",
        f"llm.tool_result_max_chars={tool_chars}",
        f"llm.summary_max_tokens={summary_tokens}",
        f"llm.verification_max_tokens={verification_tokens}",
    ]


# ---- 检索模式 → hydra overrides ----------------------------------------
def build_mode_overrides(mode: str) -> List[str]:
    """根据已 normalize 的 mode 构建 hydra overrides。"""
    model_name = _default_model_name()
    model_tool = _default_model_tool_name()
    model_fast = _default_model_fast_name()
    model_thinking = _default_model_thinking_name()
    model_summary = _default_model_summary_name()

    if mode == "production-web":
        max_tokens = max(1024, _env_int("PRODUCTION_WEB_LLM_MAX_TOKENS", 3072))
        tool_chars = max(2000, _env_int("PRODUCTION_WEB_TOOL_RESULT_MAX_CHARS", 6000))
        return [
            "agent=prod_search_only",
            "agent.main_agent.max_turns=12",
            "agent.keep_tool_result=-1",
            "agent.context_compress_limit=0",
            "agent.retry_with_summary=false",
            f"llm.model_name={model_name}",
            f"+llm.model_tool_name={model_tool}",
            f"+llm.model_fast_name={model_fast}",
            f"+llm.model_thinking_name={model_thinking}",
            f"+llm.model_summary_name={model_summary}",
            f"llm.max_tokens={max_tokens}",
            "+llm.max_retries=3",
            "+llm.retry_wait_seconds=3",
            f"llm.tool_result_max_chars={tool_chars}",
        ]
    if mode == "verified":
        max_tokens = max(1024, _env_int("VERIFIED_LLM_MAX_TOKENS", 3072))
        tool_chars = max(2000, _env_int("VERIFIED_TOOL_RESULT_MAX_CHARS", 6000))
        keep_tool = max(-1, _env_int("VERIFIED_KEEP_TOOL_RESULT", 4))
        compress = max(0, _env_int("VERIFIED_CONTEXT_COMPRESS_LIMIT", 3))
        return [
            "agent=demo_verified_search",
            "agent.main_agent.max_turns=14",
            f"agent.keep_tool_result={keep_tool}",
            f"agent.context_compress_limit={compress}",
            "agent.retry_with_summary=false",
            f"llm.model_name={model_name}",
            f"+llm.model_tool_name={model_tool}",
            f"+llm.model_fast_name={model_fast}",
            f"+llm.model_thinking_name={model_thinking}",
            f"+llm.model_summary_name={model_summary}",
            f"llm.max_tokens={max_tokens}",
            "+llm.max_retries=3",
            "+llm.retry_wait_seconds=3",
            f"llm.tool_result_max_chars={tool_chars}",
        ]
    if mode == "research":
        max_tokens = max(1024, _env_int("RESEARCH_LLM_MAX_TOKENS", 3072))
        tool_chars = max(2000, _env_int("RESEARCH_TOOL_RESULT_MAX_CHARS", 6000))
        keep_tool = max(-1, _env_int("RESEARCH_KEEP_TOOL_RESULT", 3))
        compress = max(0, _env_int("RESEARCH_CONTEXT_COMPRESS_LIMIT", 2))
        return [
            "agent=demo_search_only",
            "agent.main_agent.max_turns=10",
            f"agent.keep_tool_result={keep_tool}",
            f"agent.context_compress_limit={compress}",
            "agent.retry_with_summary=false",
            f"llm.model_name={model_name}",
            f"+llm.model_tool_name={model_tool}",
            f"+llm.model_fast_name={model_fast}",
            f"+llm.model_thinking_name={model_thinking}",
            f"+llm.model_summary_name={model_summary}",
            f"llm.max_tokens={max_tokens}",
            "+llm.max_retries=4",
            "+llm.retry_wait_seconds=6",
            f"llm.tool_result_max_chars={tool_chars}",
        ]
    if mode == "quota":
        max_tokens = max(1024, _env_int("QUOTA_LLM_MAX_TOKENS", 2048))
        tool_chars = max(2000, _env_int("QUOTA_TOOL_RESULT_MAX_CHARS", 3500))
        keep_tool = max(-1, _env_int("QUOTA_KEEP_TOOL_RESULT", 2))
        compress = max(0, _env_int("QUOTA_CONTEXT_COMPRESS_LIMIT", 1))
        return [
            "agent=demo_search_only",
            "agent.main_agent.max_turns=7",
            f"agent.keep_tool_result={keep_tool}",
            f"agent.context_compress_limit={compress}",
            "agent.retry_with_summary=false",
            f"llm.model_name={model_fast}",
            f"+llm.model_tool_name={model_fast}",
            f"+llm.model_fast_name={model_fast}",
            f"+llm.model_thinking_name={model_thinking}",
            f"+llm.model_summary_name={model_summary}",
            f"llm.max_tokens={max_tokens}",
            "+llm.max_retries=2",
            "+llm.retry_wait_seconds=2",
            f"llm.tool_result_max_chars={tool_chars}",
        ]
    if mode == "thinking":
        max_tokens = max(1024, _env_int("THINKING_LLM_MAX_TOKENS", 3072))
        tool_chars = max(2000, _env_int("THINKING_TOOL_RESULT_MAX_CHARS", 3000))
        return [
            "agent=demo_no_tools",
            "agent.main_agent.max_turns=6",
            "agent.keep_tool_result=0",
            "agent.context_compress_limit=1",
            "agent.retry_with_summary=false",
            f"llm.model_name={model_thinking}",
            f"+llm.model_tool_name={model_tool}",
            f"+llm.model_fast_name={model_fast}",
            f"+llm.model_thinking_name={model_thinking}",
            f"+llm.model_summary_name={model_summary}",
            f"llm.max_tokens={max_tokens}",
            "+llm.max_retries=2",
            "+llm.retry_wait_seconds=2",
            f"llm.tool_result_max_chars={tool_chars}",
        ]
    # balanced（默认 fallback）
    max_tokens = max(1024, _env_int("BALANCED_LLM_MAX_TOKENS", 3072))
    tool_chars = max(2000, _env_int("BALANCED_TOOL_RESULT_MAX_CHARS", 5000))
    keep_tool = max(-1, _env_int("BALANCED_KEEP_TOOL_RESULT", 3))
    compress = max(0, _env_int("BALANCED_CONTEXT_COMPRESS_LIMIT", 2))
    return [
        "agent=demo_search_only",
        "agent.main_agent.max_turns=11",
        f"agent.keep_tool_result={keep_tool}",
        f"agent.context_compress_limit={compress}",
        "agent.retry_with_summary=false",
        f"llm.model_name={model_name}",
        f"+llm.model_tool_name={model_tool}",
        f"+llm.model_fast_name={model_fast}",
        f"+llm.model_thinking_name={model_thinking}",
        f"+llm.model_summary_name={model_summary}",
        f"llm.max_tokens={max_tokens}",
        "+llm.max_retries=2",
        "+llm.retry_wait_seconds=2",
        f"llm.tool_result_max_chars={tool_chars}",
    ]


# ---- 顶层聚合 ----------------------------------------------------------
def build_search_env(profile: str, result_num: int) -> Dict[str, str]:
    base = dict(
        SEARCH_PROFILE_ENV_MAP.get(profile, SEARCH_PROFILE_ENV_MAP["searxng-first"])
    )
    base["SEARCH_RESULT_NUM"] = str(result_num)
    return base


def build_full_overrides(
    mode: str,
    search_profile: str,
    search_result_num: int,
    verification_min_search_rounds: int,
    output_detail_level: str,
) -> Tuple[Dict[str, str], List[str]]:
    """根据请求参数构建 (search_env_dict, hydra_overrides_list)。

    返回值已 normalize 完毕，可直接用于进程 env 注入与 hydra compose。
    """
    resolved_mode = normalize_research_mode(mode)
    resolved_profile = normalize_search_profile(search_profile)
    resolved_result_num = normalize_search_result_num(search_result_num)
    resolved_min_rounds = resolve_effective_min_search_rounds(
        resolved_mode, verification_min_search_rounds
    )
    resolved_detail = normalize_output_detail_level(output_detail_level)

    search_env = build_search_env(resolved_profile, resolved_result_num)
    overrides = list(build_mode_overrides(resolved_mode))
    overrides.extend(get_mode_overrides_for_output_detail(resolved_detail))
    if resolved_mode == "verified":
        overrides.append(
            f"agent.verification.min_search_rounds={resolved_min_rounds}"
        )
    return search_env, overrides
