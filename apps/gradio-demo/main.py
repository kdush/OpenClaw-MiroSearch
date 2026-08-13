import asyncio
import base64
import io
import html
import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

import gradio as gr
from dotenv import load_dotenv
from hydra import compose, initialize_config_dir
from omegaconf import DictConfig
from prompt_patch import apply_prompt_patch
from src.config.settings import expose_sub_agents_as_tools
from src.cache.result_cache import ResultCache
from src.core.pipeline import create_pipeline_components, execute_task_pipeline
from utils import replace_chinese_punctuation

import api_client

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


def _env_int_at_least(name: str, default_value: int, minimum: int) -> int:
    """读取带下限的整数环境变量；越界值按配置错误回退默认值。"""
    parsed_value = _env_int(name, default_value)
    return parsed_value if parsed_value >= minimum else default_value


def _env_int_choice(
    name: str,
    default_value: int,
    choices: List[int],
) -> int:
    """读取枚举整数；环境值非法或不在允许集合时回退到默认值。"""
    parsed_value = _env_int(name, default_value)
    return parsed_value if parsed_value in choices else default_value


def _env_int_range(
    name: str,
    default_value: int,
    minimum: int,
    maximum: int,
) -> int:
    """读取范围整数；越界时回退默认值而不是夹到另一运行档位。"""
    parsed_value = _env_int(name, default_value)
    if minimum <= parsed_value <= maximum:
        return parsed_value
    return default_value


