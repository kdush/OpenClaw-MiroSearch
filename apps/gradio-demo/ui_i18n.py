"""UI language strings and option labels for the Gradio demo."""

from __future__ import annotations

import contextvars
import html
from typing import Optional

SKILLS_DOWNLOAD_FALLBACK_HINT_EN = "No download URL detected. Please configure SKILLS_PACKAGE_URL environment variable."
SKILLS_DOWNLOAD_COPIED_TEXT_EN = "Link Copied"
SKILLS_DOWNLOAD_FALLBACK_HINT_CN = (
    "未检测到可用下载地址，请配置环境变量 SKILLS_PACKAGE_URL。"
)
SKILLS_DOWNLOAD_COPIED_TEXT_CN = "已复制链接"

LANG_EN = "en"
LANG_CN = "cn"
DEFAULT_LANG = LANG_CN

_UI_LANG: "contextvars.ContextVar[str]" = contextvars.ContextVar(
    "ui_lang", default=DEFAULT_LANG
)

I18N = {
    LANG_EN: {
        "page_title": "Diting - Deep Research",
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
        "lang_toggle_btn": "中文",
        "export_file_label": "Download exported conclusion",
        "skills_download_fallback": SKILLS_DOWNLOAD_FALLBACK_HINT_EN,
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
        "search_confidence": "Confidence",
        "search_threshold": "threshold",
        "search_passed": "passed",
        "search_route_trace": "Route trace",
        "search_fallback_errors": "Recheck errors",
        "search_provider_errors": "Search provider errors",
        "search_display_truncated": "Showing first {visible} of {total} results.",
        "search_failed": "Search failed",
        "search_no_valid_results": "Search providers returned no usable results.",
        "output_idle_placeholder": "*Waiting for research to start...*",
        "thought_card_label": "Thinking & planning",
        "tool_status_done": "Done",
        "agent_display_names": {
            "Main Agent": "Main agent",
            "Sub Agent": "Sub agent",
            "Search Agent": "Search agent",
        },
        "runtime_elapsed": "Elapsed",
        "runtime_search_rounds": "{n} search round(s) done",
        "runtime_detail_summary_retry": "Summary retry #{n}",
        "runtime_detail_degraded_retry": "Degraded retry",
        "runtime_detail_verify_recheck": "Verification failed, extra search",
        "runtime_phase_labels": {
            "初始化": "Getting ready",
            "排队": "Queued",
            "推理": "Analyzing",
            "检索": "Searching",
            "总结": "Writing report",
            "校验": "Cross-checking",
            "工具调用": "Running tool",
            "并行工具": "Running tools in parallel",
            "线索追踪": "Following leads",
            "异常": "Interrupted",
            "已取消": "Stopped",
            "完成": "Finished",
            "执行中": "Working",
        },
        "tool_display_names": {
            "google_search": "Web search",
            "sogou_search": "Sogou search",
            "scrape": "Web scrape",
            "scrape_website": "Web scrape",
            "scrape_webpage": "Web scrape",
            "scrape_url": "Web scrape",
            "scrape_and_extract_info": "Info extraction",
            "show_text": "Text display",
        },
        "output_detail_labels": {
            "compact": "Compact",
            "balanced": "Balanced",
            "detailed": "Detailed",
        },
    },
    LANG_CN: {
        "page_title": "谛听 - 深度研究",
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
        "lang_toggle_btn": "English",
        "export_file_label": "下载导出文件",
        "skills_download_fallback": SKILLS_DOWNLOAD_FALLBACK_HINT_CN,
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
        "search_confidence": "置信度",
        "search_threshold": "阈值",
        "search_passed": "通过",
        "search_route_trace": "链路跟踪",
        "search_fallback_errors": "补检异常",
        "search_provider_errors": "搜索源异常",
        "search_display_truncated": "仅展示前 {visible} 条，完整结果共 {total} 条。",
        "search_failed": "检索失败",
        "search_no_valid_results": "搜索源未返回有效结果",
        "output_idle_placeholder": "*等待开始研究...*",
        "thought_card_label": "思考与规划",
        "tool_status_done": "完成",
        "agent_display_names": {
            "Main Agent": "主智能体 (Main Agent)",
            "Sub Agent": "子智能体 (Sub Agent)",
            "Search Agent": "检索智能体 (Search Agent)",
        },
        "runtime_elapsed": "已用",
        "runtime_search_rounds": "已完成 {n} 次检索",
        "runtime_detail_summary_retry": "第 {n} 次重试生成总结",
        "runtime_detail_degraded_retry": "降级重试",
        "runtime_detail_verify_recheck": "校验未通过，补充检索",
        "runtime_phase_labels": {
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
            "执行中": "执行中",
        },
        "tool_display_names": {
            "google_search": "网络搜索",
            "sogou_search": "搜狗搜索",
            "scrape": "网页抓取",
            "scrape_website": "网页抓取",
            "scrape_webpage": "网页抓取",
            "scrape_url": "网页抓取",
            "scrape_and_extract_info": "信息提取",
            "show_text": "文本展示",
        },
        "output_detail_labels": {
            "compact": "精简",
            "balanced": "适中",
            "detailed": "详细",
        },
    },
}

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


def _resolve_ui_lang(lang: Optional[str] = None) -> str:
    """Explicit lang when valid, else the current UI lang ContextVar."""
    try:
        current = _UI_LANG.get()
    except LookupError:
        current = DEFAULT_LANG
    return lang if lang in I18N else (current if current in I18N else DEFAULT_LANG)


def _label_map(key: str, *, lang: Optional[str] = None) -> dict:
    """Nested label table for the resolved UI lang (runtime phases, tool names)."""
    resolved = _resolve_ui_lang(lang)
    entry = I18N.get(resolved, I18N[DEFAULT_LANG]).get(key)
    if not isinstance(entry, dict):
        entry = I18N[DEFAULT_LANG].get(key)
    return entry if isinstance(entry, dict) else {}


def _progress_copy(key: str, *, lang: Optional[str] = None, **fmt) -> str:
    """UI progress strings; follow explicit lang, else current UI lang ContextVar."""
    resolved = _resolve_ui_lang(lang)
    template = (
        I18N.get(resolved, I18N[DEFAULT_LANG]).get(key)
        or I18N[DEFAULT_LANG].get(key)
        or key
    )
    try:
        return str(template).format(**fmt)
    except Exception:
        return str(template)
