import asyncio
import contextvars
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
from functools import partial
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

import gradio as gr
from dotenv import load_dotenv
from hydra import compose, initialize_config_dir
from omegaconf import DictConfig
from prompt_patch import apply_prompt_patch
from src.config.search_policy import (
    apply_explicit_budget_overrides,
    apply_user_search_env_precedence,
)
from src.config.settings import expose_sub_agents_as_tools
from src.cache.result_cache import ResultCache
from src.core.deep_efficiency import resolve_research_intensity
from src.core.pipeline import create_pipeline_components, execute_task_pipeline
from src.io.report_presentation import prepare_user_facing_report
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


def _load_logo_data_uri(logo_name: str = "diting_logo.png") -> str:
    """加载本地 Logo 并转换为 data URI，避免依赖外部静态资源服务。"""
    logo_path = Path(__file__).resolve().parents[2] / "assets" / logo_name
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
    # 应用自定义系统提示补丁（注入谛听身份）
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
SKILLS_PACKAGE_DEFAULT_FILENAME = "diting.zip"
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
EXPORT_FORMAT_EXTENSIONS = {"md": ".md", "pdf": ".pdf", "docx": ".docx"}
EXPORT_FILENAME_PREFIX = os.getenv("EXPORT_FILENAME_PREFIX", "diting-conclusion")
EXPORT_OUTPUT_DIR = Path(
    os.getenv(
        "EXPORT_OUTPUT_DIR",
        str(Path(tempfile.gettempdir()) / "diting-exports"),
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
DEFAULT_LANG = LANG_CN

_UI_LANG: "contextvars.ContextVar[str]" = contextvars.ContextVar(
    "ui_lang", default=DEFAULT_LANG
)

I18N = {
    LANG_EN: {
        "page_title": "Diting - Deep Research",
        "nav_brand_text": "Diting Deep Research",
        "hero_title": "Deep Research, Insight into the Future",
        "hero_subtitle": "Verifiable search and reasoning for research tasks.",
        "input_placeholder": "Enter your research question...",
        "btn_stop": "Stop",
        "btn_run": "Start Research",
        "btn_settings": "Settings",
        "settings_modal_title": "Settings",
        "btn_close": "Close",
        "output_waiting": "### Ready when you are\n\nType a question above and click **Start Research**.\n\nProgress, sources, and the final report will appear here.",
        "output_running_hint": "Research started — analyzing your question. Sources and findings will appear here.",
        "options_title": "Options / Advanced Settings",
        "mode_label": "Search Mode",
        "mode_info": "Daily: Balanced. Need multi-source checks: Verified. Items marked Advanced are optional.",
        "search_profile_label": "Search Source Strategy",
        "search_profile_info": "Active default: {profile}. Switch only if that provider is configured.",
        "search_result_num_label": "Results per Search",
        "search_result_num_info": "Hits fetched per round · default {n}",
        "verification_rounds_label": "Min Search Rounds (verified mode)",
        "verification_rounds_info": "Verified mode only",
        "output_detail_label": "Output Length",
        "output_detail_info": "Longer output costs more tokens · default {level}",
        "settings_hint": "Not sure what these options do? Pick a preset below — Balanced is recommended for everyday use.",
        "preset_quick": "Quick look",
        "preset_balanced": "Balanced",
        "preset_deep": "Deep research",
        "settings_preset_filled": 'Preset "{preset}" filled in · click Apply to review the summary',
        "references_heading": "References",
        "btn_settings_reset": "Restore defaults",
        "btn_settings_apply": "Apply",
        "settings_applied": "Applied · {summary} — takes effect from the next run",
        "settings_reset": "Defaults restored · {summary} — takes effect from the next run",
        "settings_summary_join": " / ",
        "settings_summary_rounds": "{n} per round",
        "footer_text": "Generated by AI. Please verify key information.",
        "lang_toggle_btn": "中文",
        "export_file_label": "Download exported conclusion",
        "skills_download_fallback": SKILLS_DOWNLOAD_FALLBACK_HINT_EN,
        "skills_download_btn": SKILLS_DOWNLOAD_BUTTON_TEXT_EN,
        "skills_download_copied": SKILLS_DOWNLOAD_COPIED_TEXT_EN,
        "skills_download_title": "Download Skills",
        "progress_search": "Search",
        "progress_found": "Found {n} results",
        "progress_provider_mode": "Provider mode",
        "progress_sources_hit": "Sources hit",
        "progress_code_exec": "Code execution",
        "progress_output": "Output",
        "progress_executed": "Executed",
        "progress_untitled": "Untitled",
        "scrape_status_failed": "Failed",
        "scrape_status_done": "Done",
        "progress_process_summary": "Thinking & search process (done — click to expand)",
        "btn_settings_title": "Settings",
        "btn_export_title": "Export",
        "btn_stop_title": "Stop research",
        "btn_run_title": "Start research",
        "output_detail_labels": {
            "compact": "Compact",
            "balanced": "Balanced",
            "detailed": "Detailed",
        },
    },
    LANG_CN: {
        "page_title": "谛听 - 深度研究",
        "nav_brand_text": "谛听 深度研究",
        "hero_title": "深度研究，洞察未来",
        "hero_subtitle": "用可验证的检索与推理完成研究任务。",
        "input_placeholder": "请输入你的研究问题...",
        "btn_stop": "停止",
        "btn_run": "开始研究",
        "btn_settings": "设置",
        "settings_modal_title": "设置",
        "btn_close": "关闭",
        "output_waiting": "### 准备就绪\n\n在上方输入问题，点击 **开始研究**。\n\n进度、来源与最终报告会显示在这里。",
        "output_running_hint": "研究已启动，正在分析问题；来源与结论会陆续显示在这里。",
        "options_title": "Options / 高级配置",
        "mode_label": "检索模式",
        "mode_info": "日常用「均衡」；要多源交叉验证用「交叉验证」。带「高级」的一般不用。",
        "search_profile_label": "检索源策略",
        "search_profile_info": "当前生效的默认值：{profile}。仅在对应检索源已配置时才建议更换。",
        "search_result_num_label": "单轮检索条数",
        "search_result_num_info": "每轮检索返回条数 · 默认 {n}",
        "verification_rounds_label": "最少检索轮次（verified 生效）",
        "verification_rounds_info": "仅 verified 模式生效",
        "output_detail_label": "输出篇幅",
        "output_detail_info": "篇幅越长越耗 token · 默认 {level}",
        "settings_hint": "不知道这些参数怎么选？点下方预设即可，日常使用推荐「均衡推荐」。",
        "preset_quick": "快速了解",
        "preset_balanced": "均衡推荐",
        "preset_deep": "深度研究",
        "settings_preset_filled": "已填入「{preset}」配置 · 点「应用」可查看配置摘要",
        "references_heading": "参考来源",
        "btn_settings_reset": "恢复默认",
        "btn_settings_apply": "应用",
        "settings_applied": "已应用 · {summary} · 下一次运行生效",
        "settings_reset": "已恢复默认 · {summary} · 下一次运行生效",
        "settings_summary_join": " · ",
        "settings_summary_rounds": "每轮 {n} 条",
        "footer_text": "由 AI 生成，请对关键信息进行复核。",
        "lang_toggle_btn": "English",
        "export_file_label": "下载导出文件",
        "skills_download_fallback": SKILLS_DOWNLOAD_FALLBACK_HINT_CN,
        "skills_download_btn": SKILLS_DOWNLOAD_BUTTON_TEXT_CN,
        "skills_download_copied": SKILLS_DOWNLOAD_COPIED_TEXT_CN,
        "skills_download_title": "下载 Skills",
        "progress_search": "检索",
        "progress_found": "找到 {n} 条结果",
        "progress_provider_mode": "检索模式",
        "progress_sources_hit": "命中搜索源",
        "progress_code_exec": "代码执行",
        "progress_output": "输出",
        "progress_executed": "已执行",
        "progress_untitled": "无标题",
        "scrape_status_failed": "抓取失败",
        "scrape_status_done": "抓取完成",
        "progress_process_summary": "思考与检索过程（已完成，点击展开）",
        "btn_settings_title": "设置",
        "btn_export_title": "导出",
        "btn_stop_title": "停止研究",
        "btn_run_title": "开始研究",
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

# (chinese, english) per option key — the settings modal is the only consumer,
# so labels travel with the language toggle instead of staying Chinese-only.
RESEARCH_MODE_LABELS = {
    "balanced": ("均衡（推荐）", "Balanced (recommended)"),
    "verified": ("交叉验证", "Cross-verified"),
    "research": ("深挖研究", "Deep research"),
    "production-web": ("生产网页（高级）", "Web producer (advanced)"),
    "quota": ("配额优先（高级）", "Quota-saver (advanced)"),
    "thinking": ("强推理（高级）", "Heavy reasoning (advanced)"),
}

SEARCH_PROFILE_LABELS = {
    "searxng-first": ("优先 SearXNG", "Prefer SearXNG"),
    "serp-first": ("优先 SERP", "Prefer SERP API"),
    "multi-route": ("多路融合", "Multi-route merge"),
    "parallel": ("并行更广", "Parallel broad"),
    "parallel-trusted": ("并行可信源", "Parallel trusted sources"),
    "searxng-only": ("仅 SearXNG", "SearXNG only"),
}

# 每个选项擅长什么——设置弹窗里跟随选中项展示（中/英与 LABELS 同序）
RESEARCH_MODE_DESCRIPTIONS = {
    "balanced": (
        "质量、速度与额度最均衡，日常问题首选。",
        "Balanced quality, speed and quota; best for everyday questions.",
    ),
    "verified": (
        "多轮交叉验证后才下结论，适合事实核查与重要决策。",
        "Cross-verifies across rounds before concluding; best for fact-checking.",
    ),
    "research": (
        "质量优先、允许跑得更久更深，适合复杂调研。",
        "Quality-first and willing to run longer; best for complex topics.",
    ),
    "production-web": (
        "生产稳态预设：不压缩上下文，输出更完整。",
        "Production preset: no context compression, fuller output.",
    ),
    "quota": (
        "全部切快速模型并压缩上下文，最省额度但深度下降。",
        "Forces fast models and compression; cheapest but less depth.",
    ),
    "thinking": (
        "纯思考问答，不联网检索。",
        "Reasoning only, no web search.",
    ),
}

SEARCH_PROFILE_DESCRIPTIONS = {
    "searxng-first": (
        "免费源优先，失败自动回退付费源。",
        "Free engines first, automatic fallback to paid APIs.",
    ),
    "serp-first": (
        "付费 API 优先，结果更稳定。",
        "Paid APIs first for maximum stability.",
    ),
    "multi-route": (
        "多路串行聚合去重，覆盖更全。",
        "Serial multi-route merge with dedup; broader coverage.",
    ),
    "parallel": (
        "多路并发取最快结果，速度优先。",
        "Parallel routes, fastest response wins.",
    ),
    "parallel-trusted": (
        "并发检索 + 高可信度信源补检，敏感问题最稳。",
        "Parallel plus trusted-source recheck; most reliable for sensitive topics.",
    ),
    "searxng-only": (
        "只用 SearXNG，零付费成本。",
        "SearXNG only; zero paid-API cost.",
    ),
}


def _localized_labels(labels: dict, lang: str) -> list:
    idx = 1 if lang == LANG_EN else 0
    return [(value[idx], key) for key, value in labels.items()]


def _option_hint_html(descriptions: dict, key: str, lang: str) -> str:
    idx = 1 if lang == LANG_EN else 0
    entry = descriptions.get(key) or ("", "")
    desc = str(entry[idx]).strip()
    if not desc:
        return ""
    return f'<div class="option-hint">{html.escape(desc, quote=False)}</div>'


def _label_for(labels: dict, key: str, lang: str) -> str:
    entry = labels.get(key)
    if not entry:
        return key
    return entry[1] if lang == LANG_EN else entry[0]


def _build_setting_infos(lang: str) -> dict:
    """Field hints that quote the values actually in effect, not literals."""
    i18n = I18N[lang]
    return {
        "mode_info": i18n["mode_info"],
        "search_profile_info": i18n["search_profile_info"].format(
            profile=_label_for(
                SEARCH_PROFILE_LABELS,
                _normalize_search_profile(DEFAULT_SEARCH_PROFILE),
                lang,
            )
        ),
        "search_result_num_info": i18n["search_result_num_info"].format(
            n=_normalize_search_result_num(DEFAULT_SEARCH_RESULT_NUM)
        ),
        "verification_rounds_info": i18n["verification_rounds_info"],
        "output_detail_info": i18n["output_detail_info"].format(
            level=i18n["output_detail_labels"][
                _normalize_output_detail_level(DEFAULT_OUTPUT_DETAIL_LEVEL)
            ]
        ),
    }


def _build_settings_summary(
    lang: str,
    mode: str,
    output_detail_level: str,
    search_profile: str,
    search_result_num: int,
) -> str:
    i18n = I18N[lang]
    return i18n["settings_summary_join"].join(
        [
            _label_for(RESEARCH_MODE_LABELS, _normalize_research_mode(mode), lang),
            i18n["output_detail_labels"][
                _normalize_output_detail_level(output_detail_level)
            ],
            _label_for(
                SEARCH_PROFILE_LABELS,
                _normalize_search_profile(search_profile),
                lang,
            ),
            i18n["settings_summary_rounds"].format(
                n=_normalize_search_result_num(search_result_num)
            ),
        ]
    )


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
    "scrape_url": "网页抓取",
    "scrape_and_extract_info": "信息提取",
    "show_text": "文本展示",
}


def _tool_display_name(raw_name: str) -> str:
    return TOOL_DISPLAY_NAMES.get(raw_name, raw_name)


def _progress_copy(key: str, *, lang: Optional[str] = None, **fmt) -> str:
    """UI progress strings; follow explicit lang, else current UI lang ContextVar."""
    try:
        current = _UI_LANG.get()
    except LookupError:
        current = DEFAULT_LANG
    resolved = lang if lang in I18N else (current if current in I18N else DEFAULT_LANG)
    template = (
        I18N.get(resolved, I18N[DEFAULT_LANG]).get(key)
        or I18N[DEFAULT_LANG].get(key)
        or key
    )
    try:
        return str(template).format(**fmt)
    except Exception:
        return str(template)


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
        "SEARCH_PROVIDER_ORDER_STRICT": "1",
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
    raw = str(level).strip()
    alias = {
        "compact": "compact",
        "balanced": "balanced",
        "detailed": "detailed",
        "精简": "compact",
        "适中": "balanced",
        "详细": "detailed",
        "详细（默认）": "detailed",
    }
    for key, label in (OUTPUT_DETAIL_LEVEL_LABELS or {}).items():
        alias[str(label).strip()] = key
        alias[str(label).strip().lower()] = key
    # i18n dropdown may pass localized labels too
    for lang_map in (I18N or {}).values():
        labels = (lang_map or {}).get("output_detail_labels") or {}
        for key, label in labels.items():
            alias[str(label).strip()] = key
            alias[str(label).strip().lower()] = key
    normalized_level = alias.get(raw) or alias.get(raw.lower())
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

    overrides = apply_explicit_budget_overrides(overrides)

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
    search_env = apply_user_search_env_precedence(
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
    with _CANCEL_LOCK:
        _ACTIVE_CANCEL_EVENTS[workflow_id] = cancel_event
    first_non_heartbeat_logged = False
    event_counts: Dict[str, int] = {}
    stage_state: Dict[str, Any] = {
        "phase": "初始化",
        "search_round": 0,
        "agent_name": "",
        "detail": "等待开始",
        "last_tool": "",
        "updated_at": time.time(),
    }

    def _touch_stage(
        phase: Optional[str] = None,
        *,
        detail: Optional[str] = None,
        agent_name: Optional[str] = None,
        last_tool: Optional[str] = None,
        search_round_increment: bool = False,
    ) -> None:
        if phase:
            stage_state["phase"] = phase
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
                detail=data.get("detail"),
                agent_name=data.get("agent_name"),
            )
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
                        effective_config={
                            "mode": resolved_mode,
                            "search_profile": resolved_search_profile,
                            "search_result_num": resolved_search_result_num,
                            "verification_min_search_rounds": (
                                resolved_verification_min_rounds
                            ),
                            "output_detail_level": resolved_output_detail_level,
                            "research_intensity": resolve_research_intensity(
                                profile_cache["cfg"]
                            ),
                        },
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
        with _CANCEL_LOCK:
            _ACTIVE_CANCEL_EVENTS.pop(workflow_id, None)
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
            "search_round": 0,
            "detail": "等待开始",
            "agent_name": "",
            "last_tool": "",
            "started_at": time.time(),
            "updated_at": 0.0,
        },
    }


_RUNTIME_PHASE_LABELS = {
    "初始化": "准备中",
    "排队": "排队中",
    "推理": "正在分析",
    "检索": "正在检索",
    "总结": "正在生成报告",
    "校验": "正在交叉校验",
    "工具调用": "正在调用工具",
    "并行工具": "正在并行处理",
    "线索追踪": "正在追踪线索",
    "异常": "研究中断",
    "已取消": "已停止",
    "完成": "研究完成",
}

# agent 心跳里的内部措辞 → 用户可读文案；返回 "" 表示与阶段重复、不展示
_RUNTIME_DETAIL_DROP = {
    "等待开始",
    "模型推理中",
    "主模型推理中",
    "内容生成中",
    "交叉校验中（无工具）",
    "进入最终总结阶段",
    "最终总结生成中",
    "输出前交叉校验汇总中",
    "执行出现错误",
    "任务执行失败",
    "任务已取消",
    "任务已完成",
    "最终结果已生成",
    "主流程已启动",
    "追踪研究线索",
}

# 与阶段标签重复的模板化措辞：{agent} 已启动 / 执行工具 X / 并行执行 N 个工具调用
_RUNTIME_DETAIL_DROP_RE = re.compile(
    r"^(?:.+ 已启动|执行工具 \S+|并行执行 \d+ 个工具调用)$"
)


def _clean_runtime_detail(detail: str) -> str:
    text = str(detail or "").strip()
    if not text:
        return ""
    retry_match = re.match(r"^最终总结生成中（第 (\d+)/(\d+) 次）$", text)
    if retry_match:
        if retry_match.group(1) == "1":
            return ""
        return f"第 {retry_match.group(1)} 次重试生成总结"
    if text in _RUNTIME_DETAIL_DROP or _RUNTIME_DETAIL_DROP_RE.match(text):
        return ""
    if text == "交叉校验降级重试":
        return "降级重试"
    if text == "命中交叉校验门槛，追加检索指令":
        return "校验未通过，补充检索"
    tool_running = re.match(r"^(.+?) 执行中$", text)
    if tool_running:
        return tool_running.group(1)
    return text


def _format_runtime_status_label(state: dict) -> str:
    runtime_stage = state.get("runtime_stage") or {}
    phase = str(runtime_stage.get("phase") or "执行中")
    search_round = int(runtime_stage.get("search_round") or 0)
    detail = _clean_runtime_detail(str(runtime_stage.get("detail") or ""))
    parts = [_RUNTIME_PHASE_LABELS.get(phase, phase)]
    if search_round > 0:
        parts.append(f"已完成 {search_round} 次检索")
    if detail:
        parts.append(detail)
    return " · ".join(parts)


def _format_elapsed_value(started_at: float) -> str:
    seconds = max(0, int(time.time() - float(started_at or 0)))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _runtime_status_markup(state: dict) -> str:
    """运行态状态卡：状态文案 + 耗时（耗时由前端每秒自增）。"""
    started_at = float((state.get("runtime_stage") or {}).get("started_at") or 0.0)
    elapsed_html = ""
    if started_at > 0:
        # 起始时间放进 style 自定义属性：Markdown 净化会剥掉 data-*，
        # 只有 class/style 能活着到达前端，供每秒自增的计时脚本读取。
        elapsed_html = (
            '<span class="runtime-elapsed">已用 <span class="runtime-elapsed-value" '
            f'style="--start-ts:{int(started_at)}">'
            f"{_format_elapsed_value(started_at)}</span></span>"
        )
    return _spinner_markup(_format_runtime_status_label(state), elapsed_html)


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
        lines.append(
            f'<span class="search-query">{_progress_copy("progress_search")}: "{query}"</span>'
        )
        lines.append("</div>")

    # Results count
    if results:
        lines.append(
            f'<div class="search-count">≡ {_progress_copy("progress_found", n=len(results))}</div>'
        )
        if provider_mode:
            lines.append(
                f'<div class="search-count">{_progress_copy("progress_provider_mode")}: <strong>{provider_mode}</strong></div>'
            )
        if providers_with_results:
            providers_text = ", ".join(providers_with_results)
            lines.append(
                f'<div class="search-count">{_progress_copy("progress_sources_hit")}: <strong>{providers_text}</strong></div>'
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
            title = item.get("title") or _progress_copy("progress_untitled")
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
        line_parts.append(
            f'{_progress_copy("progress_search")}: "{html.escape(truncated_query)}"'
        )
    if result_count is not None:
        line_parts.append(_progress_copy("progress_found", n=result_count))
    if provider_mode:
        line_parts.append(
            f'{_progress_copy("progress_provider_mode")}: {html.escape(provider_mode)}'
        )
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
        lines.append(
            f'<span class="search-query">{_progress_copy("progress_search")}: "{query}"</span>'
        )
        lines.append("</div>")

    # Results count
    if results:
        lines.append(
            f'<div class="search-count">≡ {_progress_copy("progress_found", n=len(results))}</div>'
        )

        # Results list
        lines.append('<div class="search-results">')
        for item in results[:10]:  # Limit to 10 results
            title = item.get("title") or _progress_copy("progress_untitled")
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
        line_parts.append(
            f'{_progress_copy("progress_search")}: "{html.escape(truncated_query)}"'
        )
    if result_count is not None:
        line_parts.append(_progress_copy("progress_found", n=result_count))
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
        lines.append(
            f'<div class="scrape-status error">{_progress_copy("scrape_status_failed")}</div>'
        )
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
        lines.append(
            f'<div class="scrape-status success">{_progress_copy("scrape_status_done")}</div>'
        )
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
    r"(?:参考文献|参考资料|参考来源|引用|references?|sources?)"
    r"(?:[ \t]*\*+)?[ \t]*$"
)
_REFERENCE_ENTRY_RE = re.compile(r"\[(\d{1,4})\][^\n]*?(https?://\S+)")
# 有序列表形态的参考文献条目（``3. 标题 …`` / ``3) 标题 …``）：LLM 通常按此写 References
_REFERENCE_LIST_ENTRY_RE = re.compile(r"(?m)^[ \t]*(\d{1,4})[.)][ \t]+(\S[^\n]*)$")
# 无 scheme 的裸域名：仅用于参考文献区补 https 链接，限定常见 TLD，
# 避免把 DOI（10.1007/…）、arXiv 号（2507.09911）当成域名。
_BARE_DOMAIN_RE = re.compile(
    r"(?<![\w./-])((?:www\.)?(?:[a-z0-9-]+\.)+"
    r"(?:com|org|net|edu|gov|io|ai|co|uk|de|jp|fr|cn|au|ca|us|info|me|dev|app|tech|work|xyz|tv|news)"
    r"\b(?:/[^\s，；）)】\]]*)?)",
    re.I,
)
# 规范化参考文献条目之间的换行：确保每条 [N] 前有双换行，Markdown 渲染时才能正确分行
_REFERENCE_NEWLINE_RE = re.compile(r"(?<!\n)\n(\[\d{1,4}\])")
_CITATION_RE = re.compile(r"\[(\d{1,4})\]")
_CODE_SEGMENT_RE = re.compile(r"```[\s\S]*?```|`[^`\n]+`")
_REFERENCE_URL_TRAILING = ".,;:)]>。，、；：）】》」’”"


def _reference_url_in_entry(body: str) -> str:
    """条目里的来源 URL：优先完整 http(s)，否则把裸域名补成 https。"""
    full = re.search(r"https?://\S+", body or "")
    if full:
        return full.group(0).rstrip(_REFERENCE_URL_TRAILING)
    bare = _BARE_DOMAIN_RE.search(body or "")
    if bare:
        return "https://" + bare.group(1).rstrip(_REFERENCE_URL_TRAILING)
    return ""


def _linkify_reference_citations(markdown_text: str) -> str:
    """将研究总结中形如 ``[N]`` 的引用标记替换为指向文末 References 区真实 URL 的可点击链接。"""
    if not markdown_text:
        return markdown_text
    heading_match = _REFERENCES_HEADING_RE.search(markdown_text)
    if not heading_match:
        return markdown_text

    body = markdown_text[: heading_match.start()]
    head_part = markdown_text[heading_match.start() : heading_match.end()]
    references_tail = markdown_text[heading_match.end() :]
    # References 之后常还有兄弟章节（如 深入了解）：只有标题到下一个标题之间
    # 才是来源条目区，否则后续编号列表、裸域名会被误当成来源。
    next_heading = re.search(r"(?m)^#{1,6}[ \t]", references_tail)
    if next_heading:
        references_block = references_tail[: next_heading.start()]
        rest = references_tail[next_heading.start() :]
    else:
        references_block, rest = references_tail, ""

    id_to_url: Dict[str, str] = {}
    for entry in _REFERENCE_ENTRY_RE.finditer(references_block):
        ref_id = entry.group(1)
        raw_url = entry.group(2).rstrip(_REFERENCE_URL_TRAILING)
        if ref_id and raw_url and ref_id not in id_to_url:
            id_to_url[ref_id] = raw_url
    # 有序列表条目按序号入表，正文 [N] 才能指向对应来源
    for entry in _REFERENCE_LIST_ENTRY_RE.finditer(references_block):
        ref_id = entry.group(1)
        if ref_id in id_to_url:
            continue
        url = _reference_url_in_entry(entry.group(2))
        if url:
            id_to_url[ref_id] = url

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

    # 裸域名补成 Markdown 链接，来源条目才有可点的落点
    def _link_bare_domain(match: "re.Match[str]") -> str:
        token = match.group(1)
        return f"[{token}](https://{token.rstrip(_REFERENCE_URL_TRAILING)})"

    references_block = _BARE_DOMAIN_RE.sub(_link_bare_domain, references_block)
    # 规范化参考文献条目之间的换行，确保每条 [N] 独占一行
    references_block = _REFERENCE_NEWLINE_RE.sub(r"\n\n\1", references_block)
    return "".join(pieces) + head_part + references_block + rest


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
    """剥离最终总结里来自 OutputFormatter 的调试分段（Final Answer / Extracted / Token Usage）。

    Also drops truncated ``https://www`` stub reference lines so Web export
    does not look mid-cut.
    """
    if not text:
        return text
    cleaned = _DIAGNOSTIC_FINAL_ANSWER_HEADER_RE.sub("", text, count=1)
    cleaned = _DIAGNOSTIC_TRUNCATE_RE.sub("", cleaned)
    # Pricing / token dump variants that lack the exact header
    cleaned = re.sub(
        r"(?ms)\n?-{5,}.*?\b(?:Pricing is disabled|Total Input Tokens)\b.*",
        "",
        cleaned,
    )
    # Drop dangling incomplete URL stubs in References
    kept = []
    for line in cleaned.splitlines():
        s = line.strip()
        if re.fullmatch(r"(?:\d+\.\s*)?https?://(?:www\.)?", s):
            continue
        if re.search(r"https?://[\w\-]+$", s) and s.rstrip("/").count(".") == 0:
            # e.g. https://www with no TLD
            continue
        kept.append(line)
    cleaned = "\n".join(kept)
    return cleaned.rstrip()


def _prepare_user_facing_report_safe(
    text: str, detail_level: Optional[str] = None
) -> str:
    """Prefer agent presentation pipeline; never fail the Gradio stream.

    Threads the stream's output_detail_level into prepare_user_facing_report so
    compact mode stays compact (no auto 内容分析 / Mermaid topology). The
    pipeline is a no-op for reports the orchestrator already prepared.
    """
    raw = str(text or "")
    resolved_detail = _normalize_output_detail_level(detail_level)
    try:
        return prepare_user_facing_report(raw, detail_level=resolved_detail)
    except Exception:
        return _strip_diagnostic_markers(raw)


def _decorate_report_for_web(
    markdown_text: str, detail_level: Optional[str] = None
) -> str:
    """Add lightweight HTML wrappers so Gradio renders clearer report chrome.

    Keeps Markdown headings intact (avoid replacing ``##`` with raw ``<h2>``)
    so Gradio's Markdown renderer does not drop subsequent body formatting.
    Citation chip class is applied after linkify (caller order).
    """
    text = str(markdown_text or "")
    if not text.strip():
        return text

    resolved_detail = _normalize_output_detail_level(detail_level)
    if resolved_detail == "compact":
        # Hard guard: never show deep-dive folds on compact even if upstream leaked them.
        text = re.sub(
            r"(?ms)^##\s*[^\n]*(?:深入了解|深入分析)[^\n]*\n+.*?(?=^##\s|\Z)",
            "",
            text,
        )

    # 1) Glance card for consumer ## 结论
    def _wrap_glance(match: "re.Match[str]") -> str:
        body = (match.group(1) or "").strip()
        conf_m = re.search(r"<!--\s*confidence:(high|mid|low)\s*-->", body)
        level = conf_m.group(1) if conf_m else "mid"
        body = re.sub(r"<!--\s*confidence:(?:high|mid|low)\s*-->\s*", "", body)
        label_m = re.search(r"\*\*?置信度：([高中低])[。.]?\*\*?", body)
        if label_m:
            label = f"置信度：{label_m.group(1)}"
        else:
            label = {
                "high": "置信度：高",
                "mid": "置信度：中",
                "low": "置信度：低",
            }.get(level, "置信度：中")
        conf = (
            f'<span class="confidence-badge confidence-{level}">'
            f"{html.escape(label, quote=False)}</span>"
        )
        # Strip every leftover confidence label (bold/plain, whole line or inline),
        # then keep the answer in ONE escaped <div>: Gradio Markdown drops bare
        # text / <p> siblings inside HTML blocks.
        body = re.sub(
            r"(?m)^[ \t]*\*{0,2}置信度：[高中低][。.]?\*{0,2}[ \t]*$"
            r"|\*{0,2}置信度：[高中低][。.]?\*{0,2}",
            "",
            body,
        ).strip()
        answer = body.strip() if body.strip() else "（结论正文缺失，请展开过程或重试）"
        # Collapse excessive blank lines but keep readable line breaks as <br>.
        answer_html = html.escape(answer, quote=False).replace("\n", "<br>")
        return (
            f'<div class="report-glance">'
            f'<div class="report-glance-meta">'
            f'<span class="report-glance-kicker">结论</span>'
            f"{conf}"
            f"</div>"
            f'<p class="report-glance-body">{answer_html}</p>'
            f"</div>\n\n"
        )

    text = re.sub(
        r"(?ms)^##\s*结论\s*\n+(.*?)(?=^##\s|\Z)",
        _wrap_glance,
        text,
        count=1,
    )

    # 2) Legacy TL;DR callout (skip if already glance-wrapped; exclude bare 结论)
    def _wrap_tldr(match: "re.Match[str]") -> str:
        title = html.escape(match.group(1).strip(), quote=False)
        body = (match.group(2) or "").strip()
        conf = ""
        conf_m = re.search(
            r"(高|中|低)\s*置信|confidence\s*[:=]?\s*(high|medium|low|\d+%?)",
            match.group(1) + "\n" + body,
            re.I,
        )
        if conf_m:
            label = html.escape(conf_m.group(0), quote=False)
            level = "mid"
            if re.search(r"高|high", label, re.I):
                level = "high"
            elif re.search(r"低|low", label, re.I):
                level = "low"
            conf = f'<span class="confidence-badge confidence-{level}">{label}</span>'
        return (
            f'<div class="report-tldr">\n\n'
            f'<div class="report-tldr-head"><strong>{title}</strong>{conf}</div>\n\n'
            f"{body}\n\n"
            f"</div>\n\n"
        )

    if 'class="report-glance"' not in text:
        text = re.sub(
            r"(?ms)^##\s*([^\n]*(?:TL;?DR|总览|Executive Summary)[^\n]*)\n+(.*?)(?=^##\s|\Z)",
            _wrap_tldr,
            text,
            count=1,
        )

    # 3) Conflict tag on heading (keep open, short)
    text = re.sub(
        r"(?m)^(##\s*[^\n]*(?:争议与不确定|冲突|不确定|Conflicts?|Uncertainties?)[^\n]*)$",
        r'\1 <span class="conflict-tag">冲突/不确定</span>',
        text,
        count=1,
    )

    # 4) Fold heavy sections (evidence / deep dive). Nested headings are ###.
    def _fold_section(match: "re.Match[str]") -> str:
        heading = match.group(1).strip()
        body = (match.group(2) or "").strip()
        if not body:
            return ""  # never show an empty fold
        n_links = len(re.findall(r"https?://", body))
        n_items = len(re.findall(r"(?m)^\s*(?:\d+\.|[-*•])\s+", body))
        n = n_links or n_items
        count_hint = f"（{n}）" if n else ""
        title = html.escape(re.sub(r"^##\s*", "", heading), quote=False)
        # Mermaid card inside fold body
        body = re.sub(
            r"(?ms)(###\s*[^\n]*(?:关系拓扑|Relationship Map|拓扑)[^\n]*\n+)(```mermaid\n.*?```)",
            r'<div class="mermaid-card">\n\n\1\2\n\n</div>\n\n',
            body,
            count=1,
        )
        return (
            f'<details class="report-fold">\n'
            f"<summary>{title}{count_hint}</summary>\n\n"
            f"{body}\n\n"
            f"</details>\n\n"
        )

    text = re.sub(
        r"(?ms)^(##\s*[^\n]*(?:证据与来源|证据和来源|深入了解|深入分析)[^\n]*)\n+(.*?)(?=^##\s|\Z)",
        _fold_section,
        text,
    )

    text = text.replace('class="ref-citation"', 'class="ref-citation ref-chip"')
    return text


def _keep_last_report_glance(decorated_blocks: List[str]) -> List[str]:
    """Keep a single consumer glance card when streamed summaries differ only by confidence.

    Balanced/detailed merge uses ``all_unique``, so an early mid-confidence reshape and a
    later high-confidence reshape can both survive. Rendering both produces duplicate
    「结论 / 置信度」 chrome in the a11y tree. Prefer the latest glance-bearing block.
    """
    if len(decorated_blocks) <= 1:
        return decorated_blocks
    glance_indexes = [
        idx
        for idx, block in enumerate(decorated_blocks)
        if 'class="report-glance"' in str(block or "")
    ]
    if len(glance_indexes) <= 1:
        return decorated_blocks
    drop = set(glance_indexes[:-1])
    return [block for idx, block in enumerate(decorated_blocks) if idx not in drop]


_REPORT_SOURCE_TOOL_NAMES = {"google_search", "sogou_search"}
_REPORT_SCRAPE_TOOL_NAMES = {
    "scrape",
    "scrape_website",
    "scrape_webpage",
    "scrape_and_extract_info",
}
# 参考来源上限：论文式文末列表，太多反而淹没阅读
MAX_REPORT_SOURCES = 30


def _normalize_source_url(url: Any) -> str:
    candidate = str(url or "").strip()
    if not candidate:
        return ""
    if not candidate.lower().startswith(("http://", "https://")):
        return ""
    return candidate


def _source_display_title(title: Any, url: str) -> str:
    # 搜索结果标题常带 <b> 等高亮标签，剥掉避免破坏 Markdown 链接文本
    text = re.sub(r"<[^>]+>", "", str(title or ""))
    text = re.sub(r"\s+", " ", text).strip()
    if text:
        return text[:120]
    host = urlparse(url).netloc or url
    return host


def _collect_report_sources(state: dict) -> List[Dict[str, str]]:
    """汇总本轮研究实际命中/访问过的来源，按首次出现顺序去重。"""
    sources: List[Dict[str, str]] = []
    seen_urls = set()

    def _add(url: Any, title: Any = "") -> None:
        normalized = _normalize_source_url(url)
        if not normalized or normalized in seen_urls:
            return
        seen_urls.add(normalized)
        sources.append(
            {"url": normalized, "title": _source_display_title(title, normalized)}
        )

    for agent_id in (state or {}).get("agent_order", []):
        agent = (state or {}).get("agents", {}).get(agent_id, {})
        for call_id in agent.get("tool_call_order", []):
            call = agent.get("tools", {}).get(call_id, {})
            tool_name = call.get("tool_name", "")
            tool_input = call.get("input", {})
            tool_output = call.get("output", {})
            if tool_name in _REPORT_SOURCE_TOOL_NAMES:
                result_data: Dict[str, Any] = {}
                result_payload = (
                    tool_output.get("result", "")
                    if isinstance(tool_output, dict)
                    else ""
                )
                if isinstance(result_payload, str) and result_payload.strip():
                    try:
                        parsed = json.loads(result_payload)
                        if isinstance(parsed, dict):
                            result_data = parsed
                    except json.JSONDecodeError:
                        result_data = {}
                elif isinstance(result_payload, dict):
                    result_data = result_payload
                if not result_data and isinstance(tool_output, dict):
                    result_data = tool_output
                organic = result_data.get("organic")
                if not isinstance(organic, list):
                    organic = result_data.get("Pages")
                if isinstance(organic, list):
                    for item in organic:
                        if not isinstance(item, dict):
                            continue
                        _add(
                            item.get("link") or item.get("url"),
                            item.get("title") or item.get("siteName"),
                        )
            elif tool_name in _REPORT_SCRAPE_TOOL_NAMES and isinstance(
                tool_input, dict
            ):
                _add(tool_input.get("url") or tool_input.get("link"))
            if len(sources) >= MAX_REPORT_SOURCES:
                return sources
    return sources


def _build_references_section(sources: List[Dict[str, str]]) -> List[str]:
    if not sources:
        return []
    heading = _progress_copy("references_heading")
    lines = ["", "\n---\n", f"### {heading}\n"]
    for idx, src in enumerate(sources, 1):
        title = str(src.get("title") or src["url"]).replace("[", "(").replace("]", ")")
        lines.append(f"[{idx}] [{title}]({src['url']})")
        # 条目间空行：相邻行会被 Markdown 合并成一段，来源挤在一起无法逐条阅读
        lines.append("")
    return lines


def _build_summary_section(
    final_summary_blocks: List[str],
    output_detail_level: Optional[str] = None,
) -> List[str]:
    if not final_summary_blocks:
        return []
    # 前置空字符串项：确保和上一个 HTML block（如 search-step-board 的 </div>）之间
    # 有一个空行，否则 CommonMark 会把 `## 📋 研究总结` 视为 HTML block 的延续，
    # 导致 `## ` 字面显示而非作为标题渲染。
    # Glance card already leads with 结论; keep a quieter label.
    lines = ["", "### 研究报告\n\n"]
    resolved_detail = _normalize_output_detail_level(output_detail_level)
    # 先剥离 OutputFormatter 注入的调试分段标记，再做 LaTeX → Markdown 规范化
    sanitized = (
        _prepare_user_facing_report_safe(block, detail_level=resolved_detail)
        for block in final_summary_blocks
    )
    normalized = (_normalize_latex_like_markup(block) for block in sanitized)
    rewritten = (_humanize_pipeline_fallback(block) for block in normalized)
    # linkify first, then decorate so ref-chip class lands on citation anchors
    linkified = [_linkify_reference_citations(block) for block in rewritten]
    decorated = [
        _decorate_report_for_web(block, detail_level=resolved_detail)
        for block in linkified
    ]
    lines.extend(_keep_last_report_glance(decorated))
    return lines


def _set_thought_cards_expanded(
    process_lines: List[str], *, expanded: bool
) -> List[str]:
    """Streaming: keep thoughts open. Finished: fold them shut inside process panel."""
    out: List[str] = []
    for line in process_lines:
        text = str(line or "")
        if expanded:
            text = text.replace(
                '<details class="thought-card">',
                '<details class="thought-card" open>',
            )
        else:
            text = text.replace(
                '<details class="thought-card" open>',
                '<details class="thought-card">',
            )
        out.append(text)
    return out


def _build_process_details_section(
    process_lines: List[str],
    *,
    step_count: Optional[int] = None,
) -> List[str]:
    if not process_lines:
        return []
    label = _progress_copy("progress_process_summary")
    if step_count and step_count > 0:
        label = f"{label} · {step_count}"
    # data-collapsed + fingerprint: Gradio markdown morph can preserve a stale
    # [open] from an earlier stream tick; fingerprint remounts and JS force-closes once.
    fingerprint = (
        abs(hash((label, len(process_lines), step_count or 0))) % 1_000_000_007
    )
    lines = [
        "\n\n",
        (
            f'<details class="process-details" data-collapsed="1" data-fp="{fingerprint}">\n'
            f"<summary>🧭 {html.escape(label, quote=False)}</summary>\n\n"
        ),
    ]
    lines.extend(_set_thought_cards_expanded(process_lines, expanded=False))
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
        # Builders already html.escape query/provider fragments; still neutralize
        # any raw angle brackets that slipped through without double-escaping entities.
        if "<" in normalized_line or ">" in normalized_line:
            # Only escape if it looks like raw tags (not already entity-encoded)
            if "&lt;" not in normalized_line and "&gt;" not in normalized_line:
                normalized_line = html.escape(normalized_line, quote=False)
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


def _find_details_block_end(text: str, block_start: int) -> int:
    open_tag_len = len("<details")
    close_tag = "</details>"
    depth = 1
    cursor = block_start + open_tag_len
    while depth > 0:
        next_open = text.find("<details", cursor)
        next_close = text.find(close_tag, cursor)
        if next_close == -1:
            return len(text)
        if next_open != -1 and next_open < next_close:
            depth += 1
            cursor = next_open + open_tag_len
            continue
        depth -= 1
        cursor = next_close + len(close_tag)
    return cursor


def _split_details_block(
    text: str, class_name: str
) -> Optional[Tuple[str, str, str, str]]:
    """Return (before, summary, inner, after) for the first details block of a class."""
    opening = f'<details class="{class_name}"'
    block_start = text.find(opening)
    if block_start == -1:
        return None
    tag_end = text.find(">", block_start)
    body_start = (
        text.find("\n", tag_end) + 1 if tag_end != -1 else block_start + len(opening)
    )
    summary_match = re.match(
        r"\s*<summary>(.*?)</summary>\s*", text[body_start:], re.DOTALL
    )
    summary = summary_match.group(1) if summary_match else ""
    inner_start = body_start + (summary_match.end() if summary_match else 0)
    block_end = _find_details_block_end(text, block_start)
    inner = text[inner_start : block_end - len("</details>")].strip()
    return text[:block_start], summary.strip(), inner, text[block_end:]


def _strip_details_blocks(text: str, class_name: str) -> str:
    while True:
        parts = _split_details_block(text, class_name)
        if parts is None:
            return text
        before, _summary, _inner, after = parts
        text = f"{before.rstrip()}\n\n{after.lstrip()}"


def _unwrap_report_folds(text: str) -> str:
    while True:
        parts = _split_details_block(text, "report-fold")
        if parts is None:
            return text
        before, summary, inner, after = parts
        title = html.unescape(re.sub(r"（\d+）\s*$", "", summary)).strip()
        text = f"{before.rstrip()}\n\n## {title}\n\n{inner}\n\n{after.lstrip()}"


def _plainify_report_html(text: str) -> str:
    """Turn web-only report chrome back into plain Markdown for exported files."""

    # Glance card body is HTML-escaped for the browser; restore it as the 结论 section.
    def _restore_glance(glance_match: "re.Match[str]") -> str:
        block = glance_match.group(0)
        body = html.unescape(glance_match.group(1) or "")
        body = re.sub(r"(?i)<br\s*/?>", "\n", body).strip()
        confidence_match = re.search(
            r'class="confidence-badge[^"]*">([^<]*)</span>', block
        )
        confidence = confidence_match.group(1).strip() if confidence_match else ""
        head = f"## 结论\n\n**{confidence}**\n\n" if confidence else "## 结论\n\n"
        return f"{head}{body}\n\n"

    text = re.sub(
        r'(?ms)<div class="report-glance">.*?'
        r'<p class="report-glance-body">(.*?)</p>\s*</div>',
        _restore_glance,
        text,
    )
    text = re.sub(
        r'(?ms)<div class="report-tldr">\s*'
        r'<div class="report-tldr-head"><strong>(.*?)</strong>(.*?)</div>\s*(.*?)\n</div>',
        lambda m: (
            f"## {html.unescape(m.group(1).strip())}\n\n"
            f"{html.unescape(re.sub(r'(?s)<[^>]*>', '', m.group(2)).strip())}\n\n"
            f"{m.group(3).strip()}\n\n"
        ),
        text,
    )
    text = _unwrap_report_folds(text)
    text = re.sub(
        r'(?ms)<div class="mermaid-card">(.*?)</div>',
        lambda m: f"{m.group(1).strip()}\n\n",
        text,
    )
    text = re.sub(r'(?ms)\s*<span class="conflict-tag">.*?</span>', "", text)
    text = re.sub(
        r'(?ms)<a href="([^"]*)"[^>]*class="ref-citation[^"]*"[^>]*>(.*?)</a>',
        lambda m: f"{m.group(2)}({html.unescape(m.group(1))})",
        text,
    )
    return re.sub(r"(?m)</?(?:div|span|p)[^>]*>\s*", "", text)


def _extract_conclusion_markdown(rendered_markdown: str) -> str:
    """Keep only the research report: the process panel and live progress are UI-only."""
    text = str(rendered_markdown or "")
    heading_match = re.search(r"(?m)^###\s*研究报告[^\n]*\n", text)
    if heading_match:
        text = text[heading_match.start() :]
    text = _strip_details_blocks(text, "process-details")
    return _plainify_report_html(text)


def _prepare_export_markdown(markdown_text: str) -> str:
    conclusion = _extract_conclusion_markdown(markdown_text)
    normalized_markdown = _strip_diagnostic_markers(conclusion).strip()
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
    output_detail_level: Optional[str] = None,
    ui_lang: Optional[str] = None,
) -> str:
    resolved_lang = (
        ui_lang
        if ui_lang in I18N
        else (
            (state or {}).get("ui_lang")
            if (state or {}).get("ui_lang") in I18N
            else DEFAULT_LANG
        )
    )
    _lang_token = _UI_LANG.set(resolved_lang)
    try:
        return _render_markdown_inner(
            state,
            render_mode=render_mode,
            final_summary_merge_strategy=final_summary_merge_strategy,
            output_detail_level=output_detail_level,
            ui_lang=resolved_lang,
        )
    finally:
        _UI_LANG.reset(_lang_token)


