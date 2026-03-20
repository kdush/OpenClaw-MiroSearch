import base64
import asyncio
import html
import json
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import AsyncGenerator, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

import gradio as gr
from dotenv import load_dotenv
from hydra import compose, initialize_config_dir
from omegaconf import DictConfig
from prompt_patch import apply_prompt_patch
from src.config.settings import expose_sub_agents_as_tools
from src.core.pipeline import create_pipeline_components, execute_task_pipeline
from utils import replace_chinese_punctuation

# Create global cleanup thread pool for operations that won't be affected by asyncio.cancel
cleanup_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cleanup")

logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv()


def _env_flag(name: str, default_value: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default_value
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default_value: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default_value
    try:
        return int(raw_value)
    except ValueError:
        return default_value


def _load_logo_data_uri() -> str:
    """加载本地 Logo 并转换为 data URI，避免依赖外部静态资源服务。"""
    logo_path = Path(__file__).resolve().parents[2] / "assets" / "mirologo.png"
    if not logo_path.exists():
        logger.warning("未找到本地 logo 文件: %s", logo_path)
        return ""
    try:
        logo_bytes = logo_path.read_bytes()
    except OSError as exc:
        logger.warning("读取本地 logo 失败: %s", exc)
        return ""
    encoded_logo = base64.b64encode(logo_bytes).decode("ascii")
    return f"data:image/png;base64,{encoded_logo}"


def _build_fallback_favicon_data_uri() -> str:
    """生成内联 SVG favicon，避免缺失本地 logo 时依赖远程资源。"""
    svg_markup = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        '<rect width="64" height="64" rx="14" fill="#0f766e"/>'
        '<path d="M18 44V20h10c6.5 0 11 4.3 11 10.2c0 6-4.7 10.4-11 10.4h-3.5V44H18z" fill="#ffffff"/>'
        '<circle cx="45" cy="44" r="5" fill="#99f6e4"/>'
        "</svg>"
    )
    encoded_svg = base64.b64encode(svg_markup.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded_svg}"


def _resolve_safe_skills_package_path() -> Path:
    """将 skills 包路径限制在仓库 skills 目录中，防止越权读取。"""
    safe_dir = SKILLS_PACKAGE_SAFE_DIR.resolve()
    fallback_path = (safe_dir / SKILLS_PACKAGE_DEFAULT_FILENAME).resolve()
    candidate_path = Path(DEFAULT_SKILLS_PACKAGE_PATH).expanduser()
    try:
        resolved_candidate = candidate_path.resolve()
    except OSError as exc:
        logger.warning("解析 SKILLS_PACKAGE_PATH 失败，使用默认路径: %s", exc)
        return fallback_path
    if (
        safe_dir in resolved_candidate.parents
        and resolved_candidate.suffix.lower() == ".zip"
    ):
        return resolved_candidate
    logger.warning(
        "检测到不安全的 skills 包路径，已回退默认路径: %s", resolved_candidate
    )
    return fallback_path


def _sanitize_skills_download_url(raw_url: str) -> str:
    """校验下载 URL，防止脚本注入与危险协议。"""
    candidate = (raw_url or "").strip()
    if not candidate:
        return ""
    if any(char in candidate for char in ("\r", "\n", "\x00")):
        logger.warning("检测到非法下载 URL（包含控制字符）")
        return ""
    parsed = urlparse(candidate)
    if not parsed.scheme and not parsed.netloc:
        if candidate.startswith(SKILLS_DOWNLOAD_ALLOWED_RELATIVE_PREFIX):
            return candidate
        logger.warning("检测到非法相对下载 URL: %s", candidate)
        return ""
    if parsed.scheme not in SKILLS_DOWNLOAD_ALLOWED_SCHEMES:
        logger.warning("检测到非法下载 URL 协议: %s", parsed.scheme)
        return ""
    if not parsed.netloc:
        logger.warning("检测到非法下载 URL（缺少主机）: %s", candidate)
        return ""
    if parsed.username or parsed.password:
        logger.warning("检测到非法下载 URL（包含用户信息）: %s", candidate)
        return ""
    return candidate


def _resolve_skills_package_download() -> Tuple[str, str]:
    """解析 skills 包下载地址，优先使用环境变量，回退到本地文件路由。"""
    package_path = _resolve_safe_skills_package_path()
    configured_url = _sanitize_skills_download_url(DEFAULT_SKILLS_PACKAGE_URL)
    if configured_url:
        return configured_url, package_path.as_posix()
    if package_path.exists():
        absolute_path = package_path.resolve().as_posix()
        encoded_path = quote(absolute_path, safe="/")
        return f"/gradio_api/file={encoded_path}", absolute_path
    return "", package_path.as_posix()


def _collect_gradio_allowed_paths() -> List[str]:
    """收集 Gradio 需要放行的本地文件路径。"""
    allowed_paths: List[str] = []
    skills_package_path = _resolve_safe_skills_package_path()
    if skills_package_path.exists():
        allowed_paths.append(str(skills_package_path.resolve()))
    return allowed_paths


# 控制是否启用 demo prompt patch
ENABLE_PROMPT_PATCH = _env_flag("ENABLE_PROMPT_PATCH", True)
if ENABLE_PROMPT_PATCH:
    # 应用自定义系统提示补丁（注入 OpenClaw-MiroSearch 身份）
    apply_prompt_patch()

# 允许通过环境变量控制 DEMO_MODE，默认开启
DEMO_MODE_ENABLED = _env_flag("DEMO_MODE", True)
os.environ["DEMO_MODE"] = "1" if DEMO_MODE_ENABLED else "0"

# Global Hydra initialization flag
_hydra_initialized = False

DEFAULT_MAIN_AGENT_MAX_TURNS = 12
DEFAULT_KEEP_TOOL_RESULT = 2
DEFAULT_CONTEXT_COMPRESS_LIMIT = 2
DEFAULT_RETRY_WITH_SUMMARY = False
DEFAULT_LLM_TEMPERATURE = 0.2
DEFAULT_LLM_MAX_TOKENS = 2048
DEFAULT_BENCHMARK_NAME = "debug"
DEFAULT_RESEARCH_MODE = os.getenv("DEFAULT_RESEARCH_MODE", "balanced")
DEFAULT_SEARCH_PROFILE = os.getenv("DEFAULT_SEARCH_PROFILE", "searxng-first")
SEARCH_RESULT_NUM_CHOICES = [10, 20, 30]
DEFAULT_SEARCH_RESULT_NUM = _env_int(
    "DEFAULT_SEARCH_RESULT_NUM", SEARCH_RESULT_NUM_CHOICES[1]
)
SEARCH_RESULT_DISPLAY_MAX = max(1, _env_int("SEARCH_RESULT_DISPLAY_MAX", 30))
DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS = max(
    1, _env_int("DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS", 3)
)
MAX_VERIFICATION_MIN_SEARCH_ROUNDS = 8
DEFAULT_MODEL_NAME = os.getenv("DEFAULT_MODEL_NAME", "gpt-4o-mini")
DEFAULT_MODEL_TOOL_NAME = os.getenv("MODEL_TOOL_NAME", DEFAULT_MODEL_NAME)
DEFAULT_MODEL_FAST_NAME = os.getenv("MODEL_FAST_NAME", DEFAULT_MODEL_NAME)
DEFAULT_MODEL_THINKING_NAME = os.getenv("MODEL_THINKING_NAME", DEFAULT_MODEL_NAME)
DEFAULT_MODEL_SUMMARY_NAME = os.getenv("MODEL_SUMMARY_NAME", DEFAULT_MODEL_FAST_NAME)
ENABLE_TIMING_DIAGNOSTICS = _env_flag("ENABLE_TIMING_DIAGNOSTICS", True)
LOCAL_FONT_FAMILY_STACK = (
    "'Inter', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', "
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
)
SKILLS_PACKAGE_SAFE_DIR = Path(__file__).resolve().parents[2] / "skills"
SKILLS_PACKAGE_DEFAULT_FILENAME = "openclaw-mirosearch.zip"
DEFAULT_SKILLS_PACKAGE_PATH = os.getenv(
    "SKILLS_PACKAGE_PATH",
    str(SKILLS_PACKAGE_SAFE_DIR / SKILLS_PACKAGE_DEFAULT_FILENAME),
)
DEFAULT_SKILLS_PACKAGE_URL = os.getenv("SKILLS_PACKAGE_URL", "").strip()
SKILLS_DOWNLOAD_FALLBACK_HINT = "未检测到可用下载地址，请配置环境变量 SKILLS_PACKAGE_URL。"
SKILLS_DOWNLOAD_BUTTON_TEXT = "skills下载"
SKILLS_DOWNLOAD_COPIED_TEXT = "skills下载（已复制）"
SKILLS_DOWNLOAD_ALLOWED_RELATIVE_PREFIX = "/gradio_api/file="
SKILLS_DOWNLOAD_ALLOWED_SCHEMES = {"https", "http"}

# 输出丰富度配置（可通过环境变量覆盖）
PRODUCTION_WEB_MAX_TOKENS = max(1024, _env_int("PRODUCTION_WEB_LLM_MAX_TOKENS", 3072))
PRODUCTION_WEB_TOOL_RESULT_MAX_CHARS = max(
    2000, _env_int("PRODUCTION_WEB_TOOL_RESULT_MAX_CHARS", 6000)
)
VERIFIED_MAX_TOKENS = max(1024, _env_int("VERIFIED_LLM_MAX_TOKENS", 3072))
VERIFIED_TOOL_RESULT_MAX_CHARS = max(
    2000, _env_int("VERIFIED_TOOL_RESULT_MAX_CHARS", 6000)
)
VERIFIED_KEEP_TOOL_RESULT = max(-1, _env_int("VERIFIED_KEEP_TOOL_RESULT", 4))
VERIFIED_CONTEXT_COMPRESS_LIMIT = max(
    0, _env_int("VERIFIED_CONTEXT_COMPRESS_LIMIT", 3)
)
RESEARCH_MAX_TOKENS = max(1024, _env_int("RESEARCH_LLM_MAX_TOKENS", 3072))
RESEARCH_TOOL_RESULT_MAX_CHARS = max(
    2000, _env_int("RESEARCH_TOOL_RESULT_MAX_CHARS", 6000)
)
RESEARCH_KEEP_TOOL_RESULT = max(-1, _env_int("RESEARCH_KEEP_TOOL_RESULT", 3))
RESEARCH_CONTEXT_COMPRESS_LIMIT = max(
    0, _env_int("RESEARCH_CONTEXT_COMPRESS_LIMIT", 2)
)
BALANCED_MAX_TOKENS = max(1024, _env_int("BALANCED_LLM_MAX_TOKENS", 3072))
BALANCED_TOOL_RESULT_MAX_CHARS = max(
    2000, _env_int("BALANCED_TOOL_RESULT_MAX_CHARS", 5000)
)
BALANCED_KEEP_TOOL_RESULT = max(-1, _env_int("BALANCED_KEEP_TOOL_RESULT", 3))
BALANCED_CONTEXT_COMPRESS_LIMIT = max(
    0, _env_int("BALANCED_CONTEXT_COMPRESS_LIMIT", 2)
)
QUOTA_MAX_TOKENS = max(1024, _env_int("QUOTA_LLM_MAX_TOKENS", 2048))
QUOTA_TOOL_RESULT_MAX_CHARS = max(
    2000, _env_int("QUOTA_TOOL_RESULT_MAX_CHARS", 3500)
)
QUOTA_KEEP_TOOL_RESULT = max(-1, _env_int("QUOTA_KEEP_TOOL_RESULT", 2))
QUOTA_CONTEXT_COMPRESS_LIMIT = max(0, _env_int("QUOTA_CONTEXT_COMPRESS_LIMIT", 1))
THINKING_MAX_TOKENS = max(1024, _env_int("THINKING_LLM_MAX_TOKENS", 3072))
THINKING_TOOL_RESULT_MAX_CHARS = max(
    2000, _env_int("THINKING_TOOL_RESULT_MAX_CHARS", 3000)
)

# 输出篇幅档位控制（默认详细）
OUTPUT_DETAIL_LEVEL_CHOICES = ["compact", "balanced", "detailed"]
OUTPUT_DETAIL_LEVEL_LABELS = {
    "compact": "精简",
    "balanced": "适中",
    "detailed": "详细",
}
DEFAULT_OUTPUT_DETAIL_LEVEL = os.getenv("DEFAULT_OUTPUT_DETAIL_LEVEL", "detailed")
OUTPUT_DETAIL_RENDER_MODE_MAP = {
    "compact": "summary_only",
    "balanced": "summary_with_details",
    "detailed": "full",
}
OUTPUT_DETAIL_SUMMARY_MERGE_MAP = {
    "compact": "latest",
    "balanced": "all_unique",
    "detailed": "all_unique",
}

DETAIL_COMPACT_MAX_TOKENS = max(1024, _env_int("DETAIL_COMPACT_MAX_TOKENS", 2048))
DETAIL_COMPACT_TOOL_RESULT_MAX_CHARS = max(
    2000, _env_int("DETAIL_COMPACT_TOOL_RESULT_MAX_CHARS", 2600)
)
DETAIL_COMPACT_SUMMARY_MAX_TOKENS = max(
    1024, _env_int("DETAIL_COMPACT_SUMMARY_MAX_TOKENS", 2048)
)
DETAIL_COMPACT_VERIFICATION_MAX_TOKENS = max(
    1024, _env_int("DETAIL_COMPACT_VERIFICATION_MAX_TOKENS", 1536)
)
DETAIL_COMPACT_KEEP_TOOL_RESULT = max(
    -1, _env_int("DETAIL_COMPACT_KEEP_TOOL_RESULT", 1)
)
DETAIL_COMPACT_CONTEXT_COMPRESS_LIMIT = max(
    0, _env_int("DETAIL_COMPACT_CONTEXT_COMPRESS_LIMIT", 1)
)
DETAIL_COMPACT_MAIN_AGENT_MAX_TURNS = max(
    1, _env_int("DETAIL_COMPACT_MAIN_AGENT_MAX_TURNS", 8)
)

DETAIL_BALANCED_MAX_TOKENS = max(1024, _env_int("DETAIL_BALANCED_MAX_TOKENS", 4096))
DETAIL_BALANCED_TOOL_RESULT_MAX_CHARS = max(
    2000, _env_int("DETAIL_BALANCED_TOOL_RESULT_MAX_CHARS", 5000)
)
DETAIL_BALANCED_SUMMARY_MAX_TOKENS = max(
    1024, _env_int("DETAIL_BALANCED_SUMMARY_MAX_TOKENS", 4096)
)
DETAIL_BALANCED_VERIFICATION_MAX_TOKENS = max(
    1024, _env_int("DETAIL_BALANCED_VERIFICATION_MAX_TOKENS", 3072)
)
DETAIL_BALANCED_KEEP_TOOL_RESULT = max(
    -1, _env_int("DETAIL_BALANCED_KEEP_TOOL_RESULT", 2)
)
DETAIL_BALANCED_CONTEXT_COMPRESS_LIMIT = max(
    0, _env_int("DETAIL_BALANCED_CONTEXT_COMPRESS_LIMIT", 2)
)
DETAIL_BALANCED_MAIN_AGENT_MAX_TURNS = max(
    1, _env_int("DETAIL_BALANCED_MAIN_AGENT_MAX_TURNS", 10)
)

DETAIL_DETAILED_MAX_TOKENS = max(1024, _env_int("DETAIL_DETAILED_MAX_TOKENS", 8192))
DETAIL_DETAILED_TOOL_RESULT_MAX_CHARS = max(
    2000, _env_int("DETAIL_DETAILED_TOOL_RESULT_MAX_CHARS", 12000)
)
DETAIL_DETAILED_SUMMARY_MAX_TOKENS = max(
    1024, _env_int("DETAIL_DETAILED_SUMMARY_MAX_TOKENS", 8192)
)
DETAIL_DETAILED_VERIFICATION_MAX_TOKENS = max(
    1024, _env_int("DETAIL_DETAILED_VERIFICATION_MAX_TOKENS", 6144)
)
DETAIL_DETAILED_KEEP_TOOL_RESULT = max(
    -1, _env_int("DETAIL_DETAILED_KEEP_TOOL_RESULT", -1)
)
DETAIL_DETAILED_CONTEXT_COMPRESS_LIMIT = max(
    0, _env_int("DETAIL_DETAILED_CONTEXT_COMPRESS_LIMIT", 0)
)
DETAIL_DETAILED_MAIN_AGENT_MAX_TURNS = max(
    1, _env_int("DETAIL_DETAILED_MAIN_AGENT_MAX_TURNS", 16)
)