def _env_float(name: str, default_value: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default_value
    try:
        return float(raw_value)
    except ValueError:
        return default_value


def _env_non_empty(name: str, default_value: str) -> str:
    """读取非空字符串环境变量；空白值安全回退。"""
    raw_value = os.getenv(name)
    if raw_value is None:
        return default_value
    normalized = raw_value.strip()
    if normalized:
        return normalized
    logger.warning(
        "环境变量 %s=%r 为空，回退到 %s",
        name,
        raw_value,
        default_value,
    )
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
DEFAULT_SEARCH_RESULT_NUM = _env_int_choice(
    "DEFAULT_SEARCH_RESULT_NUM",
    SEARCH_RESULT_NUM_CHOICES[1],
    SEARCH_RESULT_NUM_CHOICES,
)
SEARCH_RESULT_DISPLAY_MAX = max(1, _env_int("SEARCH_RESULT_DISPLAY_MAX", 30))
SEARCH_STEP_QUERY_PREVIEW_CHARS = max(
    20, _env_int("SEARCH_STEP_QUERY_PREVIEW_CHARS", 96)
)
SEARCH_STEP_SOURCE_PREVIEW_CHARS = max(
    10, _env_int("SEARCH_STEP_SOURCE_PREVIEW_CHARS", 48)
)
COLLAPSE_PROCESS_AFTER_SUMMARY = _env_flag("COLLAPSE_PROCESS_AFTER_SUMMARY", True)
MAX_VERIFICATION_MIN_SEARCH_ROUNDS = 8
DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS = _env_int_range(
    "DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS",
    3,
    1,
    MAX_VERIFICATION_MIN_SEARCH_ROUNDS,
)
DEFAULT_MODEL_NAME = _env_non_empty(
    "DEFAULT_MODEL_NAME",
    "qwen/qwen3.6-plus",
)
DEFAULT_MODEL_TOOL_NAME = _env_non_empty("MODEL_TOOL_NAME", DEFAULT_MODEL_NAME)
DEFAULT_MODEL_FAST_NAME = _env_non_empty(
    "MODEL_FAST_NAME",
    "qwen/qwen3.6-35b-a3b",
)
DEFAULT_MODEL_THINKING_NAME = _env_non_empty(
    "MODEL_THINKING_NAME",
    DEFAULT_MODEL_NAME,
)
DEFAULT_MODEL_SUMMARY_NAME = _env_non_empty(
    "MODEL_SUMMARY_NAME",
    DEFAULT_MODEL_FAST_NAME,
)
ENABLE_TIMING_DIAGNOSTICS = _env_flag("ENABLE_TIMING_DIAGNOSTICS", True)
PIPELINE_CANCEL_POLL_INTERVAL_SECONDS = max(
    0.01,
    _env_float("PIPELINE_CANCEL_POLL_INTERVAL_SECONDS", 0.1),
)
STALE_TASK_REAPER_ENABLED = _env_flag("STALE_TASK_REAPER_ENABLED", True)
STALE_TASK_REAPER_INTERVAL_SECONDS = max(
    30, _env_int("STALE_TASK_REAPER_INTERVAL_SECONDS", 120)
)
STALE_TASK_RUNNING_TIMEOUT_SECONDS = max(
    120, _env_int("STALE_TASK_RUNNING_TIMEOUT_SECONDS", 900)
)
STALE_TASK_REAPER_SCAN_LIMIT = max(10, _env_int("STALE_TASK_REAPER_SCAN_LIMIT", 500))
LOCAL_FONT_FAMILY_STACK = (
    "'Avenir Next', 'SF Pro Display', 'PingFang SC', 'Hiragino Sans GB', "
    "'Microsoft YaHei', -apple-system, BlinkMacSystemFont, sans-serif"
)
SKILLS_PACKAGE_SAFE_DIR = Path(__file__).resolve().parents[2] / "skills"
SKILLS_PACKAGE_DEFAULT_FILENAME = "openclaw-mirosearch.zip"
DEFAULT_SKILLS_PACKAGE_PATH = os.getenv(
    "SKILLS_PACKAGE_PATH",
    str(SKILLS_PACKAGE_SAFE_DIR / SKILLS_PACKAGE_DEFAULT_FILENAME),
)
DEFAULT_SKILLS_PACKAGE_URL = os.getenv("SKILLS_PACKAGE_URL", "").strip()
SKILLS_DOWNLOAD_FALLBACK_HINT_EN = "No download URL detected. Please configure SKILLS_PACKAGE_URL environment variable."
SKILLS_DOWNLOAD_BUTTON_TEXT_EN = "Download Skills"
SKILLS_DOWNLOAD_COPIED_TEXT_EN = "Link Copied"
SKILLS_DOWNLOAD_FALLBACK_HINT_CN = (
    "未检测到可用下载地址，请配置环境变量 SKILLS_PACKAGE_URL。"
)
SKILLS_DOWNLOAD_BUTTON_TEXT_CN = "skills下载"
SKILLS_DOWNLOAD_COPIED_TEXT_CN = "已复制链接"
EXPORT_FORMAT_CHOICES = [
    ("Markdown (.md)", "md"),
    ("PDF (.pdf)", "pdf"),
    ("Word (.docx)", "docx"),
]
EXPORT_FORMAT_EXTENSIONS = {"md": ".md", "pdf": ".pdf", "docx": ".docx"}
EXPORT_FILENAME_PREFIX = os.getenv("EXPORT_FILENAME_PREFIX", "mirosearch-conclusion")
EXPORT_OUTPUT_DIR = Path(
    os.getenv(
        "EXPORT_OUTPUT_DIR",
        str(Path(tempfile.gettempdir()) / "openclaw-mirosearch-exports"),
    )
)
EXPORT_PDF_PAGE_WIDTH = 595
EXPORT_PDF_PAGE_HEIGHT = 842
EXPORT_PDF_MARGIN_LEFT = 50
EXPORT_PDF_MARGIN_TOP = 800
EXPORT_PDF_FONT_SIZE = 11
EXPORT_PDF_LINE_HEIGHT = 14
EXPORT_PDF_LINES_PER_PAGE = 52
EXPORT_PDF_LINE_CHARS = 58

LANG_EN = "en"
LANG_CN = "cn"
DEFAULT_LANG = LANG_EN

I18N = {
    LANG_EN: {
        "page_title": "OpenClaw-MiroSearch - Deep Research",
        "nav_brand_text": "OpenClaw-MiroSearch Deep Research",
        "hero_title": "Deep Research, Insight into the Future",
        "hero_subtitle": "Beyond chat, complete research tasks with verifiable search and reasoning.",
        "input_placeholder": "Enter your research question...",
        "btn_stop": "⏹ Stop",
        "btn_run": "Start Research ➤",
        "output_label": "Research Progress",
        "output_waiting": "*Waiting to start research...*",
        "options_title": "Options / Advanced Settings",
        "mode_label": "Search Mode",
        "mode_info": "verified=multi-round verification(high-quality sources) / research=quality first / balanced=recommended default / quota=quota priority / thinking=pure reasoning / production-web=production style",
        "search_profile_label": "Search Source Strategy",
        "search_profile_info": "searxng-first=default / serp-first=Serp priority / multi-route=serial aggregation / parallel=parallel aggregation / parallel-trusted=parallel+confidence fallback / searxng-only=SearXNG only",
        "search_result_num_label": "Results per Search",
        "search_result_num_info": "Maximum results returned per google_search aggregation. Recommended: 20 or 30 for cross-verification.",
        "verification_rounds_label": "Min Search Rounds (verified mode)",
        "verification_rounds_info": "Only effective in verified mode to enforce minimum search rounds threshold.",
        "output_detail_label": "Output Length",
        "output_detail_info": "Compact=short / Balanced=core conclusions + necessary details / Detailed=full report (default)",
        "footer_text": "Generated by AI. Please verify key information.",
        "lang_toggle_btn": "中文",
        "export_format_label": "Export Format",
        "export_btn": "Export Conclusion",
        "export_file_label": "Download exported conclusion",
        "export_hint": "Markdown / PDF / Word export is available. Long screenshot and community sharing are planned.",
        "skills_download_fallback": SKILLS_DOWNLOAD_FALLBACK_HINT_EN,
        "skills_download_btn": SKILLS_DOWNLOAD_BUTTON_TEXT_EN,
        "skills_download_copied": SKILLS_DOWNLOAD_COPIED_TEXT_EN,
        "output_detail_labels": {
            "compact": "Compact",
            "balanced": "Balanced",
            "detailed": "Detailed",
        },
    },
    LANG_CN: {
        "page_title": "OpenClaw-MiroSearch - 深度研究",
        "nav_brand_text": "OpenClaw-MiroSearch 深度研究",
        "hero_title": "深度研究，洞察未来",
        "hero_subtitle": "不止于聊天，用可验证的检索与推理完成研究任务。",
        "input_placeholder": "请输入你的研究问题...",
        "btn_stop": "⏹ 停止",
        "btn_run": "开始研究 ➤",
        "output_label": "研究进度",
        "output_waiting": "*等待开始研究...*",
        "options_title": "Options / 高级配置",
        "mode_label": "检索模式",
        "mode_info": "verified=多轮校验(高质量源) / research=质量优先 / balanced=推荐默认 / quota=额度优先 / thinking=纯思考 / production-web=生产风格",
        "search_profile_label": "检索源策略",
        "search_profile_info": "searxng-first=默认 / serp-first=Serp优先 / multi-route=串行聚合 / parallel=并发聚合 / parallel-trusted=并发+置信不足串行高信源补检 / searxng-only=仅SearXNG",
        "search_result_num_label": "单轮检索条数",
        "search_result_num_info": "每次 google_search 聚合返回的结果上限，建议 20 或 30 用于交叉验证。",
        "verification_rounds_label": "最少检索轮次（verified 生效）",
        "verification_rounds_info": "仅在 verified 模式下用于强制多轮检索门槛。",
        "output_detail_label": "输出篇幅",
        "output_detail_info": "精简=当前短篇幅 / 适中=核心结论+必要非核心信息 / 详细=超长报告（默认）",
        "footer_text": "由 AI 生成，请对关键信息进行复核。",
        "lang_toggle_btn": "English",
        "export_format_label": "导出格式",
        "export_btn": "导出结论",
        "export_file_label": "下载导出文件",
        "export_hint": "已支持 Markdown / PDF / Word 导出；长截图与社区分享已纳入规划。",
        "skills_download_fallback": SKILLS_DOWNLOAD_FALLBACK_HINT_CN,
        "skills_download_btn": SKILLS_DOWNLOAD_BUTTON_TEXT_CN,
        "skills_download_copied": SKILLS_DOWNLOAD_COPIED_TEXT_CN,
        "output_detail_labels": {
            "compact": "精简",
            "balanced": "适中",
            "detailed": "详细",
        },
    },
}
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
VERIFIED_CONTEXT_COMPRESS_LIMIT = max(0, _env_int("VERIFIED_CONTEXT_COMPRESS_LIMIT", 3))
RESEARCH_MAX_TOKENS = max(1024, _env_int("RESEARCH_LLM_MAX_TOKENS", 3072))
RESEARCH_TOOL_RESULT_MAX_CHARS = max(
    2000, _env_int("RESEARCH_TOOL_RESULT_MAX_CHARS", 6000)
)
RESEARCH_KEEP_TOOL_RESULT = max(-1, _env_int("RESEARCH_KEEP_TOOL_RESULT", 3))
RESEARCH_CONTEXT_COMPRESS_LIMIT = max(0, _env_int("RESEARCH_CONTEXT_COMPRESS_LIMIT", 2))
BALANCED_MAX_TOKENS = max(1024, _env_int("BALANCED_LLM_MAX_TOKENS", 3072))
BALANCED_TOOL_RESULT_MAX_CHARS = max(
    2000, _env_int("BALANCED_TOOL_RESULT_MAX_CHARS", 5000)
)
BALANCED_KEEP_TOOL_RESULT = max(-1, _env_int("BALANCED_KEEP_TOOL_RESULT", 3))
BALANCED_CONTEXT_COMPRESS_LIMIT = max(0, _env_int("BALANCED_CONTEXT_COMPRESS_LIMIT", 2))
QUOTA_MAX_TOKENS = max(1024, _env_int("QUOTA_LLM_MAX_TOKENS", 2048))
QUOTA_TOOL_RESULT_MAX_CHARS = max(2000, _env_int("QUOTA_TOOL_RESULT_MAX_CHARS", 3500))
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

DETAIL_DETAILED_MAX_TOKENS = max(1024, _env_int("DETAIL_DETAILED_MAX_TOKENS", 16384))
DETAIL_DETAILED_TOOL_RESULT_MAX_CHARS = max(
    2000, _env_int("DETAIL_DETAILED_TOOL_RESULT_MAX_CHARS", 20000)
)
DETAIL_DETAILED_SUMMARY_MAX_TOKENS = max(
    1024, _env_int("DETAIL_DETAILED_SUMMARY_MAX_TOKENS", 16384)
)
DETAIL_DETAILED_VERIFICATION_MAX_TOKENS = max(
    1024, _env_int("DETAIL_DETAILED_VERIFICATION_MAX_TOKENS", 12288)
)
DETAIL_DETAILED_KEEP_TOOL_RESULT = max(
    -1, _env_int("DETAIL_DETAILED_KEEP_TOOL_RESULT", -1)
)
DETAIL_DETAILED_CONTEXT_COMPRESS_LIMIT = max(
    0, _env_int("DETAIL_DETAILED_CONTEXT_COMPRESS_LIMIT", 0)
)
DETAIL_DETAILED_MAIN_AGENT_MAX_TURNS = max(
    1, _env_int("DETAIL_DETAILED_MAIN_AGENT_MAX_TURNS", 20)
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

SEARCH_STAGE_TOOL_NAMES = {
    "google_search",
    "sogou_search",
    "scrape",
    "scrape_website",
    "scrape_webpage",
    "scrape_and_extract_info",
}

# 工具名 → 前端友好显示名
TOOL_DISPLAY_NAMES: dict[str, str] = {
    "google_search": "网络搜索",
    "sogou_search": "搜狗搜索",
    "scrape": "网页抓取",
    "scrape_website": "网页抓取",
    "scrape_webpage": "网页抓取",
    "scrape_and_extract_info": "信息提取",
    "show_text": "文本展示",
}


def _tool_display_name(raw_name: str) -> str:
    return TOOL_DISPLAY_NAMES.get(raw_name, raw_name)


RENDER_MODE_CHOICES = {"full", "summary_with_details", "summary_only"}
DEFAULT_UI_RENDER_MODE = os.getenv("DEFAULT_UI_RENDER_MODE", "summary_with_details")
DEFAULT_API_RENDER_MODE = os.getenv("DEFAULT_API_RENDER_MODE", "summary_with_details")
FINAL_SUMMARY_MERGE_STRATEGY_CHOICES = {"latest", "all_unique"}
DEFAULT_FINAL_SUMMARY_MERGE_STRATEGY = os.getenv(
    "FINAL_SUMMARY_MERGE_STRATEGY", "all_unique"
)

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


def _resolve_effective_verification_min_search_rounds(
    mode: Optional[str],
    min_rounds: Optional[int],
) -> int:
    """仅在 verified 模式启用最少检索轮次，其它模式固定为默认值。"""
    if _normalize_research_mode(mode) != "verified":
        return _normalize_verification_min_search_rounds(
            DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS
        )
    return _normalize_verification_min_search_rounds(min_rounds)


def _is_verified_mode(mode: Optional[str]) -> bool:
    return _normalize_research_mode(mode) == "verified"


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


def _hydra_override_key(override: str) -> str:
    """提取 Hydra override 的字段或配置组键。"""
    normalized = override.lstrip("+")
    key, separator, _ = normalized.partition("=")
    return key if separator else normalized


def _is_config_group_override(override: str) -> bool:
    """判断是否为 ``agent=...`` 一类配置组选择。"""
    key = _hydra_override_key(override)
    return not override.startswith("+") and "." not in key


def _combine_detail_and_mode_overrides(
    detail_overrides: List[str],
    mode_overrides: List[str],
) -> List[str]:
    """组合配置组、篇幅默认值及模式硬约束，并明确模式的最高优先级。"""
    config_group_overrides = [
        override for override in mode_overrides if _is_config_group_override(override)
    ]
    mode_value_overrides = [
        override
        for override in mode_overrides
        if not _is_config_group_override(override)
    ]
    return config_group_overrides + detail_overrides + mode_value_overrides


def _build_profile_overrides(
    mode: str,
    output_detail_level: str,
    verification_min_search_rounds: int,
) -> List[str]:
    """构建一次预加载使用的完整策略 overrides。"""
    resolved_mode = _normalize_research_mode(mode)
    overrides = _combine_detail_and_mode_overrides(
        detail_overrides=_get_mode_overrides_for_output_detail(output_detail_level),
        mode_overrides=list(
            MODE_OVERRIDE_MAP.get(resolved_mode, MODE_OVERRIDE_MAP["balanced"])
        ),
    )
    if resolved_mode == "verified":
        overrides.append(
            f"agent.verification.min_search_rounds={verification_min_search_rounds}"
        )
    return overrides


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
    model_name = _env_non_empty(
        "DEFAULT_MODEL_NAME",
        DEFAULT_MODEL_NAME,
    )
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
        error_message = f"Failed to compose Hydra config: {e}"
        logger.error(error_message)
        raise RuntimeError(error_message) from e


# Lazy loading for tool definitions to speed up page load
# 按检索模式缓存，支持通过参数动态切换
_preload_cache = {}
_preload_cache_lock = threading.Lock()
_preload_inflight: Dict[Tuple[str, str, int, int, str], Dict[str, Any]] = {}
_component_env_lock = threading.Lock()
_stale_task_reaper_started = False
_stale_task_reaper_lock = threading.Lock()


def _build_search_environment(
    search_profile: str,
    search_result_num: int,
) -> Dict[str, str]:
    """构建创建检索 MCP 参数时使用的临时环境。"""
    search_env = dict(
        SEARCH_PROFILE_ENV_MAP.get(
            search_profile,
            SEARCH_PROFILE_ENV_MAP["searxng-first"],
        )
    )
    search_env["SEARCH_RESULT_NUM"] = str(search_result_num)
    return search_env


def _create_task_runtime_components(
    profile_cache: Dict[str, Any],
) -> Tuple[Any, Dict[str, Any], Any]:
    """为单个本地任务创建独立的有状态 pipeline 组件。

    ToolManager 会持有 task_log 与 browser_session，不能跨任务复用。组件工厂还会
    从进程环境读取检索路由参数，因此组件工厂必须串行，避免不同 profile 并发
    创建时互相覆盖环境；该锁不覆盖工具定义的网络发现。
    """
    search_env = _build_search_environment(
        profile_cache["search_profile"],
        profile_cache["search_result_num"],
    )
    with _component_env_lock:
        with _temporary_env_vars(search_env):
            return create_pipeline_components(profile_cache["cfg"])


async def _close_preload_tool_managers(
    main_agent_tool_manager: Any,
    sub_agent_tool_managers: Dict[str, Any],
) -> None:
    """按 identity 去重并尽力关闭预加载阶段的临时 manager。"""
    managers = [main_agent_tool_manager, *sub_agent_tool_managers.values()]
    seen_manager_ids = set()
    unique_managers = []
    for tool_manager in managers:
        manager_id = id(tool_manager)
        if manager_id in seen_manager_ids:
            continue
        seen_manager_ids.add(manager_id)
        unique_managers.append(tool_manager)

    async def close_one(tool_manager: Any) -> None:
        close = getattr(tool_manager, "aclose", None)
        if not callable(close):
            return
        try:
            await close()
        except (Exception, asyncio.CancelledError):
            pass

    await asyncio.gather(*(close_one(manager) for manager in unique_managers))


async def _discover_preload_tool_definitions(
    main_agent_tool_manager: Any,
    sub_agent_tool_managers: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    """获取静态工具定义，并确保临时 manager 在所有路径都被关闭。"""
    try:
        tool_definitions = await main_agent_tool_manager.get_all_tool_definitions()
        sub_agent_tool_definitions = {}
        for name, sub_agent_tool_manager in sub_agent_tool_managers.items():
            sub_agent_tool_definitions[
                name
            ] = await sub_agent_tool_manager.get_all_tool_definitions()
        return tool_definitions, sub_agent_tool_definitions
    finally:
        await _close_preload_tool_managers(
            main_agent_tool_manager,
            sub_agent_tool_managers,
        )


def _preload_info(
    cache_key: Tuple[str, str, int, int, str],
    *,
    cache_hit: bool,
    started_at: float,
) -> Dict[str, Any]:
    return {
        "cache_key": cache_key,
        "cache_hit": cache_hit,
        "duration_ms": int((time.perf_counter() - started_at) * 1000),
    }


def _ensure_preloaded(
    mode: str,
    search_profile: str,
    search_result_num: int,
    verification_min_search_rounds: int,
    output_detail_level: str,
):
    """按检索模式与检索源策略懒加载配置及工具定义。"""
    global _preload_cache
    preload_start_time = time.perf_counter()
    resolved_mode = _normalize_research_mode(mode)
    resolved_result_num = _normalize_search_result_num(search_result_num)
    resolved_min_rounds = _resolve_effective_verification_min_search_rounds(
        resolved_mode,
        verification_min_search_rounds,
    )
    resolved_output_detail_level = _normalize_output_detail_level(output_detail_level)
    cache_key = _compose_profile_cache_key(
        resolved_mode,
        search_profile,
        resolved_result_num,
        resolved_min_rounds,
        resolved_output_detail_level,
    )

    with _preload_cache_lock:
        if cache_key in _preload_cache:
            preload_info = _preload_info(
                cache_key,
                cache_hit=True,
                started_at=preload_start_time,
            )
            is_loader = False
            preload_state = None
        else:
            preload_state = _preload_inflight.get(cache_key)
            if preload_state is None:
                preload_state = {
                    "event": threading.Event(),
                    "error": None,
                }
                _preload_inflight[cache_key] = preload_state
                is_loader = True
            else:
                is_loader = False
            preload_info = None

    if preload_info is not None:
        if ENABLE_TIMING_DIAGNOSTICS:
            logger.info(
                "Pipeline preload cache hit | cache_key=%s | duration_ms=%s",
                cache_key,
                preload_info["duration_ms"],
            )
        return preload_info

    if not is_loader:
        preload_state["event"].wait()
        with _preload_cache_lock:
            cache_available = cache_key in _preload_cache
            preload_error = preload_state.get("error")
        if not cache_available:
            if preload_error is not None:
                raise RuntimeError(
                    f"Pipeline preload failed for cache key {cache_key}: "
                    f"{preload_error}"
                ) from preload_error
            raise RuntimeError(
                f"Pipeline preload finished without cache entry: {cache_key}"
            )
        preload_info = _preload_info(
            cache_key,
            cache_hit=True,
            started_at=preload_start_time,
        )
        if ENABLE_TIMING_DIAGNOSTICS:
            logger.info(
                "Pipeline preload shared result | cache_key=%s | duration_ms=%s",
                cache_key,
                preload_info["duration_ms"],
            )
        return preload_info

    try:
        search_env = _build_search_environment(
            search_profile,
            resolved_result_num,
        )
        mode_overrides = _build_profile_overrides(
            mode=resolved_mode,
            output_detail_level=resolved_output_detail_level,
            verification_min_search_rounds=resolved_min_rounds,
        )
        logger.info(
            "Loading pipeline components | mode=%s | search_profile=%s | result_num=%s | min_rounds=%s | detail_level=%s | provider_order=%s | provider_mode=%s",
            resolved_mode,
            search_profile,
            resolved_result_num,
            resolved_min_rounds,
            resolved_output_detail_level,
            search_env.get("SEARCH_PROVIDER_ORDER", ""),
            search_env.get("SEARCH_PROVIDER_MODE", ""),
        )
        with _component_env_lock:
            with _temporary_env_vars(search_env):
                cfg = load_miroflow_config(mode_overrides)
                (
                    preload_main_tool_manager,
                    preload_sub_tool_managers,
                    _,
                ) = create_pipeline_components(cfg)

        (
            tool_definitions,
            sub_agent_tool_definitions,
        ) = asyncio.run(
            _discover_preload_tool_definitions(
                preload_main_tool_manager,
                preload_sub_tool_managers,
            )
        )
        if cfg.agent.sub_agents:
            tool_definitions += expose_sub_agents_as_tools(cfg.agent.sub_agents)

        cache_entry = {
            "cfg": cfg,
            "tool_definitions": tool_definitions,
            "sub_agent_tool_definitions": sub_agent_tool_definitions,
            "search_profile": search_profile,
            "search_result_num": resolved_result_num,
            "verification_min_search_rounds": resolved_min_rounds,
            "output_detail_level": resolved_output_detail_level,
        }
    except BaseException as exc:
        with _preload_cache_lock:
            preload_state["error"] = exc
            if _preload_inflight.get(cache_key) is preload_state:
                _preload_inflight.pop(cache_key, None)
            preload_state["event"].set()
        raise

    with _preload_cache_lock:
        _preload_cache[cache_key] = cache_entry
        if _preload_inflight.get(cache_key) is preload_state:
            _preload_inflight.pop(cache_key, None)
        preload_state["event"].set()

    logger.info(
        "Pipeline static resources loaded successfully | mode=%s | search_profile=%s | result_num=%s | min_rounds=%s",
        resolved_mode,
        search_profile,
        resolved_result_num,
        resolved_min_rounds,
    )
    preload_info = _preload_info(
        cache_key,
        cache_hit=False,
        started_at=preload_start_time,
    )
    if ENABLE_TIMING_DIAGNOSTICS:
        logger.info(
            "Pipeline preload ready | cache_key=%s | cache_hit=%s | duration_ms=%s",
            cache_key,
            False,
            preload_info["duration_ms"],
        )
    return preload_info


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
    resolved_verification_min_rounds = (
        _resolve_effective_verification_min_search_rounds(
            resolved_mode,
            verification_min_search_rounds,
        )
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
    stage_state: Dict[str, Any] = {
        "phase": "初始化",
        "turn": 0,
        "search_round": 0,
        "agent_name": "",
        "detail": "等待开始",
        "last_tool": "",
        "updated_at": time.time(),
    }

    def _touch_stage(
        phase: Optional[str] = None,
        *,
        turn: Optional[int] = None,
        detail: Optional[str] = None,
        agent_name: Optional[str] = None,
        last_tool: Optional[str] = None,
        search_round_increment: bool = False,
    ) -> None:
        if phase:
            stage_state["phase"] = phase
        if turn is not None:
            stage_state["turn"] = max(0, int(turn))
        if detail is not None:
            stage_state["detail"] = str(detail)
        if agent_name is not None:
            stage_state["agent_name"] = str(agent_name)
        if last_tool is not None:
            stage_state["last_tool"] = str(last_tool)
        if search_round_increment:
            stage_state["search_round"] = int(stage_state.get("search_round", 0)) + 1
        stage_state["updated_at"] = time.time()

    def _update_stage_by_message(message: Dict[str, Any]) -> None:
        event_type = str(message.get("event", ""))
        data = message.get("data") or {}
        if event_type == "stage_heartbeat":
            _touch_stage(
                data.get("phase"),
                turn=data.get("turn"),
                detail=data.get("detail"),
                agent_name=data.get("agent_name"),
            )
            if data.get("search_round") is not None:
                stage_state["search_round"] = int(data.get("search_round") or 0)
            return
        if event_type == "start_of_agent":
            current_agent = str(data.get("agent_name") or "")
            phase = "总结" if current_agent == "Final Summary" else "推理"
            _touch_stage(
                phase,
                detail=f"{current_agent or 'Agent'} 已启动",
                agent_name=current_agent,
            )
            return
        if event_type == "start_of_llm":
            current_agent = str(data.get("agent_name") or stage_state.get("agent_name"))
            phase = "总结" if current_agent == "Final Summary" else "推理"
            _touch_stage(phase, detail="模型推理中", agent_name=current_agent)
            return
        if event_type == "tool_call":
            tool_name = str(data.get("tool_name") or "")
            if not tool_name:
                return
            tool_payload = data.get("tool_input")
            is_search_output = bool(
                isinstance(tool_payload, dict)
                and "result" in tool_payload
                and tool_name in {"google_search", "sogou_search"}
            )
            phase = "检索" if tool_name in SEARCH_STAGE_TOOL_NAMES else "工具调用"
            _touch_stage(
                phase,
                detail=f"{_tool_display_name(tool_name)} 执行中",
                last_tool=tool_name,
                search_round_increment=is_search_output,
            )
            return
        if event_type == "error":
            _touch_stage("异常", detail="执行出现错误")
            return
        if event_type == "done":
            status = str(data.get("status") or "")
            if status == "cancelled":
                _touch_stage("已取消", detail="任务已取消")
            elif status == "failed":
                _touch_stage("异常", detail="任务执行失败")
            elif status in {"completed", "cached"}:
                _touch_stage("完成", detail="任务已完成")

    def run_pipeline_in_thread():
        def publish_event(event: str, data: Dict[str, Any]) -> None:
            stream_queue.put_nowait_threadsafe(
                {
                    "event": event,
                    "data": {
                        "workflow_id": workflow_id,
                        **data,
                    },
                }
            )

        def publish_pipeline_result(pipeline_result: Any) -> None:
            if not isinstance(pipeline_result, dict):
                raise RuntimeError("Pipeline returned a non-mapping result.")
            status = str(pipeline_result.get("status") or "").strip().lower()
            error = str(
                pipeline_result.get("error")
                or pipeline_result.get("final_summary")
                or ""
            ).strip()
            if status == "failed":
                resolved_error = error or "Pipeline execution failed."
                publish_event("error", {"error": resolved_error})
                publish_event(
                    "done",
                    {
                        "status": "failed",
                        "error": resolved_error,
                    },
                )
                return
            if status == "cancelled":
                done_data = {"status": "cancelled"}
                if error:
                    done_data["detail"] = error
                publish_event("done", done_data)
                return
            if status == "completed":
                publish_event("done", {"status": "completed"})
                return
            raise RuntimeError(f"Unknown pipeline status: {status or '<empty>'}")

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
            (
                main_agent_tool_manager,
                sub_agent_tool_managers,
                output_formatter,
            ) = _create_task_runtime_components(profile_cache)

            async def pipeline_with_cancellation():
                pipeline_task = asyncio.create_task(
                    execute_task_pipeline(
                        cfg=profile_cache["cfg"],
                        task_id=workflow_id,
                        task_description=query,
                        task_file_name=None,
                        main_agent_tool_manager=main_agent_tool_manager,
                        sub_agent_tool_managers=sub_agent_tool_managers,
                        output_formatter=output_formatter,
                        stream_queue=wrapper_queue,
                        log_dir=os.getenv("LOG_DIR", "logs/api-server"),
                        tool_definitions=profile_cache["tool_definitions"],
                        sub_agent_tool_definitions=profile_cache[
                            "sub_agent_tool_definitions"
                        ],
                    )
                )

                async def check_cancellation():
                    while not cancel_event.is_set():
                        await asyncio.sleep(PIPELINE_CANCEL_POLL_INTERVAL_SECONDS)

                cancel_task = asyncio.create_task(check_cancellation())

                try:
                    done, _ = await asyncio.wait(
                        [pipeline_task, cancel_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if cancel_task in done and not pipeline_task.done():
                        logger.info("Cancel event detected, cancelling pipeline")
                        pipeline_task.cancel()
                    try:
                        return await pipeline_task
                    except asyncio.CancelledError:
                        logger.info("Pipeline task was cancelled")
                        return {
                            "status": "cancelled",
                            "error": "Pipeline task was cancelled.",
                        }
                finally:
                    cancel_task.cancel()
                    await asyncio.gather(cancel_task, return_exceptions=True)

            pipeline_result = loop.run_until_complete(pipeline_with_cancellation())
            publish_pipeline_result(pipeline_result)
        except Exception as e:
            if not cancel_event.is_set():
                logger.error(f"Pipeline error: {e}", exc_info=True)
                publish_event("error", {"error": str(e)})
                publish_event(
                    "done",
                    {
                        "status": "failed",
                        "error": str(e),
                    },
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
                _update_stage_by_message(message)
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
                        "data": {
                            "timestamp": current_time,
                            "workflow_id": workflow_id,
                            "stage": dict(stage_state),
                        },
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
        # concurrent.futures.Future.result() 会阻塞当前 Gradio 事件循环；
        # 包装为 asyncio Future 后等待，既保证线程完成清理，也不冻结其他请求。
        wrapped_future = asyncio.wrap_future(future)
        cancellation_received = False
        while True:
            try:
                await asyncio.shield(wrapped_future)
                break
            except asyncio.CancelledError:
                cancellation_received = True
                if wrapped_future.done():
                    break
            except Exception:
                break
        executor.shutdown(wait=False)
        if ENABLE_TIMING_DIAGNOSTICS:
            logger.info(
                "Task stream finished | workflow_id=%s | total_ms=%s | event_counts=%s",
                workflow_id,
                int((time.perf_counter() - stream_start_time) * 1000),
                json.dumps(event_counts, ensure_ascii=False, sort_keys=True),
            )
        if cancellation_received:
            raise asyncio.CancelledError


# ========================= Gradio Integration =========================


def _init_render_state():
    return {
        "agent_order": [],
        "agents": {},  # agent_id -> {"agent_name": str, "tool_call_order": [], "tools": {tool_call_id: {...}}}
        "current_agent_id": None,
        "errors": [],
        "runtime_stage": {
            "phase": "初始化",
            "turn": 0,
            "search_round": 0,
            "detail": "等待开始",
            "agent_name": "",
            "last_tool": "",
            "updated_at": 0.0,
        },
    }


def _format_runtime_status_label(
    state: dict, heartbeat_ts: Optional[float] = None
) -> str:
    runtime_stage = state.get("runtime_stage") or {}
    phase = str(runtime_stage.get("phase") or "执行中")
    turn = int(runtime_stage.get("turn") or 0)
    search_round = int(runtime_stage.get("search_round") or 0)
    detail = str(runtime_stage.get("detail") or "").strip()
    parts = [f"生成中 · 阶段:{phase}"]
    if turn > 0:
        parts.append(f"回合:{turn}")
    if search_round > 0:
        parts.append(f"检索轮次:{search_round}")
    if heartbeat_ts:
        parts.append(
            f"最近心跳 {time.strftime('%H:%M:%S', time.localtime(float(heartbeat_ts)))}"
        )
    label = " | ".join(parts)
    if detail:
        label = f"{label} | {detail}"
    return label


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
    search_success = True
    search_error = ""
    if isinstance(tool_output, dict):
        # Case 1: output has "result" field containing JSON string
        result_str = tool_output.get("result", "")
        if isinstance(result_str, str) and result_str.strip():
            try:
                result_data = json.loads(result_str)
                if isinstance(result_data, dict):
                    search_success = bool(result_data.get("success", True))
                    search_error = str(result_data.get("error", "") or "").strip()
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
                        raw_route_trace = result_data.get(
                            "route_trace"
                        ) or search_params.get("route_trace")
                        if isinstance(raw_route_trace, list):
                            route_trace = [
                                item
                                for item in raw_route_trace
                                if isinstance(item, dict)
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
            safe_display_limit = max(
                1, min(SEARCH_RESULT_DISPLAY_MAX, int(display_limit))
            )
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
    elif not search_success:
        lines.append(
            f'<div class="search-count">⚠️ 检索失败: <strong>{search_error or "搜索源未返回有效结果"}</strong></div>'
        )
        if fallback_errors:
            lines.append(
                f'<div class="search-count">搜索源异常: {"; ".join(fallback_errors[:3])}</div>'
            )
        if route_trace:
            route_items = []
            for item in route_trace[:8]:
                phase = item.get("phase", "")
                provider = item.get("provider", "")
                status = item.get("status", "")
                route_items.append(f"{phase}:{provider}:{status}")
            if route_items:
                lines.append(
                    f'<div class="search-count">链路跟踪: {" | ".join(route_items)}</div>'
                )

    lines.append("</div>")

    return "\n".join(lines)


def _truncate_single_line(raw_value: str, max_chars: int) -> str:
    normalized_value = " ".join(str(raw_value or "").split()).strip()
    if not normalized_value:
        return ""
    if len(normalized_value) <= max_chars:
        return normalized_value
    return normalized_value[: max_chars - 3] + "..."


def _extract_google_search_step_summary(tool_input: dict, tool_output: dict) -> str:
    query = ""
    if isinstance(tool_input, dict):
        query = str(tool_input.get("q", "") or tool_input.get("query", "")).strip()

    result_count = None
    provider_mode = ""
    providers_with_results: List[str] = []

    def _extract_result_data(output_payload: dict) -> Dict[str, Any]:
        if not isinstance(output_payload, dict):
            return {}
        result_payload = output_payload.get("result", "")
        if isinstance(result_payload, str) and result_payload.strip():
            try:
                parsed_payload = json.loads(result_payload)
                if isinstance(parsed_payload, dict):
                    return parsed_payload
            except json.JSONDecodeError:
                return {}
        if isinstance(result_payload, dict):
            return result_payload
        if isinstance(output_payload.get("organic"), list):
            return output_payload
        return {}

    result_data = _extract_result_data(
        tool_output if isinstance(tool_output, dict) else {}
    )
    organic_results = result_data.get("organic", [])
    if isinstance(organic_results, list):
        result_count = len(organic_results)
    search_params = result_data.get("searchParameters", {})
    if isinstance(search_params, dict):
        provider_mode = str(search_params.get("provider_mode", "")).strip()
        providers_with_results = [
            str(item).strip()
            for item in search_params.get("providers_with_results", [])
            if str(item).strip()
        ]

    if (
        not query
        and result_count is None
        and not provider_mode
        and not providers_with_results
    ):
        return ""

    line_parts: List[str] = []
    if query:
        truncated_query = _truncate_single_line(query, SEARCH_STEP_QUERY_PREVIEW_CHARS)
        line_parts.append(f'Search: "{html.escape(truncated_query)}"')
    if result_count is not None:
        line_parts.append(f"Found {result_count} results")
    if provider_mode:
        line_parts.append(f"检索模式: {html.escape(provider_mode)}")
    if providers_with_results:
        provider_text = ",".join(providers_with_results)
        provider_text = _truncate_single_line(
            provider_text, SEARCH_STEP_SOURCE_PREVIEW_CHARS
        )
        line_parts.append(f"命中源: {html.escape(provider_text)}")
    if not line_parts:
        return ""
    return f"🔍 {' | '.join(line_parts)}"


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


def _extract_sogou_search_step_summary(tool_input: dict, tool_output: dict) -> str:
    query = ""
    if isinstance(tool_input, dict):
        query = str(tool_input.get("q", "") or tool_input.get("query", "")).strip()

    result_count = None
    if isinstance(tool_output, dict):
        result_payload = tool_output.get("result", "")
        pages = []
        if isinstance(result_payload, str) and result_payload.strip():
            try:
                parsed_payload = json.loads(result_payload)
                if isinstance(parsed_payload, dict):
                    pages = parsed_payload.get("Pages", [])
            except json.JSONDecodeError:
                pages = []
        elif isinstance(result_payload, dict):
            pages = result_payload.get("Pages", [])
        elif isinstance(tool_output.get("Pages"), list):
            pages = tool_output.get("Pages", [])
        if isinstance(pages, list):
            result_count = len(pages)

    if not query and result_count is None:
        return ""

    line_parts: List[str] = []
    if query:
        truncated_query = _truncate_single_line(query, SEARCH_STEP_QUERY_PREVIEW_CHARS)
        line_parts.append(f'Search: "{html.escape(truncated_query)}"')
    if result_count is not None:
        line_parts.append(f"Found {result_count} results")
    return f"🔍 {' | '.join(line_parts)}" if line_parts else ""


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
    unique_blocks = _deduplicate_non_empty_blocks(
        [_strip_diagnostic_markers(block) for block in final_summary_blocks]
    )
    if not unique_blocks:
        return []
    resolved_merge_strategy = _normalize_final_summary_merge_strategy(merge_strategy)
    if resolved_merge_strategy == "all_unique":
        return unique_blocks
    return [unique_blocks[-1]]


_REFERENCES_HEADING_RE = re.compile(
    r"(?im)^[ \t]*(?:#{1,6}[ \t]+)?(?:\*+[ \t]*)?"
    r"(?:参考文献|参考资料|引用|references?|sources?)"
    r"(?:[ \t]*\*+)?[ \t]*$"
)
_REFERENCE_ENTRY_RE = re.compile(r"\[(\d{1,4})\][^\n]*?(https?://\S+)")
# 规范化参考文献条目之间的换行：确保每条 [N] 前有双换行，Markdown 渲染时才能正确分行
_REFERENCE_NEWLINE_RE = re.compile(r"(?<!\n)\n(\[\d{1,4}\])")
_CITATION_RE = re.compile(r"\[(\d{1,4})\]")
_CODE_SEGMENT_RE = re.compile(r"```[\s\S]*?```|`[^`\n]+`")
_REFERENCE_URL_TRAILING = ".,;:)]>。，、；：）】》」’”"


def _linkify_reference_citations(markdown_text: str) -> str:
    """将研究总结中形如 ``[N]`` 的引用标记替换为指向文末 References 区真实 URL 的可点击链接。"""
    if not markdown_text:
        return markdown_text
    heading_match = _REFERENCES_HEADING_RE.search(markdown_text)
    if not heading_match:
        return markdown_text

    body = markdown_text[: heading_match.start()]
    references_section = markdown_text[heading_match.start() :]

    id_to_url: Dict[str, str] = {}
    for entry in _REFERENCE_ENTRY_RE.finditer(references_section):
        ref_id = entry.group(1)
        raw_url = entry.group(2).rstrip(_REFERENCE_URL_TRAILING)
        if ref_id and raw_url and ref_id not in id_to_url:
            id_to_url[ref_id] = raw_url

    if not id_to_url:
        return markdown_text

    def _replace_citation(match: "re.Match[str]") -> str:
        ref_id = match.group(1)
        url = id_to_url.get(ref_id)
        if not url:
            return match.group(0)
        href = html.escape(url, quote=True)
        return (
            f'<a href="{href}" target="_blank" rel="noopener noreferrer" '
            f'class="ref-citation">[{ref_id}]</a>'
        )

    pieces: List[str] = []
    cursor = 0
    for code_match in _CODE_SEGMENT_RE.finditer(body):
        start, end = code_match.span()
        pieces.append(_CITATION_RE.sub(_replace_citation, body[cursor:start]))
        pieces.append(body[start:end])
        cursor = end
    pieces.append(_CITATION_RE.sub(_replace_citation, body[cursor:]))

    # 规范化参考文献条目之间的换行，确保 Markdown 渲染时每条 [N] 独占一行
    references_section = _REFERENCE_NEWLINE_RE.sub(r"\n\n\1", references_section)
    return "".join(pieces) + references_section


FORMAT_ERROR_MARKERS = (
    "No \\boxed{} content found in the final answer.",
    "No \\boxed{} content found.",
    "Task incomplete - reached maximum turns",
)


# LaTeX → Markdown 规范化：部分 LLM 会把整份报告塞进 \boxed{...} 或直接混用
# \textbf{}、\section*{}、\begin{itemize}\item 等 LaTeX 控制命令。Gradio 默认
# Markdown 组件只识别少量 KaTeX 分隔符，不会解析这些命令，会把它们作为字面文本
# 显示。此处在下游渲染链路中对常见命令做一次保守转换，避免 UI 出现成片未渲染的
# LaTeX 控制序列（最终答案展示场景）。
#
# 保守原则：
#  1. 只处理显式的 LaTeX 命令（`\cmd{...}` 或 `\begin{env}`/`\end{env}`）以及
#     几个 LaTeX 特有的字符转义（\%、\$、\&、\#）；不触碰 \_、\*、\{、\} 等
#     可能是 Markdown 本身的转义写法，保持对正常 Markdown 的兼容。
#  2. 花括号匹配支持嵌套，并跳过 `\{`/`\}` 转义。
#  3. 规范化只作为展示层兜底，不影响上游保存的原始 `final_answer_text`/
#     `final_boxed_answer`。
_LATEX_CMD_OPEN_RE_CACHE: Dict[str, "re.Pattern[str]"] = {}

_LATEX_STRIP_ENV_NAMES = (
    "itemize",
    "enumerate",
    "description",
    "center",
    "flushleft",
    "flushright",
)

_LATEX_SECTION_REPLACEMENTS = (
    ("subsubsection*", "\n\n#### {inner}\n\n"),
    ("subsubsection", "\n\n#### {inner}\n\n"),
    ("subsection*", "\n\n### {inner}\n\n"),
    ("subsection", "\n\n### {inner}\n\n"),
    ("section*", "\n\n## {inner}\n\n"),
    ("section", "\n\n## {inner}\n\n"),
    ("paragraph", "\n\n**{inner}**\n\n"),
)

_LATEX_INLINE_REPLACEMENTS = (
    ("textbf", "**{inner}**"),
    ("mathbf", "**{inner}**"),
    ("textit", "*{inner}*"),
    ("mathit", "*{inner}*"),
    ("emph", "*{inner}*"),
    ("underline", "<u>{inner}</u>"),
    ("texttt", "`{inner}`"),
    ("boxed", "**{inner}**"),
)

_LATEX_ESCAPE_PAIRS = (
    ("\\%", "%"),
    ("\\$", "$"),
    ("\\&", "&"),
    ("\\#", "#"),
)


def _latex_cmd_open_pattern(cmd: str) -> "re.Pattern[str]":
    """构造 ``\\cmd{`` 开头的匹配正则，结果带缓存。"""
    cached = _LATEX_CMD_OPEN_RE_CACHE.get(cmd)
    if cached is not None:
        return cached
    pattern = re.compile(r"\\" + re.escape(cmd) + r"\s*\{")
    _LATEX_CMD_OPEN_RE_CACHE[cmd] = pattern
    return pattern


def _replace_latex_cmd(text: str, cmd: str, replacement_fmt: str) -> str:
    """
    将 ``\\cmd{X}`` 形式的 LaTeX 命令替换为 ``replacement_fmt.format(inner=X)``，
    支持嵌套花括号与 ``\\{``/``\\}`` 转义。未闭合的 ``\\cmd{`` 会原样保留。
    """
    if not text or "\\" not in text or cmd not in text:
        return text
    pattern = _latex_cmd_open_pattern(cmd)
    out: List[str] = []
    cursor = 0
    n = len(text)
    while cursor < n:
        match = pattern.search(text, cursor)
        if not match:
            out.append(text[cursor:])
            break
        out.append(text[cursor : match.start()])
        content_start = match.end()
        depth = 1
        j = content_start
        while j < n and depth > 0:
            ch = text[j]
            if ch == "\\" and j + 1 < n:
                # 跳过 \{、\} 等转义序列，避免干扰花括号配平
                j += 2
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    inner = text[content_start:j]
                    if inner.strip() == "":
                        # 空命令（如 \boxed{}）原样保留，避免干扰后续字面匹配
                        # （例如 fallback 文案依赖 "No \\boxed{} content found" 这一 marker）
                        out.append(text[match.start() : j + 1])
                    else:
                        out.append(replacement_fmt.format(inner=inner))
                    cursor = j + 1
                    break
            j += 1
        else:
            # 未找到匹配的右花括号，保留原文（从本次命令起点往后），终止循环
            out.append(text[match.start() :])
            cursor = n
            break
    return "".join(out)


_OUTER_BOXED_OPEN_RE = re.compile(r"^\s*\\boxed\s*\{", re.DOTALL)


def _unwrap_outer_boxed(text: str) -> str:
    """当整段文本恰好被单个最外层 ``\\boxed{...}`` 包围时剥掉该层；否则原样返回。"""
    if not text:
        return text
    match = _OUTER_BOXED_OPEN_RE.match(text)
    if not match:
        return text
    depth = 1
    j = match.end()
    n = len(text)
    while j < n:
        ch = text[j]
        if ch == "\\" and j + 1 < n:
            j += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                if text[j + 1 :].strip() == "":
                    return text[match.end() : j].strip()
                return text
        j += 1
    return text


def _strip_latex_environments(text: str) -> str:
    """移除 ``\\begin{env}``/``\\end{env}``（保留内部内容），并用空行分隔便于 Markdown 列表识别。"""
    if not text or "\\begin" not in text and "\\end" not in text:
        return text
    for env in _LATEX_STRIP_ENV_NAMES:
        begin_pat = re.compile(r"\\begin\s*\{\s*" + re.escape(env) + r"\*?\s*\}\s*")
        end_pat = re.compile(r"\\end\s*\{\s*" + re.escape(env) + r"\*?\s*\}\s*")
        text = begin_pat.sub("\n\n", text)
        text = end_pat.sub("\n\n", text)
    return text


def _normalize_latex_like_markup(text: str) -> str:
    """
    将常见 LaTeX 控制序列转换为 Markdown 等价物，兜底渲染层。

    处理范围：
      - 整段被 ``\\boxed{...}`` 包围时剥掉外层；
      - ``\\section*{}``/``\\subsection*{}``/``\\paragraph{}`` 等标题 → ``##``/``###``/``**..**``；
      - ``\\textbf{}``/``\\emph{}``/``\\texttt{}``/``\\boxed{}`` 等行内命令 → Markdown 等价；
      - ``\\begin{itemize|enumerate|description|center|flushleft|flushright}`` 环境 → 空行；
      - ``\\item`` → ``\\n- ``；
      - ``\\\\`` 行末换行 → Markdown 硬换行；
      - LaTeX 特有转义 ``\\%``/``\\$``/``\\&``/``\\#`` → 字面字符。

    若文本不包含任何反斜杠，直接返回，保证正常 Markdown 不受影响。
    """
    if not text or "\\" not in text:
        return text
    t = _unwrap_outer_boxed(text)
    t = _strip_latex_environments(t)
    for cmd, fmt in _LATEX_SECTION_REPLACEMENTS:
        t = _replace_latex_cmd(t, cmd, fmt)
    for cmd, fmt in _LATEX_INLINE_REPLACEMENTS:
        t = _replace_latex_cmd(t, cmd, fmt)
    # \item 转换为 Markdown 列表项；配合上面的环境替换，列表前会有空行
    t = re.sub(r"\\item\b[ \t]*", "\n- ", t)
    # LaTeX 换行命令 \\（行末）→ Markdown 硬换行（两个空格 + 换行）
    t = re.sub(r"\\\\(?=\s)", "  \n", t)
    # LaTeX 特有字符转义
    for src, dst in _LATEX_ESCAPE_PAIRS:
        t = t.replace(src, dst)
    # 合并多余空行
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _humanize_pipeline_fallback(text: str) -> str:
    """探测 pipeline 在未收敛时的兜底文案，重写为对用户友好的中文提示。

    pipeline 在 LLM 未输出 \\boxed{} 或达到最大轮次时，会用一句固定字符串占位。
    直接展示该字符串容易让用户误以为 demo 出错。这里集中重写，避免散落补丁。
    """
    stripped = (text or "").strip()
    if not stripped:
        return text
    for marker in FORMAT_ERROR_MARKERS:
        if marker in stripped:
            return (
                "> 本轮检索未能在限定回合内收敛出可信结论（模型未输出 `\\boxed{}` 或达到最大轮次）。\n"
                "> 建议操作：稍后重试一次；若仍未收敛，可降级 `mode`（如 `verified` → `balanced`）"
                "或换用更明确的提问表达。"
            )
    return text


# 用于剥离 miroflow-agent OutputFormatter 注入的诊断分段标记。
# 这些标记面向 CLI / 评测日志使用，前端展示时属于噪声，必须在 UI 渲染前剔除：
#   - "========== Final Answer ==========" 顶部分段头
#   - "-------- Extracted Result --------" 之后的重复内容
#   - "-------- Token Usage --------" 之后的统计与 Pricing 提示
_DIAGNOSTIC_TRUNCATE_RE = re.compile(
    r"\n?-{5,}\s*(?:Extracted Result|Token Usage(?:\s*&\s*Cost)?)\s*-{5,}.*\Z",
    re.DOTALL,
)
_DIAGNOSTIC_FINAL_ANSWER_HEADER_RE = re.compile(
    r"^\s*={5,}\s*Final Answer\s*={5,}\s*\n?",
)


def _strip_diagnostic_markers(text: str) -> str:
    """剥离最终总结里来自 OutputFormatter 的调试分段（Final Answer / Extracted / Token Usage）。"""
    if not text:
        return text
    cleaned = _DIAGNOSTIC_FINAL_ANSWER_HEADER_RE.sub("", text, count=1)
    cleaned = _DIAGNOSTIC_TRUNCATE_RE.sub("", cleaned)
    return cleaned.rstrip()


def _build_summary_section(final_summary_blocks: List[str]) -> List[str]:
    if not final_summary_blocks:
        return []
    # 前置空字符串项：确保和上一个 HTML block（如 search-step-board 的 </div>）之间
    # 有一个空行，否则 CommonMark 会把 `## 📋 研究总结` 视为 HTML block 的延续，
    # 导致 `## ` 字面显示而非作为标题渲染。
    lines = ["", "## 📋 研究总结\n\n"]
    # 先剥离 OutputFormatter 注入的调试分段标记，再做 LaTeX → Markdown 规范化
    sanitized = (_strip_diagnostic_markers(block) for block in final_summary_blocks)
    normalized = (_normalize_latex_like_markup(block) for block in sanitized)
    rewritten = (_humanize_pipeline_fallback(block) for block in normalized)
    lines.extend(_linkify_reference_citations(block) for block in rewritten)
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


def _build_search_steps_section(search_step_lines: List[str]) -> List[str]:
    if not search_step_lines:
        return []
    lines = ['<div class="search-step-board">']
    for step_line in search_step_lines:
        normalized_line = str(step_line or "").strip()
        if not normalized_line:
            continue
        lines.append(f'<div class="search-step-item">{normalized_line}</div>')
    lines.append("</div>")
    return lines


def _normalize_export_format(export_format: Optional[str]) -> str:
    normalized_format = str(export_format or "md").strip().lower()
    if normalized_format in EXPORT_FORMAT_EXTENSIONS:
        return normalized_format
    return "md"


def _sanitize_export_task_id(task_id: Optional[str]) -> str:
    normalized_task_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(task_id or "").strip())
    return normalized_task_id.strip("-")[:48]


def _build_export_filename(export_format: str, task_id: Optional[str] = None) -> str:
    extension = EXPORT_FORMAT_EXTENSIONS[_normalize_export_format(export_format)]
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    unique_suffix = uuid.uuid4().hex[:8]
    safe_task_id = _sanitize_export_task_id(task_id)
    suffix = f"-{safe_task_id}" if safe_task_id else ""
    return f"{EXPORT_FILENAME_PREFIX}{suffix}-{timestamp}-{unique_suffix}{extension}"


def _prepare_export_markdown(markdown_text: str) -> str:
    normalized_markdown = _strip_diagnostic_markers(str(markdown_text or "")).strip()
    if not normalized_markdown:
        normalized_markdown = "当前没有可导出的研究结论。"
    return normalized_markdown + "\n"


def _wrap_export_text(text: str) -> List[str]:
    wrapped_lines: List[str] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.rstrip()
        if not line:
            wrapped_lines.append("")
            continue
        while len(line) > EXPORT_PDF_LINE_CHARS:
            wrapped_lines.append(line[:EXPORT_PDF_LINE_CHARS])
            line = line[EXPORT_PDF_LINE_CHARS:]
        wrapped_lines.append(line)
    return wrapped_lines or [""]


def _pdf_hex_text(text: str) -> str:
    safe_text = "".join(char if ord(char) <= 0xFFFF else "□" for char in text)
    return safe_text.encode("utf-16-be", errors="replace").hex().upper()


def _build_pdf_content_stream(lines: List[str]) -> bytes:
    stream_lines = [
        "BT",
        f"/F1 {EXPORT_PDF_FONT_SIZE} Tf",
        f"{EXPORT_PDF_MARGIN_LEFT} {EXPORT_PDF_MARGIN_TOP} Td",
        f"{EXPORT_PDF_LINE_HEIGHT} TL",
    ]
    for line in lines:
        if line:
            stream_lines.append(f"<{_pdf_hex_text(line)}> Tj")
        stream_lines.append("T*")
    stream_lines.append("ET")
    return "\n".join(stream_lines).encode("ascii")


def _build_pdf_bytes(markdown_text: str) -> bytes:
    wrapped_lines = _wrap_export_text(markdown_text)
    page_chunks = [
        wrapped_lines[index : index + EXPORT_PDF_LINES_PER_PAGE]
        for index in range(0, len(wrapped_lines), EXPORT_PDF_LINES_PER_PAGE)
    ] or [[""]]
    page_count = len(page_chunks)
    font_object_id = 3 + page_count * 2
    cid_font_object_id = font_object_id + 1
    objects: Dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: (
            "<< /Type /Pages /Kids ["
            + " ".join(f"{3 + index * 2} 0 R" for index in range(page_count))
            + f"] /Count {page_count} >>"
        ).encode("ascii"),
        font_object_id: (
            f"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light "
            f"/Encoding /UniGB-UCS2-H /DescendantFonts [{cid_font_object_id} 0 R] >>"
        ).encode("ascii"),
        cid_font_object_id: (
            "<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light "
            "/CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 2 >> "
            "/DW 1000 >>"
        ).encode("ascii"),
    }
    for index, page_lines in enumerate(page_chunks):
        page_object_id = 3 + index * 2
        content_object_id = page_object_id + 1
        content_stream = _build_pdf_content_stream(page_lines)
        objects[page_object_id] = (
            f"<< /Type /Page /Parent 2 0 R "
            f"/MediaBox [0 0 {EXPORT_PDF_PAGE_WIDTH} {EXPORT_PDF_PAGE_HEIGHT}] "
            f"/Resources << /Font << /F1 {font_object_id} 0 R >> >> "
            f"/Contents {content_object_id} 0 R >>"
        ).encode("ascii")
        objects[content_object_id] = (
            f"<< /Length {len(content_stream)} >>\nstream\n".encode("ascii")
            + content_stream
            + b"\nendstream"
        )

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0] * (max(objects) + 1)
    for object_id in sorted(objects):
        offsets[object_id] = len(pdf)
        pdf.extend(f"{object_id} 0 obj\n".encode("ascii"))
        pdf.extend(objects[object_id])
        pdf.extend(b"\nendobj\n")
    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for object_id in range(1, len(offsets)):
        pdf.extend(f"{offsets[object_id]:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        (
            f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(pdf)


def _build_docx_bytes(markdown_text: str) -> bytes:
    paragraphs = []
    for line in str(markdown_text or "").splitlines():
        escaped_line = html.escape(line, quote=False)
        paragraphs.append(f"<w:p><w:r><w:t>{escaped_line}</w:t></w:r></w:p>")
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>" + "".join(paragraphs) + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>'
        "</w:sectPr></w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/>'
            "</Relationships>",
        )
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def _create_export_file(
    markdown_text: str,
    export_format: Optional[str],
    output_dir: Optional[Path] = None,
    task_id: Optional[str] = None,
) -> str:
    resolved_format = _normalize_export_format(export_format)
    resolved_output_dir = (
        Path(output_dir) if output_dir is not None else EXPORT_OUTPUT_DIR
    )
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    output_path = resolved_output_dir / _build_export_filename(resolved_format, task_id)
    export_markdown = _prepare_export_markdown(markdown_text)
    if resolved_format == "md":
        output_path.write_text(export_markdown, encoding="utf-8")
    elif resolved_format == "pdf":
        output_path.write_bytes(_build_pdf_bytes(export_markdown))
    else:
        output_path.write_bytes(_build_docx_bytes(export_markdown))
    return output_path.as_posix()


def _export_conclusion(
    markdown_text: str,
    export_format: Optional[str],
    ui_state: Optional[dict],
):
    task_id = ""
    if isinstance(ui_state, dict):
        task_id = str(ui_state.get("task_id") or "")
    try:
        export_path = _create_export_file(markdown_text, export_format, task_id=task_id)
    except OSError as exc:
        logger.warning("导出结论文件失败: %s", exc)
        return gr.update(value=None, visible=False, label=f"导出失败: {exc}")
    return gr.update(value=export_path, visible=True)


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
    search_step_lines = []
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
                        display_name = agent_name
                        if display_name == "Main Agent":
                            display_name = "主智能体 (Main Agent)"
                        elif display_name == "Sub Agent":
                            display_name = "子智能体 (Sub Agent)"
                        elif "Search" in display_name:
                            display_name = "检索智能体 (Search Agent)"

                        formatted_thought = (
                            f'<details class="thought-card" open>\n'
                            f"  <summary>💭 {display_name} 思考与规划</summary>\n"
                            f'  <div class="thought-content">\n\n{content}\n\n</div>\n'
                            f"</details>\n"
                        )
                        process_lines.append(formatted_thought)
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
                search_step = _extract_google_search_step_summary(
                    tool_input, tool_output
                )
                if search_step:
                    search_step_lines.append(search_step)
                if formatted:
                    process_lines.append(formatted)
                continue

            # Special formatting for sogou_search
            if tool_name == "sogou_search" and (has_input or has_output):
                formatted = _format_sogou_search_results(tool_input, tool_output)
                search_step = _extract_sogou_search_step_summary(
                    tool_input, tool_output
                )
                if search_step:
                    search_step_lines.append(search_step)
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
                        f"{k}: {str(v)[:30]}..." if len(str(v)) > 30 else f"{k}: {v}"
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
    has_final_summary = bool(merged_final_summary_blocks)

    if has_final_summary and COLLAPSE_PROCESS_AFTER_SUMMARY:
        lines.extend(_build_search_steps_section(search_step_lines))
        lines.extend(_build_summary_section(merged_final_summary_blocks))
        lines.extend(_build_process_details_section(process_lines))
    elif resolved_render_mode == "full":
        lines.extend(process_lines)
        if has_final_summary:
            lines.append("\n\n---\n\n")
            lines.extend(_build_summary_section(merged_final_summary_blocks))
    elif resolved_render_mode == "summary_only":
        if has_final_summary:
            lines.extend(_build_summary_section(merged_final_summary_blocks))
        else:
            lines.extend(process_lines)
    else:
        if has_final_summary:
            lines.extend(_build_search_steps_section(search_step_lines))
            lines.extend(_build_summary_section(merged_final_summary_blocks))
            lines.extend(_build_process_details_section(process_lines))
        else:
            lines.extend(process_lines)

    if lines:
        return "\n".join(lines)

    runtime_stage = state.get("runtime_stage") or {}
    if float(runtime_stage.get("updated_at") or 0) > 0:
        status_label = _format_runtime_status_label(state)
        return (
            f"*{status_label}*\n\n"
            "> 当前任务已启动，暂时还没有可展示的正文或工具输出。"
        )

    return "*等待开始研究...*"


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
        runtime_stage = state.setdefault("runtime_stage", {})
        runtime_stage["agent_name"] = agent_name
        runtime_stage["phase"] = "总结" if agent_name == "Final Summary" else "推理"
        runtime_stage["detail"] = f"{agent_name} 已启动"
        runtime_stage["updated_at"] = time.time()
    elif event == "end_of_agent":
        # End marker, no special handling needed, keep structure
        state["current_agent_id"] = None
    elif event == "start_of_llm":
        agent_name = str((data or {}).get("agent_name") or "")
        runtime_stage = state.setdefault("runtime_stage", {})
        runtime_stage["agent_name"] = agent_name or runtime_stage.get("agent_name", "")
        runtime_stage["phase"] = "总结" if agent_name == "Final Summary" else "推理"
        runtime_stage["detail"] = "模型推理中"
        runtime_stage["updated_at"] = time.time()
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
        runtime_stage = state.setdefault("runtime_stage", {})
        runtime_stage["phase"] = (
            "检索" if tool_name in SEARCH_STAGE_TOOL_NAMES else "工具调用"
        )
        runtime_stage["last_tool"] = tool_name
        runtime_stage["detail"] = f"{_tool_display_name(tool_name)} 执行中"
        runtime_stage["updated_at"] = time.time()
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
                    if tool_name in {"google_search", "sogou_search"}:
                        runtime_stage["search_round"] = (
                            int(runtime_stage.get("search_round", 0)) + 1
                        )
                        runtime_stage["detail"] = (
                            f"{_tool_display_name(tool_name)} 已完成（第 {runtime_stage['search_round']} 轮）"
                        )
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
        runtime_stage = state.setdefault("runtime_stage", {})
        runtime_stage["phase"] = (
            "总结" if agent.get("agent_name") == "Final Summary" else "推理"
        )
        runtime_stage["detail"] = "内容生成中"
        runtime_stage["updated_at"] = time.time()
    elif event == "stage_heartbeat":
        runtime_stage = state.setdefault("runtime_stage", {})
        phase = str((data or {}).get("phase") or "").strip()
        if phase:
            runtime_stage["phase"] = phase
        if (data or {}).get("turn") is not None:
            try:
                runtime_stage["turn"] = max(0, int((data or {}).get("turn") or 0))
            except (TypeError, ValueError):
                pass
        if (data or {}).get("search_round") is not None:
            try:
                runtime_stage["search_round"] = max(
                    0, int((data or {}).get("search_round") or 0)
                )
            except (TypeError, ValueError):
                pass
        detail = str((data or {}).get("detail") or "").strip()
        if detail:
            runtime_stage["detail"] = detail
        agent_name = str((data or {}).get("agent_name") or "").strip()
        if agent_name:
            runtime_stage["agent_name"] = agent_name
        runtime_stage["updated_at"] = time.time()
    elif event == "error":
        # Collect errors, display uniformly during rendering
        err_text = data.get("error") if isinstance(data, dict) else None
        if not err_text:
            try:
                err_text = json.dumps(data, ensure_ascii=False)
            except Exception:
                err_text = str(data)
        state.setdefault("errors", []).append(err_text)
        runtime_stage = state.setdefault("runtime_stage", {})
        runtime_stage["phase"] = "异常"
        runtime_stage["detail"] = "执行出现错误"
        runtime_stage["updated_at"] = time.time()
    elif event == "done":
        status = str(data.get("status") or "") if isinstance(data, dict) else ""
        runtime_stage = state.setdefault("runtime_stage", {})
        if status == "cancelled":
            runtime_stage["phase"] = "已取消"
            runtime_stage["detail"] = "任务已取消"
        elif status == "failed":
            runtime_stage["phase"] = "异常"
            runtime_stage["detail"] = "任务执行失败"
        elif status in {"completed", "cached"}:
            runtime_stage["phase"] = "完成"
            runtime_stage["detail"] = "任务已完成"
        runtime_stage["updated_at"] = time.time()
    elif event == "final_output":
        markdown = ""
        if isinstance(data, dict):
            markdown = str(data.get("markdown") or "")
        elif isinstance(data, str):
            markdown = data
        if not markdown.strip():
            return state
        agent_id = "final-output"
        if agent_id not in state["agents"]:
            state["agents"][agent_id] = {
                "agent_name": "Final Summary",
                "tool_call_order": [],
                "tools": {},
            }
            state["agent_order"].append(agent_id)
        call_id = "final-output-message"
        agent = state["agents"][agent_id]
        if call_id not in agent["tools"]:
            agent["tools"][call_id] = {"tool_name": "message", "content": ""}
            agent["tool_call_order"].append(call_id)
        agent["tools"][call_id]["content"] = markdown
        runtime_stage = state.setdefault("runtime_stage", {})
        runtime_stage["phase"] = "完成"
        runtime_stage["detail"] = "最终结果已生成"
        runtime_stage["updated_at"] = time.time()
    else:
        if event == "heartbeat":
            runtime_stage = state.setdefault("runtime_stage", {})
            stage_payload = (data or {}).get("stage")
            if isinstance(stage_payload, dict):
                runtime_stage.update(stage_payload)
                runtime_stage["updated_at"] = time.time()

    return state


_CANCEL_FLAGS = {}
_ACTIVE_TASK_IDS: dict[str, str] = {}  # {task_id: caller_id}
_CANCEL_LOCK = threading.Lock()

# 最近一次任务的结构化运行指标，由 run_research_once 在任务结束后写入
_last_run_metrics: Optional[dict] = None
_last_run_metrics_lock = threading.Lock()

# 研究结果缓存（相同 query+mode+profile+detail_level 命中缓存）
_result_cache = ResultCache(
    max_size=_env_int_at_least("RESULT_CACHE_MAX_SIZE", 128, 1),
    ttl_seconds=_env_int_at_least("RESULT_CACHE_TTL_SECONDS", 3600, 0),
)


def _reset_cancel_flag(task_id: str):
    with _CANCEL_LOCK:
        _CANCEL_FLAGS[task_id] = False


def _register_active_task(task_id: str, caller_id: str = ""):
    with _CANCEL_LOCK:
        _ACTIVE_TASK_IDS[task_id] = caller_id
        _CANCEL_FLAGS.setdefault(task_id, False)


def _unregister_active_task(task_id: str):
    with _CANCEL_LOCK:
        _ACTIVE_TASK_IDS.pop(task_id, None)
        _CANCEL_FLAGS.pop(task_id, None)


def _get_active_task_ids(
    caller_id: Optional[str] = None,
    *,
    mark_cancelled: bool = False,
) -> List[str]:
    """原子筛选活动任务，并可在同一临界区内设置取消标记。"""
    with _CANCEL_LOCK:
        if caller_id is not None:
            task_ids = [
                tid for tid, cid in _ACTIVE_TASK_IDS.items() if cid == caller_id
            ]
        else:
            task_ids = list(_ACTIVE_TASK_IDS.keys())
        if mark_cancelled:
            for task_id in task_ids:
                _CANCEL_FLAGS[task_id] = True
        return task_ids


def _cancel_task_ids(task_ids: List[str]) -> int:
    """只标记当前仍处于活动表中的任务，避免创建孤立取消标记。"""
    with _CANCEL_LOCK:
        active_task_ids = [
            task_id for task_id in task_ids if task_id and task_id in _ACTIVE_TASK_IDS
        ]
        for task_id in active_task_ids:
            _CANCEL_FLAGS[task_id] = True
        return len(active_task_ids)


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


# ---------- API 模式（断电重连）------------------------------------------------
#
# 当 BACKEND_MODE=api 时，gradio_run 不再本地跑 pipeline，而是：
#   1) POST /v1/research 创建任务，拿到服务端 task_id
#   2) 把 task_id 写入 ui_state（前端 JS 据此把 ?task_id=xxx 同步到 URL）
#   3) 订阅 GET /v1/research/{task_id}/stream，把 SSE 事件喂给既有渲染逻辑
#
# 刷新页面后由 demo.load -> reconnect_or_init 接管：
#   - 从 URL ?task_id 取回任务，再次订阅 SSE，服务端会回放历史事件 + 实时增量。


def _build_initial_ui_state(
    *,
    task_id: Optional[str],
    mode: str,
    search_profile: str,
    search_result_num: int,
    verification_min_search_rounds: int,
    output_detail_level: str,
    render_mode: str,
    summary_merge_strategy: str,
) -> dict:
    return {
        "task_id": task_id,
        "mode": mode,
        "search_profile": search_profile,
        "search_result_num": search_result_num,
        "verification_min_search_rounds": verification_min_search_rounds,
        "render_mode": render_mode,
        "output_detail_level": output_detail_level,
        "final_summary_merge_strategy": summary_merge_strategy,
    }


def _task_id_bridge_value(state: Optional[dict]) -> str:
    if not state:
        return ""
    task_id = state.get("task_id")
    return str(task_id or "")


def _build_launch_kwargs(host: str, port: int) -> dict:
    launch_kwargs = {
        "server_name": host,
        "server_port": port,
        "prevent_thread_lock": _read_env_bool("GRADIO_PREVENT_THREAD_LOCK", False),
    }
    allowed_paths = _collect_gradio_allowed_paths()
    if allowed_paths:
        launch_kwargs["allowed_paths"] = allowed_paths
    return launch_kwargs


def _build_reconnect_initial_render_state(snapshot: dict) -> dict:
    state = _init_render_state()
    meta = snapshot.get("meta") or {}
    status = str(snapshot.get("status") or meta.get("status") or "").strip().lower()
    current_stage = str(meta.get("current_stage") or "").strip().lower()
    try:
        event_count = max(0, int(snapshot.get("event_count") or 0))
    except (TypeError, ValueError):
        event_count = 0

    runtime_stage = state.setdefault("runtime_stage", {})
    if status == "queued":
        runtime_stage["phase"] = "排队"
        runtime_stage["detail"] = "任务排队中"
    elif status == "running":
        if (
            current_stage.startswith("search")
            or "search" in current_stage
            or "检索" in current_stage
        ):
            runtime_stage["phase"] = "检索"
        elif (
            current_stage.startswith("tool")
            or "tool" in current_stage
            or "工具" in current_stage
        ):
            runtime_stage["phase"] = "工具调用"
        elif (
            current_stage.startswith("summary")
            or "summary" in current_stage
            or "总结" in current_stage
        ):
            runtime_stage["phase"] = "总结"
        else:
            runtime_stage["phase"] = "推理"
        runtime_stage["detail"] = "任务恢复中"
    else:
        return state

    has_started = bool(meta.get("started_at")) or event_count > 0 or bool(current_stage)
    if status == "running" and not has_started:
        return state

    runtime_stage["updated_at"] = float(
        meta.get("started_at") or meta.get("created_at") or time.time()
    )
    return state


async def _render_stream_via_api(
    task_id: str,
    *,
    ui_state: dict,
    resolved_ui_render_mode: str,
    resolved_summary_merge_strategy: str,
    initial_state: Optional[dict] = None,
):
    """订阅 api-server SSE 流并按既有渲染管线产出 Gradio 输出元组。

    Yields:
        (markdown, run_btn_update, stop_btn_update, ui_state)
    """
    state = initial_state or _init_render_state()
    initial_markdown = _render_markdown(
        state,
        render_mode=resolved_ui_render_mode,
        final_summary_merge_strategy=resolved_summary_merge_strategy,
    )
    yield (
        initial_markdown + _spinner_markup(True, _format_runtime_status_label(state)),
        gr.update(interactive=False),
        gr.update(interactive=True),
        ui_state,
        _task_id_bridge_value(ui_state),
    )

    # 取消检查复用本地 _CANCEL_FLAGS：stop 按钮按下后会 set 标志
    async def _cancel_check() -> bool:
        return await _disconnect_check_for_task(task_id)

    try:
        async for message in api_client.stream_task_events(
            task_id, cancel_check=_cancel_check
        ):
            event_type = message.get("event", "unknown")
            if event_type == "done":
                # 服务端终态信号：completed / cancelled / failed / cached
                done_status = (message.get("data") or {}).get("status", "completed")
                if done_status == "failed":
                    state["errors"].append("任务执行失败")
                break
            if event_type == "heartbeat":
                state = _update_state_with_event(state, message)
                heartbeat_ts = (message.get("data") or {}).get("timestamp")
                heartbeat_label = _format_runtime_status_label(state, heartbeat_ts)
                heartbeat_md = _render_markdown(
                    state,
                    render_mode=resolved_ui_render_mode,
                    final_summary_merge_strategy=resolved_summary_merge_strategy,
                )
                yield (
                    heartbeat_md + _spinner_markup(True, heartbeat_label),
                    gr.update(interactive=False),
                    gr.update(interactive=True),
                    ui_state,
                    _task_id_bridge_value(ui_state),
                )
                continue
            state = _update_state_with_event(state, message)
            md = _render_markdown(
                state,
                render_mode=resolved_ui_render_mode,
                final_summary_merge_strategy=resolved_summary_merge_strategy,
            )
            yield (
                md + _spinner_markup(True, _format_runtime_status_label(state)),
                gr.update(interactive=False),
                gr.update(interactive=True),
                ui_state,
                _task_id_bridge_value(ui_state),
            )
            await asyncio.sleep(0.01)
    except api_client.TaskNotFoundError:
        cleared_ui_state = {**ui_state, "task_id": None}
        yield (
            f"任务 `{task_id}` 不存在或已过期，请重新发起检索。",
            gr.update(interactive=True),
            gr.update(interactive=False),
            cleared_ui_state,
            _task_id_bridge_value(cleared_ui_state),
        )
        return
    except api_client.ApiClientError as exc:
        logger.error("API SSE 订阅失败: %s", exc)
        yield (
            f"连接 api-server 失败：{exc}",
            gr.update(interactive=True),
            gr.update(interactive=False),
            ui_state,
            _task_id_bridge_value(ui_state),
        )
        return
    except Exception as exc:
        logger.exception("API 流处理异常")
        yield (
            f"流处理异常：{exc}",
            gr.update(interactive=True),
            gr.update(interactive=False),
            ui_state,
            _task_id_bridge_value(ui_state),
        )
        return

    final_md = _render_markdown(
        state,
        render_mode=resolved_ui_render_mode,
        final_summary_merge_strategy=resolved_summary_merge_strategy,
    )
    yield (
        final_md,
        gr.update(interactive=True),
        gr.update(interactive=False),
        ui_state,
        _task_id_bridge_value(ui_state),
    )


async def _gradio_run_via_api(
    query: str,
    resolved_mode: str,
    resolved_search_profile: str,
    resolved_search_result_num: int,
    resolved_verification_min_rounds: int,
    resolved_output_detail_level: str,
    resolved_ui_render_mode: str,
    resolved_summary_merge_strategy: str,
    ui_state: dict,
):
    """API 后端模式下的 gradio_run 主体。"""
    try:
        created = await api_client.safe_create_task(
            query=query,
            mode=resolved_mode,
            search_profile=resolved_search_profile,
            search_result_num=resolved_search_result_num,
            verification_min_search_rounds=resolved_verification_min_rounds,
            output_detail_level=resolved_output_detail_level,
        )
    except api_client.ApiClientError as exc:
        logger.error("api-server 创建任务失败: %s", exc)
        yield (
            f"提交任务到 api-server 失败：{exc}",
            gr.update(interactive=True),
            gr.update(interactive=False),
            ui_state,
            _task_id_bridge_value(ui_state),
        )
        return

    task_id = created.get("task_id")
    if not task_id:
        yield (
            "api-server 未返回 task_id，请检查后端日志。",
            gr.update(interactive=True),
            gr.update(interactive=False),
            ui_state,
            _task_id_bridge_value(ui_state),
        )
        return

    new_ui_state = {**ui_state, "task_id": task_id}
    _reset_cancel_flag(task_id)
    _register_active_task(task_id)

    try:
        async for tup in _render_stream_via_api(
            task_id,
            ui_state=new_ui_state,
            resolved_ui_render_mode=resolved_ui_render_mode,
            resolved_summary_merge_strategy=resolved_summary_merge_strategy,
        ):
            yield tup
    finally:
        _unregister_active_task(task_id)


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
    resolved_verification_min_rounds = (
        _resolve_effective_verification_min_search_rounds(
            resolved_mode,
            verification_min_search_rounds,
        )
    )
    resolved_output_detail_level = _normalize_output_detail_level(output_detail_level)
    resolved_ui_render_mode = _normalize_render_mode(
        None,
        _get_render_mode_for_output_detail(resolved_output_detail_level),
    )
    resolved_summary_merge_strategy = _normalize_final_summary_merge_strategy(
        _get_summary_merge_for_output_detail(resolved_output_detail_level)
    )

    # ===== API 后端模式：把任务交给 api-server，刷新页面可由 task_id 重连 =====
    if api_client.is_api_mode_enabled():
        base_state = ui_state or {}
        new_ui_state = _build_initial_ui_state(
            task_id=None,  # 真正的 task_id 由 api-server 生成
            mode=resolved_mode,
            search_profile=resolved_search_profile,
            search_result_num=resolved_search_result_num,
            verification_min_search_rounds=resolved_verification_min_rounds,
            output_detail_level=resolved_output_detail_level,
            render_mode=resolved_ui_render_mode,
            summary_merge_strategy=resolved_summary_merge_strategy,
        )
        merged_state = {**base_state, **new_ui_state}
        async for tup in _gradio_run_via_api(
            query=query,
            resolved_mode=resolved_mode,
            resolved_search_profile=resolved_search_profile,
            resolved_search_result_num=resolved_search_result_num,
            resolved_verification_min_rounds=resolved_verification_min_rounds,
            resolved_output_detail_level=resolved_output_detail_level,
            resolved_ui_render_mode=resolved_ui_render_mode,
            resolved_summary_merge_strategy=resolved_summary_merge_strategy,
            ui_state=merged_state,
        ):
            yield tup
        return

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
            initial_markdown
            + _spinner_markup(True, _format_runtime_status_label(state)),
            gr.update(interactive=False),
            gr.update(interactive=True),
            ui_state,
            _task_id_bridge_value(ui_state),
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
                state = _update_state_with_event(state, message)
                heartbeat_ts = (message.get("data") or {}).get("timestamp")
                heartbeat_label = _format_runtime_status_label(state, heartbeat_ts)
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
                    _task_id_bridge_value(ui_state),
                )
                continue

            state = _update_state_with_event(state, message)
            md = _render_markdown(
                state,
                render_mode=resolved_ui_render_mode,
                final_summary_merge_strategy=resolved_summary_merge_strategy,
            )
            yield (
                md + _spinner_markup(True, _format_runtime_status_label(state)),
                gr.update(interactive=False),
                gr.update(interactive=True),
                ui_state,
                _task_id_bridge_value(ui_state),
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
            _task_id_bridge_value(ui_state),
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
    caller_id: Optional[str] = None,
) -> str:
    """统一 API：支持按请求控制检索条数，最少检索轮次仅在 verified 模式生效。"""
    query = replace_chinese_punctuation(query or "")
    resolved_caller_id = str(caller_id or "").strip()
    resolved_mode = _normalize_research_mode(mode)
    resolved_search_profile = _normalize_search_profile(search_profile)
    resolved_search_result_num = _normalize_search_result_num(search_result_num)
    resolved_verification_min_rounds = (
        _resolve_effective_verification_min_search_rounds(
            resolved_mode,
            verification_min_search_rounds,
        )
    )
    resolved_output_detail_level = _normalize_output_detail_level(output_detail_level)
    resolved_api_render_mode = _normalize_render_mode(
        render_mode,
        _get_render_mode_for_output_detail(resolved_output_detail_level),
    )
    resolved_summary_merge_strategy = _normalize_final_summary_merge_strategy(
        _get_summary_merge_for_output_detail(resolved_output_detail_level)
    )

    if api_client.is_api_mode_enabled():
        return await _run_research_once_via_api(
            query=query,
            resolved_mode=resolved_mode,
            resolved_search_profile=resolved_search_profile,
            resolved_search_result_num=resolved_search_result_num,
            resolved_verification_min_rounds=resolved_verification_min_rounds,
            resolved_output_detail_level=resolved_output_detail_level,
            resolved_api_render_mode=resolved_api_render_mode,
            resolved_summary_merge_strategy=resolved_summary_merge_strategy,
            caller_id=resolved_caller_id,
        )

    # 本地结果缓存：检索深度参数也会改变最终结论，必须参与缓存键。
    cache_key = ResultCache.make_key(
        query,
        resolved_mode,
        resolved_search_profile,
        resolved_output_detail_level,
        search_result_num=resolved_search_result_num,
        verification_min_search_rounds=resolved_verification_min_rounds,
    )
    cached = _result_cache.get(cache_key)
    if cached is not None:
        logger.info("Cache hit | key=%s | query=%s", cache_key, query[:60])
        return cached

    task_id = str(uuid.uuid4())
    _reset_cancel_flag(task_id)
    _register_active_task(task_id, caller_id=resolved_caller_id)
    state = _init_render_state()
    terminal_status = ""
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
            # 捕获 pipeline 发出的 run_metrics 事件
            if message.get("event") == "run_metrics":
                global _last_run_metrics
                with _last_run_metrics_lock:
                    _last_run_metrics = message.get("data")
                continue
            if message.get("event") == "done":
                done_data = message.get("data")
                if isinstance(done_data, dict):
                    terminal_status = str(done_data.get("status") or "").strip().lower()
            state = _update_state_with_event(state, message)
        result = _render_markdown(
            state,
            render_mode=resolved_api_render_mode,
            final_summary_merge_strategy=resolved_summary_merge_strategy,
        )
        # 只有明确成功且收到最终总结时才写缓存；失败、取消或流异常不能污染后续请求。
        if (
            terminal_status in {"completed", "cached"}
            and "final-output" in state["agents"]
            and result
            and len(result) > 100
        ):
            _result_cache.put(cache_key, result)
        return result
    finally:
        _unregister_active_task(task_id)


async def _run_research_once_via_api(
    *,
    query: str,
    resolved_mode: str,
    resolved_search_profile: str,
    resolved_search_result_num: int,
    resolved_verification_min_rounds: int,
    resolved_output_detail_level: str,
    resolved_api_render_mode: str,
    resolved_summary_merge_strategy: str,
    caller_id: str,
) -> str:
    """通过 api-server 完成非流式公开调用，不触发本地 Pipeline。"""
    try:
        created = await api_client.safe_create_task(
            query=query,
            mode=resolved_mode,
            search_profile=resolved_search_profile,
            search_result_num=resolved_search_result_num,
            verification_min_search_rounds=resolved_verification_min_rounds,
            output_detail_level=resolved_output_detail_level,
            caller_id=caller_id or None,
        )
    except api_client.ApiClientError as exc:
        logger.error("api-server 创建任务失败: %s", exc)
        return f"提交任务到 api-server 失败：{exc}"
    except Exception as exc:
        logger.exception("api-server 创建任务异常")
        return f"提交任务到 api-server 异常：{exc}"

    task_id = str(created.get("task_id") or "").strip()
    if not task_id:
        return "api-server 未返回 task_id，请检查后端日志。"

    _reset_cancel_flag(task_id)
    _register_active_task(task_id, caller_id=caller_id)
    state = _init_render_state()
    done_status = ""
    cancelled_locally = False
    try:
        async for message in api_client.stream_task_events(
            task_id,
            cancel_check=lambda: _disconnect_check_for_task(task_id),
        ):
            event_type = str(message.get("event") or "")
            if event_type != "done":
                state = _update_state_with_event(state, message)
                continue

            done_data = message.get("data")
            if not isinstance(done_data, dict):
                return "api-server 终态协议错误：done.data 必须是对象。"
            done_status = str(done_data.get("status") or "").strip().lower()
            if done_status not in {"completed", "cached", "failed", "cancelled"}:
                return (
                    "api-server 终态协议错误：done.status 必须是 "
                    "completed、cached、failed 或 cancelled。"
                )
            detail = str(
                done_data.get("error") or done_data.get("detail") or ""
            ).strip()
            if done_status == "failed":
                message_text = "任务执行失败"
                if detail:
                    message_text = f"{message_text}：{detail}"
                state.setdefault("errors", []).append(message_text)
            elif done_status == "cancelled":
                message_text = "任务已取消"
                if detail:
                    message_text = f"{message_text}：{detail}"
                state.setdefault("errors", []).append(message_text)
            break
        if not done_status:
            cancelled_locally = await _disconnect_check_for_task(task_id)
    except asyncio.CancelledError:
        await asyncio.shield(_cancel_remote_task_ids([task_id]))
        raise
    except api_client.TaskNotFoundError:
        return f"任务 `{task_id}` 不存在或已过期，请重新发起检索。"
    except api_client.ApiClientError as exc:
        logger.error("API SSE 订阅失败 task_id=%s err=%s", task_id, exc)
        return f"连接 api-server 失败：{exc}"
    except Exception as exc:
        logger.exception("API 非流式调用处理异常 task_id=%s", task_id)
        return f"流处理异常：{exc}"
    finally:
        _unregister_active_task(task_id)

    if not done_status and cancelled_locally:
        state.setdefault("errors", []).append("任务已取消")
    elif not done_status:
        return "api-server 事件流已结束，但未收到终态，请稍后重试。"
    result = _render_markdown(
        state,
        render_mode=resolved_api_render_mode,
        final_summary_merge_strategy=resolved_summary_merge_strategy,
    )
    if done_status in {"completed", "cached"} and "final-output" not in state["agents"]:
        return "api-server 已完成任务，但未返回可展示的最终结果。"
    return result


async def run_research_once_api_binding(
    query: str,
    mode: str,
    search_profile: str = DEFAULT_SEARCH_PROFILE,
    search_result_num: int = DEFAULT_SEARCH_RESULT_NUM,
    verification_min_search_rounds: int = DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS,
    output_detail_level: str = DEFAULT_OUTPUT_DETAIL_LEVEL,
    caller_id: Optional[str] = None,
) -> str:
    """Gradio 公开绑定适配器：第 7 个位置参数专用于 caller_id。"""
    return await run_research_once(
        query=query,
        mode=mode,
        search_profile=search_profile,
        search_result_num=search_result_num,
        verification_min_search_rounds=verification_min_search_rounds,
        output_detail_level=output_detail_level,
        caller_id=caller_id,
    )


async def _cancel_remote_task_ids(task_ids: List[str]) -> None:
    """向 api-server 逐个发送取消请求；单个失败不影响其余任务。"""
    for task_id in task_ids:
        try:
            await api_client.cancel_task(task_id)
        except Exception as exc:
            logger.warning("远程 cancel_task 失败 task_id=%s err=%s", task_id, exc)


def _schedule_remote_task_cancellation(task_ids: List[str]) -> int:
    """在当前事件循环调度远端取消，无事件循环时同步完成。"""
    resolved_task_ids = [task_id for task_id in task_ids if task_id]
    if not resolved_task_ids:
        return 0
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_cancel_remote_task_ids(resolved_task_ids))
    else:
        loop.create_task(_cancel_remote_task_ids(resolved_task_ids))
    return len(resolved_task_ids)


def stop_current_ui(ui_state: Optional[dict] = None):
    tid = (ui_state or {}).get("task_id")
    target_ids = [tid] if tid else _get_active_task_ids()
    _cancel_task_ids(target_ids)
    # API 模式：同步通知 api-server 设置取消标记，让 worker 协作式中止
    if api_client.is_api_mode_enabled() and tid:
        _schedule_remote_task_cancellation([tid])
    return (
        gr.update(interactive=True),
        gr.update(interactive=False),
    )


# ---------- 重连入口（demo.load 调用）----------------------------------------


async def reconnect_or_init(
    ui_state: Optional[dict],
    task_id_bridge: Optional[str] = "",
    request: Optional[gr.Request] = None,
):
    """页面加载时根据 URL `?task_id=...` 决定是否重连任务。

    Yields 与 gradio_run 相同的 4 元组：
        (markdown, run_btn_update, stop_btn_update, ui_state)

    当 URL 没有 task_id，或不在 API 模式时，仅恢复初始空闲态。
    """
    base_state = ui_state or {}

    # 首屏空闲态：等待用户输入
    idle_tuple = (
        I18N[DEFAULT_LANG]["output_waiting"],
        gr.update(interactive=True),
        gr.update(interactive=False),
        base_state,
        _task_id_bridge_value(base_state),
    )

    if not api_client.is_api_mode_enabled():
        yield idle_tuple
        return

    if request is None and hasattr(task_id_bridge, "query_params"):
        request = task_id_bridge
        task_id_bridge = ""

    query_params = getattr(request, "query_params", {}) or {}
    # query_params 可能是 dict / Mapping 类型
    try:
        task_id = query_params.get("task_id") if hasattr(query_params, "get") else None
    except Exception:
        task_id = None
    if not task_id:
        task_id = str(task_id_bridge or "").strip() or None
    if not task_id:
        yield idle_tuple
        return

    # 校验任务是否存在
    try:
        snapshot = await api_client.get_task(task_id)
    except api_client.ApiClientError as exc:
        cleared_ui_state = {**base_state, "task_id": None}
        logger.warning("get_task 失败 task_id=%s err=%s", task_id, exc)
        yield (
            f"无法连接 api-server：{exc}",
            gr.update(interactive=True),
            gr.update(interactive=False),
            cleared_ui_state,
            _task_id_bridge_value(cleared_ui_state),
        )
        return

    if snapshot is None:
        cleared_ui_state = {**base_state, "task_id": None}
        yield (
            f"任务 `{task_id}` 不存在或已过期，请重新发起检索。",
            gr.update(interactive=True),
            gr.update(interactive=False),
            cleared_ui_state,
            _task_id_bridge_value(cleared_ui_state),
        )
        return

    # 还原任务参数到 ui_state（便于下次 stop / 取消）
    meta = snapshot.get("meta") or {}
    resolved_output_detail_level = _normalize_output_detail_level(
        meta.get("output_detail_level") or DEFAULT_OUTPUT_DETAIL_LEVEL
    )
    resolved_ui_render_mode = _normalize_render_mode(
        None,
        _get_render_mode_for_output_detail(resolved_output_detail_level),
    )
    resolved_summary_merge_strategy = _normalize_final_summary_merge_strategy(
        _get_summary_merge_for_output_detail(resolved_output_detail_level)
    )
    new_ui_state = _build_initial_ui_state(
        task_id=task_id,
        mode=_normalize_research_mode(meta.get("mode") or DEFAULT_RESEARCH_MODE),
        search_profile=_normalize_search_profile(
            meta.get("search_profile") or DEFAULT_SEARCH_PROFILE
        ),
        search_result_num=_normalize_search_result_num(
            meta.get("search_result_num") or DEFAULT_SEARCH_RESULT_NUM
        ),
        verification_min_search_rounds=_normalize_verification_min_search_rounds(
            meta.get("verification_min_search_rounds")
            or DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS
        ),
        output_detail_level=resolved_output_detail_level,
        render_mode=resolved_ui_render_mode,
        summary_merge_strategy=resolved_summary_merge_strategy,
    )
    new_ui_state = {**base_state, **new_ui_state}

    # 任何状态（queued/running/completed/cached/failed/cancelled）都通过 SSE 重建 UI：
    #
    # api-server 端 `_event_generator` 会从 Redis Stream 头部回放全部历史事件，
    # 终态任务还会立即追加 `event: done`。这样无论任务进行中还是已结束，
    # 刷新页面后看到的渲染都与实时观察完全一致（包含搜索列表、检索过程、研究总结），
    # 而不是只展示 76 字节的兜底 result 字段。
    #
    # status 仅用于决定按钮可交互性：终态任务恢复 Run 可点、Stop 不可点。
    status = (snapshot.get("status") or (meta.get("status") or "")).lower()
    is_terminal = status in {"completed", "cached", "failed", "cancelled"}
    initial_render_state = _build_reconnect_initial_render_state(snapshot)
    if not is_terminal:
        _reset_cancel_flag(task_id)
        _register_active_task(task_id)
    try:
        async for tup in _render_stream_via_api(
            task_id,
            ui_state=new_ui_state,
            resolved_ui_render_mode=resolved_ui_render_mode,
            resolved_summary_merge_strategy=resolved_summary_merge_strategy,
            initial_state=initial_render_state,
        ):
            # 终态任务的事件流会在很短时间内结束并 yield 最终态元组（Run 可点、Stop 不可点）；
            # 中间过程的元组保持原样按 SSE 节奏 yield。
            yield tup
    finally:
        if not is_terminal:
            _unregister_active_task(task_id)


def stop_current_api(caller_id: Optional[str] = None):
    caller_id = str(caller_id).strip() if caller_id is not None else None
    caller_id = caller_id or None
    active_task_ids = _get_active_task_ids(
        caller_id=caller_id,
        mark_cancelled=True,
    )
    cancelled = len(active_task_ids)
    remote_cancel_requested = 0
    if api_client.is_api_mode_enabled():
        remote_cancel_requested = _schedule_remote_task_cancellation(active_task_ids)
    return {
        "cancelled": cancelled,
        "active_task_ids": active_task_ids,
        "remote_cancel_requested": remote_cancel_requested,
    }


def stop_current_by_caller_api(caller_id: Optional[str] = None):
    """公开定向取消入口；空 caller_id 不得退化为全局取消。"""
    resolved_caller_id = str(caller_id or "").strip()
    if not resolved_caller_id:
        return {
            "cancelled": 0,
            "active_task_ids": [],
            "remote_cancel_requested": 0,
            "reason": "caller_id_required",
        }
    return stop_current_api(caller_id=resolved_caller_id)


def get_last_metrics() -> dict:
    """返回最近一次任务的结构化运行指标。"""
    with _last_run_metrics_lock:
        if _last_run_metrics is None:
            return {"status": "no_data", "message": "尚无已完成的任务"}
        return _last_run_metrics


def _resolve_task_log_dir() -> Path:
    configured_log_dir = os.getenv("LOG_DIR", "logs/api-server")
    log_dir_path = Path(configured_log_dir)
    if not log_dir_path.is_absolute():
        log_dir_path = (Path(__file__).resolve().parent / log_dir_path).resolve()
    return log_dir_path


def _mark_stale_running_task(task_file_path: Path, stale_age_seconds: int) -> bool:
    try:
        task_payload = json.loads(task_file_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("读取任务日志失败，跳过: %s | %s", task_file_path, exc)
        return False

    if str(task_payload.get("status", "")).lower() != "running":
        return False

    task_id = str(task_payload.get("task_id", "")).strip()
    if task_id and task_id in _get_active_task_ids():
        return False

    now_ts = time.time()
    try:
        file_age_seconds = int(now_ts - task_file_path.stat().st_mtime)
    except OSError:
        return False
    if file_age_seconds < stale_age_seconds:
        return False

    now_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts))
    task_payload["status"] = "failed"
    if not task_payload.get("end_time"):
        task_payload["end_time"] = now_text
    if not task_payload.get("error"):
        task_payload["error"] = (
            "任务长时间未更新且进程状态未知，已自动从 running 收敛为 failed。"
        )

    step_logs = task_payload.get("step_logs")
    if not isinstance(step_logs, list):
        step_logs = []
    step_logs.append(
        {
            "step_name": "task_auto_reconcile",
            "message": (
                "检测到陈旧 running 任务，已自动收敛为 failed，"
                f"最后活动距今约 {file_age_seconds}s。"
            ),
            "timestamp": now_text,
            "info_level": "warning",
            "metadata": {
                "stale_age_seconds": file_age_seconds,
                "reconciler": "gradio-demo-stale-task-reaper",
            },
        }
    )
    task_payload["step_logs"] = step_logs

    temp_file_path = task_file_path.with_suffix(f"{task_file_path.suffix}.tmp")
    try:
        temp_file_path.write_text(
            json.dumps(task_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_file_path.replace(task_file_path)
    except Exception as exc:
        logger.warning("写回任务日志失败，跳过: %s | %s", task_file_path, exc)
        try:
            temp_file_path.unlink(missing_ok=True)
        except Exception:
            pass
        return False

    logger.warning(
        "已自动收敛陈旧 running 任务为 failed | task_id=%s | file=%s | stale_age_seconds=%s",
        task_id or "unknown",
        task_file_path.name,
        file_age_seconds,
    )
    return True


def _reconcile_stale_running_tasks_once() -> int:
    if not STALE_TASK_REAPER_ENABLED:
        return 0
    log_dir_path = _resolve_task_log_dir()
    if not log_dir_path.exists():
        return 0

    task_files = sorted(
        log_dir_path.glob("task_*.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )[:STALE_TASK_REAPER_SCAN_LIMIT]
    reconciled_count = 0
    for task_file_path in task_files:
        if _mark_stale_running_task(
            task_file_path,
            stale_age_seconds=STALE_TASK_RUNNING_TIMEOUT_SECONDS,
        ):
            reconciled_count += 1
    if reconciled_count > 0:
        logger.info("陈旧任务自动收敛完成，本轮处理 %s 条。", reconciled_count)
    return reconciled_count


def _stale_task_reaper_loop():
    logger.info(
        "陈旧任务巡检线程已启动 | interval=%ss | stale_timeout=%ss | enabled=%s",
        STALE_TASK_REAPER_INTERVAL_SECONDS,
        STALE_TASK_RUNNING_TIMEOUT_SECONDS,
        STALE_TASK_REAPER_ENABLED,
    )
    while STALE_TASK_REAPER_ENABLED:
        try:
            _reconcile_stale_running_tasks_once()
        except Exception as exc:
            logger.warning("陈旧任务巡检异常: %s", exc, exc_info=True)
        time.sleep(STALE_TASK_REAPER_INTERVAL_SECONDS)


def _start_stale_task_reaper():
    global _stale_task_reaper_started
    if not STALE_TASK_REAPER_ENABLED:
        return
    with _stale_task_reaper_lock:
        if _stale_task_reaper_started:
            return
        _stale_task_reaper_started = True
        threading.Thread(
            target=_stale_task_reaper_loop,
            name="stale-task-reaper",
            daemon=True,
        ).start()


def _update_verification_rounds_visibility(mode: str):
    return gr.update(visible=_is_verified_mode(mode))


def build_demo():
    api_client.get_backend_mode()
    logo_data_uri = _load_logo_data_uri()
    fallback_favicon_data_uri = _build_fallback_favicon_data_uri()

    custom_css = """
    /* ========== MiroThinker - Clean Emerald Design ========== */
    
    /* Base */
    .gradio-container {
        --app-bg: #f9fafb;
        --panel-bg: #ffffff;
        --panel-border: rgba(0, 0, 0, 0.06);
        --panel-shadow: 0 1px 3px rgba(0, 0, 0, 0.04), 0 1px 2px rgba(0, 0, 0, 0.03);
        --ink-strong: #111827;
        --ink-body: #374151;
        --ink-soft: #6b7280;
        --accent: #10b981;
        --accent-strong: #059669;
        --accent-soft: #d1fae5;
        max-width: 100% !important;
        margin: 0 !important;
        padding: 0 !important;
        font-family: __LOCAL_FONT_FAMILY_STACK__ !important;
        background: #ffffff !important;
        color: var(--ink-strong);
        min-height: 100vh;
        position: relative;
    }

    .gradio-container::before {
        display: none;
    }

    /* 强力清除 Gradio 默认包装盒的丑陋背景与边框 */
    .gradio-container .form,
    .gradio-container fieldset,
    #main-content-column .form,
    #right-options-column .form,
    #input-section .block,
    #input-section .solid,
    #options-panel .block,
    #options-panel .solid {
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
    }



    /* ===== Options Panel ===== */
    #right-options-column {
        gap: 0 !important;
    }

    #options-panel {
        width: 100% !important;
        background: #ffffff !important;
        border: 1px solid rgba(0, 0, 0, 0.03) !important;
        border-radius: 16px !important;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.02) !important;
        padding: 18px 16px !important;
    }

    .options-title {
        font-size: 0.72em;
        font-weight: 700;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        color: #64748b;
        margin-bottom: 12px;
    }

    #mode-selector,
    #search-profile-selector,
    #search-result-num-selector,
    #verification-rounds-selector,
    #output-detail-level-selector {
        border: 0 !important;
        background: transparent !important;
        padding: 0 !important;
        margin-bottom: 14px !important;
        box-shadow: none !important;
    }

    #mode-selector .container,
    #search-profile-selector .container,
    #search-result-num-selector .container,
    #verification-rounds-selector .container,
    #output-detail-level-selector .container {
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
        padding: 0 !important;
    }

    #mode-selector label,
    #search-profile-selector label,
    #search-result-num-selector label,
    #verification-rounds-selector label,
    #output-detail-level-selector label {
        color: var(--ink-strong) !important;
        font-weight: 600 !important;
    }

    #mode-selector [data-testid="block-info"],
    #search-profile-selector [data-testid="block-info"],
    #search-result-num-selector [data-testid="block-info"],
    #verification-rounds-selector [data-testid="block-info"],
    #output-detail-level-selector [data-testid="block-info"] {
        color: var(--ink-strong) !important;
        font-weight: 700 !important;
        font-size: 0.9em !important;
        letter-spacing: 0.01em;
    }

    #mode-selector .md p,
    #search-profile-selector .md p,
    #search-result-num-selector .md p,
    #verification-rounds-selector .md p,
    #output-detail-level-selector .md p {
        color: #94a3b8 !important;
        font-size: 0.7em !important;
        line-height: 1.45 !important;
        margin: 4px 0 8px !important;
    }

    #mode-selector .wrap,
    #search-profile-selector .wrap,
    #search-result-num-selector .wrap,
    #verification-rounds-selector .wrap,
    #output-detail-level-selector .wrap {
        background: #ffffff !important;
        border: 1px solid rgba(0, 0, 0, 0.1) !important;
        border-radius: 10px !important;
        box-shadow: 0 1px 2px rgba(0, 0, 0, 0.02) !important;
        transition: border-color 0.2s ease;
    }
    
    #mode-selector .wrap:hover,
    #search-profile-selector .wrap:hover,
    #search-result-num-selector .wrap:hover,
    #verification-rounds-selector .wrap:hover,
    #output-detail-level-selector .wrap:hover {
        border-color: rgba(16, 185, 129, 0.4) !important;
    }

    #mode-selector input,
    #search-profile-selector input,
    #search-result-num-selector input,
    #verification-rounds-selector input,
    #output-detail-level-selector input {
        color: var(--ink-strong) !important;
        font-weight: 600 !important;
    }

    #mode-selector svg,
    #search-profile-selector svg,
    #search-result-num-selector svg,
    #verification-rounds-selector svg,
    #output-detail-level-selector svg {
        fill: #475569 !important;
    }
    
    #btn-row {
        padding: 12px 24px 16px !important;
        border-top: 1px solid rgba(0, 0, 0, 0.04);
        gap: 12px !important;
        background: #ffffff;
    }
    
    #run-btn {
        background: #10b981 !important;
        color: #ffffff !important;
        border: none !important;
        border-radius: 12px !important;
        padding: 12px 20px !important;
        font-size: 0.92em !important;
        font-weight: 600 !important;
        letter-spacing: 0.05em !important;
        cursor: pointer !important;
        transition: all 0.2s ease !important;
        box-shadow: 0 2px 4px rgba(16, 185, 129, 0.15) !important;
    }
    
    #run-btn:hover {
        background: #059669 !important;
        transform: translateY(-1px) !important;
        box-shadow: 0 4px 8px rgba(16, 185, 129, 0.25) !important;
    }
    
    #stop-btn {
        background: #ffffff !important;
        color: #64748b !important;
        border: 1px solid rgba(0, 0, 0, 0.06) !important;
        border-radius: 12px !important;
        padding: 12px 20px !important;
        font-size: 0.92em !important;
        font-weight: 500 !important;
        cursor: pointer !important;
        transition: all 0.2s ease !important;
    }
    
    #stop-btn:hover {
        color: #dc2626 !important;
        border-color: rgba(220, 38, 38, 0.2) !important;
        background: #fef2f2 !important;
    }
    
    /* ===== Output Section ===== */
    #output-section {
        width: 100% !important;
        max-width: 980px !important;
        margin: 0 auto !important;
        padding: 0 0 60px !important;
    }
    
    .output-label {
        font-size: 0.78em;
        font-weight: 700;
        color: var(--ink-soft);
        text-transform: uppercase;
        letter-spacing: 0.12em;
        margin-bottom: 12px;
        padding: 0 6px;
    }
    
    #log-view {
        padding: 40px 48px !important;
        min-height: 420px;
        height: auto !important;
        overflow: visible !important;
        background: #ffffff !important;
        border: none !important;
        border-radius: 28px !important;
        box-shadow: 0 4px 20px -4px rgba(0, 0, 0, 0.04), 0 0 0 1px rgba(0, 0, 0, 0.03) !important;
    }

    #export-row {
        margin-top: 14px !important;
        gap: 12px !important;
        align-items: flex-end !important;
    }

    #export-btn {
        border-radius: 12px !important;
        border: 1px solid rgba(16, 185, 129, 0.18) !important;
        color: #047857 !important;
        background: #ecfdf5 !important;
        font-weight: 700 !important;
    }

    .export-hint {
        margin: 8px 6px 0;
        color: #64748b;
        font-size: 0.82em;
        line-height: 1.6;
    }
    
    #log-view h3 {
        font-size: 1.02em;
        font-weight: 700;
        color: var(--ink-strong);
        margin: 28px 0 16px 0;
        padding-bottom: 10px;
        border-bottom: 1px solid rgba(15, 23, 42, 0.08);
    }
    
    #log-view h3:first-child {
        margin-top: 0;
    }
    
    /* Error block */
    .error-block {
        background: linear-gradient(180deg, #fff6f6 0%, #fff0f0 100%);
        border: 1px solid rgba(239, 68, 68, 0.18);
        border-radius: 16px;
        padding: 14px 16px;
        margin: 12px 0;
        color: #b91c1c;
        font-size: 0.9em;
    }
    
    /* Thought details card */
    .thought-card {
        background: linear-gradient(180deg, rgba(248, 250, 252, 0.94), rgba(255, 255, 255, 0.98));
        border: 1px solid rgba(15, 23, 42, 0.06);
        border-left: 3px solid #10b981;
        border-radius: 14px;
        padding: 12px 16px;
        margin: 12px 0;
        box-shadow: 0 4px 12px rgba(15, 23, 42, 0.015);
    }
    
    .thought-card > summary {
        cursor: pointer;
        font-size: 0.9em;
        font-weight: 600;
        color: #475569;
        outline: none;
        user-select: none;
    }

    .thought-card[open] > summary {
        margin-bottom: 10px;
        border-bottom: 1px solid rgba(15, 23, 42, 0.05);
        padding-bottom: 6px;
    }
    
    .thought-content {
        font-size: 0.9em;
        color: var(--ink-body);
        line-height: 1.7;
    }
    
    /* Tool card */
    .tool-card {
        background: linear-gradient(180deg, rgba(246, 248, 250, 0.94), rgba(255, 255, 255, 0.98));
        border: 1px solid rgba(15, 23, 42, 0.07);
        border-radius: 18px;
        padding: 14px 16px;
        margin: 14px 0;
        box-shadow: 0 8px 20px rgba(15, 23, 42, 0.04);
    }
    
    .tool-header {
        font-size: 0.9em;
        font-weight: 500;
        color: var(--ink-strong);
        margin-bottom: 4px;
    }
    
    .tool-brief {
        font-size: 0.8em;
        color: var(--ink-soft);
        margin-top: 4px;
    }
    
    .tool-status {
        font-size: 0.8em;
        color: var(--accent);
        margin-top: 6px;
    }
    
    #log-view blockquote {
        background: rgba(209, 250, 229, 0.3);
        border: none;
        border-left: 3px solid var(--accent);
        padding: 18px 22px;
        margin: 18px 0;
        border-radius: 0 18px 18px 0;
        font-style: normal;
        color: #065f46;
        font-size: 0.95em;
        line-height: 1.8;
    }
    
    #log-view pre {
        background: #f6f8fb !important;
        color: #1e293b !important;
        border-radius: 18px !important;
        padding: 18px !important;
        font-size: 0.85em !important;
        line-height: 1.6 !important;
        overflow-x: auto;
        margin: 14px 0;
        border: 1px solid rgba(148, 163, 184, 0.2);
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
        background: #eef4f7 !important;
        color: #1e293b !important;
        padding: 3px 7px !important;
        border-radius: 6px !important;
        font-size: 0.9em !important;
    }
    
    #log-view p {
        line-height: 1.85;
        color: #334155;
        margin: 0 0 14px;
    }

    #log-view .process-details {
        margin-top: 14px;
        border: 1px solid rgba(15, 23, 42, 0.08);
        border-radius: 16px;
        background: rgba(248, 250, 252, 0.72);
        padding: 12px 14px;
    }

    #log-view .process-details > summary {
        cursor: pointer;
        color: #334155;
        font-size: 0.92em;
        font-weight: 600;
    }

    #log-view .process-details[open] > summary {
        margin-bottom: 10px;
    }

    #log-view .search-step-board {
        margin: 16px 0 12px;
        border: 1px solid #e5e7eb;
        border-radius: 14px;
        overflow: hidden;
        background: #ffffff;
    }

    #log-view .search-step-item {
        padding: 12px 16px;
        font-size: 0.88em;
        line-height: 1.6;
        color: #334155;
        border-bottom: 1px solid #eef2f7;
        background: #f9fafb;
    }

    #log-view .search-step-item:last-child {
        border-bottom: none;
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
    
    /* ===== Top Navigation ===== */
    .top-nav {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 16px;
        width: min(1560px, calc(100% - 40px));
        margin: 12px auto 2px;
        padding: 10px 20px 8px;
        border: 0;
        background: transparent;
        box-shadow: none;
        position: relative;
    }

    .top-nav::after {
        content: "";
        position: absolute;
        bottom: 0;
        left: 50%;
        transform: translateX(-50%);
        width: min(1560px, calc(100% - 40px));
        height: 1px;
        background: linear-gradient(90deg, transparent, rgba(16, 185, 129, 0.12), transparent);
    }

    .nav-left {
        display: flex;
        align-items: center;
        gap: 20px;
    }

    #lang-toggle-btn {
        background: #f8fafc !important;
        color: #475569 !important;
        border: 1px solid rgba(0, 0, 0, 0.08) !important;
        border-radius: 8px !important;
        padding: 8px 16px !important;
        font-size: 0.85em !important;
        font-weight: 500 !important;
        cursor: pointer !important;
        transition: all 0.2s ease !important;
        min-width: 80px !important;
    }

    #lang-toggle-btn:hover {
        background: #f1f5f9 !important;
        border-color: rgba(16, 185, 129, 0.3) !important;
        color: #10b981 !important;
    }
    }

    .nav-brand {
        display: flex;
        align-items: center;
        gap: 10px;
        font-weight: 600;
        font-size: 0.92em;
        color: var(--ink-strong);
    }

    .brand-logo {
        height: 36px !important;
        width: auto !important;
        max-width: 140px !important;
        max-height: 36px !important;
        object-fit: contain !important;
        display: block !important;
        flex-shrink: 0 !important;
    }

    .nav-brand-text {
        line-height: 1.2;
        white-space: nowrap;
    }

    .nav-right {
        display: flex;
        align-items: center;
        justify-content: flex-end;
        gap: 12px;
    }

    /* ===== Hero Section ===== */
    .hero-section {
        text-align: center;
        padding: 16px 16px 32px;
        max-width: 1040px;
        margin: 0 auto 8px;
        position: relative;
    }

    .hero-brand {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 10px;
        margin-bottom: 16px;
    }

    .hero-logo {
        width: min(160px, 36vw);
        max-height: 64px;
        height: auto;
        object-fit: contain;
        box-shadow: none;
        border-radius: 0;
        flex-shrink: 0;
        opacity: 0.9;
    }

    .hero-brand-name {
        font-size: 0.96em;
        font-weight: 700;
        color: #0f172a;
        letter-spacing: 0.01em;
    }

    .hero-title {
        font-size: clamp(1.8rem, 3.2vw, 3rem);
        font-weight: 900;
        background: linear-gradient(135deg, #065f46 0%, #10b981 40%, #059669 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        margin: 0 0 10px 0;
        letter-spacing: -0.04em;
        line-height: 1.15;
    }

    .hero-subtitle {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 16px;
        color: #94a3b8;
        font-size: 0.92em;
        font-weight: 500;
        letter-spacing: 0.02em;
    }

    .hero-line {
        width: 40px;
        height: 1px;
        background: linear-gradient(90deg, transparent, rgba(16, 185, 129, 0.3), transparent);
    }

    /* ===== Responsive ===== */
    @media (max-width: 768px) {
        .hero-title {
            font-size: 2.2em;
        }

        .brand-logo {
            height: 28px;
            max-width: 96px;
        }

        .top-nav {
            width: calc(100% - 24px);
            margin: 12px auto 8px;
            padding: 12px 14px;
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
            padding: 8px 16px 16px;
        }

        .hero-brand {
            margin-bottom: 14px;
            gap: 8px;
        }

        .hero-logo {
            width: min(188px, 68vw);
            max-height: 72px;
        }

        .hero-brand-name {
            font-size: 0.9em;
        }

        .hero-subtitle {
            gap: 10px;
            font-size: 0.92em;
        }

        #layout-shell {
            padding: 0 16px 28px !important;
            gap: 16px !important;
        }

        #main-content-column {
            order: 1;
            padding: 0 !important;
        }

        #right-options-column {
            order: 2;
            position: static;
        }

        #input-section,
        #options-panel,
        #log-view {
            border-radius: 22px !important;
        }

        
        #log-view {
            min-height: 260px;
            padding: 22px 20px !important;
        }
    }

    /* task_id <-> URL 同步桥的隐藏样式：
       Gradio 5 中 visible=False 的组件不进入 DOM，JS 无法找到，
       因此用 CSS 隐藏一个 visible=True 的 textbox。 */
    #gr-task-id-bridge { position: absolute !important; left: -9999px !important; top: -9999px !important; width: 1px !important; height: 1px !important; opacity: 0 !important; pointer-events: none !important; }
    """
    custom_css = custom_css.replace(
        "__LOCAL_FONT_FAMILY_STACK__", LOCAL_FONT_FAMILY_STACK
    )

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
    hero_brand_name_html = (
        ""
        if logo_data_uri
        else '<span class="hero-brand-name">OpenClaw-MiroSearch</span>'
    )

    skills_download_url, _ = _resolve_skills_package_download()

    skills_bind_script = """
    <script>
    (() => {
        const SELECTORS = {
            skillsDownloadLink: '#skills-download-link',
        };

        const copyTextToClipboard = async (text) => {
            const normalizedText = String(text || '').trim();
            if (!normalizedText) { return false; }
            if (navigator.clipboard && window.isSecureContext) {
                try {
                    await navigator.clipboard.writeText(normalizedText);
                    return true;
                } catch (e) { void e; }
            }
            const el = document.createElement('textarea');
            el.value = normalizedText;
            el.setAttribute('readonly', '');
            el.style.cssText = 'position:fixed;opacity:0;pointer-events:none';
            document.body.appendChild(el);
            el.focus(); el.select();
            let copied = false;
            try { copied = document.execCommand('copy'); } catch (e) { void e; }
            document.body.removeChild(el);
            return copied;
        };

        const bindSkillsDownloadAction = () => {
            const linkEl = document.querySelector(SELECTORS.skillsDownloadLink);
            if (!linkEl || linkEl.dataset.boundCopyAction === '1') { return; }
            linkEl.dataset.boundCopyAction = '1';
            linkEl.addEventListener('click', () => {
                const rawUrl = linkEl.dataset.copyUrl || linkEl.getAttribute('href') || '';
                let absoluteUrl = '';
                try { absoluteUrl = new URL(rawUrl, window.location.origin).toString(); } catch (e) { return; }
                const copiedText = linkEl.dataset.copiedText || 'Link Copied';
                const originalText = linkEl.dataset.originalText || 'Download Skills';
                copyTextToClipboard(absoluteUrl).then((copied) => {
                    if (!copied) { return; }
                    linkEl.textContent = copiedText;
                    window.setTimeout(() => { linkEl.textContent = originalText; }, 1200);
                });
            });
        };

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', bindSkillsDownloadAction, { once: true });
        } else {
            bindSkillsDownloadAction();
        }
    })();
    </script>
    """
    # 任务 ID URL 同步桥：把 ?task_id=xxx 写入 / 读取 URL。
    task_id_url_bridge_script = """
    <script>
    (() => {
        // gradio_run / reconnect_or_init 通过隐藏 textbox#gr-task-id-bridge 写入当前 task_id；
        // 我们监听其变化，把 task_id 同步到 URL，避免刷新丢失。
        const observe = () => {
            const wrapper = document.querySelector('#gr-task-id-bridge');
            if (!wrapper) { return false; }
            const input = wrapper.querySelector('textarea, input');
            if (!input) { return false; }
            const initialUrlTaskId = new URL(window.location.href).searchParams.get('task_id') || '';
            if (!input.value && initialUrlTaskId) {
                input.value = initialUrlTaskId;
            }
            const sync = () => {
                const value = (input.value || '').trim();
                const url = new URL(window.location.href);
                const current = url.searchParams.get('task_id') || '';
                if (value && value !== current) {
                    url.searchParams.set('task_id', value);
                    window.history.replaceState(null, '', url.toString());
                } else if (!value && current) {
                    url.searchParams.delete('task_id');
                    window.history.replaceState(null, '', url.toString());
                }
            };
            input.addEventListener('input', sync);
            input.addEventListener('change', sync);
            // 兼容 Gradio 内部 set value 但不触发 input 事件的情况
            const observer = new MutationObserver(sync);
            observer.observe(input, { attributes: true, attributeFilter: ['value'] });
            // 初次轮询
            let last = input.value;
            window.setInterval(() => {
                if (input.value !== last) {
                    last = input.value;
                    sync();
                }
            }, 500);
            sync();
            return true;
        };
        const start = () => {
            if (observe()) { return; }
            const t = window.setInterval(() => {
                if (observe()) { window.clearInterval(t); }
            }, 300);
        };
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', start, { once: true });
        } else {
            start();
        }
    })();
    </script>
    """
    demo_head = f"{favicon_head}{skills_bind_script}{task_id_url_bridge_script}"

    def _get_i18n(lang: str):
        return I18N.get(lang, I18N[DEFAULT_LANG])

    def _build_skills_link_html(lang: str):
        i18n = _get_i18n(lang)
        if skills_download_url:
            escaped_url = html.escape(skills_download_url, quote=True)
            return (
                f'<a id="skills-download-link" class="skills-top-link" href="{escaped_url}" '
                f'data-copy-url="{escaped_url}" data-original-text="{i18n["skills_download_btn"]}" '
                f'data-copied-text="{i18n["skills_download_copied"]}" target="_blank" rel="noopener noreferrer">'
                f'{i18n["skills_download_btn"]}</a>'
            )
        return (
            f'<span class="skills-top-link skills-top-link-disabled" title="{html.escape(i18n["skills_download_fallback"], quote=True)}">'
            f'{i18n["skills_download_btn"]}</span>'
        )

    def _build_nav_html(lang: str):
        i18n = _get_i18n(lang)
        skills_link = _build_skills_link_html(lang)
        return f"""
            <nav class="top-nav">
                <div class="nav-left">
                    <div class="nav-brand">
                        {nav_logo_html}
                        <span class="nav-brand-text">{i18n["nav_brand_text"]}</span>
                    </div>
                </div>
                <div class="nav-right">
                    {skills_link}
                </div>
            </nav>
        """

    def _build_hero_html(lang: str):
        i18n = _get_i18n(lang)
        return f"""
            <div class="hero-section">
                <div class="hero-brand">
                    <img src="{hero_logo_src}" class="hero-logo" alt="OpenClaw-MiroSearch logo" />
                    {hero_brand_name_html}
                </div>
                <h1 class="hero-title">{i18n["hero_title"]}</h1>
                <div class="hero-subtitle">
                    <span class="hero-line"></span>
                    {i18n["hero_subtitle"]}
                    <span class="hero-line"></span>
                </div>
            </div>
        """

    def _build_output_detail_choices(lang: str):
        i18n = _get_i18n(lang)
        labels = i18n["output_detail_labels"]
        return [
            (labels["compact"], "compact"),
            (labels["balanced"], "balanced"),
            (labels["detailed"], "detailed"),
        ]

    def toggle_language(lang: str):
        new_lang = LANG_CN if lang == LANG_EN else LANG_EN
        i18n = _get_i18n(new_lang)
        return (
            new_lang,
            gr.HTML(_build_nav_html(new_lang)),
            gr.HTML(_build_hero_html(new_lang)),
            gr.Textbox(placeholder=i18n["input_placeholder"]),
            gr.Button(value=i18n["btn_stop"]),
            gr.Button(value=i18n["btn_run"]),
            gr.HTML(f'<div class="output-label">{i18n["output_label"]}</div>'),
            gr.Markdown(i18n["output_waiting"]),
            gr.HTML(f'<div class="options-title">{i18n["options_title"]}</div>'),
            gr.update(label=i18n["mode_label"], info=i18n["mode_info"]),
            gr.update(
                label=i18n["search_profile_label"], info=i18n["search_profile_info"]
            ),
            gr.update(
                label=i18n["search_result_num_label"],
                info=i18n["search_result_num_info"],
            ),
            gr.update(
                label=i18n["verification_rounds_label"],
                info=i18n["verification_rounds_info"],
            ),
            gr.update(
                label=i18n["output_detail_label"],
                choices=_build_output_detail_choices(new_lang),
                info=i18n["output_detail_info"],
            ),
            gr.update(label=i18n["export_format_label"]),
            gr.Button(value=i18n["export_btn"]),
            gr.update(label=i18n["export_file_label"], visible=False, value=None),
            gr.HTML(f'<div class="export-hint">{i18n["export_hint"]}</div>'),
            gr.Button(value=i18n["lang_toggle_btn"]),
            gr.HTML(f'<div class="app-footer">{i18n["footer_text"]}</div>'),
        )

    with gr.Blocks(
        css=custom_css,
        title=I18N[DEFAULT_LANG]["page_title"],
        theme=gr.themes.Base(),
        head=demo_head,
    ) as demo:
        lang_state = gr.State(DEFAULT_LANG)

        nav_html = gr.HTML(_build_nav_html(DEFAULT_LANG))
        hero_html = gr.HTML(_build_hero_html(DEFAULT_LANG))

        with gr.Row(elem_id="layout-shell", equal_height=False):
            with gr.Column(
                scale=4,
                min_width=720,
                elem_id="main-content-column",
            ):
                with gr.Column(elem_id="input-section"):
                    inp = gr.Textbox(
                        lines=4,
                        placeholder=I18N[DEFAULT_LANG]["input_placeholder"],
                        show_label=False,
                        elem_id="question-input",
                    )
                    with gr.Row(elem_id="btn-row"):
                        stop_btn = gr.Button(
                            I18N[DEFAULT_LANG]["btn_stop"],
                            elem_id="stop-btn",
                            variant="stop",
                            interactive=False,
                            scale=1,
                        )
                        run_btn = gr.Button(
                            I18N[DEFAULT_LANG]["btn_run"],
                            elem_id="run-btn",
                            variant="primary",
                            scale=2,
                        )

                with gr.Column(elem_id="output-section"):
                    output_label_html = gr.HTML(
                        f'<div class="output-label">{I18N[DEFAULT_LANG]["output_label"]}</div>'
                    )
                    out_md = gr.Markdown(
                        I18N[DEFAULT_LANG]["output_waiting"], elem_id="log-view"
                    )
                    with gr.Row(elem_id="export-row"):
                        export_format_selector = gr.Dropdown(
                            label=I18N[DEFAULT_LANG]["export_format_label"],
                            choices=EXPORT_FORMAT_CHOICES,
                            value="md",
                            elem_id="export-format-selector",
                            scale=1,
                        )
                        export_btn = gr.Button(
                            I18N[DEFAULT_LANG]["export_btn"],
                            elem_id="export-btn",
                            variant="secondary",
                            scale=1,
                        )
                    export_file = gr.File(
                        label=I18N[DEFAULT_LANG]["export_file_label"],
                        visible=False,
                        elem_id="export-file",
                    )
                    export_hint_html = gr.HTML(
                        f'<div class="export-hint">{I18N[DEFAULT_LANG]["export_hint"]}</div>'
                    )

            with gr.Column(
                scale=1,
                min_width=220,
                elem_id="right-options-column",
            ):
                with gr.Column(elem_id="options-panel"):
                    options_title_html = gr.HTML(
                        f'<div class="options-title">{I18N[DEFAULT_LANG]["options_title"]}</div>'
                    )
                    mode_selector = gr.Dropdown(
                        label=I18N[DEFAULT_LANG]["mode_label"],
                        choices=RESEARCH_MODE_CHOICES,
                        value=_normalize_research_mode(DEFAULT_RESEARCH_MODE),
                        info=I18N[DEFAULT_LANG]["mode_info"],
                        elem_id="mode-selector",
                    )
                    search_profile_selector = gr.Dropdown(
                        label=I18N[DEFAULT_LANG]["search_profile_label"],
                        choices=SEARCH_PROFILE_CHOICES,
                        value=_normalize_search_profile(DEFAULT_SEARCH_PROFILE),
                        info=I18N[DEFAULT_LANG]["search_profile_info"],
                        elem_id="search-profile-selector",
                    )
                    search_result_num_selector = gr.Dropdown(
                        label=I18N[DEFAULT_LANG]["search_result_num_label"],
                        choices=SEARCH_RESULT_NUM_CHOICES,
                        value=_normalize_search_result_num(DEFAULT_SEARCH_RESULT_NUM),
                        info=I18N[DEFAULT_LANG]["search_result_num_info"],
                        elem_id="search-result-num-selector",
                    )
                    verification_min_rounds_selector = gr.Slider(
                        minimum=1,
                        maximum=MAX_VERIFICATION_MIN_SEARCH_ROUNDS,
                        step=1,
                        label=I18N[DEFAULT_LANG]["verification_rounds_label"],
                        value=_normalize_verification_min_search_rounds(
                            DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS
                        ),
                        info=I18N[DEFAULT_LANG]["verification_rounds_info"],
                        visible=_is_verified_mode(DEFAULT_RESEARCH_MODE),
                        elem_id="verification-rounds-selector",
                    )
                    output_detail_level_selector = gr.Dropdown(
                        label=I18N[DEFAULT_LANG]["output_detail_label"],
                        choices=_build_output_detail_choices(DEFAULT_LANG),
                        value=_normalize_output_detail_level(
                            DEFAULT_OUTPUT_DETAIL_LEVEL
                        ),
                        info=I18N[DEFAULT_LANG]["output_detail_info"],
                        elem_id="output-detail-level-selector",
                    )
                    lang_toggle_btn = gr.Button(
                        I18N[DEFAULT_LANG]["lang_toggle_btn"],
                        elem_id="lang-toggle-btn",
                        variant="secondary",
                        scale=1,
                    )

        footer_html = gr.HTML(
            f'<div class="app-footer">{I18N[DEFAULT_LANG]["footer_text"]}</div>'
        )

        # 供统一 API 调用的隐藏输出
        api_output = gr.Markdown(visible=False)
        api_btn = gr.Button(value="api-run", visible=False)
        gr.Textbox(visible=False, value="")
        api_caller_id = gr.Textbox(
            visible=False,
            value="",
            elem_id="api-caller-id",
        )
        api_stop_output = gr.JSON(visible=False)
        api_stop_btn = gr.Button(value="api-stop", visible=False)

        # task_id <-> URL 同步桥：JS 监听该 textbox 的 value 变化，把 ?task_id=xxx 写入 URL。
        # 注意：Gradio 5 中 visible=False 的组件不会进入 DOM，因此这里 visible=True，
        # 通过 #gr-task-id-bridge 的 CSS 规则把它定位到屏幕外。
        task_id_box = gr.Textbox(
            value="",
            visible=True,
            elem_id="gr-task-id-bridge",
            interactive=False,
            show_label=False,
            container=False,
            label=None,
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
        run_event = run_btn.click(
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
            outputs=[out_md, run_btn, stop_btn, ui_state, task_id_box],
            api_name="run_research_stream",
        )

        # ui_state 任意一次更新都同步 task_id 到隐藏 textbox（JS 据此写 URL）
        ui_state.change(
            fn=_task_id_bridge_value,
            inputs=[ui_state],
            outputs=[task_id_box],
            api_name=False,
            queue=False,
        )
        export_btn.click(
            fn=_export_conclusion,
            inputs=[out_md, export_format_selector, ui_state],
            outputs=[export_file],
            api_name=False,
            queue=False,
        )

        # 页面加载时根据 URL ?task_id 决定空闲态 / 重连进行中的任务
        demo.load(
            fn=reconnect_or_init,
            inputs=[ui_state, task_id_box],
            outputs=[out_md, run_btn, stop_btn, ui_state, task_id_box],
            api_name=False,
            js="""
            (uiState, taskIdBridge) => {
                const urlTaskId = new URL(window.location.href).searchParams.get('task_id') || '';
                return [uiState, urlTaskId || taskIdBridge || ''];
            }
            """,
        )
        mode_selector.change(
            fn=_update_verification_rounds_visibility,
            inputs=[mode_selector],
            outputs=[verification_min_rounds_selector],
            api_name=False,
        )
        stop_btn.click(
            fn=stop_current_ui,
            inputs=[ui_state],
            outputs=[run_btn, stop_btn],
            cancels=[run_event],
            api_name=False,
            queue=False,
        )
        api_run_event = api_btn.click(
            fn=run_research_once_api_binding,
            inputs=[
                inp,
                mode_selector,
                search_profile_selector,
                search_result_num_selector,
                verification_min_rounds_selector,
                output_detail_level_selector,
                api_caller_id,
            ],
            outputs=[api_output],
            api_name="run_research_once",
        )
        api_stop_btn.click(
            fn=stop_current_by_caller_api,
            inputs=[api_caller_id],
            outputs=[api_stop_output],
            cancels=[api_run_event],
            api_name="stop_current",
            queue=False,
        )
        api_stop_by_caller_btn = gr.Button(value="api-stop-caller", visible=False)
        api_stop_by_caller_output = gr.JSON(visible=False)
        api_stop_by_caller_btn.click(
            fn=stop_current_by_caller_api,
            inputs=[api_caller_id],
            outputs=[api_stop_by_caller_output],
            cancels=[api_run_event],
            api_name="stop_current_by_caller",
            queue=False,
        )

        # GET /metrics/last — 返回最近一次任务的结构化运行指标
        api_metrics_btn = gr.Button(value="api-metrics-last", visible=False)
        api_metrics_output = gr.JSON(visible=False)
        api_metrics_btn.click(
            fn=get_last_metrics,
            inputs=[],
            outputs=[api_metrics_output],
            api_name="metrics_last",
        )

        lang_toggle_btn.click(
            fn=toggle_language,
            inputs=[lang_state],
            outputs=[
                lang_state,
                nav_html,
                hero_html,
                inp,
                stop_btn,
                run_btn,
                output_label_html,
                out_md,
                options_title_html,
                mode_selector,
                search_profile_selector,
                search_result_num_selector,
                verification_min_rounds_selector,
                output_detail_level_selector,
                export_format_selector,
                export_btn,
                export_file,
                export_hint_html,
                lang_toggle_btn,
                footer_html,
            ],
            api_name=False,
        )

    return demo


if __name__ == "__main__":
    _start_stale_task_reaper()
    demo = build_demo()
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))
    launch_kwargs = _build_launch_kwargs(host, port)
    demo.queue().launch(**launch_kwargs)