def _render_markdown_inner(
    state: dict,
    render_mode: Optional[str] = None,
    final_summary_merge_strategy: Optional[str] = None,
    output_detail_level: Optional[str] = None,
    ui_lang: Optional[str] = None,
) -> str:
    resolved_render_mode = _normalize_render_mode(render_mode, DEFAULT_UI_RENDER_MODE)
    resolved_output_detail_level = _normalize_output_detail_level(output_detail_level)
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
            error_lines.append(
                f'<div class="error-block">❌ {html.escape(str(err), quote=False)}</div>'
            )

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

                        safe_name = html.escape(str(display_name), quote=False)
                        # Escape HTML so agent text cannot break the card shell;
                        # Markdown emphasis/links still render after entity decode.
                        safe_content = html.escape(str(content), quote=False)
                        formatted_thought = (
                            f'<details class="thought-card">\n'
                            f"  <summary>💭 {safe_name} 思考与规划</summary>\n"
                            f'  <div class="thought-content">\n\n{safe_content}\n\n</div>\n'
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
                process_lines.append(
                    f"#### 💻 {_progress_copy('progress_code_exec')}\n"
                )
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
                        process_lines.append(
                            f"\n**{_progress_copy('progress_output')}:**\n"
                        )
                        process_lines.append(
                            f'\n```text\n{output[:1000]}{"..." if len(output) > 1000 else ""}\n```\n'
                        )
                process_lines.append(f"\n✅ {_progress_copy('progress_executed')}\n")
                continue

            # Other tools - show as compact card
            if has_input or has_output:
                safe_tool = html.escape(_tool_display_name(str(tool_name)), quote=False)
                process_lines.append('<div class="tool-card">')
                process_lines.append(f'<div class="tool-header">🔧 {safe_tool}</div>')
                if has_input and isinstance(tool_input, dict):
                    brief = ", ".join(
                        f"{k}: {str(v)[:30]}..." if len(str(v)) > 30 else f"{k}: {v}"
                        for k, v in list(tool_input.items())[:2]
                    )
                    process_lines.append(
                        f'<div class="tool-brief">{html.escape(brief, quote=False)}</div>'
                    )
                if has_output:
                    process_lines.append('<div class="tool-status">✓ 完成</div>')
                process_lines.append("</div>")

    merged_final_summary_blocks = _merge_final_summary_blocks(
        final_summary_blocks,
        merge_strategy=final_summary_merge_strategy,
    )
    lines = list(error_lines)
    has_final_summary = bool(merged_final_summary_blocks)

    if has_final_summary and COLLAPSE_PROCESS_AFTER_SUMMARY:
        # Answer-first (ChatGPT/Claude style): final report on top, process folded below.
        lines.extend(
            _build_summary_section(
                merged_final_summary_blocks,
                output_detail_level=resolved_output_detail_level,
            )
        )
        folded_process: List[str] = []
        folded_process.extend(_build_search_steps_section(search_step_lines))
        folded_process.extend(process_lines)
        lines.extend(
            _build_process_details_section(
                folded_process,
                step_count=len(search_step_lines) or None,
            )
        )
    elif resolved_render_mode == "full":
        # Live / no-collapse: expand thoughts while work is streaming.
        live_process = (
            _set_thought_cards_expanded(process_lines, expanded=True)
            if not has_final_summary
            else process_lines
        )
        lines.extend(live_process)
        if has_final_summary:
            lines.append("\n\n---\n\n")
            lines.extend(
                _build_summary_section(
                    merged_final_summary_blocks,
                    output_detail_level=resolved_output_detail_level,
                )
            )
    elif resolved_render_mode == "summary_only":
        if has_final_summary:
            lines.extend(
                _build_summary_section(
                    merged_final_summary_blocks,
                    output_detail_level=resolved_output_detail_level,
                )
            )
        else:
            lines.extend(_set_thought_cards_expanded(process_lines, expanded=True))
    else:
        if has_final_summary:
            lines.extend(
                _build_summary_section(
                    merged_final_summary_blocks,
                    output_detail_level=resolved_output_detail_level,
                )
            )
            folded_process = []
            folded_process.extend(_build_search_steps_section(search_step_lines))
            folded_process.extend(process_lines)
            lines.extend(
                _build_process_details_section(
                    folded_process,
                    step_count=len(search_step_lines) or None,
                )
            )
        else:
            lines.extend(_set_thought_cards_expanded(process_lines, expanded=True))

    if has_final_summary:
        # 论文式文末来源：LLM 没自己给出参考文献时，用实际命中/访问的来源补一节
        joined_summary = "\n".join(merged_final_summary_blocks)
        if not _REFERENCES_HEADING_RE.search(joined_summary):
            sources = _collect_report_sources(state)
            if sources:
                lines.extend(_build_references_section(sources))

    runtime_stage = state.get("runtime_stage") or {}
    if str(runtime_stage.get("phase") or "") == "已取消":
        # 停止后最后一帧不再带状态卡，补一行终态文案说明输出为何到此为止。
        lines.append(f"*{_format_runtime_status_label(state)}*")

    if lines:
        return "\n".join(lines)

    if float(runtime_stage.get("updated_at") or 0) > 0:
        return f"*{_progress_copy('output_running_hint')}*"

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
                        # 轮次改由状态标签的「已完成 N 次检索」呈现，避免重复
                        runtime_stage["detail"] = ""
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
        # 心跳里的 search_round 是 agent 侧的「校验轮次」，非本次检索次数；
        # 检索轮次统一由检索工具的输出事件累加，避免两个口径互相覆盖。
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
        elif status == "failed":
            runtime_stage["phase"] = "异常"
            reason = ""
            if isinstance(data, dict):
                reason = str(data.get("error") or data.get("detail") or "").strip()
            errors = state.setdefault("errors", [])
            label = f"研究中断：{reason}" if reason else "研究中断"
            # 终态前通常已有 error 事件：同因就地升级为带状态标签的文案，
            # 既保证失败态有可读的终态文案，也不重复渲染两块 ❌。
            if reason and reason in errors:
                errors[errors.index(reason)] = label
            else:
                errors.append(label)
        elif status in {"completed", "cached"}:
            runtime_stage["phase"] = "完成"
        runtime_stage["detail"] = ""
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
# 本地模式运行中 pipeline 的线程内 cancel_event；停止按钮直接置位，
# 不依赖被取消的事件流生成器被回收后才触发取消。
_ACTIVE_CANCEL_EVENTS: dict[str, object] = {}
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


def _signal_pipeline_cancel(task_ids: List[str]) -> None:
    """置位本地 pipeline 的 cancel_event 并摘除注册。"""
    with _CANCEL_LOCK:
        events = [_ACTIVE_CANCEL_EVENTS.pop(task_id, None) for task_id in task_ids]
    for event in events:
        if event is not None:
            event.set()


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


def _spinner_markup(status_text: str, elapsed_html: str = "") -> str:
    safe = html.escape(str(status_text), quote=False)
    return (
        '\n\n<div class="runtime-status">'
        '<div class="runtime-spinner" aria-hidden="true"></div>'
        f'<span class="runtime-status-text">{safe}</span>'
        f"{elapsed_html}"
        "</div>\n"
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
    ui_lang: str = DEFAULT_LANG,
) -> dict:
    return {
        "task_id": task_id,
        "ui_lang": ui_lang if ui_lang in I18N else DEFAULT_LANG,
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
    # Optional temporary public share (set GRADIO_SHARE=1). Prefer pairing with auth.
    if _read_env_bool("GRADIO_SHARE", False):
        launch_kwargs["share"] = True
    auth_user = (os.getenv("GRADIO_AUTH_USER") or "").strip()
    auth_pass = (os.getenv("GRADIO_AUTH_PASS") or "").strip()
    if auth_user and auth_pass:
        launch_kwargs["auth"] = (auth_user, auth_pass)
        # Keep the share link from being casually browsed without credentials.
        launch_kwargs["auth_message"] = "Private demo — sign in required."
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
    else:
        return state

    has_started = bool(meta.get("started_at")) or event_count > 0 or bool(current_stage)
    if status == "running" and not has_started:
        return state

    runtime_stage["updated_at"] = float(
        meta.get("started_at") or meta.get("created_at") or time.time()
    )
    return state


def _is_waiting_output_markdown(markdown: Optional[str]) -> bool:
    """True when markdown is the idle waiting placeholder (EN/CN)."""
    text = str(markdown or "").strip()
    if not text:
        return True
    for lang_pack in I18N.values():
        waiting = str(lang_pack.get("output_waiting") or "").strip()
        if waiting and text == waiting:
            return True
    return False


def _pack_ui_stream(
    markdown,
    run_btn_update,
    stop_btn_update,
    ui_state,
    *,
    show_output: Optional[bool] = None,
):
    """Pack Gradio stream outputs including output-section visibility.

    Returns:
        (markdown, run_btn, stop_btn, ui_state, task_id_bridge,
         output_section_update, export_bar_update)
    """
    if show_output is None:
        show_output = not _is_waiting_output_markdown(markdown)
    # 导出条只在「有正文且无运行中 spinner」时可见：流式过程中隐藏，
    # 终态（完成/失败/重连快照）出现。
    export_visible = show_output and 'class="runtime-spinner"' not in str(markdown)
    return (
        markdown,
        run_btn_update,
        stop_btn_update,
        ui_state,
        _task_id_bridge_value(ui_state),
        gr.update(visible=bool(show_output)),
        gr.update(visible=export_visible),
    )


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
        (markdown, run_btn_update, stop_btn_update, ui_state, task_id_bridge,
         output_section, export_bar)
    """
    state = initial_state or _init_render_state()
    initial_markdown = _render_markdown(
        state,
        render_mode=resolved_ui_render_mode,
        final_summary_merge_strategy=resolved_summary_merge_strategy,
        output_detail_level=(ui_state or {}).get("output_detail_level"),
        ui_lang=(ui_state or {}).get("ui_lang"),
    )
    yield _pack_ui_stream(
        initial_markdown + _runtime_status_markup(state),
        gr.update(interactive=False),
        gr.update(interactive=True),
        ui_state,
        show_output=True,
    )

    # 取消检查复用本地 _CANCEL_FLAGS：stop 按钮按下后会 set 标志
    async def _cancel_check() -> bool:
        return await _disconnect_check_for_task(task_id)

    try:
        async for message in api_client.stream_task_events(
            task_id, cancel_check=_cancel_check
        ):
            state = _update_state_with_event(state, message)
            if str(message.get("event") or "") == "done":
                # 服务端终态信号：completed / cancelled / failed / cached
                break
            md = _render_markdown(
                state,
                render_mode=resolved_ui_render_mode,
                final_summary_merge_strategy=resolved_summary_merge_strategy,
                output_detail_level=(ui_state or {}).get("output_detail_level"),
                ui_lang=(ui_state or {}).get("ui_lang"),
            )
            yield _pack_ui_stream(
                md + _runtime_status_markup(state),
                gr.update(interactive=False),
                gr.update(interactive=True),
                ui_state,
                show_output=True,
            )
            await asyncio.sleep(0.01)
    except api_client.TaskNotFoundError:
        cleared_ui_state = {**ui_state, "task_id": None}
        yield _pack_ui_stream(
            f"任务 `{task_id}` 不存在或已过期，请重新发起检索。",
            gr.update(interactive=True),
            gr.update(interactive=False),
            cleared_ui_state,
            show_output=True,
        )
        return
    except api_client.ApiClientError as exc:
        logger.error("API SSE 订阅失败: %s", exc)
        yield _pack_ui_stream(
            f"连接 api-server 失败：{exc}",
            gr.update(interactive=True),
            gr.update(interactive=False),
            ui_state,
            show_output=True,
        )
        return
    except Exception as exc:
        logger.exception("API 流处理异常")
        yield _pack_ui_stream(
            f"流处理异常：{exc}",
            gr.update(interactive=True),
            gr.update(interactive=False),
            ui_state,
            show_output=True,
        )
        return

    phase = str((state.get("runtime_stage") or {}).get("phase") or "")
    cancelled_locally = await _disconnect_check_for_task(task_id)
    if cancelled_locally and phase not in ("异常", "已取消", "完成"):
        # 本地停止后 done 事件不会再到达，补一个终态，避免最后一帧看不到取消标记。
        state = _update_state_with_event(
            state, {"event": "done", "data": {"status": "cancelled"}}
        )

    final_md = _render_markdown(
        state,
        render_mode=resolved_ui_render_mode,
        final_summary_merge_strategy=resolved_summary_merge_strategy,
        output_detail_level=(ui_state or {}).get("output_detail_level"),
        ui_lang=(ui_state or {}).get("ui_lang"),
    )
    yield _pack_ui_stream(
        final_md,
        gr.update(interactive=True),
        gr.update(interactive=False),
        ui_state,
        show_output=True,
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
        yield _pack_ui_stream(
            f"提交任务到 api-server 失败：{exc}",
            gr.update(interactive=True),
            gr.update(interactive=False),
            ui_state,
            show_output=True,
        )
        return

    task_id = created.get("task_id")
    if not task_id:
        yield _pack_ui_stream(
            "api-server 未返回 task_id，请检查后端日志。",
            gr.update(interactive=True),
            gr.update(interactive=False),
            ui_state,
            show_output=True,
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
    lang: str = DEFAULT_LANG,
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
    resolved_ui_lang = lang if lang in I18N else DEFAULT_LANG

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
            ui_lang=resolved_ui_lang,
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
            "ui_lang": resolved_ui_lang,
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
            "ui_lang": resolved_ui_lang,
            "final_summary_merge_strategy": resolved_summary_merge_strategy,
        }
    state = _init_render_state()
    try:
        initial_markdown = _render_markdown(
            state,
            render_mode=resolved_ui_render_mode,
            final_summary_merge_strategy=resolved_summary_merge_strategy,
            output_detail_level=resolved_output_detail_level,
        )
        # Initial: disable Run, enable Stop, and show spinner at bottom of text
        yield _pack_ui_stream(
            initial_markdown + _runtime_status_markup(state),
            gr.update(interactive=False),
            gr.update(interactive=True),
            ui_state,
            show_output=True,
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
            state = _update_state_with_event(state, message)
            md = _render_markdown(
                state,
                render_mode=resolved_ui_render_mode,
                final_summary_merge_strategy=resolved_summary_merge_strategy,
                output_detail_level=resolved_output_detail_level,
            )
            yield _pack_ui_stream(
                md + _runtime_status_markup(state),
                gr.update(interactive=False),
                gr.update(interactive=True),
                ui_state,
                show_output=True,
            )
            # Small delay to allow Gradio to process the update
            await asyncio.sleep(0.01)
        # End: enable Run, disable Stop, remove spinner
        yield _pack_ui_stream(
            _render_markdown(
                state,
                render_mode=resolved_ui_render_mode,
                final_summary_merge_strategy=resolved_summary_merge_strategy,
                output_detail_level=resolved_output_detail_level,
            ),
            gr.update(interactive=True),
            gr.update(interactive=False),
            ui_state,
            show_output=True,
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
            output_detail_level=resolved_output_detail_level,
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
            state = _update_state_with_event(state, message)
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
        state = _update_state_with_event(
            state, {"event": "done", "data": {"status": "cancelled"}}
        )
    elif not done_status:
        return "api-server 事件流已结束，但未收到终态，请稍后重试。"
    result = _render_markdown(
        state,
        render_mode=resolved_api_render_mode,
        final_summary_merge_strategy=resolved_summary_merge_strategy,
        output_detail_level=resolved_output_detail_level,
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


def _mark_runtime_status_cancelled(markdown: Optional[str]) -> Optional[str]:
    """把流式状态块改写为终态「已停止」。

    停止按钮会 cancel 掉事件流，最后一帧的 spinner 不会再被覆盖，
    因此在这里就地清掉 spinner 并换成终态文案。
    """
    text = str(markdown or "")
    if 'class="runtime-status"' not in text:
        return markdown
    label = _format_runtime_status_label({"runtime_stage": {"phase": "已取消"}})
    text = re.sub(r'<div class="runtime-spinner"[^>]*></div>', "", text)
    # 终态不再自增：摘掉计时钩子，并按停止这一刻重算耗时
    # （最后一帧是上一次服务端渲染的，直接沿用会让耗时回跳到几秒前）
    text = re.sub(
        r'<span class="runtime-elapsed-value" style="--start-ts:(\d+)">[^<]*</span>',
        lambda match: (
            '<span class="runtime-elapsed-value">'
            f"{_format_elapsed_value(float(match.group(1)))}</span>"
        ),
        text,
    )
    text = re.sub(
        r'(<span class="runtime-status-text">)[^<]*(</span>)',
        lambda match: f"{match.group(1)}{label}{match.group(2)}",
        text,
    )
    return text


def stop_current_ui(
    ui_state: Optional[dict] = None, markdown: Optional[str] = None
):
    tid = (ui_state or {}).get("task_id")
    target_ids = [tid] if tid else _get_active_task_ids()
    _cancel_task_ids(target_ids)
    _signal_pipeline_cancel(target_ids)
    # API 模式：同步通知 api-server 设置取消标记，让 worker 协作式中止
    if api_client.is_api_mode_enabled() and tid:
        _schedule_remote_task_cancellation([tid])
    return (
        _mark_runtime_status_cancelled(markdown),
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

    Yields 与 gradio_run 相同的 7 元组：
        (markdown, run_btn_update, stop_btn_update, ui_state, task_id_bridge,
         output_section, export_bar)

    当 URL 没有 task_id，或不在 API 模式时，仅恢复初始空闲态。
    """
    base_state = ui_state or {}

    # 首屏空闲态：等待用户输入
    idle_tuple = _pack_ui_stream(
        I18N[DEFAULT_LANG]["output_waiting"],
        gr.update(interactive=True),
        gr.update(interactive=False),
        base_state,
        show_output=False,
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
        yield _pack_ui_stream(
            f"无法连接 api-server：{exc}",
            gr.update(interactive=True),
            gr.update(interactive=False),
            cleared_ui_state,
            show_output=True,
        )
        return

    if snapshot is None:
        cleared_ui_state = {**base_state, "task_id": None}
        yield _pack_ui_stream(
            f"任务 `{task_id}` 不存在或已过期，请重新发起检索。",
            gr.update(interactive=True),
            gr.update(interactive=False),
            cleared_ui_state,
            show_output=True,
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
        ui_lang=(base_state or {}).get("ui_lang") or DEFAULT_LANG,
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


# Unified outline icon set (24x24, stroke 1.75, round caps/joins).
# Used by top-right Skills button (inline SVG) and action buttons (CSS masks).
_ICON_SVG_ATTRS = (
    'xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" '
    'fill="none" stroke="currentColor" stroke-width="1.75" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"'
)
_ICON_PATHS = {
    "download": '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/>',
    "settings": (
        '<circle cx="12" cy="12" r="3"/>'
        '<path d="M12 2v2"/><path d="M12 20v2"/>'
        '<path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/>'
        '<path d="M2 12h2"/><path d="M20 12h2"/>'
        '<path d="m4.93 19.07 1.41-1.41"/><path d="m17.66 6.34 1.41-1.41"/>'
    ),
    "export": ('<path d="M12 21V9"/><path d="m7 14 5-5 5 5"/>' '<path d="M5 3h14"/>'),
    "stop": '<rect x="6" y="6" width="12" height="12" rx="1.5"/>',
    "run": '<path d="M8 5.5v13l11-6.5-11-6.5z"/>',
    "close": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
}


def _icon_svg(name: str) -> str:
    paths = _ICON_PATHS[name]
    return f"<svg {_ICON_SVG_ATTRS}>{paths}</svg>"


def _icon_mask_data_uri(name: str) -> str:
    """CSS mask-friendly SVG data URI (black strokes for mask luminance)."""
    paths = _ICON_PATHS[name]
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="black" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">'
        f"{paths}</svg>"
    )
    # compact encode for CSS url()
    encoded = (
        svg.replace("%", "%25")
        .replace("#", "%23")
        .replace('"', "%22")
        .replace("<", "%3C")
        .replace(">", "%3E")
        .replace(" ", "%20")
    )
    return f"url(%22data:image/svg+xml,{encoded}%22)"


def build_demo():
    api_client.get_backend_mode()
    logo_data_uri = _load_logo_data_uri()
    favicon_data_uri = _load_logo_data_uri("diting_mark.png")
    fallback_favicon_data_uri = _build_fallback_favicon_data_uri()

    custom_css = """
    /* ========== MiroThinker - Deep Space / Cosmic Dark ========== */
    
    /* Base tokens — also on :root so html/body wash can resolve vars */
    :root, .gradio-container {
        --app-bg: #000208;
        --app-bg-elevated: #060a14;
        --panel-bg: rgba(6, 10, 20, 0.72);
        --panel-bg-solid: rgba(6, 10, 20, 0.78);
        --panel-border: rgba(140, 170, 220, 0.14);
        --panel-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
        --glass-bg: rgba(6, 10, 20, 0.55);
        --glass-border: rgba(140, 170, 220, 0.16);
        --ink-strong: #e8eef8;
        --ink-body: #c5d0e2;
        --ink-soft: #8b9bb8;
        --accent: #7dd3fc;
        --accent-strong: #38bdf8;
        --accent-soft: rgba(56, 189, 248, 0.14);
        --accent-emerald: #34d399;
        --btn-gray: rgba(6, 10, 20, 0.72);
        --btn-gray-hover: rgba(12, 18, 36, 0.85);
        --icon-muted: #9eb0cc;
        --icon-accent: #7dd3fc;
        --focus-ring: 0 0 0 2px rgba(125, 211, 252, 0.35);
        --search-pill-bg: rgba(8, 12, 24, 0.72);
        --search-pill-border: rgba(140, 170, 220, 0.22);
        --search-pill-glow: 0 0 0 1px rgba(56, 189, 248, 0.06), 0 10px 36px rgba(0, 0, 0, 0.45);
        /* Soft theme still ships light checkbox-label fills — pin dark tokens */
        --checkbox-label-background-fill: rgba(8, 14, 32, 0.92);
        --checkbox-label-background-fill-hover: rgba(14, 22, 44, 0.95);
        --checkbox-label-background-fill-selected: rgba(14, 165, 233, 0.78);
        --checkbox-label-text-color: #cbd5e1;
        --checkbox-label-text-color-selected: #f8fafc;
        --checkbox-label-border-color: rgba(148, 163, 184, 0.22);
        --checkbox-label-border-color-hover: rgba(148, 163, 184, 0.35);
        --checkbox-label-border-color-selected: rgba(56, 189, 248, 0.55);
        --block-info-text-color: #e2e8f0;
        --block-label-text-color: #e2e8f0;
        max-width: 100% !important;
        margin: 0 !important;
        padding: 0 !important;
        font-family: __LOCAL_FONT_FAMILY_STACK__ !important;
        background: transparent !important;
        color: var(--ink-body);
        min-height: 100vh;
        position: relative;
        z-index: 1;
    }

    .gradio-container::before {
        display: none;
    }

    /* Deep void base — near-black; nebulae stay local so most of the canvas is empty space */
    html, body {
        background: #000208 !important;
        color: #c5d0e2 !important;
    }
    body {
        background:
            radial-gradient(800px 440px at 12% 8%, rgba(56, 20, 110, 0.18), transparent 62%),
            radial-gradient(920px 500px at 88% 4%, rgba(10, 50, 110, 0.15), transparent 60%),
            radial-gradient(720px 400px at 48% 96%, rgba(18, 36, 90, 0.13), transparent 64%),
            radial-gradient(540px 300px at 40% 36%, rgba(40, 120, 200, 0.05), transparent 55%),
            #000208 !important;
        background-attachment: fixed !important;
    }
    .gradio-container, .main, .wrap, .app,
    #layout-shell, .hero-section-parent, .hero-wrap,
    #main-content-column, #top-utility-bar {
        background: transparent !important;
        color: var(--ink-body, #c5d0e2) !important;
    }
    .gradio-container {
        position: relative !important;
        z-index: 1 !important;
    }
    #bg-particles {
        z-index: 0 !important;
    }

    /* Thin dark scrollbar — default OS bar breaks the void look */
    * {
        scrollbar-width: thin;
        scrollbar-color: rgba(120, 150, 190, 0.32) transparent;
    }
    *::-webkit-scrollbar {
        width: 8px;
        height: 8px;
    }
    *::-webkit-scrollbar-track {
        background: transparent;
    }
    *::-webkit-scrollbar-thumb {
        background: rgba(120, 150, 190, 0.28);
        border-radius: 999px;
    }
    *::-webkit-scrollbar-thumb:hover {
        background: rgba(140, 170, 220, 0.42);
    }

    /* 强力清除 Gradio 默认包装盒的丑陋背景与边框 */
    .gradio-container .form,
    .gradio-container fieldset,
    #main-content-column .form,
    #settings-modal .form,
    #input-section .block,
    #input-section .solid,
    #settings-modal .block,
    #settings-modal .solid {
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
    }

    /* ===== Search home (void glass capsule) ===== */
    /* 搜索行：右侧操作图标叠在输入框内（绝对定位），行内只剩输入框占满整行，
       窄屏不再出现「按钮换行」。半透明玻璃胶囊 + 悬停抬升。 */
    #input-section {
        position: relative !important;
        background: transparent !important;
        border: none !important;
        border-radius: 0 !important;
        box-shadow: none !important;
        padding: 0 !important;
        margin: 0 auto 12px !important;
        width: 100% !important;
        max-width: 584px !important;
    }

    #input-section #question-input {
        flex: 1 1 auto !important;
        min-width: 0 !important;
    }

    #input-section #question-input,
    #input-section #question-input .block,
    #input-section #question-input .solid {
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
    }

    /* 深空玻璃胶囊：半透明蓝黑底 + 细星光边，贴合宇宙背景 */
    #input-section #question-input textarea,
    #input-section textarea {
        background: var(--search-pill-bg) !important;
        border: 1px solid var(--search-pill-border) !important;
        border-radius: 28px !important;
        min-height: 52px !important;
        max-height: 140px !important;
        /* 右侧留出两个 36px 图标位，文字不会钻到图标下面 */
        padding: 14px 96px 14px 24px !important;
        font-size: 16px !important;
        line-height: 1.5 !important;
        color: #e8eef8 !important;
        box-shadow: var(--search-pill-glow) !important;
        backdrop-filter: blur(14px) saturate(1.15);
        -webkit-backdrop-filter: blur(14px) saturate(1.15);
        resize: none !important;
        text-align: left !important;
        transition: box-shadow 0.18s ease, border-color 0.18s ease, background 0.18s ease !important;
    }

    #input-section #question-input textarea::placeholder,
    #input-section textarea::placeholder {
        color: #8b9bb8 !important;
        opacity: 1 !important;
    }

    #input-section #question-input textarea:hover,
    #input-section textarea:hover {
        border-color: rgba(140, 170, 220, 0.34) !important;
        background: rgba(10, 14, 28, 0.82) !important;
        box-shadow:
            0 0 0 1px rgba(56, 189, 248, 0.08),
            0 12px 40px rgba(0, 0, 0, 0.5) !important;
    }

    #input-section #question-input textarea:focus,
    #input-section textarea:focus {
        border-color: rgba(125, 211, 252, 0.4) !important;
        box-shadow:
            0 0 0 1px rgba(56, 189, 248, 0.12),
            0 0 24px rgba(56, 189, 248, 0.1),
            0 12px 40px rgba(0, 0, 0, 0.55) !important;
        background: rgba(10, 14, 28, 0.88) !important;
        outline: none !important;
    }

    /* 图标按钮组：贴输入框内部右端，相对输入框几何中心水平/垂直居中。
       Gradio 给每个 block 子项压了 min-width: min(160px, 100%)，不放开的话行宽
       至少 160px，按钮会从行左端排布、离右端差出一大截。 */
    #btn-row {
        position: absolute !important;
        right: 10px !important;
        top: 0 !important;
        bottom: 0 !important;
        transform: none !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        gap: 2px !important;
        width: auto !important;
        min-width: 0 !important;
        max-width: none !important;
        height: auto !important;
        margin: 0 !important;
        padding: 0 !important;
        z-index: 3 !important;
        border: none !important;
        background: transparent !important;
    }

    #btn-row > * {
        flex: 0 0 auto !important;
        min-width: 0 !important;
    }

    /* 图标化：36×36 圆形幽灵按钮。文字用 font-size:0 收起，
       文案仍留在 DOM 里——无障碍名与悬停提示都取自它，不新增文案 key。
       ::before 绝对居中，避免 Gradio 文本节点把图标挤偏。 */
    #btn-row button {
        position: relative !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        width: 36px !important;
        min-width: 36px !important;
        max-width: 36px !important;
        height: 36px !important;
        min-height: 36px !important;
        margin: 0 !important;
        padding: 0 !important;
        font-size: 0 !important;
        line-height: 0 !important;
        letter-spacing: 0 !important;
        border-radius: 50% !important;
        border: none !important;
        box-shadow: none !important;
        transform: none !important;
        background: transparent !important;
        background-image: none !important;
        color: #9eb0cc !important;
        transition: background 0.15s ease, color 0.15s ease !important;
    }

    #btn-row button::before {
        position: absolute !important;
        left: 50% !important;
        top: 50% !important;
        transform: translate(-50%, -50%) !important;
        margin: 0 !important;
        flex: none !important;
    }

    /* 放大镜视觉重心偏左上：多挪一点做光学居中 */
    #btn-row #run-btn::before {
        transform: translate(calc(-50% + 1.5px), calc(-50% + 1.5px)) !important;
    }
    #btn-row #settings-open-btn::before {
        transform: translate(-50%, calc(-50% + 0.5px)) !important;
    }

    #btn-row button:hover {
        background: rgba(148, 180, 230, 0.12) !important;
        color: #e8eef8 !important;
    }

    #btn-row button:focus-visible {
        outline: 2px solid rgba(125, 211, 252, 0.7) !important;
        outline-offset: 1px !important;
    }

    /* ID 选择器压过 Gradio primary/secondary 默认底色；悬停再各自强调 */
    #run-btn,
    #stop-btn,
    #settings-open-btn {
        background: transparent !important;
        background-image: none !important;
        border: none !important;
        box-shadow: none !important;
        color: #9eb0cc !important;
    }

    #run-btn:hover {
        color: #7dd3fc !important;
        background: rgba(125, 211, 252, 0.14) !important;
        background-image: none !important;
    }

    #stop-btn:hover {
        background: rgba(248, 113, 113, 0.14) !important;
        background-image: none !important;
        color: #f28b82 !important;
    }

    #settings-open-btn:hover {
        color: #e8eef8 !important;
        background: rgba(148, 180, 230, 0.12) !important;
        background-image: none !important;
    }

    /* 开始/停止互斥显示：状态机已用 interactive 精确表达互斥，这里把不可用的那个收起 */
    #run-btn[disabled],
    #stop-btn[disabled] {
        display: none !important;
    }

    /* ===== Modals: settings + export ======================================
       Single source of truth for both overlays. Gradio's Column already
       renders display:flex and hides itself with display:none for
       visible=False, so never override `display` on the overlay itself. */
    #settings-modal{
        position: fixed !important;
        inset: 0 !important;
        z-index: 1000 !important;
        flex-direction: column !important;
        align-items: flex-start !important;
        justify-content: flex-start !important;
        gap: 0 !important;
        padding: 24px !important;
        box-sizing: border-box !important;
        overflow: auto !important;
        cursor: pointer !important;
        background: rgba(2, 6, 23, 0.66) !important;
        backdrop-filter: blur(6px) !important;
        -webkit-backdrop-filter: blur(6px) !important;
    }

    /* Set by the dialog script while a modal is mounted. */
    html.miro-modal-open,
    html.miro-modal-open body {
        overflow: hidden !important;
    }
    /* The card is the dialog's focus target; it has no visible edge to ring. */
    #settings-modal .modal-card:focus{
        outline: none !important;
    }

    #settings-modal .modal-card{
        /* Gradio gives every Column flex:1 1 auto. In a full-viewport overlay
           that stretched the card to its max-height and left a blank bottom. */
        flex: 0 0 auto !important;
        display: flex !important;
        flex-direction: column !important;
        gap: 8px !important;
        /* Auto margins centre the card, yet collapse to 0 when the card is
           taller than the overlay — so the top stays reachable while scrolling. */
        margin: auto !important;
        width: min(460px, 100%) !important;
        max-width: 460px !important;
        min-width: 0 !important;
        padding: 20px 24px 18px !important;
        box-sizing: border-box !important;
        position: relative !important;
        z-index: 1 !important;
        cursor: default !important;
        color: var(--ink-strong) !important;
        background: linear-gradient(180deg, rgba(8, 12, 24, 0.96), rgba(3, 5, 12, 0.98)) !important;
        border: 1px solid rgba(140, 170, 220, 0.14) !important;
        border-radius: 16px !important;
        box-shadow: 0 24px 64px rgba(0, 0, 0, 0.65), 0 0 0 1px rgba(140, 170, 220, 0.08) !important;
        backdrop-filter: blur(18px) saturate(1.1) !important;
        -webkit-backdrop-filter: blur(18px) saturate(1.1) !important;
        animation: miro-modal-pop 0.16s ease-out !important;
        /* Option lists are absolutely positioned inside their own block, so no
           ancestor may clip. The overlay scrolls instead of the card. */
        overflow: visible !important;
    }
    @keyframes miro-modal-pop {
        from { opacity: 0; transform: translateY(10px) scale(0.985); }
        to { opacity: 1; transform: none; }
    }
    /* Accent bar along the top edge of the card. */
    #settings-modal .modal-card::before {
        content: "" !important;
        position: absolute !important;
        top: 0 !important;
        left: 24px !important;
        right: 24px !important;
        height: 3px !important;
        border-radius: 0 0 4px 4px !important;
        background: linear-gradient(90deg, #22d3ee, #818cf8) !important;
    }
    @media (prefers-reduced-motion: reduce) {
        #settings-modal .modal-card {
            animation: none !important;
        }
    }
    @media (max-width: 480px) {
        #settings-modal {
            padding: 12px !important;
        }
        #settings-modal .modal-card {
            padding: 18px 16px 16px !important;
        }
    }

    /* Keep fields in one column and never wider than the card. */
    #settings-modal .modal-card > *,
    #settings-modal .modal-card .form,
    #settings-modal .modal-card .block,
    #settings-modal .modal-card .wrap,
    #settings-modal .modal-card .container,
    #settings-modal .modal-card label{
        min-width: 0 !important;
        max-width: 100% !important;
        box-sizing: border-box !important;
    }
    #settings-modal .modal-card .form{
        display: flex !important;
        flex-direction: column !important;
        flex-wrap: nowrap !important;
        align-items: stretch !important;
        gap: 10px !important;
        width: 100% !important;
        overflow: visible !important;
    }
    #settings-modal .modal-card .form > *{
        flex: 0 0 auto !important;
        width: 100% !important;
    }
    #settings-modal .modal-card .svelte-select,
    #settings-modal .modal-card [data-testid="dropdown"]{
        width: 100% !important;
        max-width: 100% !important;
    }
    /* Gradio stacks a 14px block margin plus 10px of padding inside every HTML
       block; together they added ~150px of blank to the card. The 12px side
       padding also indented hint text past every field label, so it goes too:
       text starts flush with the fields and spacing comes from the top margin. */
    #settings-modal .modal-card .block{
        margin-bottom: 0 !important;
    }
    #settings-modal .modal-card .html-container{
        padding: 0 !important;
        margin-top: 8px !important;
    }
    #settings-modal .modal-card .prose{
        margin: 0 !important;
    }
    /* Gradio pads the dropdown's inner wrap by 12px top/bottom, which made each
       field ~24px taller than it looks. Keep padding symmetric + flex-center the
       chevron so the arrow sits on the text midline. */
    #settings-modal .modal-card .wrap-inner{
        display: flex !important;
        align-items: center !important;
        padding-top: 8px !important;
        padding-bottom: 8px !important;
        min-height: 40px !important;
        box-sizing: border-box !important;
        background: rgba(4, 8, 16, 0.78) !important;
        border-color: rgba(140, 170, 220, 0.18) !important;
        border-radius: 10px !important;
    }
    #settings-modal .modal-card .wrap-inner .secondary-wrap,
    #settings-modal .modal-card .wrap-inner .icon-wrap,
    #settings-modal .modal-card .wrap-inner > .icon,
    #settings-modal .modal-card .wrap-inner svg {
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        align-self: center !important;
        margin-top: 0 !important;
        margin-bottom: 0 !important;
    }
    #settings-modal .modal-card .wrap-inner input,
    #settings-modal .modal-card .wrap-inner .single-select {
        display: flex !important;
        align-items: center !important;
        line-height: 1.35 !important;
        padding-top: 0 !important;
        padding-bottom: 0 !important;
    }

    /* Gradio writes overflow:hidden inline on blocks; the option list must
       be able to escape the trigger it is anchored to. */
    #mode-selector,
    #search-profile-selector,
    #search-result-num-selector,
    #verification-rounds-selector,
    #output-detail-level-selector {
        overflow: visible !important;
    }

    /* Option lists keep Gradio's absolute anchoring so their width and offset
       follow the trigger; only the palette is overridden here. */
    #settings-modal ul[role="listbox"]{
        z-index: 5000 !important;
        max-height: 240px !important;
        overflow-y: auto !important;
        background: #060a14 !important;
        border: 1px solid rgba(140, 170, 220, 0.2) !important;
        border-radius: 10px !important;
        box-shadow: 0 12px 32px rgba(0, 0, 0, 0.55) !important;
        color: #e8eef8 !important;
    }
    #settings-modal ul[role="listbox"] li{
        color: #e8eef8 !important;
        background: transparent !important;
        padding: 8px 12px !important;
        white-space: normal !important;
    }
    #settings-modal ul[role="listbox"] li:hover,
    #settings-modal ul[role="listbox"] li[aria-selected="true"]{
        background: rgba(56, 189, 248, 0.12) !important;
        color: #a5f3fc !important;
    }

    /* Dark form controls inside modals */
    #settings-modal label,
    #settings-modal .wrap{
        color: var(--ink-strong) !important;
    }
    #settings-modal input,
    #settings-modal select,
    #settings-modal textarea{
        background: rgba(4, 8, 16, 0.82) !important;
        border-color: rgba(140, 170, 220, 0.18) !important;
        color: var(--ink-strong) !important;
    }

    /* Footer: status line + restore/apply actions */
    #settings-modal .modal-footer{
        display: flex !important;
        flex-direction: row !important;
        flex-wrap: wrap !important;
        align-items: center !important;
        justify-content: flex-end !important;
        gap: 8px !important;
        width: 100% !important;
        margin-top:22px !important;
    }
    /* Status sits on its own full-width row above the action buttons. The
       direct flex child is Gradio's block wrapper (#settings-status), not
       the inner .modal-status div. */
    #settings-modal .modal-footer > #settings-status{
        flex: 1 1 100% !important;
        min-width: 0 !important;
    }
    /* Before the first Apply/Reset the status block is an empty prose box;
       it still reserved a row above the buttons. */
    #settings-modal .modal-footer > #settings-status:not(:has(.modal-status)){
        display: none !important;
    }
    /* 应用/恢复/预设的确认信息：原来只是一行 11px 浅蓝小字，几乎看不出
       点击有反应，改为带边框的提示条。 */
    #settings-modal .modal-footer .modal-status{
        display: block !important;
        margin: 0 !important;
        padding: 7px 10px !important;
        border-radius: 8px !important;
        border: 1px solid rgba(34, 211, 238, 0.35) !important;
        background: rgba(34, 211, 238, 0.10) !important;
        color: #a5f3fc !important;
        font-size: 12.5px !important;
        line-height: 1.5 !important;
        text-align: left !important;
        animation: miro-status-in 0.18s ease-out !important;
    }
    #settings-modal .modal-footer .modal-status::before {
        content: "✓ " !important;
        font-weight: 700 !important;
    }
    @keyframes miro-status-in {
        from { opacity: 0; transform: translateY(3px); }
        to { opacity: 1; transform: none; }
    }
    /* 选择器要与上面的提示条完全一致，否则同权重的 prefers-reduced-motion 规则
       会因书写顺序在前而被覆盖。 */
    @media (prefers-reduced-motion: reduce) {
        #settings-modal .modal-footer .modal-status {
            animation: none !important;
        }
    }
    .modal-status:empty {
        display: none;
    }
    #settings-reset-btn,
    #settings-apply-btn {
        flex: 0 0 auto !important;
        width: auto !important;
        height: 34px !important;
        min-height: 34px !important;
        padding: 0 12px !important;
        border-radius: 10px !important;
        font-size: 13px !important;
        font-weight: 600 !important;
    }
    #settings-reset-btn {
        background: rgba(4, 8, 16, 0.78) !important;
        border: 1px solid rgba(140, 170, 220, 0.2) !important;
        color: #c5d0e2 !important;
    }
    #settings-reset-btn:hover {
        border-color: rgba(34, 211, 238, 0.45) !important;
        color: #22d3ee !important;
    }

    .modal-title {
        font-size: 20px;
        font-weight: 700;
        color: var(--ink-strong);
        margin: 0 44px 2px 0;
        letter-spacing: 0.02em;
        line-height: 36px;
        min-height: 36px;
    }

    /* ===== Settings modal polish ======================================
       Typography, colour and the divider rhythm only — geometry lives in the
       "Modals: settings" block above. */
    #settings-modal .modal-title::after {
        content: "" !important;
        display: block !important;
        width: 30px !important;
        height: 3px !important;
        margin-top: 6px !important;
        border-radius: 2px !important;
        background: linear-gradient(90deg, #22d3ee, #818cf8) !important;
    }
    #settings-close-btn {
        top: 16px !important;
        right: 16px !important;
        width: 32px !important;
        min-width: 32px !important;
        max-width: 32px !important;
        height: 32px !important;
        min-height: 32px !important;
        border-radius: 50% !important;
        transition: color 0.15s ease, border-color 0.15s ease, background 0.15s ease !important;
    }
    /* Divider rhythm: one faint line above each field group. The option hints
       are card children, not form children, so they hug the field above. */
    #settings-modal .modal-card > .form {
        border-top: 1px solid rgba(148, 163, 184, 0.10) !important;
        padding-top: 10px !important;
    }
    #mode-option-hint,
    #search-profile-option-hint {
        margin-top: -6px !important;
    }
    /* Gradio 默认给每个表单块较大内边距，与分隔线叠加后会显得松散 */
    #settings-modal .modal-card .form > .block.padded {
        padding: 0 !important;
    }
    #settings-modal input[type="range"],
    #settings-modal input[type="radio"] {
        accent-color: #22d3ee !important;
    }
    /* Gradio 用 --neutral-200 画滑块的未填充轨道（浅色主题的近白色 #e2e8f0），
       在深色卡片上会变成一根刺眼的白条；只在弹窗范围内把它压深，已填充段用主题青蓝。 */
    #settings-modal input[type="range"] {
        --neutral-200: rgba(148, 163, 184, 0.28);
        --slider-color: #0ea5e9;
    }
    #settings-apply-btn {
        background: linear-gradient(135deg, #0ea5e9, #22d3ee) !important;
        border: none !important;
        color: #06222c !important;
        box-shadow: 0 4px 14px rgba(34, 211, 238, 0.28) !important;
        transition: filter 0.15s ease, box-shadow 0.15s ease !important;
    }
    #settings-apply-btn:hover {
        filter: brightness(1.08) !important;
        box-shadow: 0 6px 18px rgba(34, 211, 238, 0.38) !important;
    }
    #settings-modal button:focus-visible,
    #settings-modal [role="listbox"]:focus-visible {
        outline: 2px solid rgba(34, 211, 238, 0.6) !important;
        outline-offset: 2px !important;
    }
    #settings-modal .settings-hint {
        margin: -4px 0 0 !important;
        font-size: 12px !important;
        line-height: 1.55 !important;
        color: #94a3b8 !important;
    }
    #settings-modal .option-hint {
        margin: 0 !important;
        padding: 6px 10px !important;
        border-left: 2px solid rgba(125, 211, 252, 0.4) !important;
        border-radius: 0 6px 6px 0 !important;
        background: rgba(8, 14, 28, 0.72) !important;
        font-size: 12px !important;
        line-height: 1.55 !important;
        color: #9eb0cc !important;
    }
    #settings-modal .preset-row {
        display: flex !important;
        gap: 8px !important;
        width: 100% !important;
    }
    #settings-modal .preset-row > * {
        flex: 1 1 0 !important;
        min-width: 0 !important;
    }
    #settings-modal .preset-btn {
        height: 32px !important;
        min-height: 32px !important;
        padding: 0 6px !important;
        font-size: 12.5px !important;
        font-weight: 500 !important;
        border-radius: 999px !important;
        background: rgba(4, 8, 16, 0.7) !important;
        border: 1px solid rgba(140, 170, 220, 0.2) !important;
        color: var(--ink-body) !important;
        box-shadow: none !important;
        transition: border-color 0.15s ease, color 0.15s ease, background 0.15s ease !important;
    }
    #settings-modal .preset-btn:hover {
        border-color: rgba(34, 211, 238, 0.5) !important;
        color: #22d3ee !important;
        background: rgba(34, 211, 238, 0.08) !important;
    }
    #settings-modal .preset-btn-primary {
        border-color: rgba(34, 211, 238, 0.4) !important;
        color: #22d3ee !important;
        background: rgba(34, 211, 238, 0.1) !important;
    }

    /* ===== Export icon bar (result footer) ============================ */
    #export-bar {
        display: flex !important;
        flex-direction: row !important;
        align-items: center !important;
        flex-wrap: wrap !important;
        justify-content: flex-start !important;
        gap: 8px !important;
        margin-top: 14px !important;
        padding: 0 !important;
        border: none !important;
        border-radius: 0 !important;
        background: transparent !important;
    }
    #export-bar > * {
        min-width: 0 !important;
    }
    #export-bar .block {
        flex: 0 0 auto !important;
    }
    .export-icon-btn {
        /* scale=0 pins Gradio's flex-basis to 0% — restore content sizing or
           the label text overflows and overlaps the neighbouring buttons. */
        flex: 0 0 auto !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        gap: 6px !important;
        height: 30px !important;
        min-height: 30px !important;
        padding: 0 12px !important;
        border-radius: 8px !important;
        font-size: 12.5px !important;
        font-weight: 500 !important;
        letter-spacing: 0.01em !important;
        background: transparent !important;
        border: 1px solid rgba(140, 170, 220, 0.18) !important;
        color: var(--ink-soft) !important;
        box-shadow: none !important;
        transition: color 0.15s ease, border-color 0.15s ease, background 0.15s ease !important;
    }
    .export-icon-btn::before {
        content: "" !important;
        display: inline-block !important;
        font-weight: 700 !important;
        font-size: 11px !important;
        line-height: 1 !important;
        letter-spacing: 0.02em !important;
    }
    /* 每格式语义化徽标：Markdown 通用 M↓，PDF/Word 用格式缩写 */
    .export-md-btn::before {
        content: "M\\2193" !important;
        font-size: 12px !important;
    }
    .export-pdf-btn::before {
        content: "PDF" !important;
        font-size: 9px !important;
        letter-spacing: 0.06em !important;
    }
    .export-docx-btn::before {
        content: "W" !important;
        font-size: 12px !important;
    }
    .export-icon-btn:hover {
        color: #22d3ee !important;
        border-color: rgba(34, 211, 238, 0.5) !important;
        background: rgba(34, 211, 238, 0.06) !important;
    }
    /* 导出文件会自动下载（export_titles_script 监听该容器并点击生成的链接），
       文件块本身只是触发载体，不再占据工具栏位置。 */
    #export-file {
        display: none !important;
    }

    #lang-toggle-btn {
        height: 36px !important;
        min-height: 36px !important;
        font-size: 14px !important;
        font-weight: 500 !important;
        border-radius: 10px !important;
    }

    /* Corner icon close — full-width footer "关闭" was the wrong pattern */
    #settings-close-btn,
    #export-close-btn {
        position: absolute !important;
        top: 12px !important;
        right: 12px !important;
        z-index: 3 !important;
        width: 36px !important;
        min-width: 36px !important;
        max-width: 36px !important;
        height: 36px !important;
        min-height: 36px !important;
        margin: 0 !important;
        padding: 0 !important;
        border-radius: 10px !important;
        border: 1px solid rgba(34, 211, 238, 0.18) !important;
        background: rgba(15, 23, 42, 0.85) !important;
        color: #94a3b8 !important;
        box-shadow: none !important;
        font-size: 0 !important;
        line-height: 0 !important;
        overflow: hidden !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
    }
    #settings-close-btn:hover,
    #export-close-btn:hover {
        color: #22d3ee !important;
        border-color: rgba(34, 211, 238, 0.45) !important;
        background: rgba(34, 211, 238, 0.08) !important;
    }
    #settings-close-btn::before,
    #export-close-btn::before {
        content: "" !important;
        display: block !important;
        width: 18px !important;
        height: 18px !important;
        background-color: currentColor !important;
        -webkit-mask-repeat: no-repeat !important;
        mask-repeat: no-repeat !important;
        -webkit-mask-position: center !important;
        mask-position: center !important;
        -webkit-mask-size: contain !important;
        mask-size: contain !important;
        -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.75' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M18 6 6 18'/%3E%3Cpath d='m6 6 12 12'/%3E%3C/svg%3E") !important;
        mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.75' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M18 6 6 18'/%3E%3Cpath d='m6 6 12 12'/%3E%3C/svg%3E") !important;
    }
    #settings-close-btn *,
    #export-close-btn * {
        font-size: 0 !important;
        opacity: 0 !important;
        pointer-events: none !important;
    }

    /* Options helper text under each field (block-info is the TITLE).
       0.72em of the 12px info font landed at 8.6px — off the readable scale,
       so the helpers sit on the same 12px floor as the rest of the card. */
    #settings-modal .md p{
        color: #94a3b8 !important;
        font-size: 12px !important;
        line-height: 1.4 !important;
        margin: 2px 0 6px !important;
    }

    /* Empty progress state */
    #log-view.progress-empty,
    #log-view:has(h3),
    #output-section #log-view {
        display: flex;
        flex-direction: column;
    }

    #log-view h3 {
        color: #e2e8f0 !important;
        font-size: 1.15em !important;
        border-bottom: none !important;
        margin: 8px 0 8px !important;
        padding-bottom: 0 !important;
    }

    #log-view p {
        color: #94a3b8 !important;
        font-size: 0.95em !important;
        max-width: 36em;
    }

    .progress-empty-wrap {
        min-height: 220px;
        display: flex;
        flex-direction: column;
        align-items: flex-start;
        justify-content: center;
        padding: 12px 8px 24px;
        background:
            radial-gradient(1200px 240px at 10% 0%, rgba(56,189,248,0.06), transparent 60%),
            var(--panel-bg-solid);
        border-radius: 16px;
    }

    /* ===== Settings field controls (live inside #settings-modal) ===== */
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
        font-size: 12px !important;
        line-height: 1.45 !important;
        margin: 4px 0 8px !important;
    }

    /* Gradio 在每个 block 里额外放了一层纯背景层（.wrap.default.full，与控件同级），
       它盖住整块（含标题与说明），并不是输入框；整块底色交给弹窗卡片，这层须透明。
       （radio 块的这一层已由下方 #output-detail-level-selector .wrap 一并置透明） */
    #mode-selector > .wrap.default.full,
    #search-profile-selector > .wrap.default.full,
    #search-result-num-selector > .wrap.default.full,
    #verification-rounds-selector > .wrap.default.full {
        background: transparent !important;
        border-color: transparent !important;
        box-shadow: none !important;
    }

    /* 只有真正能点的输入面才铺「浅底 + 浅描边」：弹窗卡片是深色渐变，输入框若与卡片
       同色就会糊成一片认不出来。浅底与同卡片里的单选项 chip 用同一套语言。
       .tab-like-container 是滑块里的数字框（Gradio 默认给它 4px 圆角窄边框，这里
       按同组控件对齐到 10px）；#output-detail-level-selector 的 .wrap 是 chip 组而
       非输入框，样式见下方 radio 块。 */
    #mode-selector .wrap:not(.default),
    #search-profile-selector .wrap:not(.default),
    #search-result-num-selector .wrap:not(.default),
    #verification-rounds-selector .tab-like-container {
        background: rgba(51, 65, 85, 0.6) !important;
        border: 1px solid rgba(148, 163, 184, 0.3) !important;
        border-radius: 10px !important;
        box-shadow: none !important;
        transition: background 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease !important;
    }

    #mode-selector .wrap:not(.default):hover,
    #search-profile-selector .wrap:not(.default):hover,
    #search-result-num-selector .wrap:not(.default):hover,
    #verification-rounds-selector .tab-like-container:hover {
        background: rgba(65, 82, 110, 0.65) !important;
        border-color: rgba(148, 163, 184, 0.5) !important;
    }

    #mode-selector .wrap:not(.default):focus-within,
    #search-profile-selector .wrap:not(.default):focus-within,
    #search-result-num-selector .wrap:not(.default):focus-within,
    #verification-rounds-selector .tab-like-container:focus-within {
        background: rgba(65, 82, 110, 0.75) !important;
        border-color: rgba(56, 189, 248, 0.65) !important;
        box-shadow: var(--focus-ring) !important;
    }

    /* 输入区一律透明：控件底色只由 .wrap 决定，避免出现「浅色外框 + 深色内芯」双层盒子 */
    #mode-selector input,
    #search-profile-selector input,
    #search-result-num-selector input,
    #verification-rounds-selector input,
    #output-detail-level-selector input {
        background: transparent !important;
        color: var(--ink-strong) !important;
        font-weight: 600 !important;
    }

    #mode-selector svg,
    #search-profile-selector svg,
    #search-result-num-selector svg,
    #verification-rounds-selector svg,
    #output-detail-level-selector svg {
        fill: #94a3b8 !important;
    }
    
    /* ===== Output Section ===== */
    #output-section {
        width: 100% !important;
        max-width: 800px !important;
        margin: 0 auto !important;
        padding: 0 0 60px !important;
    }
    
    #log-view {
        padding: 8px 4px 28px !important;
        min-height: 120px;
        height: auto !important;
        overflow: visible !important;
        background: transparent !important;
        border: none !important;
        border-radius: 0 !important;
        box-shadow: none !important;
    }

    #export-row {
        margin-top: 14px !important;
        gap: 12px !important;
        align-items: flex-end !important;
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
        background: transparent;
        border: none;
        border-left: 2px solid rgba(52, 211, 153, 0.45);
        border-radius: 0;
        padding: 4px 0 4px 12px;
        margin: 10px 0;
        box-shadow: none;
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
        background: transparent;
        border: none;
        border-left: 2px solid rgba(148, 163, 184, 0.28);
        border-radius: 0;
        padding: 4px 0 4px 12px;
        margin: 10px 0;
        box-shadow: none;
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

    /* Finished-run process bar: quiet, clickable, answer stays above */
    #log-view .process-details {
        margin: 14px 0 8px !important;
        border-radius: 12px !important;
        border: 1px solid rgba(148, 163, 184, 0.22) !important;
        background: rgba(15, 23, 42, 0.55) !important;
        padding: 0 !important;
    }
    #log-view .process-details > summary {
        cursor: pointer !important;
        list-style: none !important;
        padding: 10px 14px !important;
        color: #94a3b8 !important;
        font-size: 13px !important;
        font-weight: 500 !important;
        user-select: none !important;
    }
    #log-view .process-details > summary::-webkit-details-marker { display: none !important; }
    #log-view .process-details[open] > summary {
        color: #e2e8f0 !important;
        border-bottom: 1px solid rgba(148, 163, 184, 0.14) !important;
    }
    #log-view .process-details .search-step-board,
    #log-view .process-details .thought-card,
    #log-view .process-details .tool-card {
        margin: 8px 12px !important;
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
        background: var(--panel-bg-solid);
    }

    #log-view .search-step-item {
        padding: 12px 16px;
        font-size: 0.88em;
        line-height: 1.6;
        color: #e2e8f0;
        border-bottom: 1px solid rgba(148, 163, 184, 0.16);
        background: rgba(15, 23, 42, 0.55);
    }

    #log-view .search-step-item:last-child {
        border-bottom: none;
    }

    #log-view .search-step-item a,
    #log-view .tool-card a,
    #log-view .thought-card a {
        color: #67e8f9 !important;
        text-decoration: underline !important;
        text-underline-offset: 2px;
        word-break: break-all;
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
    

    /* Force readable search-step text on dark board (overrides earlier light theme) */
    #log-view .search-step-board .search-step-item {
        color: #e2e8f0 !important;
        background: transparent !important;
        border-bottom-color: rgba(148, 163, 184, 0.14) !important;
    }
    #log-view .search-step-board .search-step-item a {
        color: #67e8f9 !important;
    }
    #log-view .tool-card, #log-view .thought-card {
        color: #e2e8f0 !important;
    }
    #log-view .tool-card a, #log-view .thought-card a {
        color: #67e8f9 !important;
    }

    /* Search result cards nested in dark #log-view — void glass, not slate slabs */
    #log-view .search-card {
        background: rgba(6, 10, 20, 0.62) !important;
        border: 1px solid rgba(140, 170, 220, 0.14) !important;
        color: #e8eef8 !important;
        backdrop-filter: blur(14px) saturate(1.05) !important;
        -webkit-backdrop-filter: blur(14px) saturate(1.05) !important;
        box-shadow: 0 10px 36px rgba(0, 0, 0, 0.4) !important;
    }
    #log-view .search-header,
    #log-view .search-count {
        background: rgba(4, 8, 16, 0.55) !important;
        border-bottom-color: rgba(140, 170, 220, 0.1) !important;
        color: #c5d0e2 !important;
    }
    #log-view .search-query {
        color: #e8eef8 !important;
    }
    #log-view .search-result-item,
    #log-view a.search-result-item {
        color: #e2e8f0 !important;
        background: transparent !important;
    }
    #log-view .search-result-item:hover,
    #log-view a.search-result-item:hover {
        background: rgba(125, 211, 252, 0.07) !important;
        border-left-color: #7dd3fc !important;
        color: #f8fafc !important;
    }
    #log-view .result-title,
    #log-view .search-result-item .result-title {
        color: #e8eef8 !important;
    }
    #log-view .search-result-item .result-icon {
        opacity: 0.9 !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        align-self: center !important;
    }

    /* ===== Footer ===== */
    .app-footer {
        display: none !important;
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
        background: rgba(6, 10, 20, 0.62);
        border: 1px solid rgba(140, 170, 220, 0.14);
        border-radius: 12px;
        margin: 16px 0;
        overflow: hidden;
        backdrop-filter: blur(14px) saturate(1.05);
        -webkit-backdrop-filter: blur(14px) saturate(1.05);
        box-shadow: 0 10px 36px rgba(0, 0, 0, 0.4);
    }
    
    .search-header {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 14px 18px;
        background: rgba(4, 8, 16, 0.55);
        border-bottom: 1px solid rgba(140, 170, 220, 0.1);
    }
    
    .search-icon {
        font-size: 1em;
        color: #7dd3fc;
    }
    
    .search-query {
        font-size: 0.9em;
        color: #e8eef8;
        font-weight: 500;
    }
    
    .search-count {
        padding: 10px 18px;
        font-size: 0.8em;
        color: #9eb0cc;
        background: rgba(4, 8, 16, 0.45);
        border-bottom: 1px solid rgba(140, 170, 220, 0.08);
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
        background: var(--panel-bg-solid);
        border: 1px solid var(--glass-border);
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
    /* Gradio HTML wrappers must not stack old+new text during updates */
    #top-utility-bar,
    #main-content-column > .html-container,
    .hero-wrap {
        overflow: visible;
    }

    .hero-section {
        position: relative !important;
        isolation: isolate;
        text-align: center;
        padding: 56px 16px 22px;
        max-width: 584px;
        margin: 0 auto 0;
    }

    .hero-brand {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 8px;
        margin-bottom: 16px;
    }

    .hero-logo {
        width: min(200px, 44vw);
        max-height: 88px;
        height: auto;
        object-fit: contain;
        box-shadow: none;
        border-radius: 0;
        flex-shrink: 0;
        opacity: 1;
    }

    .hero-brand-name {
        display: none;
    }

    .hero-title {
        font-size: 22px;
        font-weight: 500;
        color: #e2e8f0;
        background: none;
        -webkit-text-fill-color: #e2e8f0;
        margin: 0 0 8px 0;
        letter-spacing: 0.01em;
        line-height: 1.3;
    }

    .hero-subtitle {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 8px;
        color: #b8c5d6;
        font-size: 13px;
        font-weight: 400;
        letter-spacing: 0.02em;
    }

    .hero-line {
        display: none;
    }

    #layout-shell {
        width: 100% !important;
        max-width: 680px !important;
        margin: 0 auto !important;
        padding: 0 16px 48px !important;
        gap: 0 !important;
    }

    #output-section {
        max-width: 680px !important;
        margin: 36px auto 0 !important;
        padding-bottom: 48px !important;
    }

    #log-view {
        padding: 8px 4px 24px !important;
        min-height: 120px !important;
        border-radius: 0 !important;
        border: none !important;
        background: transparent !important;
        box-shadow: none !important;
        backdrop-filter: none;
        font-size: 15px !important;
        color: var(--ink-body) !important;
        line-height: 1.7 !important;
    }

    #log-view h3 {
        font-size: 16px !important;
        font-weight: 600 !important;
        color: var(--accent) !important;
        margin: 0 0 6px !important;
        border-bottom: none !important;
        padding-bottom: 0 !important;
    }

    #log-view p {
        font-size: 14px !important;
        color: var(--ink-soft) !important;
        line-height: 1.55 !important;
    }

    .top-nav {
        max-width: 980px !important;
        width: calc(100% - 32px) !important;
        margin: 8px auto 0 !important;
        padding: 8px 4px !important;
    }

    .nav-brand-text {
        font-size: 14px !important;
        font-weight: 500 !important;
        color: #94a3b8 !important;
    }

    .skills-top-link {
        font-size: 13px !important;
        color: #1a73e8 !important;
        font-weight: 500 !important;
    }

    .app-footer {
        font-size: 12px !important;
        color: #94a3b8 !important;
    }

    #main-content-column {
        width: 100% !important;
        padding: 0 !important;
        gap: 0 !important;
    }

    /* ===== Responsive ===== */
    @media (max-width: 768px) {
        .hero-title {
            font-size: 18px;
        }

        .hero-section {
            padding-top: 28px;
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
            /* 顶部固定工具栏会压在页头 logo 上，窄屏留出工具条高度 */
            padding: 88px 16px 16px;
        }

        .hero-brand {
            margin-bottom: 14px;
            gap: 8px;
        }

        .hero-logo {
            width: min(220px, 70vw);
            max-height: 84px;
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
            padding: 0 !important;
        }

        #log-view {
            min-height: 260px;
            padding: 22px 20px !important;
            border-radius: 22px !important;
        }
    }

    /* ===== Unified icon system (outline, 20px in 36px hit target) ===== */
    .ui-icon-btn,
    .skills-icon-btn {
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        width: 36px !important;
        height: 36px !important;
        min-width: 36px !important;
        padding: 0 !important;
        border-radius: 10px !important;
        border: 1px solid var(--glass-border) !important;
        background: var(--glass-bg) !important;
        color: var(--icon-muted) !important;
        text-decoration: none !important;
        box-sizing: border-box !important;
        transition: color 0.15s ease, border-color 0.15s ease, background 0.15s ease, box-shadow 0.15s ease !important;
        backdrop-filter: blur(10px);
        cursor: pointer;
    }

    .ui-icon-btn svg,
    .skills-icon-btn svg {
        width: 20px !important;
        height: 20px !important;
        stroke-width: 1.75 !important;
        display: block !important;
    }

    .ui-icon-btn:hover,
    .skills-icon-btn:hover {
        color: var(--icon-accent) !important;
        border-color: rgba(34, 211, 238, 0.4) !important;
        background: rgba(34, 211, 238, 0.08) !important;
        box-shadow: 0 0 0 1px rgba(34, 211, 238, 0.12) !important;
    }

    .skills-icon-btn.is-copied {
        color: var(--accent-emerald) !important;
        border-color: rgba(52, 211, 153, 0.45) !important;
    }

    .skills-icon-btn-disabled {
        opacity: 0.45 !important;
        cursor: not-allowed !important;
        pointer-events: none !important;
    }


    /* hide-gradio-textbox-label
       NOTE: In Gradio 5 the <label> wraps the textarea — never display:none the label itself. */
    #question-input span[data-testid="block-info"],
    #question-input .sr-only,
    #gr-task-id-bridge span[data-testid="block-info"],
    #gr-task-id-bridge .sr-only,
    #gr-task-id-bridge .label-wrap {
        position: absolute !important;
        width: 1px !important;
        height: 1px !important;
        padding: 0 !important;
        margin: -1px !important;
        overflow: hidden !important;
        clip: rect(0, 0, 0, 0) !important;
        white-space: nowrap !important;
        border: 0 !important;
    }
    #question-input label,
    #question-input .input-container,
    #question-input textarea {
        display: block !important;
        visibility: visible !important;
        opacity: 1 !important;
        width: 100% !important;
        max-width: 100% !important;
    }
    #question-input textarea {
        height: auto !important;
    }

    /* idle-output-collapse */
    #output-section.hidden,
    #output-section[style*="display: none"],
    #output-section:not([style*="display: block"]):not([style*="display:flex"]) {
        min-height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        border: none !important;
    }


    /* ===== Dark progress + report presentation ===== */
    .runtime-status {
        display: flex !important;
        align-items: center !important;
        gap: 10px !important;
        margin: 16px 0 10px !important;
        padding: 12px 14px !important;
        border-radius: 12px !important;
        border: 1px solid rgba(100, 180, 200, 0.2) !important;
        background: rgba(36, 52, 72, 0.7) !important;
        color: #bae6fd !important;
        font-size: 13px !important;
    }
    .runtime-spinner {
        width: 16px !important;
        height: 16px !important;
        border: 2px solid rgba(165, 243, 252, 0.25) !important;
        border-top-color: #22d3ee !important;
        border-radius: 50% !important;
        animation: miro-spin 0.8s linear infinite !important;
        flex: 0 0 16px !important;
    }
    @keyframes miro-spin { to { transform: rotate(360deg); } }
    .runtime-elapsed {
        margin-left: auto !important;
        flex: 0 0 auto !important;
        color: rgba(186, 230, 253, 0.75) !important;
        font-size: 12px !important;
        font-variant-numeric: tabular-nums !important;
        letter-spacing: 0.02em !important;
    }

    #log-view .thought-card,
    #log-view .tool-card,
    #log-view .process-details,
    #log-view .search-step-board {
        background: transparent !important;
        border: none !important;
        border-left: 2px solid rgba(148, 163, 184, 0.22) !important;
        border-radius: 0 !important;
        color: #cbd5e1 !important;
        box-shadow: none !important;
        padding: 6px 0 6px 12px !important;
        margin: 10px 0 !important;
    }
    #log-view .thought-card > summary,
    #log-view .process-details > summary,
    #log-view .tool-header {
        color: #e2e8f0 !important;
    }
    #log-view .thought-content,
    #log-view .tool-brief,
    #log-view p,
    #log-view li {
        color: #cbd5e1 !important;
    }
    #log-view .search-step-item {
        border-bottom: 1px solid rgba(148, 163, 184, 0.12) !important;
        color: #e2e8f0 !important;
        padding: 8px 4px !important;
        font-size: 13px !important;
    }
    #log-view h1, #log-view h2, #log-view h3 {
        color: #e2e8f0 !important;
        border-color: rgba(100, 180, 200, 0.2) !important;
    }
    #log-view blockquote {
        background: rgba(34, 211, 238, 0.08) !important;
        border-left-color: #22d3ee !important;
        color: #e2e8f0 !important;
    }
    #log-view pre {
        background: #1e293b !important;
        color: #e2e8f0 !important;
        border: 1px solid rgba(34, 211, 238, 0.15) !important;
    }
    #log-view code {
        background: rgba(34, 211, 238, 0.12) !important;
        color: #a5f3fc !important;
    }

    /* ===== Document-style report (compact, not card wall) ===== */
    /* Document typography in report body */
    #log-view h1 {
        font-size: 1.45rem !important;
        font-weight: 700 !important;
        color: #e2e8f0 !important;
        margin: 1.4em 0 0.55em !important;
        letter-spacing: -0.01em !important;
        border: none !important;
        padding: 0 !important;
    }
    #log-view h2 {
        font-size: 1.15rem !important;
        font-weight: 650 !important;
        color: #e2e8f0 !important;
        margin: 1.35em 0 0.45em !important;
        border: none !important;
        padding: 0 !important;
        border-bottom: none !important;
    }
    #log-view h3 {
        font-size: 1.02rem !important;
        font-weight: 600 !important;
        color: #cbd5e1 !important;
        margin: 1.15em 0 0.4em !important;
        border: none !important;
        padding: 0 !important;
    }
    #log-view p {
        margin: 0 0 0.85em !important;
        line-height: 1.75 !important;
        color: #cbd5e1 !important;
        max-width: none !important;
    }
    #log-view li {
        margin: 0.25em 0 !important;
        line-height: 1.65 !important;
        color: #cbd5e1 !important;
    }
    #log-view strong, #log-view b {
        color: #e2e8f0 !important;
        font-weight: 650 !important;
    }
    #log-view a {
        color: #7dd3fc !important;
        text-decoration: underline !important;
        text-underline-offset: 2px !important;
        text-decoration-color: rgba(125, 211, 252, 0.35) !important;
    }
    #log-view blockquote {
        background: transparent !important;
        border: none !important;
        border-left: 3px solid rgba(56, 189, 248, 0.4) !important;
        padding: 2px 0 2px 14px !important;
        margin: 0.9em 0 !important;
        border-radius: 0 !important;
        color: #94a3b8 !important;
        font-style: normal !important;
    }

    .report-glance {
        margin: 4px 0 20px !important;
        padding: 0 0 14px !important;
        border: none !important;
        border-bottom: 1px solid rgba(148, 163, 184, 0.16) !important;
        border-radius: 0 !important;
        background: transparent !important;
        box-shadow: none !important;
    }
    .report-glance-meta {
        display: flex !important;
        align-items: center !important;
        justify-content: flex-start !important;
        flex-wrap: wrap !important;
        gap: 10px !important;
        margin: 0 0 8px !important;
    }
    .report-glance-kicker {
        font-size: 12px !important;
        letter-spacing: 0.08em !important;
        text-transform: uppercase !important;
        color: #94a3b8 !important;
        margin: 0 !important;
        font-weight: 600 !important;
    }
    .report-glance-head {
        display: none !important; /* legacy */
    }
    .report-glance-body {
        display: block !important;
        margin: 0 !important;
        padding: 0 !important;
        color: #e2e8f0 !important;
        font-size: 1.15rem !important;
        font-weight: 650 !important;
        line-height: 1.65 !important;
        white-space: pre-wrap !important;
        word-break: break-word !important;
    }
    .report-fold {
        margin: 10px 0 12px !important;
        padding: 0 !important;
        border-radius: 0 !important;
        border: none !important;
        border-top: 1px solid rgba(148, 163, 184, 0.12) !important;
        background: transparent !important;
        box-shadow: none !important;
        overflow: visible !important;
    }
    .report-fold > summary {
        cursor: pointer !important;
        list-style: none !important;
        padding: 10px 0 !important;
        color: #94a3b8 !important;
        font-weight: 550 !important;
        font-size: 0.92rem !important;
        user-select: none !important;
    }
    .report-fold > summary::-webkit-details-marker { display: none !important; }
    .report-fold > summary::before {
        content: "▸" !important;
        display: inline-block !important;
        margin-right: 6px !important;
        color: #64748b !important;
        opacity: 0.9 !important;
        transition: transform 0.15s ease !important;
    }
    .report-fold[open] > summary::before {
        transform: rotate(90deg) !important;
    }
    .report-fold[open] > summary {
        border-bottom: none !important;
        color: #cbd5e1 !important;
    }
    .report-fold > *:not(summary) {
        padding: 0 0 8px 14px !important;
        color: #cbd5e1 !important;
        border-left: 2px solid rgba(148, 163, 184, 0.18) !important;
        margin-left: 4px !important;
    }
    /* 折叠区内的来源条目：报告正文的 #log-view li/a 规则带 !important，
       需按同级作用域覆盖，条目间距与悬挂缩进才能生效。 */
    #log-view .report-fold h3 {
        font-size: 0.95rem !important;
        font-weight: 600 !important;
        color: #cbd5e1 !important;
        margin: 10px 0 4px !important;
        border-bottom: none !important;
        padding-bottom: 0 !important;
    }
    #log-view .report-fold ol,
    #log-view .report-fold ul {
        margin: 4px 0 2px !important;
        padding-left: 26px !important;
        list-style-position: outside !important;
    }
    #log-view .report-fold ol > li,
    #log-view .report-fold ul > li {
        margin: 7px 0 !important;
    }
    #log-view .report-fold a:not(.ref-citation):not(.ref-chip) {
        color: #7dd3fc !important;
        text-decoration: none !important;
        border-bottom: 1px dashed rgba(125, 211, 252, 0.4) !important;
        word-break: break-all !important;
        overflow-wrap: anywhere !important;
    }
    #log-view .report-fold a:not(.ref-citation):not(.ref-chip):hover {
        color: #22d3ee !important;
        border-bottom-color: rgba(34, 211, 238, 0.7) !important;
    }
    .report-tldr {
        margin: 4px 0 18px !important;
        padding: 0 0 12px !important;
        border-radius: 0 !important;
        border: none !important;
        border-left: 3px solid rgba(56, 189, 248, 0.45) !important;
        padding-left: 14px !important;
        background: transparent !important;
        box-shadow: none !important;
    }
    .report-tldr-head {
        display: flex !important;
        align-items: center !important;
        justify-content: space-between !important;
        gap: 10px !important;
        margin-bottom: 6px !important;
        color: #e2e8f0 !important;
        font-size: 0.95rem !important;
    }
    .confidence-badge {
        display: inline-flex !important;
        align-items: center !important;
        padding: 1px 8px !important;
        border-radius: 6px !important;
        font-size: 11px !important;
        font-weight: 600 !important;
        border: 1px solid transparent !important;
        white-space: nowrap !important;
        letter-spacing: 0.02em !important;
    }
    .confidence-high { background: rgba(16,185,129,0.18) !important; color: #6ee7b7 !important; border-color: rgba(16,185,129,0.35) !important; }
    .confidence-mid { background: rgba(234,179,8,0.16) !important; color: #fde68a !important; border-color: rgba(234,179,8,0.35) !important; }
    .confidence-low { background: rgba(248,113,113,0.16) !important; color: #fecaca !important; border-color: rgba(248,113,113,0.35) !important; }

    .conflict-heading { display: flex !important; align-items: center !important; gap: 8px !important; flex-wrap: wrap !important; }
    .conflict-tag {
        display: inline-flex !important;
        padding: 1px 8px !important;
        border-radius: 999px !important;
        font-size: 11px !important;
        font-weight: 600 !important;
        background: rgba(251, 146, 60, 0.16) !important;
        color: #fdba74 !important;
        border: 1px solid rgba(251, 146, 60, 0.35) !important;
    }

    .mermaid-card {
        margin: 12px 0 18px !important;
        padding: 12px 14px !important;
        border-radius: 14px !important;
        border: 1px solid rgba(34, 211, 238, 0.2) !important;
        background: rgba(2, 6, 23, 0.65) !important;
        overflow-x: auto !important;
    }

    a.ref-citation, a.ref-chip {
        display: inline-flex !important;
        align-items: center !important;
        padding: 0 6px !important;
        margin: 0 2px !important;
        border-radius: 6px !important;
        background: rgba(34, 211, 238, 0.12) !important;
        color: #67e8f9 !important;
        text-decoration: none !important;
        font-weight: 600 !important;
        font-size: 0.92em !important;
        border: 1px solid rgba(34, 211, 238, 0.22) !important;
    }
    a.ref-citation:hover { background: rgba(34, 211, 238, 0.22) !important; }

    /* e2e-hide-textbox-word */
    #question-input label > span:not(.svelte-input),
    #question-input [data-testid="block-info"],
    #gr-task-id-bridge [data-testid="block-info"],
    #gr-task-id-bridge label > span {
        position: absolute !important;
        width: 1px !important;
        height: 1px !important;
        padding: 0 !important;
        margin: -1px !important;
        overflow: hidden !important;
        clip: rect(0,0,0,0) !important;
        white-space: nowrap !important;
        border: 0 !important;
        font-size: 0 !important;
        line-height: 0 !important;
        color: transparent !important;
    }
    /* Hide idle output section chrome completely */
    #output-section.hidden, #output-section[style*="display: none"],
    #output-section:not(.show) {
        /* Gradio uses visible=False — ensure no leftover min-height */
    }
    #output-section {
        margin-top: 8px !important;
    }

    /* Kill visible Gradio default "Textbox" chrome on search + bridge */
    #question-input span[data-testid="block-info"],
    #gr-task-id-bridge span[data-testid="block-info"],
    #question-input .sr-only.hide,
    #gr-task-id-bridge .sr-only.hide,
    #layout-shell > .form > label > span[data-testid="block-info"] {
        position: absolute !important;
        width: 1px !important;
        height: 1px !important;
        margin: -1px !important;
        padding: 0 !important;
        overflow: hidden !important;
        clip: rect(0, 0, 0, 0) !important;
        border: 0 !important;
        color: transparent !important;
        font-size: 0 !important;
    }
    #gr-task-id-bridge {
        position: absolute !important;
        left: -10000px !important;
        width: 1px !important;
        height: 1px !important;
        opacity: 0 !important;
        pointer-events: none !important;
        overflow: hidden !important;
    }

    /* hide Gradio queue ETA / settings chrome that collides with top-right utilities */
    .progress-text,
    .meta-text,
    .eta-bar,
    .wrap > .progress-bar,
    footer,
    .footer,
    .icon-button-wrapper {
        display: none !important;
    }
    #top-utility-bar {
        isolation: isolate !important;
        z-index: 200 !important;
    }
    #top-utility-bar .progress-text,
    #top-utility-bar .meta-text,
    #top-utility-bar .eta-bar,
    #lang-toggle-btn .progress-text,
    #lang-toggle-btn .meta-text {
        display: none !important;
    }
    #lang-toggle-btn {
        position: relative !important;
        overflow: hidden !important;
        white-space: nowrap !important;
        line-height: 36px !important;
    }

    /* anti-lang-switch-stack */
    /* Gradio can briefly keep previous HTML siblings when value updates */
    #top-utility-bar > .html-container > .prose:not(:last-child),
    #top-utility-bar .html-container > div:not(:last-child),
    .hero-section-parent > .html-container > div:not(:last-child),
    #layout-shell > .html-container > .hero-section:not(:last-of-type),
    #layout-shell .html-container .hero-section ~ .hero-section {
        display: none !important;
    }
    /* Never show offscreen bridges / hidden text as page background noise */
    #gr-task-id-bridge,
    #gr-task-id-bridge textarea,
    .gr-task-id-bridge,
    textarea[data-testid="textbox"]:not([aria-label]):is([style*="-9999"], [style*="opacity: 0"]) {
        position: absolute !important;
        left: -10000px !important;
        width: 1px !important;
        height: 1px !important;
        opacity: 0 !important;
        overflow: hidden !important;
        pointer-events: none !important;
    }

    #top-utility-bar {
        position: fixed !important;
        top: 12px !important;
        right: 16px !important;
        left: auto !important;
        z-index: 120 !important;
        display: inline-flex !important;
        flex-direction: row !important;
        flex-wrap: nowrap !important;
        justify-content: flex-end !important;
        align-items: center !important;
        gap: 8px !important;
        width: auto !important;
        max-width: none !important;
        margin: 0 !important;
        padding: 0 !important;
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
        min-height: 36px !important;
    }

    #top-utility-bar > *,
    #top-utility-bar > .block,
    #top-utility-bar > .form,
    #top-utility-bar .svelte-1svsvh2 {
        flex: 0 0 auto !important;
        width: auto !important;
        max-width: none !important;
        min-width: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
    }

    #lang-toggle-btn.ui-lang-chip,
    #lang-toggle-btn {
        height: 36px !important;
        min-height: 36px !important;
        min-width: 72px !important;
        padding: 0 12px !important;
        border-radius: 10px !important;
        border: 1px solid var(--panel-border, rgba(34, 211, 238, 0.18)) !important;
        background: var(--glass-bg, rgba(15, 23, 42, 0.72)) !important;
        color: var(--ink-body, #cbd5e1) !important;
        font-size: 13px !important;
        font-weight: 500 !important;
        letter-spacing: 0.02em !important;
        box-shadow: none !important;
        width: auto !important;
    }

    #lang-toggle-btn:hover {
        border-color: rgba(34, 211, 238, 0.45) !important;
        color: var(--accent, #22d3ee) !important;
        background: rgba(34, 211, 238, 0.08) !important;
    }

    .top-nav-minimal {
        display: inline-flex !important;
        justify-content: flex-end !important;
        align-items: center !important;
        width: auto !important;
        max-width: none !important;
        margin: 0 !important;
        padding: 0 !important;
        border: none !important;
        background: transparent !important;
        box-shadow: none !important;
    }

    .top-nav-minimal .nav-left {
        display: none !important;
    }

    .top-nav-minimal .nav-right {
        margin: 0 !important;
        display: inline-flex !important;
        align-items: center !important;
    }

    /* Hide Gradio stock footer strip */
    footer,
    .footer,
    .gradio-container > footer {
        display: none !important;
    }

    /* Hide app disclaimer footer + divider */
    .app-footer,
    #app-footer-slot {
        display: none !important;
        height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        border: none !important;
    }

    .nav-brand,
    .nav-brand-text,
    .brand-logo {
        display: none !important;
    }

    .app-footer {
        display: none !important;
    }

    /* Action buttons: shared outline icons via CSS masks（尺寸由下方按钮规则统一给定） */
    .ui-action-btn {
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        gap: 8px !important;
    }

    .ui-action-btn::before {
        content: "" !important;
        display: inline-block !important;
        width: 20px !important;
        height: 20px !important;
        flex: 0 0 20px !important;
        background-color: currentColor !important;
        -webkit-mask-repeat: no-repeat !important;
        mask-repeat: no-repeat !important;
        -webkit-mask-position: center !important;
        mask-position: center !important;
        -webkit-mask-size: contain !important;
        mask-size: contain !important;
    }

    /* 滑杆图标：语义=参数调节，替代原先的太阳形图标 */
    .ui-icon-settings::before {
        -webkit-mask-image: url("data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%27%20viewBox%3D%270%200%2024%2024%27%20fill%3D%27none%27%20stroke%3D%27black%27%20stroke-width%3D%271.75%27%20stroke-linecap%3D%27round%27%20stroke-linejoin%3D%27round%27%3E%3Cpath%20d%3D%22M20%207h-9%22%2F%3E%3Cpath%20d%3D%22M14%2017H5%22%2F%3E%3Ccircle%20cx%3D%2217%22%20cy%3D%2217%22%20r%3D%223%22%2F%3E%3Ccircle%20cx%3D%227%22%20cy%3D%227%22%20r%3D%223%22%2F%3E%3C%2Fsvg%3E") !important;
        mask-image: url("data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%27%20viewBox%3D%270%200%2024%2024%27%20fill%3D%27none%27%20stroke%3D%27black%27%20stroke-width%3D%271.75%27%20stroke-linecap%3D%27round%27%20stroke-linejoin%3D%27round%27%3E%3Cpath%20d%3D%22M20%207h-9%22%2F%3E%3Cpath%20d%3D%22M14%2017H5%22%2F%3E%3Ccircle%20cx%3D%2217%22%20cy%3D%2217%22%20r%3D%223%22%2F%3E%3Ccircle%20cx%3D%227%22%20cy%3D%227%22%20r%3D%223%22%2F%3E%3C%2Fsvg%3E") !important;
    }
    .ui-icon-stop::before {
        -webkit-mask-image: url("data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%27%20viewBox%3D%270%200%2024%2024%27%20fill%3D%27none%27%20stroke%3D%27black%27%20stroke-width%3D%271.75%27%20stroke-linecap%3D%27round%27%20stroke-linejoin%3D%27round%27%3E%3Crect%20x%3D%226%22%20y%3D%226%22%20width%3D%2212%22%20height%3D%2212%22%20rx%3D%221.5%22%2F%3E%3C%2Fsvg%3E") !important;
        mask-image: url("data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%27%20viewBox%3D%270%200%2024%2024%27%20fill%3D%27none%27%20stroke%3D%27black%27%20stroke-width%3D%271.75%27%20stroke-linecap%3D%27round%27%20stroke-linejoin%3D%27round%27%3E%3Crect%20x%3D%226%22%20y%3D%226%22%20width%3D%2212%22%20height%3D%2212%22%20rx%3D%221.5%22%2F%3E%3C%2Fsvg%3E") !important;
    }
    /* 放大镜图标：语义=「提交检索」，与问题输入框组成的搜索习惯一致 */
    .ui-icon-run::before {
        -webkit-mask-image: url("data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%27%20viewBox%3D%270%200%2024%2024%27%20fill%3D%27none%27%20stroke%3D%27black%27%20stroke-width%3D%271.75%27%20stroke-linecap%3D%27round%27%20stroke-linejoin%3D%27round%27%3E%3Ccircle%20cx%3D%2211%22%20cy%3D%2211%22%20r%3D%228%22%2F%3E%3Cpath%20d%3D%22m21%2021-4.3-4.3%22%2F%3E%3C%2Fsvg%3E") !important;
        mask-image: url("data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%27%20viewBox%3D%270%200%2024%2024%27%20fill%3D%27none%27%20stroke%3D%27black%27%20stroke-width%3D%271.75%27%20stroke-linecap%3D%27round%27%20stroke-linejoin%3D%27round%27%3E%3Ccircle%20cx%3D%2211%22%20cy%3D%2211%22%20r%3D%228%22%2F%3E%3Cpath%20d%3D%22m21%2021-4.3-4.3%22%2F%3E%3C%2Fsvg%3E") !important;
    }


    #lang-toggle-btn {
        background: var(--btn-gray) !important;
        color: var(--ink-body) !important;
        border: 1px solid var(--glass-border) !important;
    }

    #lang-toggle-btn:hover {
        color: var(--accent) !important;
        border-color: rgba(34, 211, 238, 0.35) !important;
    }

    /* Progress cards on dark */
    .thought-card,
    .tool-card {
        background: transparent !important;
        border-color: transparent !important;
        color: var(--ink-body) !important;
    }

    #log-view pre {
        background: rgba(4, 8, 16, 0.78) !important;
        color: #e8eef8 !important;
        border: 1px solid rgba(140, 170, 220, 0.14) !important;
    }

    #log-view code {
        background: rgba(34, 211, 238, 0.1) !important;
        color: #a5f3fc !important;
    }

    #log-view p,
    #log-view li {
        color: var(--ink-body) !important;
    }

    /* ===== Aesthetic polish: document + compact emphasis ===== */
    #layout-shell {
        gap: 4px !important;
    }
    .report-glance-body p,
    .report-glance-body {
        color: #e2e8f0 !important;
    }
    #log-view hr,
    #log-view .prose hr {
        border: none !important;
        border-top: 1px solid rgba(148, 163, 184, 0.12) !important;
        margin: 1.25em 0 !important;
    }
    #top-utility-bar {
        padding: 10px 16px 0 !important;
    }
    .skills-top-link {
        color: #7dd3fc !important;
        font-weight: 500 !important;
    }
    .skills-top-link:hover {
        color: #bae6fd !important;
    }
    #output-section {
        max-width: 680px !important;
        margin: 28px auto 0 !important;
    }
    /* Kill leftover hard boxes on progress empty state */
    .progress-empty-wrap {
        background: transparent !important;
        min-height: 100px !important;
        padding: 8px 0 16px !important;
    }


    /* ===== Soft leftovers: pin dark — do not fight Soft light rules ===== */
    :root, .dark, .gradio-container, .gradio-container.dark {
        --checkbox-label-background-fill: rgba(15, 23, 42, 0.88) !important;
        --checkbox-label-background-fill-hover: rgba(30, 41, 59, 0.95) !important;
        --checkbox-label-background-fill-selected: rgba(14, 165, 233, 0.78) !important;
        --checkbox-label-text-color: #cbd5e1 !important;
        --checkbox-label-text-color-selected: #f8fafc !important;
        --checkbox-label-border-color: rgba(148, 163, 184, 0.22) !important;
        --checkbox-label-border-color-hover: rgba(148, 163, 184, 0.35) !important;
        --checkbox-label-border-color-selected: rgba(56, 189, 248, 0.55) !important;
        --block-info-text-color: #e2e8f0 !important;
        --block-label-text-color: #e2e8f0 !important;
    }

    #settings-modal [data-testid="block-info"]{
        background: transparent !important;
        color: #e2e8f0 !important;
        font-weight: 650 !important;
        font-size: 0.92em !important;
        letter-spacing: 0.01em !important;
        padding: 0 0 4px !important;
        margin: 0 !important;
        border: none !important;
        border-radius: 0 !important;
        box-shadow: none !important;
        display: block !important;
        -webkit-line-clamp: unset !important;
        overflow: visible !important;
    }

    /* Radios: unselected chips must stay visible on the dark card; the
       gradient pill alone communicates selection, so the native radio dot
       is visually hidden but kept focusable for keyboard users. */
    #output-detail-level-selector label {
        background: rgba(51, 65, 85, 0.55) !important;
        color: #cbd5e1 !important;
        border: 1px solid rgba(148, 163, 184, 0.3) !important;
        border-radius: 10px !important;
        box-shadow: none !important;
        transition: background 0.15s ease, border-color 0.15s ease, color 0.15s ease !important;
    }
    #output-detail-level-selector label:hover {
        background: rgba(65, 82, 110, 0.65) !important;
        border-color: rgba(56, 189, 248, 0.5) !important;
        color: #e2e8f0 !important;
    }
    #output-detail-level-selector label.selected {
        background: linear-gradient(135deg, rgba(14, 165, 233, 0.9), rgba(34, 211, 238, 0.8)) !important;
        color: #06222c !important;
        border-color: transparent !important;
        font-weight: 600 !important;
    }
    #output-detail-level-selector .wrap {
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
        display: flex !important;
        flex-wrap: wrap !important;
        gap: 8px !important;
    }
    #output-detail-level-selector input[type="radio"] {
        position: absolute !important;
        width: 1px !important;
        height: 1px !important;
        padding: 0 !important;
        margin: 0 !important;
        opacity: 0 !important;
        pointer-events: none !important;
    }
    #output-detail-level-selector label:has(input[type="radio"]:focus-visible) {
        outline: 2px solid rgba(34, 211, 238, 0.6) !important;
        outline-offset: 2px !important;
    }

    /* Document-style process fold / log-view: never light Gradio leftover wash */
    #log-view,
    #log-view .block,
    #log-view .solid,
    #log-view .prose,
    #log-view .md,
    #log-view .process-details,
    #log-view .thought-card,
    #log-view .tool-card,
    #log-view .search-step-board {
        background: transparent !important;
        background-color: transparent !important;
        box-shadow: none !important;
    }
    #log-view .process-details {
        border: 1px solid rgba(148, 163, 184, 0.16) !important;
        border-radius: 10px !important;
        padding: 0 !important;
    }
    #log-view .process-details > summary,
    #log-view .thought-card > summary,
    #log-view .tool-header {
        color: #94a3b8 !important;
        background: transparent !important;
    }
    #log-view .report-glance-body,
    #log-view .report-glance-body p {
        color: #e2e8f0 !important;
        font-weight: 650 !important;
    }

    #gr-task-id-bridge { position: absolute !important; left: -9999px !important; top: -9999px !important; width: 1px !important; height: 1px !important; opacity: 0 !important; pointer-events: none !important; }

    /* 宽屏：报告区放宽到 960，让表格/图表有横向空间；正文段落另设 42em 上限保住阅读行宽 */
    @media (min-width: 1200px) {
        #layout-shell,
        #output-section {
            max-width: 960px !important;
        }
        #log-view p {
            max-width: 42em;
        }
    }
    """
    custom_css = custom_css.replace(
        "__LOCAL_FONT_FAMILY_STACK__", LOCAL_FONT_FAMILY_STACK
    )

    # 统一使用本地 logo，避免外部资源依赖；favicon 用方形标，横版锁合只用于页头。
    favicon_head = (
        f'<link rel="icon" href="{favicon_data_uri or fallback_favicon_data_uri}">'
    )
    hero_logo_src = logo_data_uri or fallback_favicon_data_uri
    hero_brand_name_html = (
        ""
        if logo_data_uri
        else '<span class="hero-brand-name">谛听 Diting</span>'
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
                copyTextToClipboard(absoluteUrl).then((copied) => {
                    if (!copied) { return; }
                    const prevTitle = linkEl.getAttribute('title') || linkEl.dataset.title || '';
                    linkEl.setAttribute('title', copiedText);
                    linkEl.classList.add('is-copied');
                    window.setTimeout(() => {
                        linkEl.setAttribute('title', prevTitle || (linkEl.dataset.title || ''));
                        linkEl.classList.remove('is-copied');
                    }, 1200);
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
    process_details_force_close_script = """
    <script>
    (() => {
        const closeFinishedProcessPanels = (root) => {
            const scope = root && root.querySelectorAll ? root : document;
            scope.querySelectorAll('details.process-details[data-collapsed="1"]').forEach((el) => {
                if (el.dataset.userToggled === '1') { return; }
                if (el.open) { el.open = false; }
                if (el.dataset.boundToggle === '1') { return; }
                el.dataset.boundToggle = '1';
                el.addEventListener('toggle', () => {
                    if (el.open) { el.dataset.userToggled = '1'; }
                });
            });
        };
        // 搜索行的图标按钮只显示图标（文字用 CSS font-size:0 收起），
        // 把按钮自身的文案回填成 title/aria-label，中英文随界面语言自动跟随。
        const bindActionButtonLabels = () => {
            document.querySelectorAll('#btn-row .ui-action-btn').forEach((el) => {
                if (el.dataset.labelBound === '1') { return; }
                const label = (el.textContent || '').trim();
                if (!label) { return; }
                el.dataset.labelBound = '1';
                el.setAttribute('title', label);
                if (!el.getAttribute('aria-label')) { el.setAttribute('aria-label', label); }
            });
        };
        const mo = new MutationObserver((mutations) => {
            for (const m of mutations) {
                if (m.type === 'childList' || m.type === 'attributes') {
                    closeFinishedProcessPanels(document);
                    bindActionButtonLabels();
                    break;
                }
            }
        });
        const start = () => {
            closeFinishedProcessPanels(document);
            bindActionButtonLabels();
            mo.observe(document.body, {
                childList: true,
                subtree: true,
                attributes: true,
                attributeFilter: ['open', 'data-collapsed', 'data-fp'],
            });
        };
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', start, { once: true });
        } else {
            start();
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

    miro_modal_js = """
<script id="miro-modal-dialog">
(function () {
  // Gradio re-creates the overlay subtree whenever `visible` flips, so node
  // identity is unstable: presence in the DOM *is* the open state, and every
  // handler has to be delegated from document.
  var MODALS = [
    { id: "settings-modal", open: "settings-open-btn", close: "settings-close-btn" }
  ];
  var FOCUSABLE = "a[href],button:not([disabled]),input:not([disabled])," +
    "select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex='-1'])";
  var openCards = {};
  var restoreFocusTo = null;

  function topOpenId() {
    var last = null;
    for (var i = 0; i < MODALS.length; i++) {
      if (openCards[MODALS[i].id]) last = MODALS[i].id;
    }
    return last;
  }

  function markOpen(modal, card) {
    openCards[modal.id] = card;
    var overlay = card.parentElement;
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    var title = card.querySelector(".modal-title");
    if (title) {
      title.id = modal.id + "-title";
      overlay.setAttribute("aria-labelledby", title.id);
    }
    document.documentElement.classList.add("miro-modal-open");
    // Focus the card, not a field: Gradio hydrates the modal's children in
    // passes, so "first focusable" is a race and lands on the footer buttons.
    card.setAttribute("tabindex", "-1");
    card.focus();
  }

  function markClosed(modal) {
    delete openCards[modal.id];
    if (topOpenId()) return;
    document.documentElement.classList.remove("miro-modal-open");
    if (restoreFocusTo && document.contains(restoreFocusTo)) restoreFocusTo.focus();
    restoreFocusTo = null;
  }

  function sync() {
    for (var i = 0; i < MODALS.length; i++) {
      var modal = MODALS[i];
      var overlay = document.getElementById(modal.id);
      var card = overlay && overlay.querySelector(".modal-card");
      if (card && !openCards[modal.id]) markOpen(modal, card);
      else if (!card && openCards[modal.id]) markClosed(modal);
    }
  }

  document.addEventListener("click", function (ev) {
    var trigger = ev.target && ev.target.closest ? ev.target.closest(".modal-open-trigger") : null;
    if (trigger) restoreFocusTo = trigger;
    var topId = topOpenId();
    if (topId) {
      var card = openCards[topId];
      if (!card.contains(ev.target)) {
        var btn = card.querySelector("[id$='-close-btn']");
        if (btn) btn.click();
      }
    }
    // Gradio swaps the DOM after its own handler runs; observe() usually beats
    // this, but a follow-up pass keeps the two paths consistent.
    setTimeout(sync, 0);
  }, true);

  document.addEventListener("keydown", function (ev) {
    var topId = topOpenId();
    if (!topId) return;
    var card = openCards[topId];
    if (ev.key === "Escape") {
      // An open dropdown list owns this Escape; closing the dialog here would
      // throw away the edits the user was about to confirm.
      if (card.querySelector("ul.options")) return;
      var btn = card.querySelector("[id$='-close-btn']");
      if (btn) btn.click();
      return;
    }
    if (ev.key !== "Tab") return;
    var items = [].slice.call(card.querySelectorAll(FOCUSABLE)).filter(function (el) {
      return el.getClientRects().length > 0;
    });
    if (!items.length) return;
    var first = items[0];
    var last = items[items.length - 1];
    var active = document.activeElement;
    if (!card.contains(active)) { ev.preventDefault(); first.focus(); return; }
    if (active === card) {
      ev.preventDefault();
      (ev.shiftKey ? last : first).focus();
      return;
    }
    if (ev.shiftKey && active === first) { ev.preventDefault(); last.focus(); }
    else if (!ev.shiftKey && active === last) { ev.preventDefault(); first.focus(); }
  }, true);

  var observer = new MutationObserver(function (records) {
    for (var i = 0; i < records.length; i++) {
      if (records[i].addedNodes.length || records[i].removedNodes.length) {
        sync();
        return;
      }
    }
  });

  function start() {
    observer.observe(document.body, { childList: true, subtree: true });
    sync();
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
</script>
"""
    enter_submit_script = """
<script>
// Gradio renders the question input as a <textarea> (max_lines=3), where Enter
// inserts a newline instead of firing the component's submit event. Intercept
// Enter here so it behaves like clicking "开始研究"; Shift+Enter keeps newline.
(function () {
  function onKeydown(ev) {
    if (ev.key !== "Enter" || ev.shiftKey || ev.ctrlKey || ev.altKey || ev.metaKey) return;
    if (ev.isComposing) return;
    var target = ev.target;
    if (!target || !target.closest) return;
    if (!target.closest("#question-input")) return;
    ev.preventDefault();
    var runBtn = document.getElementById("run-btn");
    if (runBtn && !runBtn.disabled) runBtn.click();
  }
  document.addEventListener("keydown", onKeydown, true);
})();
</script>
"""
    # Icon-only 导出按钮无文字标签，挂载后补 title 提示（导出栏首次出现才进 DOM）；
    # 导出文件组件出现后自动点击下载，实现「点按钮直接导出」
    export_titles_script = """
<script>
(function () {
  var TITLES = {
    "export-md-btn": "Markdown",
    "export-pdf-btn": "PDF",
    "export-docx-btn": "Word"
  };
  var lastAutoDownload = "";
  function apply() {
    Object.keys(TITLES).forEach(function (id) {
      var el = document.getElementById(id);
      if (el && !el.title) el.title = TITLES[id];
    });
  }
  function autoDownload() {
    var box = document.getElementById("export-file");
    if (!box) return;
    var link = box.querySelector("a[href*='/file=']");
    if (!link) return;
    var href = link.getAttribute("href");
    if (!href || href === lastAutoDownload) return;
    lastAutoDownload = href;
    var a = document.createElement("a");
    a.href = href;
    a.download = link.getAttribute("download") || "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }
  var observer = new MutationObserver(function () { apply(); autoDownload(); });
  function start() {
    observer.observe(document.body, { childList: true, subtree: true });
    apply();
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
</script>
"""
    # 运行态耗时：单一定时器每秒刷新所有带起始时间的计时节点；
    # 终态由服务端摘掉起始时间，计时随即停在最后一帧（不再自增）。
    elapsed_timer_script = """
<script>
(function () {
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function tick() {
    var now = Math.floor(Date.now() / 1000);
    var nodes = document.querySelectorAll(".runtime-elapsed-value");
    for (var i = 0; i < nodes.length; i++) {
      var match = /--start-ts:\\s*(\\d+)/.exec(nodes[i].getAttribute("style") || "");
      if (!match) continue;
      var seconds = Math.max(0, now - parseInt(match[1], 10));
      nodes[i].textContent = Math.floor(seconds / 60) + ":" + pad(seconds % 60);
    }
  }
  tick();
  setInterval(tick, 1000);
})();
</script>
"""
    # 全屏星空：中等视差漂浮 + 轻闪烁 + 偶发流星
    bg_particles_script = """
<script>
(function () {
  var VER = 6;
  if (window.__miroStarfieldVer === VER) return;
  window.__miroStarfieldVer = VER;

  var old = document.getElementById("bg-particles");
  if (old) old.remove();

  var LAYER = [
    { speed: 0.15, size: 0.32, alpha: 0.32, countDiv: 3600, maxN: 240, minN: 100 },
    { speed: 0.48, size: 0.72, alpha: 0.58, countDiv: 5600, maxN: 160, minN: 45 },
    { speed: 1.15, size: 1.35, alpha: 0.88, countDiv: 8600, maxN: 95, minN: 40 }
  ];

  var canvas = document.createElement("canvas");
  canvas.id = "bg-particles";
  canvas.setAttribute("aria-hidden", "true");
  canvas.style.cssText =
    "position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:0;";
  var ctx = canvas.getContext("2d");
  var layers = [[], [], []];
  var meteors = [];
  var w = 0, h = 0, rafId = 0, t = 0, nextMeteorAt = 0;
  var camVX = 0.16, camVY = 0.09, windPhase = 0;

  function newStar(depth) {
    var spec = LAYER[depth];
    var giant = depth === 2 && Math.random() < 0.1;
    var twinkly = Math.random() < (depth === 0 ? 0.16 : depth === 1 ? 0.32 : 0.5);
    var floatR = (0.08 + Math.random() * 0.28) * (0.3 + depth * 0.4);
    return {
      x: Math.random() * w,
      y: Math.random() * h,
      vx: (Math.random() - 0.5) * 0.12 * spec.speed,
      vy: (Math.random() - 0.5) * 0.12 * spec.speed,
      r: (giant ? 1.6 + Math.random() * 1.5 : 0.35 + Math.random() * 1.1) * spec.size,
      a: (0.28 + Math.random() * 0.52) * spec.alpha,
      tw: Math.random() * Math.PI * 2,
      tw2: Math.random() * Math.PI * 2,
      twSpeed: twinkly ? (0.016 + Math.random() * 0.024) : (0.006 + Math.random() * 0.01),
      twSpeed2: 0.035 + Math.random() * 0.05,
      twAmp: twinkly ? (0.32 + Math.random() * 0.28) : (0.1 + Math.random() * 0.14),
      flash: twinkly && Math.random() < 0.18,
      floatPhase: Math.random() * Math.PI * 2,
      floatSpeed: 0.005 + Math.random() * 0.01,
      floatRx: floatR * (0.55 + Math.random() * 0.7),
      floatRy: floatR * (0.45 + Math.random() * 0.7),
      glow: giant || (depth === 2 && Math.random() < 0.45) || (depth === 1 && Math.random() < 0.18),
      spike: giant || (depth === 2 && Math.random() < 0.1),
      hue: Math.random() < 0.12 ? 210
        : (Math.random() < 0.08 ? 255
          : (Math.random() < 0.1 ? 40 : 200))
    };
  }

  function spawnMeteor() {
    var heavy = Math.random() < 0.3;
    var fromLeft = Math.random() < 0.58;
    var angle = 0.26 + Math.random() * 0.5;
    var speed = heavy ? (6.5 + Math.random() * 4) : (8.5 + Math.random() * 5);
    var ux = fromLeft ? Math.cos(angle) : -Math.cos(angle);
    var uy = Math.sin(angle) * (0.72 + Math.random() * 0.35);
    return {
      x: fromLeft ? (-70 - Math.random() * 100) : (w + 70 + Math.random() * 100),
      y: Math.random() * h * 0.5,
      vx: ux * speed,
      vy: uy * speed,
      speed: speed,
      heavy: heavy,
      len: heavy ? (120 + Math.random() * 90) : (75 + Math.random() * 70),
      life: 0,
      maxLife: heavy ? (55 + Math.random() * 30) : (36 + Math.random() * 26),
      width: heavy ? (2.2 + Math.random() * 1.4) : (1.0 + Math.random() * 0.8),
      hue: heavy ? (24 + Math.random() * 24) : (188 + Math.random() * 35)
    };
  }

  function rebuild() {
    layers = [[], [], []];
    for (var d = 0; d < 3; d++) {
      var spec = LAYER[d];
      var n = Math.min(
        spec.maxN,
        Math.max(spec.minN, Math.round((w * h) / spec.countDiv))
      );
      for (var i = 0; i < n; i++) layers[d].push(newStar(d));
    }
    nextMeteorAt = t + 40 + Math.random() * 60;
  }

  function resize() {
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = window.innerWidth;
    h = window.innerHeight;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    rebuild();
  }

  function wrap(v, max) {
    if (v < -24) return v + max + 48;
    if (v > max + 24) return v - max - 48;
    return v;
  }

  function hsla(h, s, l, a) {
    return "hsla(" + h + ", " + s + "%, " + l + "%, " + a.toFixed(3) + ")";
  }

  function twinkleFactor(s) {
    var base = 0.5 + 0.5 * Math.sin(s.tw);
    if (s.flash) {
      var pulse = Math.pow(0.5 + 0.5 * Math.sin(s.tw2), 5);
      base = Math.max(base, pulse * 0.85 + base * 0.15);
    }
    return Math.max(0.12, 1 - s.twAmp + s.twAmp * base);
  }

  function drawSoftStar(s, x, y, alpha) {
    var glowR = s.r * (s.spike ? 7.5 : 5.5);
    if (s.glow && alpha > 0.14) {
      var g = ctx.createRadialGradient(x, y, 0, x, y, glowR);
      g.addColorStop(0, hsla(s.hue, 70, 88, alpha * 0.42));
      g.addColorStop(0.32, hsla(s.hue, 72, 70, alpha * 0.14));
      g.addColorStop(1, hsla(s.hue, 80, 55, 0));
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(x, y, glowR, 0, 6.2832);
      ctx.fill();
    }

    if (s.spike && alpha > 0.55) {
      var len = s.r * (3.2 + 3.5 * (alpha - 0.55));
      var spikeA = (alpha - 0.55) * 0.35;
      ctx.save();
      ctx.strokeStyle = hsla(s.hue, 75, 96, spikeA);
      ctx.lineWidth = Math.max(0.5, s.r * 0.28);
      ctx.lineCap = "round";
      ctx.beginPath();
      ctx.moveTo(x - len, y);
      ctx.lineTo(x + len, y);
      ctx.moveTo(x, y - len * 0.8);
      ctx.lineTo(x, y + len * 0.8);
      ctx.stroke();
      ctx.restore();
    }

    ctx.beginPath();
    ctx.fillStyle = hsla(s.hue, 62, 92, Math.min(1, alpha));
    ctx.arc(x, y, Math.max(0.4, s.r), 0, 6.2832);
    ctx.fill();
  }

  function fadeLife(life, maxLife) {
    var p = life / maxLife;
    if (p < 0.1) return p / 0.1;
    if (p > 0.58) return (1 - p) / 0.42;
    return 1;
  }

  function drawMeteor(m) {
    var fade = Math.max(0, Math.min(1, fadeLife(m.life, m.maxLife)));
    var tx = m.x - (m.vx / m.speed) * m.len;
    var ty = m.y - (m.vy / m.speed) * m.len;
    var grad = ctx.createLinearGradient(tx, ty, m.x, m.y);
    if (m.heavy) {
      grad.addColorStop(0, "rgba(0,0,0,0)");
      grad.addColorStop(0.4, hsla(m.hue, 90, 65, 0.18 * fade));
      grad.addColorStop(0.8, hsla(m.hue, 95, 78, 0.52 * fade));
      grad.addColorStop(1, hsla(48, 100, 94, 0.94 * fade));
    } else {
      grad.addColorStop(0, "rgba(0,0,0,0)");
      grad.addColorStop(0.5, hsla(m.hue, 80, 78, 0.22 * fade));
      grad.addColorStop(1, hsla(m.hue, 90, 94, 0.86 * fade));
    }
    ctx.strokeStyle = grad;
    ctx.lineWidth = m.width;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(tx, ty);
    ctx.lineTo(m.x, m.y);
    ctx.stroke();

    ctx.beginPath();
    ctx.fillStyle = m.heavy
      ? hsla(48, 100, 96, 0.94 * fade)
      : hsla(m.hue, 90, 96, 0.8 * fade);
    ctx.arc(m.x, m.y, m.heavy ? 2.6 : 1.4, 0, 6.2832);
    ctx.fill();

    if (m.heavy) {
      var hg = ctx.createRadialGradient(m.x, m.y, 0, m.x, m.y, 11);
      hg.addColorStop(0, hsla(m.hue, 90, 70, 0.32 * fade));
      hg.addColorStop(1, hsla(m.hue, 90, 60, 0));
      ctx.fillStyle = hg;
      ctx.beginPath();
      ctx.arc(m.x, m.y, 11, 0, 6.2832);
      ctx.fill();
    }
  }

  function draw() {
    var i, d, s, m, spec, fx, fy, alpha;
    t += 1;
    windPhase += 0.003;
    camVX += (Math.sin(windPhase * 0.7) * 0.22 + Math.sin(windPhase * 1.2) * 0.06 - camVX) * 0.028;
    camVY += (Math.cos(windPhase * 0.52) * 0.14 + Math.cos(windPhase * 1.05) * 0.04 - camVY) * 0.028;

    ctx.clearRect(0, 0, w, h);

    for (d = 0; d < 3; d++) {
      spec = LAYER[d];
      for (i = 0; i < layers[d].length; i++) {
        s = layers[d][i];
        s.floatPhase += s.floatSpeed;
        fx = Math.sin(s.floatPhase) * s.floatRx;
        fy = Math.cos(s.floatPhase * 0.83) * s.floatRy;
        s.x = wrap(s.x + s.vx + camVX * spec.speed, w);
        s.y = wrap(s.y + s.vy + camVY * spec.speed, h);
        s.tw += s.twSpeed;
        s.tw2 += s.twSpeed2;
        alpha = s.a * twinkleFactor(s);
        drawSoftStar(s, s.x + fx, s.y + fy, alpha);
      }
    }

    if (t >= nextMeteorAt && meteors.length < 3) {
      meteors.push(spawnMeteor());
      nextMeteorAt = t + (70 + Math.random() * 120);
    }

    for (i = meteors.length - 1; i >= 0; i--) {
      m = meteors[i];
      m.x += m.vx;
      m.y += m.vy;
      m.life += 1;
      drawMeteor(m);
      if (
        m.life >= m.maxLife ||
        m.x < -160 || m.x > w + 160 ||
        m.y < -120 || m.y > h + 120
      ) {
        meteors.splice(i, 1);
      }
    }
  }

  function loop() {
    draw();
    rafId = window.requestAnimationFrame(loop);
  }

  function stop() {
    if (rafId) {
      window.cancelAnimationFrame(rafId);
      rafId = 0;
    }
  }

  function start() {
    if (!document.body) {
      document.addEventListener("DOMContentLoaded", start, { once: true });
      return;
    }
    document.body.appendChild(canvas);
    resize();
    window.addEventListener("resize", resize);
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) stop();
      else if (!rafId) loop();
    });
    loop();
  }

  start();
})();
</script>
"""
    demo_head = f"{favicon_head}{skills_bind_script}{process_details_force_close_script}{task_id_url_bridge_script}{miro_modal_js}{enter_submit_script}{export_titles_script}{elapsed_timer_script}{bg_particles_script}"

    def _get_i18n(lang: str):
        return I18N.get(lang, I18N[DEFAULT_LANG])

    def _build_skills_link_html(lang: str):
        i18n = _get_i18n(lang)
        title = html.escape(i18n["skills_download_title"], quote=True)
        icon = _icon_svg("download")
        if skills_download_url:
            escaped_url = html.escape(skills_download_url, quote=True)
            copied = html.escape(i18n["skills_download_copied"], quote=True)
            return (
                f'<a id="skills-download-link" class="ui-icon-btn skills-icon-btn" href="{escaped_url}" '
                f'title="{title}" aria-label="{title}" '
                f'data-copy-url="{escaped_url}" data-title="{title}" data-copied-text="{copied}" '
                f'target="_blank" rel="noopener noreferrer">{icon}</a>'
            )
        fallback = html.escape(i18n["skills_download_fallback"], quote=True)
        return (
            f'<span class="ui-icon-btn skills-icon-btn skills-icon-btn-disabled" '
            f'title="{fallback}" aria-label="{fallback}">{icon}</span>'
        )

    def _build_nav_html(lang: str):
        skills_link = _build_skills_link_html(lang)
        # Top-left brand removed per UX feedback; keep only a compact top-right utility.
        return f"""
            <nav class="top-nav top-nav-minimal">
                <div class="nav-left" aria-hidden="true"></div>
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
                    <img src="{hero_logo_src}" class="hero-logo" alt="Diting logo" />
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

    def toggle_language(
        lang: str,
        current_mode: Optional[str] = None,
        current_profile: Optional[str] = None,
    ):
        new_lang = LANG_CN if lang == LANG_EN else LANG_EN
        i18n = _get_i18n(new_lang)
        infos = _build_setting_infos(new_lang)
        # Use gr.update only — reconstructing HTML/Button/Markdown leaves
        # stacked ghost DOM nodes (esp. with position:fixed top bar).
        # Keep out_md + output_section as-is so switching language does not
        # wipe a finished report (chrome switches; content stays).
        return (
            new_lang,
            gr.update(value=_build_nav_html(new_lang)),
            gr.update(value=_build_hero_html(new_lang)),
            gr.update(placeholder=i18n["input_placeholder"]),
            gr.update(value=i18n["btn_settings"]),
            gr.update(value=i18n["btn_stop"]),
            gr.update(value=i18n["btn_run"]),
            gr.update(),  # preserve report markdown
            gr.update(),  # preserve output section visibility
            gr.update(
                value=f'<div class="modal-title">{i18n["settings_modal_title"]}</div>'
            ),
            gr.update(
                value=f'<div class="settings-hint">{i18n["settings_hint"]}</div>'
            ),
            gr.update(value=i18n["preset_quick"]),
            gr.update(value=i18n["preset_balanced"]),
            gr.update(value=i18n["preset_deep"]),
            gr.update(
                label=i18n["mode_label"],
                choices=_localized_labels(RESEARCH_MODE_LABELS, new_lang),
                info=infos["mode_info"],
            ),
            gr.update(
                value=_option_hint_html(
                    RESEARCH_MODE_DESCRIPTIONS,
                    _normalize_research_mode(current_mode),
                    new_lang,
                )
            ),
            gr.update(
                label=i18n["search_profile_label"],
                choices=_localized_labels(SEARCH_PROFILE_LABELS, new_lang),
                info=infos["search_profile_info"],
            ),
            gr.update(
                value=_option_hint_html(
                    SEARCH_PROFILE_DESCRIPTIONS,
                    _normalize_search_profile(current_profile),
                    new_lang,
                )
            ),
            gr.update(
                label=i18n["search_result_num_label"],
                info=infos["search_result_num_info"],
            ),
            gr.update(
                label=i18n["verification_rounds_label"],
                info=infos["verification_rounds_info"],
            ),
            gr.update(
                label=i18n["output_detail_label"],
                choices=_build_output_detail_choices(new_lang),
                info=infos["output_detail_info"],
            ),
            gr.update(value=i18n["btn_settings_reset"]),
            gr.update(value=i18n["btn_settings_apply"]),
            gr.update(value=""),
            gr.update(value=i18n["lang_toggle_btn"]),
            gr.update(value=i18n["btn_close"]),
            gr.update(label=i18n["export_file_label"], visible=False, value=None),
            gr.update(value="", visible=False),
        )

    with gr.Blocks(
        css=custom_css,
        title=I18N[DEFAULT_LANG]["page_title"],
        theme=gr.themes.Soft(
            primary_hue="sky",
            neutral_hue="slate",
            text_size="md",
        ).set(
            body_background_fill="#000208",
            body_background_fill_dark="#000208",
            background_fill_primary="#060a14",
            background_fill_primary_dark="#060a14",
            background_fill_secondary="#0a1020",
            background_fill_secondary_dark="#0a1020",
            block_background_fill="#060a14",
            block_background_fill_dark="#060a14",
            border_color_primary="#1e2d4d",
            border_color_primary_dark="#1e2d4d",
            body_text_color="#c5d0e2",
            body_text_color_dark="#c5d0e2",
            # Soft defaults to white checkbox-label chips — force dark
            block_info_text_color="#e2e8f0",
            block_info_text_color_dark="#e2e8f0",
            checkbox_label_background_fill="#0a1024",
            checkbox_label_background_fill_dark="#0a1024",
            checkbox_label_background_fill_hover="#121c38",
            checkbox_label_background_fill_hover_dark="#121c38",
            checkbox_label_background_fill_selected="#0284c7",
            checkbox_label_background_fill_selected_dark="#0284c7",
            checkbox_label_text_color="#c5d0e2",
            checkbox_label_text_color_dark="#c5d0e2",
            checkbox_label_text_color_selected="#f8fafc",
            checkbox_label_text_color_selected_dark="#f8fafc",
            checkbox_label_border_color="#1e2d4d",
            checkbox_label_border_color_dark="#334155",
            checkbox_label_border_color_hover="#475569",
            checkbox_label_border_color_hover_dark="#475569",
            checkbox_label_border_color_selected="#38bdf8",
            checkbox_label_border_color_selected_dark="#38bdf8",
        ),
        head=demo_head,
    ) as demo:
        lang_state = gr.State(DEFAULT_LANG)

        with gr.Row(elem_id="top-utility-bar"):
            lang_toggle_btn = gr.Button(
                I18N[DEFAULT_LANG]["lang_toggle_btn"],
                elem_id="lang-toggle-btn",
                elem_classes=["ui-lang-chip"],
                variant="secondary",
                scale=0,
                min_width=72,
            )
            nav_html = gr.HTML(_build_nav_html(DEFAULT_LANG))
        hero_html = gr.HTML(
            _build_hero_html(DEFAULT_LANG), elem_classes=["hero-section-parent"]
        )

        with gr.Column(elem_id="layout-shell"):
            with gr.Column(elem_id="main-content-column"):
                with gr.Row(elem_id="input-section"):
                    inp = gr.Textbox(
                        lines=1,
                        max_lines=3,
                        placeholder=I18N[DEFAULT_LANG]["input_placeholder"],
                        show_label=False,
                        elem_id="question-input",
                        container=False,
                        scale=1,
                    )
                    with gr.Row(elem_id="btn-row", scale=0):
                        run_btn = gr.Button(
                            I18N[DEFAULT_LANG]["btn_run"],
                            elem_id="run-btn",
                            elem_classes=["ui-action-btn", "ui-icon-run"],
                            variant="primary",
                            scale=0,
                        )
                        stop_btn = gr.Button(
                            I18N[DEFAULT_LANG]["btn_stop"],
                            elem_id="stop-btn",
                            elem_classes=["ui-action-btn", "ui-icon-stop"],
                            variant="stop",
                            interactive=False,
                            scale=0,
                        )
                        settings_btn = gr.Button(
                            I18N[DEFAULT_LANG]["btn_settings"],
                            elem_id="settings-open-btn",
                            elem_classes=[
                                "ui-action-btn",
                                "ui-icon-settings",
                                "modal-open-trigger",
                            ],
                            variant="secondary",
                            scale=0,
                        )

                with gr.Column(
                    elem_id="output-section", visible=False
                ) as output_section:
                    out_md = gr.Markdown(
                        I18N[DEFAULT_LANG]["output_waiting"], elem_id="log-view"
                    )
                    # 导出入口放在结果末尾：三个 icon 化格式按钮 + 下载文件。
                    # 可见性由 _pack_ui_stream 统一控制（终态出现，流式中隐藏）。
                    with gr.Row(visible=False, elem_id="export-bar") as export_bar:
                        export_md_btn = gr.Button(
                            "",
                            elem_id="export-md-btn",
                            elem_classes=["export-icon-btn", "export-md-btn"],
                            variant="secondary",
                            scale=0,
                        )
                        export_pdf_btn = gr.Button(
                            "",
                            elem_id="export-pdf-btn",
                            elem_classes=["export-icon-btn", "export-pdf-btn"],
                            variant="secondary",
                            scale=0,
                        )
                        export_docx_btn = gr.Button(
                            "",
                            elem_id="export-docx-btn",
                            elem_classes=["export-icon-btn", "export-docx-btn"],
                            variant="secondary",
                            scale=0,
                        )
                        export_file = gr.File(
                            label=I18N[DEFAULT_LANG]["export_file_label"],
                            visible=False,
                            elem_id="export-file",
                        )

        # Settings modal overlay
        default_infos = _build_setting_infos(DEFAULT_LANG)
        with gr.Column(visible=False, elem_id="settings-modal") as settings_modal:
            with gr.Column(elem_classes=["modal-card"]):
                settings_modal_title_html = gr.HTML(
                    f'<div class="modal-title">{I18N[DEFAULT_LANG]["settings_modal_title"]}</div>'
                )
                settings_hint_html = gr.HTML(
                    f'<div class="settings-hint">{I18N[DEFAULT_LANG]["settings_hint"]}</div>'
                )
                with gr.Row(elem_classes=["preset-row"]):
                    preset_quick_btn = gr.Button(
                        I18N[DEFAULT_LANG]["preset_quick"],
                        elem_classes=["preset-btn"],
                        variant="secondary",
                        scale=1,
                    )
                    preset_balanced_btn = gr.Button(
                        I18N[DEFAULT_LANG]["preset_balanced"],
                        elem_classes=["preset-btn", "preset-btn-primary"],
                        variant="secondary",
                        scale=1,
                    )
                    preset_deep_btn = gr.Button(
                        I18N[DEFAULT_LANG]["preset_deep"],
                        elem_classes=["preset-btn"],
                        variant="secondary",
                        scale=1,
                    )
                close_settings_btn = gr.Button(
                    I18N[DEFAULT_LANG]["btn_close"],
                    elem_id="settings-close-btn",
                    variant="secondary",
                )
                mode_selector = gr.Dropdown(
                    label=I18N[DEFAULT_LANG]["mode_label"],
                    choices=_localized_labels(RESEARCH_MODE_LABELS, DEFAULT_LANG),
                    value=_normalize_research_mode(DEFAULT_RESEARCH_MODE),
                    info=default_infos["mode_info"],
                    elem_id="mode-selector",
                    filterable=False,
                )
                mode_hint_html = gr.HTML(
                    _option_hint_html(
                        RESEARCH_MODE_DESCRIPTIONS,
                        _normalize_research_mode(DEFAULT_RESEARCH_MODE),
                        DEFAULT_LANG,
                    ),
                    elem_id="mode-option-hint",
                    show_label=False,
                )

                # Radio is more reliable than Dropdown for 3 fixed tiers
                # (Dropdown fill/select often left the internal value stuck on detailed).
                output_detail_level_selector = gr.Radio(
                    label=I18N[DEFAULT_LANG]["output_detail_label"],
                    choices=_build_output_detail_choices(DEFAULT_LANG),
                    value=_normalize_output_detail_level(DEFAULT_OUTPUT_DETAIL_LEVEL),
                    info=default_infos["output_detail_info"],
                    elem_id="output-detail-level-selector",
                )
                search_profile_selector = gr.Dropdown(
                    label=I18N[DEFAULT_LANG]["search_profile_label"],
                    choices=_localized_labels(SEARCH_PROFILE_LABELS, DEFAULT_LANG),
                    value=_normalize_search_profile(DEFAULT_SEARCH_PROFILE),
                    info=default_infos["search_profile_info"],
                    elem_id="search-profile-selector",
                    filterable=False,
                )
                search_profile_hint_html = gr.HTML(
                    _option_hint_html(
                        SEARCH_PROFILE_DESCRIPTIONS,
                        _normalize_search_profile(DEFAULT_SEARCH_PROFILE),
                        DEFAULT_LANG,
                    ),
                    elem_id="search-profile-option-hint",
                    show_label=False,
                )
                search_result_num_selector = gr.Dropdown(
                    label=I18N[DEFAULT_LANG]["search_result_num_label"],
                    choices=SEARCH_RESULT_NUM_CHOICES,
                    value=_normalize_search_result_num(DEFAULT_SEARCH_RESULT_NUM),
                    info=default_infos["search_result_num_info"],
                    elem_id="search-result-num-selector",
                    filterable=False,
                )
                verification_min_rounds_selector = gr.Slider(
                    minimum=1,
                    maximum=MAX_VERIFICATION_MIN_SEARCH_ROUNDS,
                    step=1,
                    label=I18N[DEFAULT_LANG]["verification_rounds_label"],
                    value=_normalize_verification_min_search_rounds(
                        DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS
                    ),
                    info=default_infos["verification_rounds_info"],
                    visible=_is_verified_mode(DEFAULT_RESEARCH_MODE),
                    elem_id="verification-rounds-selector",
                )
                with gr.Row(elem_classes=["modal-footer"]):
                    settings_status_html = gr.HTML("", elem_id="settings-status")
                    settings_reset_btn = gr.Button(
                        I18N[DEFAULT_LANG]["btn_settings_reset"],
                        elem_id="settings-reset-btn",
                        variant="secondary",
                        scale=0,
                    )
                    settings_apply_btn = gr.Button(
                        I18N[DEFAULT_LANG]["btn_settings_apply"],
                        elem_id="settings-apply-btn",
                        variant="primary",
                        scale=0,
                    )
        footer_html = gr.HTML(
            "",
            visible=False,
            elem_id="app-footer-slot",
        )

        # 供统一 API 调用的隐藏输出
        api_output = gr.Markdown(visible=False)
        api_btn = gr.Button(value="api-run", visible=False)
        gr.Textbox(visible=False, value="", show_label=False, container=False)
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
                "ui_lang": DEFAULT_LANG,
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
        run_inputs = [
            inp,
            mode_selector,
            search_profile_selector,
            search_result_num_selector,
            verification_min_rounds_selector,
            output_detail_level_selector,
            lang_state,
            ui_state,
        ]
        run_outputs = [
            out_md,
            run_btn,
            stop_btn,
            ui_state,
            task_id_box,
            output_section,
            export_bar,
        ]
        run_event = run_btn.click(
            fn=gradio_run,
            inputs=run_inputs,
            outputs=run_outputs,
            api_name="run_research_stream",
            # Gradio 默认会在流式组件顶部画一条脉冲边框线；进行中状态改由正文里的
            # 状态行表达，这里关掉那条线以免看起来像一条多余的分隔线。
            show_progress="hidden",
        )
        # Enter 键同样触发研究
        submit_event = inp.submit(
            fn=gradio_run,
            inputs=run_inputs,
            outputs=run_outputs,
            api_name=False,
            show_progress="hidden",
        )

        # ui_state 任意一次更新都同步 task_id 到隐藏 textbox（JS 据此写 URL）
        ui_state.change(
            fn=_task_id_bridge_value,
            inputs=[ui_state],
            outputs=[task_id_box],
            api_name=False,
            queue=False,
        )
        for _export_fmt, _export_btn in (
            ("md", export_md_btn),
            ("pdf", export_pdf_btn),
            ("docx", export_docx_btn),
        ):
            _export_btn.click(
                fn=lambda md, state, _fmt=_export_fmt: _export_conclusion(
                    md, _fmt, state
                ),
                inputs=[out_md, ui_state],
                outputs=[export_file],
                api_name=False,
                queue=False,
            )

        # 页面加载时根据 URL ?task_id 决定空闲态 / 重连进行中的任务
        demo.load(
            fn=reconnect_or_init,
            inputs=[ui_state, task_id_box],
            outputs=[
                out_md,
                run_btn,
                stop_btn,
                ui_state,
                task_id_box,
                output_section,
                export_bar,
            ],
            api_name=False,
            show_progress="hidden",
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
            inputs=[ui_state, out_md],
            outputs=[out_md, run_btn, stop_btn],
            cancels=[run_event, submit_event],
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

        SETTINGS_INPUTS = [
            mode_selector,
            output_detail_level_selector,
            search_profile_selector,
            search_result_num_selector,
        ]

        def _settings_status(lang: str, key: str, summary: str) -> str:
            text = html.escape(I18N[lang][key].format(summary=summary))
            return f'<div class="modal-status">{text}</div>'

        def _apply_settings(
            mode: str,
            output_detail_level: str,
            search_profile: str,
            search_result_num: int,
            lang: str,
        ):
            lang = lang if lang in I18N else DEFAULT_LANG
            summary = _build_settings_summary(
                lang, mode, output_detail_level, search_profile, search_result_num
            )
            return _settings_status(lang, "settings_applied", summary)

        def _fill_settings_preset(preset: str, lang: str):
            lang = lang if lang in I18N else DEFAULT_LANG
            if preset == "quick":
                mode, detail, num = "balanced", "compact", 10
            elif preset == "deep":
                mode, detail, num = "research", "detailed", 30
            else:
                mode = _normalize_research_mode(DEFAULT_RESEARCH_MODE)
                detail = _normalize_output_detail_level(DEFAULT_OUTPUT_DETAIL_LEVEL)
                num = _normalize_search_result_num(DEFAULT_SEARCH_RESULT_NUM)
            mode = _normalize_research_mode(mode)
            text = html.escape(
                I18N[lang]["settings_preset_filled"].format(
                    preset=I18N[lang][f"preset_{preset}"]
                )
            )
            return [
                gr.update(value=mode),
                gr.update(value=detail),
                gr.update(value=num),
                gr.update(visible=_is_verified_mode(mode)),
                f'<div class="modal-status">{text}</div>',
                _option_hint_html(RESEARCH_MODE_DESCRIPTIONS, mode, lang),
            ]

        def _reset_settings(lang: str):
            lang = lang if lang in I18N else DEFAULT_LANG
            infos = _build_setting_infos(lang)
            mode = _normalize_research_mode(DEFAULT_RESEARCH_MODE)
            updates = [
                gr.update(
                    value=mode,
                    choices=_localized_labels(RESEARCH_MODE_LABELS, lang),
                    label=I18N[lang]["mode_label"],
                    info=infos["mode_info"],
                ),
                gr.update(
                    value=_normalize_output_detail_level(DEFAULT_OUTPUT_DETAIL_LEVEL),
                    choices=_build_output_detail_choices(lang),
                    label=I18N[lang]["output_detail_label"],
                    info=infos["output_detail_info"],
                ),
                gr.update(
                    value=_normalize_search_profile(DEFAULT_SEARCH_PROFILE),
                    choices=_localized_labels(SEARCH_PROFILE_LABELS, lang),
                    label=I18N[lang]["search_profile_label"],
                    info=infos["search_profile_info"],
                ),
                gr.update(
                    value=_normalize_search_result_num(DEFAULT_SEARCH_RESULT_NUM),
                    label=I18N[lang]["search_result_num_label"],
                    info=infos["search_result_num_info"],
                ),
                gr.update(
                    value=_normalize_verification_min_search_rounds(
                        DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS
                    ),
                    visible=_is_verified_mode(mode),
                    label=I18N[lang]["verification_rounds_label"],
                    info=infos["verification_rounds_info"],
                ),
                _settings_status(
                    lang,
                    "settings_reset",
                    _build_settings_summary(
                        lang,
                        mode,
                        _normalize_output_detail_level(DEFAULT_OUTPUT_DETAIL_LEVEL),
                        _normalize_search_profile(DEFAULT_SEARCH_PROFILE),
                        _normalize_search_result_num(DEFAULT_SEARCH_RESULT_NUM),
                    ),
                ),
                _option_hint_html(
                    RESEARCH_MODE_DESCRIPTIONS,
                    mode,
                    lang,
                ),
                _option_hint_html(
                    SEARCH_PROFILE_DESCRIPTIONS,
                    _normalize_search_profile(DEFAULT_SEARCH_PROFILE),
                    lang,
                ),
            ]
            return updates

        settings_btn.click(
            fn=lambda: [gr.update(visible=True), gr.update(value="")],
            inputs=None,
            outputs=[settings_modal, settings_status_html],
            api_name=False,
            queue=False,
        )
        for _preset_key, _preset_btn in (
            ("quick", preset_quick_btn),
            ("balanced", preset_balanced_btn),
            ("deep", preset_deep_btn),
        ):
            _preset_btn.click(
                fn=partial(_fill_settings_preset, _preset_key),
                inputs=[lang_state],
                outputs=[
                    mode_selector,
                    output_detail_level_selector,
                    search_result_num_selector,
                    verification_min_rounds_selector,
                    settings_status_html,
                    mode_hint_html,
                ],
                api_name=False,
                queue=False,
            )
        mode_selector.change(
            fn=lambda mode, lang: _option_hint_html(
                RESEARCH_MODE_DESCRIPTIONS, _normalize_research_mode(mode), lang
            ),
            inputs=[mode_selector, lang_state],
            outputs=[mode_hint_html],
            api_name=False,
            queue=False,
        )
        search_profile_selector.change(
            fn=lambda profile, lang: _option_hint_html(
                SEARCH_PROFILE_DESCRIPTIONS,
                _normalize_search_profile(profile),
                lang,
            ),
            inputs=[search_profile_selector, lang_state],
            outputs=[search_profile_hint_html],
            api_name=False,
            queue=False,
        )
        settings_apply_btn.click(
            fn=_apply_settings,
            inputs=[*SETTINGS_INPUTS, lang_state],
            outputs=[settings_status_html],
            api_name=False,
            queue=False,
        )
        settings_reset_btn.click(
            fn=_reset_settings,
            inputs=[lang_state],
            outputs=[
                *SETTINGS_INPUTS,
                verification_min_rounds_selector,
                settings_status_html,
                mode_hint_html,
                search_profile_hint_html,
            ],
            api_name=False,
            queue=False,
        )
        close_settings_btn.click(
            fn=lambda: gr.update(visible=False),
            inputs=None,
            outputs=settings_modal,
            api_name=False,
            queue=False,
        )

        lang_toggle_btn.click(
            fn=toggle_language,
            inputs=[lang_state, mode_selector, search_profile_selector],
            outputs=[
                lang_state,
                nav_html,
                hero_html,
                inp,
                settings_btn,
                stop_btn,
                run_btn,
                out_md,
                output_section,
                settings_modal_title_html,
                settings_hint_html,
                preset_quick_btn,
                preset_balanced_btn,
                preset_deep_btn,
                mode_selector,
                mode_hint_html,
                search_profile_selector,
                search_profile_hint_html,
                search_result_num_selector,
                verification_min_rounds_selector,
                output_detail_level_selector,
                settings_reset_btn,
                settings_apply_btn,
                settings_status_html,
                lang_toggle_btn,
                close_settings_btn,
                export_file,
                footer_html,
            ],
            api_name=False,
            queue=False,
            show_progress="hidden",
        )

    return demo


if __name__ == "__main__":
    _start_stale_task_reaper()
    demo = build_demo()
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))
    launch_kwargs = _build_launch_kwargs(host, port)
    demo.queue().launch(**launch_kwargs)