RESEARCH_MODE_CHOICES = [
    "production-web",
    "verified",
    "research",
    "balanced",
    "quota",
    "thinking",
]

SEARCH_PROFILE_CHOICES = [
    "searxng-first",
    "serp-first",
    "multi-route",
    "parallel",
    "parallel-trusted",
    "searxng-only",
]

SEARCH_HISTORY_STORAGE_KEY = os.getenv(
    "SEARCH_HISTORY_STORAGE_KEY", "mirothinker.demo.search_history"
)
SEARCH_HISTORY_MAX_ITEMS = max(1, _env_int("SEARCH_HISTORY_MAX_ITEMS", 8))
SEARCH_HISTORY_TITLE = "最近搜索"
SEARCH_HISTORY_HINT = "仅保存在当前浏览器，点击可回填并回显结果，删除不影响当前结果。"
SEARCH_HISTORY_EMPTY_TEXT = "还没有搜索历史，开始一次研究后会显示在这里。"
SEARCH_HISTORY_RESULT_MAX_HTML_CHARS = max(
    2000, _env_int("SEARCH_HISTORY_RESULT_MAX_HTML_CHARS", 120000)
)
SEARCH_HISTORY_RESULT_MAX_TEXT_CHARS = max(
    1000, _env_int("SEARCH_HISTORY_RESULT_MAX_TEXT_CHARS", 40000)
)
SEARCH_HISTORY_RESULT_CAPTURE_TIMEOUT_MS = max(
    5000, _env_int("SEARCH_HISTORY_RESULT_CAPTURE_TIMEOUT_MS", 300000)
)
SEARCH_HISTORY_RESULT_CAPTURE_INTERVAL_MS = max(
    200, _env_int("SEARCH_HISTORY_RESULT_CAPTURE_INTERVAL_MS", 600)
)
SEARCH_HISTORY_RESULT_CAPTURE_DEBOUNCE_MS = max(
    100, _env_int("SEARCH_HISTORY_RESULT_CAPTURE_DEBOUNCE_MS", 350)
)
SEARCH_HISTORY_PLACEHOLDER_KEYWORDS = ("等待开始研究", "等待开始")

RENDER_MODE_CHOICES = {"full", "summary_with_details", "summary_only"}
DEFAULT_UI_RENDER_MODE = os.getenv("DEFAULT_UI_RENDER_MODE", "summary_with_details")
DEFAULT_API_RENDER_MODE = os.getenv("DEFAULT_API_RENDER_MODE", "summary_with_details")
FINAL_SUMMARY_MERGE_STRATEGY_CHOICES = {"latest", "all_unique"}
DEFAULT_FINAL_SUMMARY_MERGE_STRATEGY = os.getenv(
    "FINAL_SUMMARY_MERGE_STRATEGY", "all_unique"
)

SEARCH_PROFILE_ENV_MAP: Dict[str, Dict[str, str]] = {
    "searxng-first": {
        "SEARCH_PROVIDER_ORDER": "searxng,serpapi,serper",
        "SEARCH_PROVIDER_MODE": "fallback",
    },
    "serp-first": {
        "SEARCH_PROVIDER_ORDER": "serpapi,searxng,serper",
        "SEARCH_PROVIDER_MODE": "fallback",
    },
    "multi-route": {
        "SEARCH_PROVIDER_ORDER": "serpapi,searxng,serper",
        "SEARCH_PROVIDER_MODE": "merge",
    },
    "parallel": {
        "SEARCH_PROVIDER_ORDER": "serpapi,searxng,serper",
        "SEARCH_PROVIDER_MODE": "parallel",
        "SEARCH_PROVIDER_PARALLEL_MAX_WAIT_MS": "4500",
        "SEARCH_PROVIDER_PARALLEL_MIN_SUCCESS": "1",
    },
    "parallel-trusted": {
        "SEARCH_PROVIDER_ORDER": "serpapi,searxng,serper",
        "SEARCH_PROVIDER_MODE": "parallel_conf_fallback",
        "SEARCH_PROVIDER_TRUSTED_ORDER": "serpapi,searxng,serper",
        "SEARCH_PROVIDER_PARALLEL_MAX_WAIT_MS": "4500",
        "SEARCH_PROVIDER_PARALLEL_MIN_SUCCESS": "1",
        "SEARCH_PROVIDER_FALLBACK_MAX_STEPS": "3",
        "SEARCH_CONFIDENCE_ENABLED": "1",
        "SEARCH_CONFIDENCE_SCORE_THRESHOLD": "0.62",
        "SEARCH_CONFIDENCE_MIN_RESULTS": "8",
        "SEARCH_CONFIDENCE_MIN_UNIQUE_DOMAINS": "5",
        "SEARCH_CONFIDENCE_MIN_PROVIDER_COVERAGE": "2",
        "SEARCH_CONFIDENCE_MIN_HIGH_CONF_HITS": "2",
        "SEARCH_CONFIDENCE_HIGH_CONF_DOMAINS": "reuters.com,apnews.com,bbc.com,aljazeera.com,state.gov,un.org,iaea.org,who.int",
    },
    "searxng-only": {
        "SEARCH_PROVIDER_ORDER": "searxng",
        "SEARCH_PROVIDER_MODE": "fallback",
    },
}

MODE_OVERRIDE_MAP = {
    "production-web": [
        "agent=prod_search_only",
        "agent.main_agent.max_turns=12",
        "agent.keep_tool_result=-1",
        "agent.context_compress_limit=0",
        "agent.retry_with_summary=false",
        f"llm.model_name={DEFAULT_MODEL_NAME}",
        f"+llm.model_tool_name={DEFAULT_MODEL_TOOL_NAME}",
        f"+llm.model_fast_name={DEFAULT_MODEL_FAST_NAME}",
        f"+llm.model_thinking_name={DEFAULT_MODEL_THINKING_NAME}",
        f"+llm.model_summary_name={DEFAULT_MODEL_SUMMARY_NAME}",
        f"llm.max_tokens={PRODUCTION_WEB_MAX_TOKENS}",
        "+llm.max_retries=3",
        "+llm.retry_wait_seconds=3",
        f"llm.tool_result_max_chars={PRODUCTION_WEB_TOOL_RESULT_MAX_CHARS}",
    ],
    "verified": [
        "agent=demo_verified_search",
        "agent.main_agent.max_turns=14",
        f"agent.keep_tool_result={VERIFIED_KEEP_TOOL_RESULT}",
        f"agent.context_compress_limit={VERIFIED_CONTEXT_COMPRESS_LIMIT}",
        "agent.retry_with_summary=false",
        f"llm.model_name={DEFAULT_MODEL_NAME}",
        f"+llm.model_tool_name={DEFAULT_MODEL_TOOL_NAME}",
        f"+llm.model_fast_name={DEFAULT_MODEL_FAST_NAME}",
        f"+llm.model_thinking_name={DEFAULT_MODEL_THINKING_NAME}",
        f"+llm.model_summary_name={DEFAULT_MODEL_SUMMARY_NAME}",
        f"llm.max_tokens={VERIFIED_MAX_TOKENS}",
        "+llm.max_retries=3",
        "+llm.retry_wait_seconds=3",
        f"llm.tool_result_max_chars={VERIFIED_TOOL_RESULT_MAX_CHARS}",
    ],
    "research": [
        "agent=demo_search_only",
        "agent.main_agent.max_turns=10",
        f"agent.keep_tool_result={RESEARCH_KEEP_TOOL_RESULT}",
        f"agent.context_compress_limit={RESEARCH_CONTEXT_COMPRESS_LIMIT}",
        "agent.retry_with_summary=false",
        f"llm.model_name={DEFAULT_MODEL_NAME}",
        f"+llm.model_tool_name={DEFAULT_MODEL_TOOL_NAME}",
        f"+llm.model_fast_name={DEFAULT_MODEL_FAST_NAME}",
        f"+llm.model_thinking_name={DEFAULT_MODEL_THINKING_NAME}",
        f"+llm.model_summary_name={DEFAULT_MODEL_SUMMARY_NAME}",
        f"llm.max_tokens={RESEARCH_MAX_TOKENS}",
        "+llm.max_retries=4",
        "+llm.retry_wait_seconds=6",
        f"llm.tool_result_max_chars={RESEARCH_TOOL_RESULT_MAX_CHARS}",
    ],
    "balanced": [
        "agent=demo_search_only",
        "agent.main_agent.max_turns=11",
        f"agent.keep_tool_result={BALANCED_KEEP_TOOL_RESULT}",
        f"agent.context_compress_limit={BALANCED_CONTEXT_COMPRESS_LIMIT}",
        "agent.retry_with_summary=false",
        f"llm.model_name={DEFAULT_MODEL_NAME}",
        f"+llm.model_tool_name={DEFAULT_MODEL_TOOL_NAME}",
        f"+llm.model_fast_name={DEFAULT_MODEL_FAST_NAME}",
        f"+llm.model_thinking_name={DEFAULT_MODEL_THINKING_NAME}",
        f"+llm.model_summary_name={DEFAULT_MODEL_SUMMARY_NAME}",
        f"llm.max_tokens={BALANCED_MAX_TOKENS}",
        "+llm.max_retries=2",
        "+llm.retry_wait_seconds=2",
        f"llm.tool_result_max_chars={BALANCED_TOOL_RESULT_MAX_CHARS}",
    ],
    "quota": [
        "agent=demo_search_only",
        "agent.main_agent.max_turns=7",
        f"agent.keep_tool_result={QUOTA_KEEP_TOOL_RESULT}",
        f"agent.context_compress_limit={QUOTA_CONTEXT_COMPRESS_LIMIT}",
        "agent.retry_with_summary=false",
        f"llm.model_name={DEFAULT_MODEL_FAST_NAME}",
        f"+llm.model_tool_name={DEFAULT_MODEL_FAST_NAME}",
        f"+llm.model_fast_name={DEFAULT_MODEL_FAST_NAME}",
        f"+llm.model_thinking_name={DEFAULT_MODEL_THINKING_NAME}",
        f"+llm.model_summary_name={DEFAULT_MODEL_SUMMARY_NAME}",
        f"llm.max_tokens={QUOTA_MAX_TOKENS}",
        "+llm.max_retries=2",
        "+llm.retry_wait_seconds=2",
        f"llm.tool_result_max_chars={QUOTA_TOOL_RESULT_MAX_CHARS}",
    ],
    "thinking": [
        "agent=demo_no_tools",
        "agent.main_agent.max_turns=6",
        "agent.keep_tool_result=0",
        "agent.context_compress_limit=1",
        "agent.retry_with_summary=false",
        f"llm.model_name={DEFAULT_MODEL_THINKING_NAME}",
        f"+llm.model_tool_name={DEFAULT_MODEL_TOOL_NAME}",
        f"+llm.model_fast_name={DEFAULT_MODEL_FAST_NAME}",
        f"+llm.model_thinking_name={DEFAULT_MODEL_THINKING_NAME}",
        f"+llm.model_summary_name={DEFAULT_MODEL_SUMMARY_NAME}",
        f"llm.max_tokens={THINKING_MAX_TOKENS}",
        "+llm.max_retries=2",
        "+llm.retry_wait_seconds=2",
        f"llm.tool_result_max_chars={THINKING_TOOL_RESULT_MAX_CHARS}",
    ],
}


def _read_env_int(name: str, default_value: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default_value
    try:
        return int(raw_value)
    except ValueError:
        return default_value


def _read_env_float(name: str, default_value: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default_value
    try:
        return float(raw_value)
    except ValueError:
        return default_value


def _read_env_bool(name: str, default_value: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default_value
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_research_mode(mode: Optional[str]) -> str:
    if not mode:
        mode = DEFAULT_RESEARCH_MODE
    normalized_mode = str(mode).strip().lower()
    if normalized_mode not in MODE_OVERRIDE_MAP:
        logger.warning("未知检索模式 %s，回退到 balanced", mode)
        return "balanced"
    return normalized_mode


def _normalize_search_profile(search_profile: Optional[str]) -> str:
    if not search_profile:
        search_profile = DEFAULT_SEARCH_PROFILE
    normalized_profile = str(search_profile).strip().lower()
    if normalized_profile not in SEARCH_PROFILE_ENV_MAP:
        logger.warning("未知检索源策略 %s，回退到 searxng-first", search_profile)
        return "searxng-first"
    return normalized_profile


def _normalize_search_result_num(result_num: Optional[int]) -> int:
    if result_num is None:
        result_num = DEFAULT_SEARCH_RESULT_NUM
    try:
        parsed = int(result_num)
    except (TypeError, ValueError):
        parsed = DEFAULT_SEARCH_RESULT_NUM
    if parsed in SEARCH_RESULT_NUM_CHOICES:
        return parsed
    return SEARCH_RESULT_NUM_CHOICES[0]


def _normalize_verification_min_search_rounds(min_rounds: Optional[int]) -> int:
    if min_rounds is None:
        min_rounds = DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS
    try:
        parsed = int(min_rounds)
    except (TypeError, ValueError):
        parsed = DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS
    return max(1, min(MAX_VERIFICATION_MIN_SEARCH_ROUNDS, parsed))


def _normalize_render_mode(render_mode: Optional[str], default_mode: str) -> str:
    resolved_default_mode = str(default_mode or "summary_with_details").strip().lower()
    if resolved_default_mode not in RENDER_MODE_CHOICES:
        resolved_default_mode = "summary_with_details"
    if render_mode is None:
        return resolved_default_mode
    normalized_render_mode = str(render_mode).strip().lower()
    if normalized_render_mode in RENDER_MODE_CHOICES:
        return normalized_render_mode
    logger.warning("未知渲染模式 %s，回退到 %s", render_mode, resolved_default_mode)
    return resolved_default_mode


def _normalize_final_summary_merge_strategy(strategy: Optional[str]) -> str:
    default_strategy = str(DEFAULT_FINAL_SUMMARY_MERGE_STRATEGY).strip().lower()
    if default_strategy not in FINAL_SUMMARY_MERGE_STRATEGY_CHOICES:
        default_strategy = "latest"
    if strategy is None:
        return default_strategy
    normalized_strategy = str(strategy).strip().lower()
    if normalized_strategy in FINAL_SUMMARY_MERGE_STRATEGY_CHOICES:
        return normalized_strategy
    logger.warning("未知总结合并策略 %s，回退到 %s", strategy, default_strategy)
    return default_strategy


def _normalize_output_detail_level(level: Optional[str]) -> str:
    resolved_default = str(DEFAULT_OUTPUT_DETAIL_LEVEL or "detailed").strip().lower()
    if resolved_default not in OUTPUT_DETAIL_LEVEL_CHOICES:
        resolved_default = "detailed"
    if level is None:
        return resolved_default
    normalized_level = str(level).strip().lower()
    if normalized_level in OUTPUT_DETAIL_LEVEL_CHOICES:
        return normalized_level
    logger.warning("未知输出篇幅档位 %s，回退到 %s", level, resolved_default)
    return resolved_default


def _get_render_mode_for_output_detail(level: Optional[str]) -> str:
    resolved_level = _normalize_output_detail_level(level)
    return OUTPUT_DETAIL_RENDER_MODE_MAP.get(resolved_level, "full")


def _get_summary_merge_for_output_detail(level: Optional[str]) -> str:
    resolved_level = _normalize_output_detail_level(level)
    return OUTPUT_DETAIL_SUMMARY_MERGE_MAP.get(resolved_level, "all_unique")


def _get_mode_overrides_for_output_detail(level: Optional[str]) -> List[str]:
    resolved_level = _normalize_output_detail_level(level)
    if resolved_level == "compact":
        return [
            "++agent.output_detail_level=compact",
            "++agent.research_report_mode=true",
            f"agent.main_agent.max_turns={DETAIL_COMPACT_MAIN_AGENT_MAX_TURNS}",
            f"agent.keep_tool_result={DETAIL_COMPACT_KEEP_TOOL_RESULT}",
            f"agent.context_compress_limit={DETAIL_COMPACT_CONTEXT_COMPRESS_LIMIT}",
            f"llm.max_tokens={DETAIL_COMPACT_MAX_TOKENS}",
            f"llm.tool_result_max_chars={DETAIL_COMPACT_TOOL_RESULT_MAX_CHARS}",
            f"llm.summary_max_tokens={DETAIL_COMPACT_SUMMARY_MAX_TOKENS}",
            f"llm.verification_max_tokens={DETAIL_COMPACT_VERIFICATION_MAX_TOKENS}",
        ]
    if resolved_level == "balanced":
        return [
            "++agent.output_detail_level=balanced",
            "++agent.research_report_mode=true",
            f"agent.main_agent.max_turns={DETAIL_BALANCED_MAIN_AGENT_MAX_TURNS}",
            f"agent.keep_tool_result={DETAIL_BALANCED_KEEP_TOOL_RESULT}",
            f"agent.context_compress_limit={DETAIL_BALANCED_CONTEXT_COMPRESS_LIMIT}",
            f"llm.max_tokens={DETAIL_BALANCED_MAX_TOKENS}",
            f"llm.tool_result_max_chars={DETAIL_BALANCED_TOOL_RESULT_MAX_CHARS}",
            f"llm.summary_max_tokens={DETAIL_BALANCED_SUMMARY_MAX_TOKENS}",
            f"llm.verification_max_tokens={DETAIL_BALANCED_VERIFICATION_MAX_TOKENS}",
        ]
    return [
        "++agent.output_detail_level=detailed",
        "++agent.research_report_mode=true",
        f"agent.main_agent.max_turns={DETAIL_DETAILED_MAIN_AGENT_MAX_TURNS}",
        f"agent.keep_tool_result={DETAIL_DETAILED_KEEP_TOOL_RESULT}",
        f"agent.context_compress_limit={DETAIL_DETAILED_CONTEXT_COMPRESS_LIMIT}",
        f"llm.max_tokens={DETAIL_DETAILED_MAX_TOKENS}",
        f"llm.tool_result_max_chars={DETAIL_DETAILED_TOOL_RESULT_MAX_CHARS}",
        f"llm.summary_max_tokens={DETAIL_DETAILED_SUMMARY_MAX_TOKENS}",
        f"llm.verification_max_tokens={DETAIL_DETAILED_VERIFICATION_MAX_TOKENS}",
    ]


def _compose_profile_cache_key(
    mode: str,
    search_profile: str,
    search_result_num: int,
    verification_min_search_rounds: int,
    output_detail_level: str,
) -> Tuple[str, str, int, int, str]:
    return (
        mode,
        search_profile,
        search_result_num,
        verification_min_search_rounds,
        output_detail_level,
    )


@contextmanager
def _temporary_env_vars(overrides: Dict[str, str]):
    """临时设置环境变量，确保不同检索策略互不影响。"""
    if not overrides:
        yield
        return

    previous_values: Dict[str, Optional[str]] = {}
    for key, value in overrides.items():
        previous_values[key] = os.environ.get(key)
        os.environ[key] = value

    try:
        yield
    finally:
        for key, old_value in previous_values.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value


def load_miroflow_config(config_overrides: Optional[object] = None) -> DictConfig:
    """
    Load the full MiroFlow configuration using Hydra, similar to how benchmarks work.
    """
    global _hydra_initialized

    # Get the path to the miroflow agent config directory
    miroflow_config_dir = Path(__file__).parent.parent / "miroflow-agent" / "conf"
    miroflow_config_dir = miroflow_config_dir.resolve()
    logger.debug(f"Config dir: {miroflow_config_dir}")

    if not miroflow_config_dir.exists():
        raise FileNotFoundError(
            f"MiroFlow config directory not found: {miroflow_config_dir}"
        )

    # Initialize Hydra if not already done
    if not _hydra_initialized:
        try:
            initialize_config_dir(
                config_dir=str(miroflow_config_dir), version_base=None
            )
            _hydra_initialized = True
        except Exception as e:
            logger.warning(f"Hydra already initialized or error: {e}")

    # Compose configuration with environment variable overrides
    overrides = []

    # Add environment variable based overrides (refer to scripts/debug.sh)
    llm_provider = os.getenv(
        "DEFAULT_LLM_PROVIDER", "qwen"
    )  # debug.sh defaults to qwen
    model_name = os.getenv(
        "DEFAULT_MODEL_NAME", DEFAULT_MODEL_NAME
    )  # debug.sh default model
    agent_set = os.getenv("DEFAULT_AGENT_SET", "demo_search_only")
    base_url = os.getenv("BASE_URL", "http://localhost:11434")
    api_key = os.getenv("API_KEY", "")  # API key for LLM endpoint
    logger.debug(f"LLM base_url: {base_url}")
    main_agent_max_turns = max(
        1, _read_env_int("MAIN_AGENT_MAX_TURNS", DEFAULT_MAIN_AGENT_MAX_TURNS)
    )
    keep_tool_result = _read_env_int("KEEP_TOOL_RESULT", DEFAULT_KEEP_TOOL_RESULT)
    keep_tool_result = max(-1, keep_tool_result)
    context_compress_limit = max(
        0, _read_env_int("CONTEXT_COMPRESS_LIMIT", DEFAULT_CONTEXT_COMPRESS_LIMIT)
    )
    retry_with_summary = _read_env_bool(
        "RETRY_WITH_SUMMARY", DEFAULT_RETRY_WITH_SUMMARY
    )
    llm_temperature = _read_env_float("LLM_TEMPERATURE", DEFAULT_LLM_TEMPERATURE)
    llm_temperature = min(2.0, max(0.0, llm_temperature))
    llm_max_tokens = max(256, _read_env_int("LLM_MAX_TOKENS", DEFAULT_LLM_MAX_TOKENS))
    benchmark_name = os.getenv("DEFAULT_BENCHMARK", DEFAULT_BENCHMARK_NAME).strip()
    if not benchmark_name:
        benchmark_name = DEFAULT_BENCHMARK_NAME

    # Map provider names to config files
    # Available configs: default.yaml, claude-3-7.yaml, gpt-5.yaml, qwen-3.yaml
    provider_config_map = {
        "anthropic": "claude-3-7",
        "openai": "gpt-5",
        "qwen": "qwen-3",
    }

    llm_config = provider_config_map.get(
        llm_provider, "qwen-3"
    )  # fallback to qwen-3 config
    overrides.extend(
        [
            f"llm={llm_config}",
            f"llm.provider={llm_provider}",
            f"llm.model_name={model_name}",
            f"llm.base_url={base_url}",
            f"llm.api_key={api_key}",
            f"agent={agent_set}",
            f"agent.main_agent.max_turns={main_agent_max_turns}",
            f"agent.keep_tool_result={keep_tool_result}",
            f"agent.context_compress_limit={context_compress_limit}",
            f"agent.retry_with_summary={str(retry_with_summary)}",
            f"llm.temperature={llm_temperature}",
            f"llm.max_tokens={llm_max_tokens}",
            f"benchmark={benchmark_name}",
        ]
    )

    # Add config overrides from request
    if config_overrides:
        if isinstance(config_overrides, list):
            overrides.extend([str(item) for item in config_overrides])
        elif isinstance(config_overrides, dict):
            for key, value in config_overrides.items():
                if isinstance(value, dict):
                    for subkey, subvalue in value.items():
                        overrides.append(f"{key}.{subkey}={subvalue}")
                else:
                    overrides.append(f"{key}={value}")

    try:
        cfg = compose(config_name="config", overrides=overrides)
        return cfg
    except Exception as e:
        logger.error(f"Failed to compose Hydra config: {e}")
        exit()


# Lazy loading for tool definitions to speed up page load
# 按检索模式缓存，支持通过参数动态切换
_preload_cache = {}
_preload_lock = threading.Lock()


def _ensure_preloaded(
    mode: str,
    search_profile: str,
    search_result_num: int,
    verification_min_search_rounds: int,
    output_detail_level: str,
):
    """按检索模式与检索源策略懒加载 pipeline 组件。"""
    global _preload_cache
    preload_start_time = time.perf_counter()
    resolved_result_num = _normalize_search_result_num(search_result_num)
    resolved_min_rounds = _normalize_verification_min_search_rounds(
        verification_min_search_rounds
    )
    resolved_output_detail_level = _normalize_output_detail_level(output_detail_level)
    cache_key = _compose_profile_cache_key(
        mode,
        search_profile,
        resolved_result_num,
        resolved_min_rounds,
        resolved_output_detail_level,
    )
    if cache_key in _preload_cache:
        duration_ms = int((time.perf_counter() - preload_start_time) * 1000)
        if ENABLE_TIMING_DIAGNOSTICS:
            logger.info(
                "Pipeline preload cache hit | cache_key=%s | duration_ms=%s",
                cache_key,
                duration_ms,
            )
        return {
            "cache_key": cache_key,
            "cache_hit": True,
            "duration_ms": duration_ms,
        }

    with _preload_lock:
        if cache_key in _preload_cache:
            duration_ms = int((time.perf_counter() - preload_start_time) * 1000)
            if ENABLE_TIMING_DIAGNOSTICS:
                logger.info(
                    "Pipeline preload cache hit(after lock) | cache_key=%s | duration_ms=%s",
                    cache_key,
                    duration_ms,
                )
            return {
                "cache_key": cache_key,
                "cache_hit": True,
                "duration_ms": duration_ms,
            }

        search_env = dict(
            SEARCH_PROFILE_ENV_MAP.get(
                search_profile, SEARCH_PROFILE_ENV_MAP["searxng-first"]
            )
        )
        search_env["SEARCH_RESULT_NUM"] = str(resolved_result_num)
        mode_overrides = list(MODE_OVERRIDE_MAP.get(mode, MODE_OVERRIDE_MAP["balanced"]))
        mode_overrides.extend(
            _get_mode_overrides_for_output_detail(resolved_output_detail_level)
        )
        if mode == "verified":
            mode_overrides.append(
                f"agent.verification.min_search_rounds={resolved_min_rounds}"
            )
        logger.info(
            "Loading pipeline components | mode=%s | search_profile=%s | result_num=%s | min_rounds=%s | detail_level=%s | provider_order=%s | provider_mode=%s",
            mode,
            search_profile,
            resolved_result_num,
            resolved_min_rounds,
            resolved_output_detail_level,
            search_env.get("SEARCH_PROVIDER_ORDER", ""),
            search_env.get("SEARCH_PROVIDER_MODE", ""),
        )
        with _temporary_env_vars(search_env):
            cfg = load_miroflow_config(mode_overrides)
            main_agent_tool_manager, sub_agent_tool_managers, output_formatter = (
                create_pipeline_components(cfg)
            )
            tool_definitions = asyncio.run(
                main_agent_tool_manager.get_all_tool_definitions()
            )
            if cfg.agent.sub_agents:
                tool_definitions += expose_sub_agents_as_tools(cfg.agent.sub_agents)

            sub_agent_tool_definitions = {
                name: asyncio.run(sub_agent_tool_manager.get_all_tool_definitions())
                for name, sub_agent_tool_manager in sub_agent_tool_managers.items()
            }

        _preload_cache[cache_key] = {
            "cfg": cfg,
            "main_agent_tool_manager": main_agent_tool_manager,
            "sub_agent_tool_managers": sub_agent_tool_managers,
            "output_formatter": output_formatter,
            "tool_definitions": tool_definitions,
            "sub_agent_tool_definitions": sub_agent_tool_definitions,
            "search_profile": search_profile,
            "search_result_num": resolved_result_num,
            "verification_min_search_rounds": resolved_min_rounds,
            "output_detail_level": resolved_output_detail_level,
        }
        logger.info(
            "Pipeline components loaded successfully | mode=%s | search_profile=%s | result_num=%s | min_rounds=%s",
            mode,
            search_profile,
            resolved_result_num,
            resolved_min_rounds,
        )
        duration_ms = int((time.perf_counter() - preload_start_time) * 1000)
        if ENABLE_TIMING_DIAGNOSTICS:
            logger.info(
                "Pipeline preload ready | cache_key=%s | cache_hit=%s | duration_ms=%s",
                cache_key,
                False,
                duration_ms,
            )
        return {
            "cache_key": cache_key,
            "cache_hit": False,
            "duration_ms": duration_ms,
        }


class ThreadSafeAsyncQueue:
    """Thread-safe async queue wrapper"""

    def __init__(self):
        self._queue = asyncio.Queue()
        self._loop = None
        self._closed = False

    def set_loop(self, loop):
        self._loop = loop

    async def put(self, item):
        """Put data safely from any thread"""
        if self._closed:
            return
        await self._queue.put(item)

    def put_nowait_threadsafe(self, item):
        """Put data from other threads - use direct queue put for lower latency"""
        if self._closed or not self._loop:
            return
        # Use put_nowait directly instead of creating a task for lower latency
        self._loop.call_soon_threadsafe(lambda: self._queue.put_nowait(item))

    async def get(self):
        return await self._queue.get()

    def close(self):
        self._closed = True


def filter_google_search_organic(organic: List[dict]) -> List[dict]:
    """
    Filter google search organic results to remove unnecessary information
    """
    result = []
    for item in organic:
        result.append(
            {
                "title": item.get("title", ""),
                "link": item.get("link", ""),
            }
        )
    return result


def filter_google_search_payload(result_dict: dict) -> dict:
    """过滤 google_search 结果，保留可视化需要的核心字段与链路元信息。"""
    filtered_payload: Dict[str, object] = {
        "organic": filter_google_search_organic(result_dict.get("organic", []))
    }
    for key in [
        "provider",
        "provider_fallback",
        "route_trace",
        "confidence",
        "searchParameters",
    ]:
        value = result_dict.get(key)
        if value is not None:
            filtered_payload[key] = value
    return filtered_payload


def is_scrape_error(result: str) -> bool:
    """
    Check if the scrape result is an error
    """
    try:
        json.loads(result)
        return False
    except json.JSONDecodeError:
        return True


def filter_message(message: dict) -> dict:
    """
    Filter message to remove unnecessary information
    """
    if message["event"] == "tool_call":
        tool_name = message["data"].get("tool_name")
        tool_input = message["data"].get("tool_input")
        if (
            tool_name == "google_search"
            and isinstance(tool_input, dict)
            and "result" in tool_input
        ):
            try:
                result_dict = json.loads(tool_input["result"])
            except (TypeError, json.JSONDecodeError):
                result_dict = {}
            if isinstance(result_dict, dict) and "organic" in result_dict:
                filtered_result = filter_google_search_payload(result_dict)
                message["data"]["tool_input"]["result"] = json.dumps(
                    filtered_result, ensure_ascii=False
                )
        if (
            tool_name in ["scrape", "scrape_website"]
            and isinstance(tool_input, dict)
            and "result" in tool_input
        ):
            # if error, it can not be json
            if is_scrape_error(tool_input["result"]):
                message["data"]["tool_input"] = {"error": tool_input["result"]}
            else:
                message["data"]["tool_input"] = {}
    return message


async def stream_events_optimized(
    task_id: str,
    query: str,
    mode: Optional[str] = None,
    search_profile: Optional[str] = None,
    search_result_num: Optional[int] = None,
    verification_min_search_rounds: Optional[int] = None,
    output_detail_level: Optional[str] = None,
    disconnect_check=None,
) -> AsyncGenerator[dict, None]:
    """Optimized event stream generator that directly outputs structured events, no longer wrapped as SSE strings."""
    stream_start_time = time.perf_counter()
    workflow_id = task_id
    resolved_mode = _normalize_research_mode(mode)
    resolved_search_profile = _normalize_search_profile(search_profile)
    resolved_search_result_num = _normalize_search_result_num(search_result_num)
    resolved_verification_min_rounds = _normalize_verification_min_search_rounds(
        verification_min_search_rounds
    )
    resolved_output_detail_level = _normalize_output_detail_level(output_detail_level)
    last_send_time = time.time()
    last_heartbeat_time = time.time()

    # Create thread-safe queue
    stream_queue = ThreadSafeAsyncQueue()
    stream_queue.set_loop(asyncio.get_event_loop())

    cancel_event = threading.Event()
    first_non_heartbeat_logged = False
    event_counts: Dict[str, int] = {}

    def run_pipeline_in_thread():
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            class ThreadQueueWrapper:
                def __init__(self, thread_queue, cancel_event):
                    self.thread_queue = thread_queue
                    self.cancel_event = cancel_event

                async def put(self, item):
                    if self.cancel_event.is_set():
                        logger.info("Pipeline cancelled, stopping execution")
                        return
                    self.thread_queue.put_nowait_threadsafe(filter_message(item))

            wrapper_queue = ThreadQueueWrapper(stream_queue, cancel_event)

            # Ensure pipeline components are loaded (lazy loading)
            preload_info = _ensure_preloaded(
                resolved_mode,
                resolved_search_profile,
                resolved_search_result_num,
                resolved_verification_min_rounds,
                resolved_output_detail_level,
            )
            if ENABLE_TIMING_DIAGNOSTICS:
                logger.info(
                    "Task stream bootstrap | workflow_id=%s | mode=%s | search_profile=%s | detail_level=%s | preload_ms=%s | cache_hit=%s",
                    workflow_id,
                    resolved_mode,
                    resolved_search_profile,
                    resolved_output_detail_level,
                    preload_info.get("duration_ms"),
                    preload_info.get("cache_hit"),
                )
            cache_key = _compose_profile_cache_key(
                resolved_mode,
                resolved_search_profile,
                resolved_search_result_num,
                resolved_verification_min_rounds,
                resolved_output_detail_level,
            )
            profile_cache = _preload_cache[cache_key]

            async def pipeline_with_cancellation():
                pipeline_task = asyncio.create_task(
                    execute_task_pipeline(
                        cfg=profile_cache["cfg"],
                        task_id=workflow_id,
                        task_description=query,
                        task_file_name=None,
                        main_agent_tool_manager=profile_cache["main_agent_tool_manager"],
                        sub_agent_tool_managers=profile_cache["sub_agent_tool_managers"],
                        output_formatter=profile_cache["output_formatter"],
                        stream_queue=wrapper_queue,
                        log_dir=os.getenv("LOG_DIR", "logs/api-server"),
                        tool_definitions=profile_cache["tool_definitions"],
                        sub_agent_tool_definitions=profile_cache["sub_agent_tool_definitions"],
                    )
                )

                async def check_cancellation():
                    while not cancel_event.is_set():
                        await asyncio.sleep(0.5)
                    logger.info("Cancel event detected, cancelling pipeline")
                    pipeline_task.cancel()

                cancel_task = asyncio.create_task(check_cancellation())

                try:
                    done, pending = await asyncio.wait(
                        [pipeline_task, cancel_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                    for task in done:
                        if task == pipeline_task:
                            try:
                                await task
                            except asyncio.CancelledError:
                                logger.info("Pipeline task was cancelled")
                except Exception as e:
                    logger.error(f"Pipeline execution error: {e}")
                    pipeline_task.cancel()
                    cancel_task.cancel()

            loop.run_until_complete(pipeline_with_cancellation())
        except Exception as e:
            if not cancel_event.is_set():
                logger.error(f"Pipeline error: {e}", exc_info=True)
                stream_queue.put_nowait_threadsafe(
                    {
                        "event": "error",
                        "data": {"error": str(e), "workflow_id": workflow_id},
                    }
                )
        finally:
            stream_queue.put_nowait_threadsafe(None)
            if "loop" in locals():
                loop.close()

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(run_pipeline_in_thread)

    try:
        while True:
            try:
                if disconnect_check and await disconnect_check():
                    logger.info("Client disconnected, stopping pipeline")
                    cancel_event.set()
                    break
                message = await asyncio.wait_for(stream_queue.get(), timeout=0.1)
                if message is None:
                    logger.info("Pipeline completed")
                    break
                event_type = str(message.get("event", "unknown"))
                event_counts[event_type] = event_counts.get(event_type, 0) + 1
                if (
                    ENABLE_TIMING_DIAGNOSTICS
                    and not first_non_heartbeat_logged
                    and event_type != "heartbeat"
                ):
                    first_non_heartbeat_logged = True
                    logger.info(
                        "Task first event | workflow_id=%s | event=%s | latency_ms=%s",
                        workflow_id,
                        event_type,
                        int((time.perf_counter() - stream_start_time) * 1000),
                    )
                yield message
                last_send_time = time.time()
            except asyncio.TimeoutError:
                current_time = time.time()
                if current_time - last_send_time > 300:
                    logger.info("Stream timeout")
                    break
                if future.done():
                    try:
                        message = stream_queue._queue.get_nowait()
                        if message is not None:
                            yield message
                            continue
                    except Exception:
                        break
                if current_time - last_heartbeat_time >= 15:
                    yield {
                        "event": "heartbeat",
                        "data": {"timestamp": current_time, "workflow_id": workflow_id},
                    }
                    last_heartbeat_time = current_time
    except Exception as e:
        logger.error(f"Stream error: {e}", exc_info=True)
        yield {
            "event": "error",
            "data": {"workflow_id": workflow_id, "error": f"Stream error: {str(e)}"},
        }
    finally:
        cancel_event.set()
        stream_queue.close()
        try:
            future.result(timeout=1.0)
        except Exception:
            pass
        executor.shutdown(wait=False)
        if ENABLE_TIMING_DIAGNOSTICS:
            logger.info(
                "Task stream finished | workflow_id=%s | total_ms=%s | event_counts=%s",
                workflow_id,
                int((time.perf_counter() - stream_start_time) * 1000),
                json.dumps(event_counts, ensure_ascii=False, sort_keys=True),
            )


# ========================= Gradio Integration =========================


def _init_render_state():
    return {
        "agent_order": [],
        "agents": {},  # agent_id -> {"agent_name": str, "tool_call_order": [], "tools": {tool_call_id: {...}}}
        "current_agent_id": None,
        "errors": [],
    }


def _format_think_content(text: str) -> str:
    """Convert <think> tags to readable markdown format."""
    import re

    # Replace <think> tags with blockquote format (no label)
    text = re.sub(r"<think>\s*", "\n> ", text)
    text = re.sub(r"\s*</think>", "\n", text)
    # Convert newlines within thinking to blockquote continuation
    lines = text.split("\n")
    result = []
    in_thinking = False
    for line in lines:
        if line.strip().startswith(">") and not in_thinking:
            in_thinking = True
            result.append(line)
        elif in_thinking and line.strip() and not line.startswith(">"):
            result.append(f"> {line}")
        else:
            if line.strip() == "" and in_thinking:
                in_thinking = False
            result.append(line)
    return "\n".join(result)


def _append_show_text(tool_entry: dict, delta: str):
    existing = tool_entry.get("content", "")
    # Skip "Final boxed answer" content (already shown in main response)
    if "Final boxed answer" in delta:
        return
    # Format think tags for display
    formatted_delta = _format_think_content(delta)
    tool_entry["content"] = existing + formatted_delta


def _is_empty_payload(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        stripped = value.strip()
        return stripped == "" or stripped in ("{}", "[]")
    if isinstance(value, (dict, list, tuple, set)):
        return len(value) == 0
    return False


def _format_search_results(
    tool_input: dict,
    tool_output: dict,
    display_limit: Optional[int] = None,
) -> str:
    """Format google_search results in a beautiful card layout."""
    lines = []

    # Get search query from input
    query = ""
    if isinstance(tool_input, dict):
        query = tool_input.get("q", "") or tool_input.get("query", "")

    # Parse results from output - handle multiple formats
    results = []
    provider_mode = ""
    providers_with_results: List[str] = []
    route_trace: List[dict] = []
    confidence_info: Dict[str, object] = {}
    fallback_errors: List[str] = []
    if isinstance(tool_output, dict):
        # Case 1: output has "result" field containing JSON string
        result_str = tool_output.get("result", "")
        if isinstance(result_str, str) and result_str.strip():
            try:
                result_data = json.loads(result_str)
                if isinstance(result_data, dict):
                    results = result_data.get("organic", [])
                    search_params = result_data.get("searchParameters", {})
                    if isinstance(search_params, dict):
                        provider_mode = str(
                            search_params.get("provider_mode", "")
                        ).strip()
                        providers_with_results = [
                            str(item)
                            for item in search_params.get("providers_with_results", [])
                        ]
                        raw_route_trace = result_data.get("route_trace") or search_params.get(
                            "route_trace"
                        )
                        if isinstance(raw_route_trace, list):
                            route_trace = [
                                item for item in raw_route_trace if isinstance(item, dict)
                            ]
                    raw_confidence = result_data.get("confidence")
                    if isinstance(raw_confidence, dict):
                        confidence_info = raw_confidence
                    raw_fallback = result_data.get("provider_fallback", [])
                    if isinstance(raw_fallback, list):
                        fallback_errors = [str(item) for item in raw_fallback if item]
            except json.JSONDecodeError:
                pass
        elif isinstance(result_str, dict):
            results = result_str.get("organic", [])

        # Case 2: output directly contains "organic" field
        if not results and "organic" in tool_output:
            results = tool_output.get("organic", [])

    if not results and not query:
        return ""

    # Build the card
    lines.append('<div class="search-card">')

    # Header with query
    if query:
        lines.append('<div class="search-header">')
        lines.append('<span class="search-icon">🔍</span>')
        lines.append(f'<span class="search-query">Search: "{query}"</span>')
        lines.append("</div>")

    # Results count
    if results:
        lines.append(f'<div class="search-count">≡ Found {len(results)} results</div>')
        if provider_mode:
            lines.append(
                f'<div class="search-count">检索模式: <strong>{provider_mode}</strong></div>'
            )
        if providers_with_results:
            providers_text = ", ".join(providers_with_results)
            lines.append(
                f'<div class="search-count">命中搜索源: <strong>{providers_text}</strong></div>'
            )
        if confidence_info:
            score = confidence_info.get("score")
            threshold = confidence_info.get("threshold")
            passed = confidence_info.get("passed")
            lines.append(
                f'<div class="search-count">置信度: <strong>{score}</strong> / 阈值 {threshold} / 通过={passed}</div>'
            )
        if route_trace:
            route_items = []
            for item in route_trace[:8]:
                phase = item.get("phase", "")
                provider = item.get("provider", "")
                status = item.get("status", "")
                count = item.get("result_count")
                suffix = f"({count})" if count is not None else ""
                route_items.append(f"{phase}:{provider}:{status}{suffix}")
            if route_items:
                lines.append(
                    f'<div class="search-count">链路跟踪: {" | ".join(route_items)}</div>'
                )
        if fallback_errors:
            lines.append(
                f'<div class="search-count">补检异常: {"; ".join(fallback_errors[:3])}</div>'
            )

        # Results list
        lines.append('<div class="search-results">')
        safe_display_limit = SEARCH_RESULT_DISPLAY_MAX
        if display_limit is not None:
            safe_display_limit = max(1, min(SEARCH_RESULT_DISPLAY_MAX, int(display_limit)))
        visible_count = min(len(results), safe_display_limit)
        for item in results[:visible_count]:
            title = item.get("title", "Untitled")
            link = item.get("link", "#")

            lines.append(f"""<a href="{link}" target="_blank" class="search-result-item">
                <span class="result-icon">🌐</span>
                <span class="result-title">{title}</span>
            </a>""")
        lines.append("</div>")
        if len(results) > visible_count:
            lines.append(
                f'<div class="search-count">仅展示前 {visible_count} 条，完整结果共 {len(results)} 条。</div>'
            )

    lines.append("</div>")

    return "\n".join(lines)


def _format_sogou_search_results(tool_input: dict, tool_output: dict) -> str:
    """Format sogou_search results in a beautiful card layout."""
    lines = []

    # Get search query from input
    query = ""
    if isinstance(tool_input, dict):
        query = tool_input.get("q", "") or tool_input.get("query", "")

    # Parse results from output - sogou uses "Pages" instead of "organic"
    results = []
    if isinstance(tool_output, dict):
        result_str = tool_output.get("result", "")
        if isinstance(result_str, str) and result_str.strip():
            try:
                result_data = json.loads(result_str)
                if isinstance(result_data, dict):
                    results = result_data.get("Pages", [])
            except json.JSONDecodeError:
                pass
        elif isinstance(result_str, dict):
            results = result_str.get("Pages", [])

        if not results and "Pages" in tool_output:
            results = tool_output.get("Pages", [])

    if not results and not query:
        return ""

    # Build the card
    lines.append('<div class="search-card">')

    # Header with query
    if query:
        lines.append('<div class="search-header">')
        lines.append('<span class="search-icon">🔍</span>')
        lines.append(f'<span class="search-query">Search: "{query}"</span>')
        lines.append("</div>")

    # Results count
    if results:
        lines.append(f'<div class="search-count">≡ Found {len(results)} results</div>')

        # Results list
        lines.append('<div class="search-results">')
        for item in results[:10]:  # Limit to 10 results
            title = item.get("title", "Untitled")
            link = item.get("url", item.get("link", "#"))

            lines.append(f"""<a href="{link}" target="_blank" class="search-result-item">
                <span class="result-icon">🌐</span>
                <span class="result-title">{title}</span>
            </a>""")
        lines.append("</div>")

    lines.append("</div>")

    return "\n".join(lines)


def _extract_scrape_preview_text(tool_output: dict, preview_chars: int) -> str:
    if preview_chars <= 0:
        return ""
    payload = ""
    if isinstance(tool_output, dict):
        candidate = tool_output.get("result", tool_output)
        if isinstance(candidate, dict):
            for key in ("text", "markdown", "content", "summary", "result"):
                value = candidate.get(key)
                if isinstance(value, str) and value.strip():
                    payload = value
                    break
            if not payload:
                try:
                    payload = json.dumps(candidate, ensure_ascii=False)
                except Exception:
                    payload = str(candidate)
        elif isinstance(candidate, str):
            payload = candidate
        else:
            try:
                payload = json.dumps(candidate, ensure_ascii=False)
            except Exception:
                payload = str(candidate)
    elif isinstance(tool_output, str):
        payload = tool_output
    normalized_payload = " ".join(str(payload or "").split()).strip()
    if not normalized_payload:
        return ""
    if len(normalized_payload) > preview_chars:
        return normalized_payload[:preview_chars] + "..."
    return normalized_payload


def _format_scrape_results(
    tool_input: dict,
    tool_output: dict,
    preview_chars: int = 0,
) -> str:
    """Format scrape/webpage results in a card layout."""
    lines = []

    # Get URL
    url = ""
    if isinstance(tool_input, dict):
        url = tool_input.get("url", tool_input.get("link", ""))

    # Check for error
    if isinstance(tool_output, dict) and "error" in tool_output:
        lines.append('<div class="scrape-card scrape-error">')
        lines.append('<div class="scrape-header">')
        lines.append('<span class="scrape-icon">🌐</span>')
        lines.append(
            f'<span class="scrape-url">{url[:60]}{"..." if len(url) > 60 else ""}</span>'
        )
        lines.append("</div>")
        lines.append('<div class="scrape-status error">❌ Failed</div>')
        lines.append("</div>")
        return "\n".join(lines)

    # Success case
    lines.append('<div class="scrape-card">')
    if url:
        lines.append('<div class="scrape-header">')
        lines.append('<span class="scrape-icon">🌐</span>')
        lines.append(
            f'<span class="scrape-url">{url[:60]}{"..." if len(url) > 60 else ""}</span>'
        )
        lines.append("</div>")
        lines.append('<div class="scrape-status success">✓ Done</div>')
    preview_text = _extract_scrape_preview_text(tool_output, preview_chars)
    if preview_text:
        lines.append("</div>")
        lines.append(
            f'<div class="tool-brief" style="padding: 0 16px 12px; line-height: 1.6;">{preview_text}</div>'
        )
        lines.append('<div class="scrape-card">')
    lines.append("</div>")

    return "\n".join(lines)


def _deduplicate_non_empty_blocks(blocks: List[str]) -> List[str]:
    deduplicated_blocks: List[str] = []
    seen_blocks = set()
    for block in blocks:
        normalized_block = (block or "").strip()
        if not normalized_block or normalized_block in seen_blocks:
            continue
        seen_blocks.add(normalized_block)
        deduplicated_blocks.append(normalized_block)
    return deduplicated_blocks


def _merge_final_summary_blocks(
    final_summary_blocks: List[str],
    merge_strategy: Optional[str] = None,
) -> List[str]:
    unique_blocks = _deduplicate_non_empty_blocks(final_summary_blocks)
    if not unique_blocks:
        return []
    resolved_merge_strategy = _normalize_final_summary_merge_strategy(merge_strategy)
    if resolved_merge_strategy == "all_unique":
        return unique_blocks
    return [unique_blocks[-1]]


def _build_summary_section(final_summary_blocks: List[str]) -> List[str]:
    if not final_summary_blocks:
        return []
    lines = ["## 📋 研究总结\n\n"]
    lines.extend(final_summary_blocks)
    return lines


def _build_process_details_section(process_lines: List[str]) -> List[str]:
    if not process_lines:
        return []
    lines = [
        "\n\n---\n\n",
        '<details class="process-details">\n<summary>🧭 查看检索过程（中间步骤）</summary>\n\n',
    ]
    lines.extend(process_lines)
    lines.append("\n</details>\n")
    return lines


def _render_markdown(
    state: dict,
    render_mode: Optional[str] = None,
    final_summary_merge_strategy: Optional[str] = None,
) -> str:
    resolved_render_mode = _normalize_render_mode(render_mode, DEFAULT_UI_RENDER_MODE)
    if resolved_render_mode == "full":
        search_display_limit = SEARCH_RESULT_DISPLAY_MAX
        scrape_preview_chars = 2200
    elif resolved_render_mode == "summary_with_details":
        search_display_limit = min(15, SEARCH_RESULT_DISPLAY_MAX)
        scrape_preview_chars = 700
    else:
        search_display_limit = min(8, SEARCH_RESULT_DISPLAY_MAX)
        scrape_preview_chars = 0
    error_lines = []
    process_lines = []
    final_summary_blocks = []

    # Render errors first if any
    if state.get("errors"):
        for err in state["errors"]:
            error_lines.append(f'<div class="error-block">❌ {err}</div>')

    # Render all agents' content
    for agent_id in state.get("agent_order", []):
        agent = state["agents"].get(agent_id, {})
        agent_name = agent.get("agent_name", "")
        is_final_summary = agent_name == "Final Summary"

        for call_id in agent.get("tool_call_order", []):
            call = agent["tools"].get(call_id, {})
            tool_name = call.get("tool_name", "unknown_tool")

            # Show text / message - display directly
            if tool_name in ("show_text", "message"):
                content = call.get("content", "")
                if content:
                    if is_final_summary:
                        final_summary_blocks.append(content)
                    else:
                        process_lines.append(content)
                continue

            tool_input = call.get("input", {})
            tool_output = call.get("output", {})
            has_input = not _is_empty_payload(tool_input)
            has_output = not _is_empty_payload(tool_output)

            # Special formatting for google_search
            if tool_name == "google_search" and (has_input or has_output):
                formatted = _format_search_results(
                    tool_input,
                    tool_output,
                    display_limit=search_display_limit,
                )
                if formatted:
                    process_lines.append(formatted)
                continue

            # Special formatting for sogou_search
            if tool_name == "sogou_search" and (has_input or has_output):
                formatted = _format_sogou_search_results(tool_input, tool_output)
                if formatted:
                    process_lines.append(formatted)
                continue

            # Special formatting for scrape/webpage tools
            if tool_name in (
                "scrape",
                "scrape_website",
                "scrape_webpage",
                "scrape_and_extract_info",
            ) and (has_input or has_output):
                formatted = _format_scrape_results(
                    tool_input,
                    tool_output,
                    preview_chars=scrape_preview_chars,
                )
                if formatted:
                    process_lines.append(formatted)
                continue

            # Special formatting for code execution tools
            if tool_name in ("python", "run_python_code") and (has_input or has_output):
                # Use pure Markdown to avoid HTML wrapper blocking Markdown rendering
                process_lines.append("\n---\n")
                process_lines.append("#### 💻 Code Execution\n")
                # Show code input - try multiple possible keys
                code = ""
                if isinstance(tool_input, dict):
                    code = tool_input.get("code") or tool_input.get("code_block") or ""
                elif isinstance(tool_input, str):
                    code = tool_input
                if code:
                    process_lines.append(f"\n```python\n{code}\n```\n")
                # Show output if available
                if has_output:
                    output = ""
                    if isinstance(tool_output, dict):
                        output = (
                            tool_output.get("result")
                            or tool_output.get("output")
                            or tool_output.get("stdout")
                            or ""
                        )
                    elif isinstance(tool_output, str):
                        output = tool_output
                    if isinstance(output, str) and output.strip():
                        process_lines.append("\n**Output:**\n")
                        process_lines.append(
                            f'\n```text\n{output[:1000]}{"..." if len(output) > 1000 else ""}\n```\n'
                        )
                process_lines.append("\n✅ Executed\n")
                continue

            # Other tools - show as compact card
            if has_input or has_output:
                process_lines.append('<div class="tool-card">')
                process_lines.append(f'<div class="tool-header">🔧 {tool_name}</div>')
                if has_input and isinstance(tool_input, dict):
                    brief = ", ".join(
                        f"{k}: {str(v)[:30]}..."
                        if len(str(v)) > 30
                        else f"{k}: {v}"
                        for k, v in list(tool_input.items())[:2]
                    )
                    process_lines.append(f'<div class="tool-brief">{brief}</div>')
                if has_output:
                    process_lines.append('<div class="tool-status">✓ Done</div>')
                process_lines.append("</div>")

    merged_final_summary_blocks = _merge_final_summary_blocks(
        final_summary_blocks,
        merge_strategy=final_summary_merge_strategy,
    )
    lines = list(error_lines)

    if resolved_render_mode == "full":
        lines.extend(process_lines)
        if merged_final_summary_blocks:
            lines.append("\n\n---\n\n")
            lines.extend(_build_summary_section(merged_final_summary_blocks))
    elif resolved_render_mode == "summary_only":
        if merged_final_summary_blocks:
            lines.extend(_build_summary_section(merged_final_summary_blocks))
        else:
            lines.extend(process_lines)
    else:
        if merged_final_summary_blocks:
            lines.extend(_build_summary_section(merged_final_summary_blocks))
            lines.extend(_build_process_details_section(process_lines))
        else:
            lines.extend(process_lines)

    return "\n".join(lines) if lines else "*等待开始研究...*"


def _update_state_with_event(state: dict, message: dict):
    event = message.get("event")
    data = message.get("data", {})
    if event == "start_of_agent":
        agent_id = data.get("agent_id")
        agent_name = data.get("agent_name", "unknown")
        if agent_id and agent_id not in state["agents"]:
            state["agents"][agent_id] = {
                "agent_name": agent_name,
                "tool_call_order": [],
                "tools": {},
            }
            state["agent_order"].append(agent_id)
        state["current_agent_id"] = agent_id
    elif event == "end_of_agent":
        # End marker, no special handling needed, keep structure
        state["current_agent_id"] = None
    elif event == "tool_call":
        tool_call_id = data.get("tool_call_id")
        tool_name = data.get("tool_name", "unknown_tool")
        agent_id = state.get("current_agent_id") or (
            state["agent_order"][-1] if state["agent_order"] else None
        )
        if not agent_id:
            return state
        agent = state["agents"].setdefault(
            agent_id, {"agent_name": "unknown", "tool_call_order": [], "tools": {}}
        )
        tools = agent["tools"]
        if tool_call_id not in tools:
            tools[tool_call_id] = {"tool_name": tool_name}
            agent["tool_call_order"].append(tool_call_id)
        entry = tools[tool_call_id]
        if tool_name == "show_text" and "delta_input" in data:
            delta = data.get("delta_input", {}).get("text", "")
            _append_show_text(entry, delta)
        elif tool_name == "show_text" and "tool_input" in data:
            ti = data.get("tool_input")
            text = ""
            if isinstance(ti, dict):
                text = ti.get("text", "") or (
                    (ti.get("result") or {}).get("text")
                    if isinstance(ti.get("result"), dict)
                    else ""
                )
            elif isinstance(ti, str):
                text = ti
            if text:
                _append_show_text(entry, text)
        else:
            # Distinguish between input and output:
            if "tool_input" in data:
                # Could be input (first time) or output with result (second time)
                ti = data["tool_input"]
                # If contains result, assign to output; otherwise assign to input
                if isinstance(ti, dict) and "result" in ti:
                    entry["output"] = ti
                else:
                    # Only update input if we don't already have valid input data, or if the new data is not empty
                    if "input" not in entry or not _is_empty_payload(ti):
                        entry["input"] = ti
    elif event == "message":
        # Same incremental text display as show_text, aggregated by message_id
        message_id = data.get("message_id")
        agent_id = state.get("current_agent_id") or (
            state["agent_order"][-1] if state["agent_order"] else None
        )
        if not agent_id:
            return state
        agent = state["agents"].setdefault(
            agent_id, {"agent_name": "unknown", "tool_call_order": [], "tools": {}}
        )
        tools = agent["tools"]
        if message_id not in tools:
            tools[message_id] = {"tool_name": "message"}
            agent["tool_call_order"].append(message_id)
        entry = tools[message_id]
        delta_content = (data.get("delta") or {}).get("content", "")
        if isinstance(delta_content, str) and delta_content:
            _append_show_text(entry, delta_content)
    elif event == "error":
        # Collect errors, display uniformly during rendering
        err_text = data.get("error") if isinstance(data, dict) else None
        if not err_text:
            try:
                err_text = json.dumps(data, ensure_ascii=False)
            except Exception:
                err_text = str(data)
        state.setdefault("errors", []).append(err_text)
    else:
        # Ignore heartbeat or other events
        pass

    return state


_CANCEL_FLAGS = {}
_ACTIVE_TASK_IDS = set()
_CANCEL_LOCK = threading.Lock()


def _set_cancel_flag(task_id: str):
    with _CANCEL_LOCK:
        _CANCEL_FLAGS[task_id] = True


def _reset_cancel_flag(task_id: str):
    with _CANCEL_LOCK:
        _CANCEL_FLAGS[task_id] = False


def _register_active_task(task_id: str):
    with _CANCEL_LOCK:
        _ACTIVE_TASK_IDS.add(task_id)
        _CANCEL_FLAGS.setdefault(task_id, False)


def _unregister_active_task(task_id: str):
    with _CANCEL_LOCK:
        _ACTIVE_TASK_IDS.discard(task_id)
        _CANCEL_FLAGS.pop(task_id, None)


def _get_active_task_ids() -> List[str]:
    with _CANCEL_LOCK:
        return list(_ACTIVE_TASK_IDS)


def _cancel_task_ids(task_ids: List[str]) -> int:
    cancelled = 0
    for task_id in task_ids:
        if not task_id:
            continue
        _set_cancel_flag(task_id)
        cancelled += 1
    return cancelled


async def _disconnect_check_for_task(task_id: str):
    with _CANCEL_LOCK:
        return _CANCEL_FLAGS.get(task_id, False)


def _spinner_markup(running: bool, status_text: str = "生成中...") -> str:
    if not running:
        return ""
    return (
        '\n\n<div class="runtime-status" style="display:flex;align-items:center;gap:8px;color:#555;margin-top:8px;">'
        '<div style="width:16px;height:16px;border:2px solid #ddd;border-top-color:#3b82f6;border-radius:50%;animation:spin 0.8s linear infinite;"></div>'
        f"<span>{status_text}</span>"
        "</div>\n<style>@keyframes spin{to{transform:rotate(360deg)}}</style>\n"
    )


async def gradio_run(
    query: str,
    mode: str,
    search_profile: str = DEFAULT_SEARCH_PROFILE,
    search_result_num: int = DEFAULT_SEARCH_RESULT_NUM,
    verification_min_search_rounds: int = DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS,
    output_detail_level: str = DEFAULT_OUTPUT_DETAIL_LEVEL,
    ui_state: Optional[dict] = None,
):
    query = replace_chinese_punctuation(query or "")
    resolved_mode = _normalize_research_mode(mode)
    resolved_search_profile = _normalize_search_profile(search_profile)
    resolved_search_result_num = _normalize_search_result_num(search_result_num)
    resolved_verification_min_rounds = _normalize_verification_min_search_rounds(
        verification_min_search_rounds
    )
    resolved_output_detail_level = _normalize_output_detail_level(output_detail_level)
    resolved_ui_render_mode = _normalize_render_mode(
        None,
        _get_render_mode_for_output_detail(resolved_output_detail_level),
    )
    resolved_summary_merge_strategy = _normalize_final_summary_merge_strategy(
        _get_summary_merge_for_output_detail(resolved_output_detail_level)
    )
    task_id = str(uuid.uuid4())
    _reset_cancel_flag(task_id)
    _register_active_task(task_id)
    if not ui_state:
        ui_state = {
            "task_id": task_id,
            "mode": resolved_mode,
            "search_profile": resolved_search_profile,
            "search_result_num": resolved_search_result_num,
            "verification_min_search_rounds": resolved_verification_min_rounds,
            "render_mode": resolved_ui_render_mode,
            "output_detail_level": resolved_output_detail_level,
            "final_summary_merge_strategy": resolved_summary_merge_strategy,
        }
    else:
        ui_state = {
            **ui_state,
            "task_id": task_id,
            "mode": resolved_mode,
            "search_profile": resolved_search_profile,
            "search_result_num": resolved_search_result_num,
            "verification_min_search_rounds": resolved_verification_min_rounds,
            "render_mode": resolved_ui_render_mode,
            "output_detail_level": resolved_output_detail_level,
            "final_summary_merge_strategy": resolved_summary_merge_strategy,
        }
    state = _init_render_state()
    try:
        initial_markdown = _render_markdown(
            state,
            render_mode=resolved_ui_render_mode,
            final_summary_merge_strategy=resolved_summary_merge_strategy,
        )
        # Initial: disable Run, enable Stop, and show spinner at bottom of text
        yield (
            initial_markdown + _spinner_markup(True),
            gr.update(interactive=False),
            gr.update(interactive=True),
            ui_state,
            initial_markdown,
        )
        async for message in stream_events_optimized(
            task_id,
            query,
            resolved_mode,
            resolved_search_profile,
            resolved_search_result_num,
            resolved_verification_min_rounds,
            resolved_output_detail_level,
            lambda: _disconnect_check_for_task(task_id),
        ):
            event_type = message.get("event", "unknown")
            if event_type == "heartbeat":
                heartbeat_ts = (message.get("data") or {}).get("timestamp")
                heartbeat_label = "生成中..."
                if heartbeat_ts:
                    heartbeat_label = (
                        f"生成中... 最近心跳 {time.strftime('%H:%M:%S', time.localtime(heartbeat_ts))}"
                    )
                heartbeat_markdown = _render_markdown(
                    state,
                    render_mode=resolved_ui_render_mode,
                    final_summary_merge_strategy=resolved_summary_merge_strategy,
                )
                yield (
                    heartbeat_markdown + _spinner_markup(True, heartbeat_label),
                    gr.update(interactive=False),
                    gr.update(interactive=True),
                    ui_state,
                    heartbeat_markdown,
                )
                continue

            state = _update_state_with_event(state, message)
            md = _render_markdown(
                state,
                render_mode=resolved_ui_render_mode,
                final_summary_merge_strategy=resolved_summary_merge_strategy,
            )
            yield (
                md + _spinner_markup(True),
                gr.update(interactive=False),
                gr.update(interactive=True),
                ui_state,
                md,
            )
            # Small delay to allow Gradio to process the update
            await asyncio.sleep(0.01)
        # End: enable Run, disable Stop, remove spinner
        yield (
            _render_markdown(
                state,
                render_mode=resolved_ui_render_mode,
                final_summary_merge_strategy=resolved_summary_merge_strategy,
            ),
            gr.update(interactive=True),
            gr.update(interactive=False),
            ui_state,
            _render_markdown(
                state,
                render_mode=resolved_ui_render_mode,
                final_summary_merge_strategy=resolved_summary_merge_strategy,
            ),
        )
    finally:
        _unregister_active_task(task_id)


async def run_research_once(
    query: str,
    mode: str,
    search_profile: str = DEFAULT_SEARCH_PROFILE,
    search_result_num: int = DEFAULT_SEARCH_RESULT_NUM,
    verification_min_search_rounds: int = DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS,
    output_detail_level: str = DEFAULT_OUTPUT_DETAIL_LEVEL,
    render_mode: Optional[str] = None,
) -> str:
    """统一 API：支持按请求控制检索条数与最少检索轮次，返回最终 Markdown。"""
    query = replace_chinese_punctuation(query or "")
    resolved_mode = _normalize_research_mode(mode)
    resolved_search_profile = _normalize_search_profile(search_profile)
    resolved_search_result_num = _normalize_search_result_num(search_result_num)
    resolved_verification_min_rounds = _normalize_verification_min_search_rounds(
        verification_min_search_rounds
    )
    resolved_output_detail_level = _normalize_output_detail_level(output_detail_level)
    resolved_api_render_mode = _normalize_render_mode(
        render_mode,
        _get_render_mode_for_output_detail(resolved_output_detail_level),
    )
    resolved_summary_merge_strategy = _normalize_final_summary_merge_strategy(
        _get_summary_merge_for_output_detail(resolved_output_detail_level)
    )
    task_id = str(uuid.uuid4())
    _reset_cancel_flag(task_id)
    _register_active_task(task_id)
    state = _init_render_state()
    try:
        async for message in stream_events_optimized(
            task_id,
            query,
            resolved_mode,
            resolved_search_profile,
            resolved_search_result_num,
            resolved_verification_min_rounds,
            resolved_output_detail_level,
            lambda: _disconnect_check_for_task(task_id),
        ):
            state = _update_state_with_event(state, message)
        return _render_markdown(
            state,
            render_mode=resolved_api_render_mode,
            final_summary_merge_strategy=resolved_summary_merge_strategy,
        )
    finally:
        _unregister_active_task(task_id)


def stop_current_ui(ui_state: Optional[dict] = None):
    tid = (ui_state or {}).get("task_id")
    target_ids = [tid] if tid else _get_active_task_ids()
    _cancel_task_ids(target_ids)
    return (
        gr.update(interactive=True),
        gr.update(interactive=False),
    )


def stop_current_api():
    active_task_ids = _get_active_task_ids()
    cancelled = _cancel_task_ids(active_task_ids)
    return {
        "cancelled": cancelled,
        "active_task_ids": active_task_ids,
    }


def restore_history_entry(
    query: str,
    result_markdown: str,
    ui_state: Optional[dict] = None,
):
    """通过 Gradio 受控更新恢复历史查询与结果，避免直接操作前端 DOM。"""
    restored_query = replace_chinese_punctuation(query or "")
    restored_markdown = result_markdown or "*该条历史暂无可回显结论，请重新运行一次。*"
    next_ui_state = dict(ui_state or {})
    return restored_query, restored_markdown, restored_markdown, next_ui_state


def build_demo():
    logo_data_uri = _load_logo_data_uri()
    fallback_favicon_data_uri = _build_fallback_favicon_data_uri()

    custom_css = """
    /* ========== MiroThinker - Modern Clean Design ========== */
    
    /* Base */
    .gradio-container {
        max-width: 100% !important;
        margin: 0 !important;
        padding: 0 !important;
        font-family: __LOCAL_FONT_FAMILY_STACK__ !important;
        background: #ffffff !important;
        min-height: 100vh;
    }
    
    footer { display: none !important; }
    
    /* ===== Top Navigation ===== */
    .top-nav {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 16px;
        padding: 14px 32px;
        border-bottom: 1px solid #f0f0f0;
        background: #ffffff;
    }
    
    .nav-left {
        display: flex;
        align-items: center;
        gap: 20px;
    }
    
    .nav-brand {
        display: flex;
        align-items: center;
        gap: 10px;
        font-weight: 600;
        font-size: 1.1em;
        color: #18181b;
    }

    .brand-logo {
        height: 32px;
        width: auto;
        max-width: 120px;
        object-fit: contain;
        display: block;
        flex-shrink: 0;
    }

    .nav-brand-text {
        line-height: 1.2;
        white-space: nowrap;
    }

    .nav-right {
        display: flex;
        align-items: center;
        justify-content: flex-end;
        min-width: 280px;
    }

    .skills-top-link {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        color: #0f766e;
        font-size: 0.83em;
        line-height: 1.2;
        text-decoration: none;
        border: 1px solid #99d8cb;
        border-radius: 999px;
        background: #f3fcfa;
        padding: 7px 14px;
        white-space: nowrap;
        transition: all 0.2s ease;
        user-select: none;
    }

    .skills-top-link:hover {
        border-color: #7ccbbc;
        background: #ecfaf6;
        color: #0f766e;
    }

    .skills-top-link:focus-visible {
        outline: 2px solid #99f6e4;
        outline-offset: 2px;
    }

    .skills-top-link-disabled {
        border-color: #d1d5db;
        background: #f8fafc;
        color: #94a3b8;
        cursor: not-allowed;
    }
    
    /* ===== Hero Section ===== */
    .hero-section {
        text-align: center;
        padding: 26px 24px 24px;
        max-width: 900px;
        margin: 0 auto;
    }

    .hero-brand {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 10px;
        margin-bottom: 18px;
    }

    .hero-logo {
        width: min(280px, 68vw);
        max-height: 104px;
        height: auto;
        object-fit: contain;
        box-shadow: none;
        border-radius: 0;
        flex-shrink: 0;
    }

    .hero-brand-name {
        font-size: 0.96em;
        font-weight: 600;
        color: #0f172a;
        letter-spacing: 0.01em;
    }
    
    .hero-title {
        font-size: 3em;
        font-weight: 700;
        background: linear-gradient(135deg, #10b981 0%, #14b8a6 50%, #06b6d4 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        margin: 0 0 16px 0;
        letter-spacing: -0.02em;
    }
    
    .hero-subtitle {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 16px;
        color: #71717a;
        font-size: 1em;
    }
    
    .hero-line {
        width: 40px;
        height: 1px;
        background: #d4d4d8;
    }

    
    /* ===== Input Section ===== */
    #input-section {
        max-width: 720px !important;
        margin: 0 auto 40px !important;
        background: #ffffff;
        border: 1px solid #e0e0e0;
        border-radius: 16px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.04);
    }
    
    #question-input {
        padding: 20px 24px !important;
        background: #ffffff !important;
        border: none !important;
    }
    
    #question-input textarea {
        background: #ffffff !important;
        border: none !important;
        font-size: 1.05em !important;
        line-height: 1.7 !important;
        color: #18181b !important;
        box-shadow: none !important;
    }
    
    #question-input textarea:focus {
        outline: none !important;
        box-shadow: none !important;
    }
    
    #question-input textarea::placeholder {
        color: #9ca3af !important;
    }

    #search-history-shell {
        padding: 0 24px 12px !important;
    }

    .search-history-card {
        border: 1px solid #e8ecef;
        border-radius: 14px;
        background: linear-gradient(180deg, #fcfffd 0%, #f7fbfa 100%);
        padding: 14px 16px;
    }

    .search-history-head {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 12px;
        margin-bottom: 10px;
    }

    .search-history-title {
        font-size: 0.92em;
        font-weight: 600;
        color: #1f2937;
        margin: 0;
    }

    .search-history-hint {
        font-size: 0.78em;
        color: #6b7280;
        line-height: 1.5;
        margin-top: 4px;
    }

    .search-history-clear {
        border: 1px solid #dbe3e8;
        background: #ffffff;
        color: #4b5563;
        border-radius: 999px;
        font-size: 0.78em;
        font-weight: 500;
        padding: 6px 12px;
        cursor: pointer;
        transition: all 0.2s ease;
        white-space: nowrap;
    }

    .search-history-clear:hover {
        border-color: #c7d2da;
        background: #f8fafc;
        color: #111827;
    }

    .search-history-list {
        display: flex;
        flex-direction: column;
        gap: 8px;
        max-height: 184px;
        overflow-y: auto;
        padding-right: 2px;
    }

    .search-history-item {
        display: flex;
        align-items: stretch;
        gap: 8px;
    }

    .search-history-entry {
        flex: 1;
        min-width: 0;
        border: 1px solid #e5eaee;
        background: #ffffff;
        border-radius: 12px;
        padding: 10px 12px;
        text-align: left;
        cursor: pointer;
        transition: all 0.2s ease;
    }

    .search-history-entry:hover {
        border-color: #b9e3d4;
        background: #f9fffc;
        transform: translateY(-1px);
    }

    .search-history-query {
        color: #111827;
        font-size: 0.9em;
        line-height: 1.5;
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
        overflow: hidden;
        word-break: break-word;
    }

    .search-history-meta {
        color: #6b7280;
        font-size: 0.76em;
        margin-top: 6px;
    }

    .search-history-delete {
        width: 38px;
        flex-shrink: 0;
        border: 1px solid #e5e7eb;
        background: #ffffff;
        color: #9ca3af;
        border-radius: 12px;
        cursor: pointer;
        transition: all 0.2s ease;
        font-size: 1.05em;
    }

    .search-history-delete:hover {
        border-color: #fecaca;
        background: #fff5f5;
        color: #dc2626;
    }

    .search-history-empty {
        border: 1px dashed #d8e2e8;
        border-radius: 12px;
        padding: 14px 12px;
        color: #6b7280;
        font-size: 0.82em;
        text-align: center;
        background: rgba(255, 255, 255, 0.72);
    }
    
    #btn-row {
        padding: 16px 24px !important;
        border-top: 1px solid #f0f0f0;
        gap: 12px !important;
    }
    
    #run-btn {
        background: linear-gradient(135deg, #10b981 0%, #14b8a6 100%) !important;
        color: #ffffff !important;
        border: none !important;
        border-radius: 10px !important;
        padding: 12px 24px !important;
        font-size: 0.95em !important;
        font-weight: 500 !important;
        cursor: pointer !important;
        transition: opacity 0.2s, transform 0.2s !important;
    }
    
    #run-btn:hover {
        opacity: 0.9 !important;
        transform: translateY(-1px) !important;
    }
    
    #stop-btn {
        background: #ffffff !important;
        color: #71717a !important;
        border: 1px solid #e5e5e5 !important;
        border-radius: 10px !important;
        padding: 12px 20px !important;
        font-size: 0.95em !important;
        font-weight: 500 !important;
        cursor: pointer !important;
        transition: all 0.2s !important;
    }
    
    #stop-btn:hover {
        color: #ef4444 !important;
        border-color: #fecaca !important;
        background: #fef2f2 !important;
    }
    
    /* ===== Output Section ===== */
    #output-section {
        max-width: 900px !important;
        margin: 0 auto !important;
        padding: 0 24px 60px !important;
    }
    
    .output-label {
        font-size: 0.85em;
        font-weight: 500;
        color: #71717a;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-bottom: 12px;
        padding: 0 4px;
    }
    
    #log-view {
        padding: 24px !important;
        min-height: 400px;
        max-height: 70vh;
        overflow-y: auto;
        background: #ffffff !important;
        border: 1px solid #e5e5e5 !important;
        border-radius: 16px !important;
    }
    
    #log-view h3 {
        font-size: 0.95em;
        font-weight: 600;
        color: #18181b;
        margin: 24px 0 16px 0;
        padding-bottom: 8px;
        border-bottom: 1px solid #f4f4f5;
    }
    
    #log-view h3:first-child {
        margin-top: 0;
    }
    
    /* Error block */
    .error-block {
        background: #fef2f2;
        border: 1px solid #fecaca;
        border-radius: 10px;
        padding: 12px 16px;
        margin: 12px 0;
        color: #dc2626;
        font-size: 0.9em;
    }
    
    /* Tool card */
    .tool-card {
        background: #fafafa;
        border: 1px solid #e5e5e5;
        border-radius: 10px;
        padding: 12px 16px;
        margin: 12px 0;
    }
    
    .tool-header {
        font-size: 0.9em;
        font-weight: 500;
        color: #3f3f46;
        margin-bottom: 4px;
    }
    
    .tool-brief {
        font-size: 0.8em;
        color: #71717a;
        margin-top: 4px;
    }
    
    .tool-status {
        font-size: 0.8em;
        color: #10b981;
        margin-top: 6px;
    }
    
    #log-view blockquote {
        background: linear-gradient(135deg, #f0fdf4 0%, #ecfeff 100%);
        border: none;
        border-left: 3px solid #10b981;
        padding: 16px 20px;
        margin: 16px 0;
        border-radius: 0 12px 12px 0;
        font-style: normal;
        color: #065f46;
        font-size: 0.9em;
        line-height: 1.7;
    }
    
    #log-view pre {
        background: #f8f9fa !important;
        color: #1e293b !important;
        border-radius: 8px !important;
        padding: 16px !important;
        font-size: 0.85em !important;
        line-height: 1.6 !important;
        overflow-x: auto;
        margin: 12px 0;
        border: 1px solid #e2e8f0;
    }
    
    #log-view pre code {
        background: transparent !important;
        color: #1e293b !important;
        font-family: 'SF Mono', 'Fira Code', 'JetBrains Mono', Consolas, monospace !important;
        font-size: inherit !important;
        padding: 0 !important;
        white-space: pre-wrap;
        word-break: break-word;
    }
    
    #log-view code {
        font-family: 'SF Mono', 'Fira Code', 'JetBrains Mono', Consolas, monospace !important;
        background: #f1f5f9 !important;
        color: #1e293b !important;
        padding: 2px 6px !important;
        border-radius: 4px !important;
        font-size: 0.9em !important;
    }
    
    #log-view p {
        line-height: 1.7;
        color: #3f3f46;
    }

    #log-view .process-details {
        margin-top: 12px;
        border: 1px solid #e5e7eb;
        border-radius: 10px;
        background: #fafafa;
        padding: 10px 12px;
    }

    #log-view .process-details > summary {
        cursor: pointer;
        color: #374151;
        font-size: 0.9em;
        font-weight: 500;
    }

    #log-view .process-details[open] > summary {
        margin-bottom: 10px;
    }
    
    #log-view::-webkit-scrollbar {
        width: 6px;
    }
    
    #log-view::-webkit-scrollbar-track {
        background: transparent;
    }
    
    #log-view::-webkit-scrollbar-thumb {
        background: #e5e5e5;
        border-radius: 3px;
    }
    
    #log-view::-webkit-scrollbar-thumb:hover {
        background: #d4d4d8;
    }
    
    /* ===== Footer ===== */
    .app-footer {
        text-align: center;
        padding: 24px;
        color: #a1a1aa;
        font-size: 0.85em;
        border-top: 1px solid #f0f0f0;
    }
    
    /* ===== Loading Spinner ===== */
    @keyframes spin {
        to { transform: rotate(360deg); }
    }
    
    .loading-indicator {
        display: inline-flex;
        align-items: center;
        gap: 10px;
        color: #10b981;
        font-size: 0.9em;
        padding: 12px 0;
    }
    
    .loading-indicator::before {
        content: '';
        width: 16px;
        height: 16px;
        border: 2px solid #d1fae5;
        border-top-color: #10b981;
        border-radius: 50%;
        animation: spin 0.8s linear infinite;
    }
    
    /* ===== Search Results Card ===== */
    .search-card {
        background: #ffffff;
        border: 1px solid #e5e5e5;
        border-radius: 12px;
        margin: 16px 0;
        overflow: hidden;
    }
    
    .search-header {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 14px 18px;
        background: #fafafa;
        border-bottom: 1px solid #f0f0f0;
    }
    
    .search-icon {
        font-size: 1em;
        color: #10b981;
    }
    
    .search-query {
        font-size: 0.9em;
        color: #3f3f46;
        font-weight: 500;
    }
    
    .search-count {
        padding: 10px 18px;
        font-size: 0.8em;
        color: #71717a;
        background: #fafafa;
        border-bottom: 1px solid #f0f0f0;
    }
    
    .search-results {
        padding: 8px 0;
    }
    
    .search-result-item {
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 12px 18px;
        text-decoration: none;
        color: #3f3f46;
        font-size: 0.9em;
        transition: background 0.15s;
        border-left: 3px solid transparent;
    }
    
    .search-result-item:hover {
        background: #f9fafb;
        border-left-color: #10b981;
    }
    
    .result-icon {
        font-size: 1em;
        flex-shrink: 0;
        opacity: 0.6;
    }
    
    .result-title {
        flex: 1;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
    
    /* ===== Scrape Card ===== */
    .scrape-card {
        background: #ffffff;
        border: 1px solid #e5e5e5;
        border-radius: 10px;
        margin: 12px 0;
        padding: 12px 16px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
    }
    
    .scrape-card.scrape-error {
        border-color: #fecaca;
        background: #fef2f2;
    }
    
    .scrape-header {
        display: flex;
        align-items: center;
        gap: 10px;
        flex: 1;
        min-width: 0;
    }
    
    .scrape-icon {
        font-size: 1em;
        opacity: 0.6;
    }
    
    .scrape-url {
        font-size: 0.85em;
        color: #52525b;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
    
    .scrape-status {
        font-size: 0.8em;
        padding: 4px 10px;
        border-radius: 6px;
        flex-shrink: 0;
    }
    
    .scrape-status.success {
        background: #ecfdf5;
        color: #059669;
    }
    
    .scrape-status.error {
        background: #fef2f2;
        color: #dc2626;
    }
    
    /* ===== Final Summary Section ===== */
    .final-summary-divider {
        height: 1px;
        background: linear-gradient(to right, transparent, #e5e5e5, transparent);
        margin: 32px 0;
    }
    
    .final-summary-section {
        background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%);
        border: 1px solid #e2e8f0;
        border-radius: 16px;
        padding: 24px;
        margin-top: 16px;
    }
    
    .final-summary-header {
        font-size: 1.1em;
        font-weight: 600;
        color: #1e293b;
        margin-bottom: 16px;
        padding-bottom: 12px;
        border-bottom: 2px solid #3b82f6;
        display: inline-block;
    }
    
    .final-summary-content {
        color: #334155;
        line-height: 1.8;
    }
    
    .final-summary-content h1,
    .final-summary-content h2,
    .final-summary-content h3 {
        color: #1e293b;
        margin-top: 1.5em;
        margin-bottom: 0.5em;
    }
    
    .final-summary-content h1 { font-size: 1.4em; }
    .final-summary-content h2 { font-size: 1.2em; }
    .final-summary-content h3 { font-size: 1.1em; }
    
    .final-summary-content p {
        margin: 0.8em 0;
    }
    
    .final-summary-content ul,
    .final-summary-content ol {
        margin: 0.8em 0;
        padding-left: 1.5em;
    }
    
    .final-summary-content li {
        margin: 0.4em 0;
    }
    
    .final-summary-content a {
        color: #3b82f6;
        text-decoration: none;
    }
    
    .final-summary-content a:hover {
        text-decoration: underline;
    }
    
    .final-summary-content code {
        background: #e2e8f0;
        padding: 2px 6px;
        border-radius: 4px;
        font-family: 'SF Mono', 'Fira Code', monospace;
        font-size: 0.9em;
    }
    
    .final-summary-content pre {
        background: #1e293b;
        color: #e2e8f0;
        padding: 16px;
        border-radius: 8px;
        overflow-x: auto;
    }
    
    .final-summary-content pre code {
        background: transparent;
        padding: 0;
        color: inherit;
    }
    
    .final-summary-content table {
        width: 100%;
        border-collapse: collapse;
        margin: 1em 0;
    }
    
    .final-summary-content th,
    .final-summary-content td {
        padding: 10px 12px;
        border: 1px solid #e2e8f0;
        text-align: left;
    }
    
    .final-summary-content th {
        background: #f1f5f9;
        font-weight: 600;
    }
    
    .final-summary-content blockquote {
        border-left: 4px solid #3b82f6;
        margin: 1em 0;
        padding: 0.5em 1em;
        background: #f8fafc;
        color: #475569;
    }
    
    /* ===== Code Execution Card ===== */
    .code-card {
        background: #1e1e2e;
        border: 1px solid #313244;
        border-radius: 12px;
        margin: 12px 0;
        padding: 16px;
        overflow: hidden;
    }
    
    .code-header {
        font-size: 0.9em;
        font-weight: 600;
        color: #cdd6f4;
        margin-bottom: 12px;
        display: flex;
        align-items: center;
        gap: 8px;
    }
    
    .code-card pre {
        background: #11111b !important;
        border-radius: 8px;
        padding: 12px 16px;
        margin: 8px 0;
        overflow-x: auto;
        font-family: 'SF Mono', 'Fira Code', 'JetBrains Mono', Consolas, monospace !important;
        font-size: 0.85em;
        line-height: 1.5;
    }
    
    .code-card code {
        background: transparent !important;
        color: #cdd6f4 !important;
        font-family: 'SF Mono', 'Fira Code', 'JetBrains Mono', Consolas, monospace !important;
    }
    
    .code-output-label {
        font-size: 0.8em;
        color: #a6adc8;
        margin-top: 12px;
        margin-bottom: 4px;
    }
    
    .code-status {
        font-size: 0.8em;
        color: #a6e3a1;
        margin-top: 8px;
        text-align: right;
    }
    
    /* ===== Responsive ===== */
    @media (max-width: 768px) {
        .hero-title {
            font-size: 2em;
        }

        .brand-logo {
            height: 28px;
            max-width: 96px;
        }

        .top-nav {
            padding: 14px 16px;
            flex-wrap: wrap;
        }

        .nav-right {
            width: 100%;
            min-width: 0;
            justify-content: flex-start;
        }

        .skills-top-link {
            padding: 6px 12px;
            font-size: 0.78em;
        }
        
        .hero-section {
            padding: 20px 16px 18px;
        }

        .hero-brand {
            margin-bottom: 14px;
            gap: 8px;
        }

        .hero-logo {
            width: min(220px, 72vw);
            max-height: 92px;
        }

        .hero-brand-name {
            font-size: 0.9em;
        }

        .hero-subtitle {
            gap: 10px;
            font-size: 0.92em;
        }

        .input-wrapper, .output-wrapper {
            padding: 0 16px;
        }

        #search-history-shell {
            padding: 0 16px 12px !important;
        }

        .search-history-head {
            flex-direction: column;
        }

        .search-history-clear {
            width: 100%;
        }
        
        #log-view {
            max-height: 50vh;
        }
    }
    """
    custom_css = custom_css.replace("__LOCAL_FONT_FAMILY_STACK__", LOCAL_FONT_FAMILY_STACK)

    # 统一使用本地 logo，避免外部资源依赖。
    if logo_data_uri:
        favicon_head = f'<link rel="icon" href="{logo_data_uri}">'
        nav_logo_html = (
            f'<img src="{logo_data_uri}" class="brand-logo" '
            'alt="OpenClaw-MiroSearch logo" />'
        )
    else:
        favicon_head = f'<link rel="icon" href="{fallback_favicon_data_uri}">'
        nav_logo_html = ""
    hero_logo_src = logo_data_uri or fallback_favicon_data_uri
    hero_brand_name_html = "" if logo_data_uri else "<span class=\"hero-brand-name\">OpenClaw-MiroSearch</span>"

    skills_download_url, _ = _resolve_skills_package_download()
    if skills_download_url:
        escaped_skills_download_url = html.escape(skills_download_url, quote=True)
        nav_skills_link_html = (
            f'<a id="skills-download-link" class="skills-top-link" href="{escaped_skills_download_url}" '
            f'data-copy-url="{escaped_skills_download_url}" target="_blank" rel="noopener noreferrer">'
            f"{SKILLS_DOWNLOAD_BUTTON_TEXT}</a>"
        )
    else:
        nav_skills_link_html = (
            f'<span class="skills-top-link skills-top-link-disabled" title="{html.escape(SKILLS_DOWNLOAD_FALLBACK_HINT, quote=True)}">'
            f"{SKILLS_DOWNLOAD_BUTTON_TEXT}</span>"
        )

    history_panel_html = f"""
        <div id="search-history-wrapper" class="search-history-card">
            <div class="search-history-head">
                <div>
                    <div class="search-history-title">{SEARCH_HISTORY_TITLE}</div>
                    <div class="search-history-hint">{SEARCH_HISTORY_HINT}</div>
                </div>
                <button
                    id="search-history-clear"
                    class="search-history-clear"
                    type="button"
                    hidden
                >
                    清空历史
                </button>
            </div>
            <div id="search-history-panel" class="search-history-list"></div>
        </div>
    """

    history_script = f"""
    <script>
    (() => {{
        const STORAGE_KEY = {json.dumps(SEARCH_HISTORY_STORAGE_KEY, ensure_ascii=False)};
        const MAX_ITEMS = {SEARCH_HISTORY_MAX_ITEMS};
        const EMPTY_TEXT = {json.dumps(SEARCH_HISTORY_EMPTY_TEXT, ensure_ascii=False)};
        const RESULT_MAX_TEXT_CHARS = {SEARCH_HISTORY_RESULT_MAX_TEXT_CHARS};
        const RESULT_CAPTURE_TIMEOUT_MS = {SEARCH_HISTORY_RESULT_CAPTURE_TIMEOUT_MS};
        const RESULT_CAPTURE_INTERVAL_MS = {SEARCH_HISTORY_RESULT_CAPTURE_INTERVAL_MS};
        const RESULT_CAPTURE_DEBOUNCE_MS = {SEARCH_HISTORY_RESULT_CAPTURE_DEBOUNCE_MS};
        const PLACEHOLDER_KEYWORDS = {json.dumps(SEARCH_HISTORY_PLACEHOLDER_KEYWORDS, ensure_ascii=False)};
        const SKILLS_BUTTON_TEXT = {json.dumps(SKILLS_DOWNLOAD_BUTTON_TEXT, ensure_ascii=False)};
        const SKILLS_BUTTON_COPIED_TEXT = {json.dumps(SKILLS_DOWNLOAD_COPIED_TEXT, ensure_ascii=False)};
        const SELECTORS = {{
            textarea: "#question-input textarea",
            runButton: "#run-btn",
            stopButton: "#stop-btn",
            historyPanel: "#search-history-panel",
            clearButton: "#search-history-clear",
            historyWrapper: "#search-history-wrapper",
            rawOutput: "#history-raw-output textarea, #history-raw-output input",
            restoreQuery: "#history-restore-query textarea, #history-restore-query input",
            restoreResult: "#history-restore-result textarea, #history-restore-result input",
            restoreButton: "#history-restore-btn button, #history-restore-btn",
            skillsDownloadLink: "#skills-download-link",
        }};
        let activeHistoryCapture = null;
        let resultCaptureDebounceTimer = null;
        let outputMutationObserver = null;

        const escapeHtml = (value) => String(value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");

        const loadHistory = () => {{
            try {{
                const rawValue = window.localStorage.getItem(STORAGE_KEY);
                if (!rawValue) {{
                    return [];
                }}
                const parsedValue = JSON.parse(rawValue);
                if (!Array.isArray(parsedValue)) {{
                    return [];
                }}
                return parsedValue.filter((item) => item && typeof item.query === "string");
            }} catch (error) {{
                console.warn("读取搜索历史失败", error);
                return [];
            }}
        }};

        const saveHistory = (historyItems) => {{
            const trimText = (value, maxLength) => {{
                const normalizedValue = typeof value === "string" ? value : "";
                if (normalizedValue.length <= maxLength) {{
                    return normalizedValue;
                }}
                return normalizedValue.slice(0, maxLength);
            }};
            const slicedItems = historyItems.slice(0, MAX_ITEMS);
            try {{
                window.localStorage.setItem(
                    STORAGE_KEY,
                    JSON.stringify(slicedItems)
                );
            }} catch (error) {{
                    try {{
                        const compactItems = slicedItems.map((item) => ({{
                            ...item,
                            result_markdown: trimText(item.result_markdown, RESULT_MAX_TEXT_CHARS),
                            result_text: trimText(item.result_text, 8000),
                        }}));
                        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(compactItems));
                }} catch (compactError) {{
                    try {{
                        const minimalItems = slicedItems.map((item) => ({{
                            id: item.id,
                            query: item.query,
                            saved_at: item.saved_at,
                            result_markdown: "",
                            result_text: "",
                            result_saved_at: item.result_saved_at || "",
                        }}));
                        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(minimalItems));
                    }} catch (minimalError) {{
                        console.warn("保存搜索历史失败", minimalError || compactError || error);
                    }}
                }}
            }}
        }};

        const formatTime = (isoTime) => {{
            if (!isoTime) {{
                return "刚刚";
            }}
            const dateValue = new Date(isoTime);
            if (Number.isNaN(dateValue.getTime())) {{
                return "刚刚";
            }}
            return dateValue.toLocaleString("zh-CN", {{
                month: "2-digit",
                day: "2-digit",
                hour: "2-digit",
                minute: "2-digit",
            }});
        }};

        const renderHistory = () => {{
            const panelElement = document.querySelector(SELECTORS.historyPanel);
            const clearButtonElement = document.querySelector(SELECTORS.clearButton);
            if (!panelElement || !clearButtonElement) {{
                return false;
            }}

            const historyItems = loadHistory();
            clearButtonElement.hidden = historyItems.length === 0;

            if (historyItems.length === 0) {{
                panelElement.innerHTML = `<div class="search-history-empty">${{escapeHtml(EMPTY_TEXT)}}</div>`;
                return true;
            }}

            panelElement.innerHTML = historyItems
                .map((item) => {{
                    const query = escapeHtml(item.query);
                    const itemId = escapeHtml(item.id || item.saved_at || item.query);
                    const savedAt = escapeHtml(formatTime(item.saved_at));
                    const resultSavedAt = item.result_saved_at
                        ? ` · 结果缓存：${{escapeHtml(formatTime(item.result_saved_at))}}`
                        : "";
                    return `
                        <div class="search-history-item">
                            <button
                                type="button"
                                class="search-history-entry"
                                data-history-action="restore"
                                data-history-id="${{itemId}}"
                            >
                                <div class="search-history-query">${{query}}</div>
                                <div class="search-history-meta">上次搜索：${{savedAt}}${{resultSavedAt}}</div>
                            </button>
                            <button
                                type="button"
                                class="search-history-delete"
                                data-history-action="delete"
                                data-history-id="${{itemId}}"
                                aria-label="删除这条搜索历史"
                                title="删除这条搜索历史"
                            >
                                ×
                            </button>
                        </div>
                    `;
                }})
                .join("");
            return true;
        }};

        const getCurrentQuery = () => {{
            const textareaElement = document.querySelector(SELECTORS.textarea);
            if (!textareaElement) {{
                return "";
            }}
            return textareaElement.value.trim();
        }};

        const setCurrentQuery = (query) => {{
            const textareaElement = document.querySelector(SELECTORS.textarea);
            if (!textareaElement) {{
                return;
            }}

            const descriptor = Object.getOwnPropertyDescriptor(
                window.HTMLTextAreaElement.prototype,
                "value"
            );
            if (descriptor && descriptor.set) {{
                descriptor.set.call(textareaElement, query);
            }} else {{
                textareaElement.value = query;
            }}
            textareaElement.dispatchEvent(new Event("input", {{ bubbles: true }}));
            textareaElement.dispatchEvent(new Event("change", {{ bubbles: true }}));
            textareaElement.focus();
            textareaElement.setSelectionRange(query.length, query.length);
        }};

        const getInputLikeElement = (selector) => {{
            if (!selector) {{
                return null;
            }}
            return document.querySelector(selector);
        }};

        const getInputLikeValue = (selector) => {{
            const inputElement = getInputLikeElement(selector);
            if (!inputElement) {{
                return "";
            }}
            return String(inputElement.value || "");
        }};

        const setInputLikeValue = (selector, value) => {{
            const inputElement = getInputLikeElement(selector);
            if (!inputElement) {{
                return false;
            }}
            const normalizedValue = String(value || "");
            const prototype = inputElement.tagName === "TEXTAREA"
                ? window.HTMLTextAreaElement.prototype
                : window.HTMLInputElement.prototype;
            const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
            if (descriptor && descriptor.set) {{
                descriptor.set.call(inputElement, normalizedValue);
            }} else {{
                inputElement.value = normalizedValue;
            }}
            inputElement.dispatchEvent(new Event("input", {{ bubbles: true }}));
            inputElement.dispatchEvent(new Event("change", {{ bubbles: true }}));
            return true;
        }};

        const getButtonElement = (selector) => {{
            const rootElement = document.querySelector(selector);
            if (!rootElement) {{
                return null;
            }}
            if (rootElement.tagName === "BUTTON") {{
                return rootElement;
            }}
            return rootElement.querySelector("button") || rootElement;
        }};

        const copyTextToClipboard = async (text) => {{
            const normalizedText = String(text || "").trim();
            if (!normalizedText) {{
                return false;
            }}
            if (navigator.clipboard && window.isSecureContext) {{
                try {{
                    await navigator.clipboard.writeText(normalizedText);
                    return true;
                }} catch (error) {{
                    console.warn("Clipboard API 复制失败，尝试降级复制", error);
                }}
            }}
            const textareaElement = document.createElement("textarea");
            textareaElement.value = normalizedText;
            textareaElement.setAttribute("readonly", "readonly");
            textareaElement.style.position = "fixed";
            textareaElement.style.opacity = "0";
            textareaElement.style.pointerEvents = "none";
            document.body.appendChild(textareaElement);
            textareaElement.focus();
            textareaElement.select();
            let copied = false;
            try {{
                copied = document.execCommand("copy");
            }} catch (error) {{
                console.warn("降级复制失败", error);
            }}
            document.body.removeChild(textareaElement);
            return copied;
        }};

        const bindSkillsDownloadAction = () => {{
            const linkElement = document.querySelector(SELECTORS.skillsDownloadLink);
            if (!linkElement) {{
                return;
            }}
            if (linkElement.dataset.boundCopyAction === "1") {{
                return;
            }}
            linkElement.dataset.boundCopyAction = "1";
            linkElement.addEventListener("click", () => {{
                const rawUrl = linkElement.dataset.copyUrl || linkElement.getAttribute("href") || "";
                let absoluteUrl = "";
                try {{
                    absoluteUrl = new URL(rawUrl, window.location.origin).toString();
                }} catch (error) {{
                    console.warn("skills 下载链接格式异常，无法复制", error);
                    return;
                }}
                copyTextToClipboard(absoluteUrl).then((copied) => {{
                    if (!copied) {{
                        return;
                    }}
                    linkElement.textContent = SKILLS_BUTTON_COPIED_TEXT;
                    window.setTimeout(() => {{
                        linkElement.textContent = SKILLS_BUTTON_TEXT;
                    }}, 1200);
                }});
            }});
        }};

        const isButtonDisabled = (buttonElement) => {{
            if (!buttonElement) {{
                return false;
            }}
            if (buttonElement.disabled) {{
                return true;
            }}
            if (buttonElement.getAttribute("aria-disabled") === "true") {{
                return true;
            }}
            return false;
        }};

        const isResearchRunning = () => {{
            const stopButtonElement = getButtonElement(SELECTORS.stopButton);
            if (stopButtonElement) {{
                return !isButtonDisabled(stopButtonElement);
            }}
            const runButtonElement = getButtonElement(SELECTORS.runButton);
            if (runButtonElement) {{
                return isButtonDisabled(runButtonElement);
            }}
            return false;
        }};

        const trimToLength = (value, maxLength) => {{
            const normalizedValue = typeof value === "string" ? value : "";
            if (normalizedValue.length <= maxLength) {{
                return normalizedValue;
            }}
            return normalizedValue.slice(0, maxLength);
        }};

        const normalizeResultText = (value) => trimToLength(
            String(value || "").replace(/\\s+/g, " ").trim(),
            RESULT_MAX_TEXT_CHARS
        );

        const isPlaceholderResult = (text) => {{
            const normalizedText = normalizeResultText(text);
            if (!normalizedText) {{
                return true;
            }}
            return PLACEHOLDER_KEYWORDS.some((keyword) =>
                normalizedText.includes(String(keyword || "").trim())
            );
        }};

        const getCurrentResultSnapshot = () => {{
            const resultMarkdown = trimToLength(
                getInputLikeValue(SELECTORS.rawOutput).trim(),
                RESULT_MAX_TEXT_CHARS
            );
            const resultText = normalizeResultText(resultMarkdown);
            if (!resultMarkdown && !resultText) {{
                return null;
            }}
            if (isPlaceholderResult(resultText)) {{
                return null;
            }}
            return {{
                result_markdown: resultMarkdown,
                result_text: resultText,
                result_saved_at: new Date().toISOString(),
            }};
        }};

        const updateHistoryItemResult = (historyId, snapshot) => {{
            if (!historyId || !snapshot) {{
                return;
            }}
            const nextHistory = loadHistory().map((item) => {{
                const itemId = String(item.id || item.saved_at || item.query);
                if (itemId !== historyId) {{
                    return item;
                }}
                return {{
                    ...item,
                    result_markdown: snapshot.result_markdown,
                    result_text: snapshot.result_text,
                    result_saved_at: snapshot.result_saved_at,
                }};
            }});
            saveHistory(nextHistory);
            renderHistory();
        }};

        const commitActiveHistorySnapshot = (forceCommit = false) => {{
            if (!activeHistoryCapture || !activeHistoryCapture.historyId) {{
                return;
            }}
            if (Date.now() - activeHistoryCapture.startedAt > RESULT_CAPTURE_TIMEOUT_MS) {{
                activeHistoryCapture = null;
                return;
            }}
            const snapshot = getCurrentResultSnapshot();
            if (!snapshot) {{
                return;
            }}
            if (!forceCommit && snapshot.result_text === activeHistoryCapture.lastResultText) {{
                return;
            }}
            updateHistoryItemResult(activeHistoryCapture.historyId, snapshot);
            activeHistoryCapture.lastResultText = snapshot.result_text;
            activeHistoryCapture.lastSavedAt = Date.now();
            if (forceCommit || !isResearchRunning()) {{
                activeHistoryCapture = null;
            }}
        }};

        const scheduleActiveHistoryCapture = () => {{
            if (!activeHistoryCapture) {{
                return;
            }}
            if (resultCaptureDebounceTimer) {{
                window.clearTimeout(resultCaptureDebounceTimer);
            }}
            resultCaptureDebounceTimer = window.setTimeout(() => {{
                resultCaptureDebounceTimer = null;
                commitActiveHistorySnapshot(false);
            }}, RESULT_CAPTURE_DEBOUNCE_MS);
        }};

        const ensureOutputObserverMounted = () => {{
            const rawOutputElement = getInputLikeElement(SELECTORS.rawOutput);
            if (!rawOutputElement) {{
                return false;
            }}
            if (outputMutationObserver) {{
                outputMutationObserver.disconnect();
            }}
            outputMutationObserver = new MutationObserver(() => {{
                scheduleActiveHistoryCapture();
            }});
            outputMutationObserver.observe(rawOutputElement, {{
                attributes: true,
                attributeFilter: ["value"],
                childList: true,
                subtree: true,
                characterData: true,
            }});
            return true;
        }};

        const startResultCaptureForHistory = (historyId) => {{
            if (!historyId) {{
                return;
            }}
            activeHistoryCapture = {{
                historyId,
                startedAt: Date.now(),
                lastResultText: "",
                lastSavedAt: 0,
            }};
            scheduleActiveHistoryCapture();
            window.setTimeout(() => {{
                commitActiveHistorySnapshot(false);
            }}, RESULT_CAPTURE_INTERVAL_MS);
        }};

        const addHistoryItem = (query) => {{
            const normalizedQuery = query.trim();
            if (!normalizedQuery) {{
                return "";
            }}

            const nextItem = {{
                id: `${{Date.now()}}-${{Math.random().toString(36).slice(2, 8)}}`,
                query: normalizedQuery,
                saved_at: new Date().toISOString(),
                result_markdown: "",
                result_text: "",
                result_saved_at: "",
            }};
            const nextHistory = [
                nextItem,
                ...loadHistory().filter((item) => item.query !== normalizedQuery),
            ];
            saveHistory(nextHistory);
            renderHistory();
            return nextItem.id;
        }};

        const deleteHistoryItem = (historyId) => {{
            if (activeHistoryCapture && activeHistoryCapture.historyId === historyId) {{
                activeHistoryCapture = null;
            }}
            if (resultCaptureDebounceTimer) {{
                window.clearTimeout(resultCaptureDebounceTimer);
                resultCaptureDebounceTimer = null;
            }}
            const nextHistory = loadHistory().filter(
                (item) => String(item.id || item.saved_at || item.query) !== historyId
            );
            saveHistory(nextHistory);
            renderHistory();
        }};

        const clearHistory = () => {{
            activeHistoryCapture = null;
            if (resultCaptureDebounceTimer) {{
                window.clearTimeout(resultCaptureDebounceTimer);
                resultCaptureDebounceTimer = null;
            }}
            window.localStorage.removeItem(STORAGE_KEY);
            renderHistory();
        }};

        const restoreHistoryItem = (historyId) => {{
            const historyItem = loadHistory().find(
                (item) => String(item.id || item.saved_at || item.query) === historyId
            );
            if (!historyItem) {{
                return;
            }}
            const restoreResult = String(
                historyItem.result_markdown || historyItem.result_text || ""
            ).trim();
            const queryReady = setInputLikeValue(SELECTORS.restoreQuery, historyItem.query);
            const resultReady = setInputLikeValue(SELECTORS.restoreResult, restoreResult);
            const restoreButtonElement = getButtonElement(SELECTORS.restoreButton);
            if (queryReady) {{
                setCurrentQuery(historyItem.query);
            }}
            if (!restoreButtonElement || !queryReady || !resultReady) {{
                console.info("该条历史暂无可回显结论，请重新运行一次后将自动缓存。");
                return;
            }}
            restoreButtonElement.click();
        }};

        const handleDocumentClick = (event) => {{
            const actionElement = event.target.closest("[data-history-action]");
            if (actionElement && actionElement.closest(SELECTORS.historyWrapper)) {{
                const actionName = actionElement.dataset.historyAction;
                const historyId = actionElement.dataset.historyId || "";
                if (actionName === "restore") {{
                    event.preventDefault();
                    restoreHistoryItem(historyId);
                }}
                if (actionName === "delete") {{
                    event.preventDefault();
                    deleteHistoryItem(historyId);
                }}
                return;
            }}

            const clearButtonElement = event.target.closest(SELECTORS.clearButton);
            if (clearButtonElement) {{
                event.preventDefault();
                clearHistory();
                return;
            }}

            const runButtonElement = event.target.closest(SELECTORS.runButton);
            if (runButtonElement) {{
                const currentQuery = getCurrentQuery();
                if (currentQuery) {{
                    const historyId = addHistoryItem(currentQuery);
                    startResultCaptureForHistory(historyId);
                }}
            }}
        }};

        const ensureHistoryMounted = () => {{
            bindSkillsDownloadAction();
            if (renderHistory()) {{
                ensureOutputObserverMounted();
                return;
            }}
            window.setTimeout(ensureHistoryMounted, 120);
        }};

        if (!window.__miroSearchHistoryInitialized) {{
            window.__miroSearchHistoryInitialized = true;
            document.addEventListener("click", handleDocumentClick, true);
            window.setInterval(() => {{
                if (activeHistoryCapture) {{
                    commitActiveHistorySnapshot(false);
                }}
            }}, RESULT_CAPTURE_INTERVAL_MS);
            if (document.readyState === "loading") {{
                document.addEventListener("DOMContentLoaded", ensureHistoryMounted, {{ once: true }});
            }} else {{
                ensureHistoryMounted();
            }}
        }}
    }})();
    </script>
    """
    demo_head = f"{favicon_head}{history_script}"

    with gr.Blocks(
        css=custom_css,
        title="OpenClaw-MiroSearch - 深度研究",
        theme=gr.themes.Base(),
        head=demo_head,
    ) as demo:
        # Top Navigation
        gr.HTML(f"""
            <nav class="top-nav">
                <div class="nav-left">
                    <div class="nav-brand">
                        {nav_logo_html}
                        <span class="nav-brand-text">OpenClaw-MiroSearch 深度研究</span>
                    </div>
                </div>
                <div class="nav-right">
                    {nav_skills_link_html}
                </div>
            </nav>
        """)

        # Hero Section
        gr.HTML(f"""
            <div class="hero-section">
                <div class="hero-brand">
                    <img src="{hero_logo_src}" class="hero-logo" alt="OpenClaw-MiroSearch logo" />
                    {hero_brand_name_html}
                </div>
                <h1 class="hero-title">深度研究，洞察未来</h1>
                <div class="hero-subtitle">
                    <span class="hero-line"></span>
                    不止于聊天，用可验证的检索与推理完成研究任务。
                    <span class="hero-line"></span>
                </div>
            </div>
        """)

        # Input Section
        with gr.Column(elem_id="input-section"):
            inp = gr.Textbox(
                lines=4,
                placeholder="请输入你的研究问题...",
                show_label=False,
                elem_id="question-input",
            )
            gr.HTML(history_panel_html, elem_id="search-history-shell")
            mode_selector = gr.Dropdown(
                label="检索模式",
                choices=RESEARCH_MODE_CHOICES,
                value=_normalize_research_mode(DEFAULT_RESEARCH_MODE),
                info="verified=多轮校验(高质量源) / research=质量优先 / balanced=推荐默认 / quota=额度优先 / thinking=纯思考 / production-web=生产风格",
            )
            search_profile_selector = gr.Dropdown(
                label="检索源策略",
                choices=SEARCH_PROFILE_CHOICES,
                value=_normalize_search_profile(DEFAULT_SEARCH_PROFILE),
                info="searxng-first=默认 / serp-first=Serp优先 / multi-route=串行聚合 / parallel=并发聚合 / parallel-trusted=并发+置信不足串行高信源补检 / searxng-only=仅SearXNG",
            )
            search_result_num_selector = gr.Dropdown(
                label="单轮检索条数",
                choices=SEARCH_RESULT_NUM_CHOICES,
                value=_normalize_search_result_num(DEFAULT_SEARCH_RESULT_NUM),
                info="每次 google_search 聚合返回的结果上限，建议 20 或 30 用于交叉验证。",
            )
            verification_min_rounds_selector = gr.Slider(
                minimum=1,
                maximum=MAX_VERIFICATION_MIN_SEARCH_ROUNDS,
                step=1,
                label="最少检索轮次（verified 生效）",
                value=_normalize_verification_min_search_rounds(
                    DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS
                ),
                info="仅在 verified 模式下用于强制多轮检索门槛。",
            )
            output_detail_level_selector = gr.Dropdown(
                label="输出篇幅",
                choices=[
                    (
                        OUTPUT_DETAIL_LEVEL_LABELS["compact"],
                        "compact",
                    ),
                    (
                        OUTPUT_DETAIL_LEVEL_LABELS["balanced"],
                        "balanced",
                    ),
                    (
                        OUTPUT_DETAIL_LEVEL_LABELS["detailed"],
                        "detailed",
                    ),
                ],
                value=_normalize_output_detail_level(DEFAULT_OUTPUT_DETAIL_LEVEL),
                info="精简=当前短篇幅 / 适中=核心结论+必要非核心信息 / 详细=超长报告（默认）",
            )
            with gr.Row(elem_id="btn-row"):
                stop_btn = gr.Button(
                    "⏹ 停止",
                    elem_id="stop-btn",
                    variant="stop",
                    interactive=False,
                    scale=1,
                )
                run_btn = gr.Button(
                    "开始研究 ➤", elem_id="run-btn", variant="primary", scale=2
                )

        # Output Section
        with gr.Column(elem_id="output-section"):
            gr.HTML('<div class="output-label">研究进度</div>')
            out_md = gr.Markdown("*等待开始研究...*", elem_id="log-view")
            history_raw_output = gr.Textbox(
                value="*等待开始研究...*",
                visible=False,
                elem_id="history-raw-output",
            )

        # 供统一 API 调用的隐藏输出
        api_output = gr.Markdown(visible=False)
        api_btn = gr.Button(value="api-run", visible=False)
        api_stop_output = gr.JSON(visible=False)
        api_stop_btn = gr.Button(value="api-stop", visible=False)
        history_restore_query = gr.Textbox(visible=False, elem_id="history-restore-query")
        history_restore_result = gr.Textbox(
            visible=False,
            elem_id="history-restore-result",
        )
        history_restore_btn = gr.Button(
            value="restore-history",
            visible=False,
            elem_id="history-restore-btn",
        )

        # State
        ui_state = gr.State(
            {
                "task_id": None,
                "mode": _normalize_research_mode(DEFAULT_RESEARCH_MODE),
                "search_profile": _normalize_search_profile(DEFAULT_SEARCH_PROFILE),
                "search_result_num": _normalize_search_result_num(
                    DEFAULT_SEARCH_RESULT_NUM
                ),
                "verification_min_search_rounds": _normalize_verification_min_search_rounds(
                    DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS
                ),
                "output_detail_level": _normalize_output_detail_level(
                    DEFAULT_OUTPUT_DETAIL_LEVEL
                ),
                "render_mode": _get_render_mode_for_output_detail(
                    _normalize_output_detail_level(DEFAULT_OUTPUT_DETAIL_LEVEL)
                ),
                "final_summary_merge_strategy": _get_summary_merge_for_output_detail(
                    _normalize_output_detail_level(DEFAULT_OUTPUT_DETAIL_LEVEL)
                ),
            }
        )

        # Event handlers
        run_btn.click(
            fn=gradio_run,
            inputs=[
                inp,
                mode_selector,
                search_profile_selector,
                search_result_num_selector,
                verification_min_rounds_selector,
                output_detail_level_selector,
                ui_state,
            ],
            outputs=[out_md, run_btn, stop_btn, ui_state, history_raw_output],
            api_name="run_research_stream",
        )
        stop_btn.click(
            fn=stop_current_ui,
            inputs=[ui_state],
            outputs=[run_btn, stop_btn],
            api_name=False,
        )
        api_btn.click(
            fn=run_research_once,
            inputs=[
                inp,
                mode_selector,
                search_profile_selector,
                search_result_num_selector,
                verification_min_rounds_selector,
                output_detail_level_selector,
            ],
            outputs=[api_output],
            api_name="run_research_once",
        )
        api_stop_btn.click(
            fn=stop_current_api,
            inputs=[],
            outputs=[api_stop_output],
            api_name="stop_current",
        )
        history_restore_btn.click(
            fn=restore_history_entry,
            inputs=[history_restore_query, history_restore_result, ui_state],
            outputs=[inp, out_md, history_raw_output, ui_state],
            api_name=False,
        )

        # Footer
        gr.HTML("""
            <div class="app-footer">
                由 AI 生成，请对关键信息进行复核。
            </div>
        """)

    return demo


if __name__ == "__main__":
    demo = build_demo()
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))
    allowed_paths = _collect_gradio_allowed_paths()
    launch_kwargs = {
        "server_name": host,
        "server_port": port,
    }
    if allowed_paths:
        launch_kwargs["allowed_paths"] = allowed_paths
    demo.queue().launch(**launch_kwargs)
