"""Gradio Blocks layout (UI only).

Handlers and pipeline-coupled helpers still live in the demo module; its
namespace is copied into this module's globals by
``ui_demo.build_gradio_blocks`` before ``build_demo`` runs. The
``TYPE_CHECKING`` import below is that list, so linters resolve the names.
"""

from __future__ import annotations

import html
from functools import partial
from typing import TYPE_CHECKING, Optional

import gradio as gr

import api_client
import static_assets
from ui_i18n import (
    DEFAULT_LANG,
    I18N,
    LANG_CN,
    LANG_EN,
    RESEARCH_MODE_DESCRIPTIONS,
    RESEARCH_MODE_LABELS,
    SEARCH_PROFILE_DESCRIPTIONS,
    SEARCH_PROFILE_LABELS,
    _localized_labels,
    _option_hint_html,
)

if TYPE_CHECKING:  # injected by ui_demo.build_gradio_blocks at runtime
    from main import (
        DEFAULT_OUTPUT_DETAIL_LEVEL,
        DEFAULT_RESEARCH_MODE,
        DEFAULT_SEARCH_PROFILE,
        DEFAULT_SEARCH_RESULT_NUM,
        DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS,
        MAX_VERIFICATION_MIN_SEARCH_ROUNDS,
        SEARCH_RESULT_NUM_CHOICES,
        _build_fallback_favicon_data_uri,
        _build_setting_infos,
        _build_settings_summary,
        _export_conclusion,
        _get_render_mode_for_output_detail,
        _get_summary_merge_for_output_detail,
        _icon_svg,
        _is_verified_mode,
        _load_logo_data_uri,
        _normalize_output_detail_level,
        _normalize_research_mode,
        _normalize_search_profile,
        _normalize_search_result_num,
        _normalize_verification_min_search_rounds,
        _resolve_skills_package_download,
        _task_id_bridge_value,
        _update_verification_rounds_visibility,
        get_last_metrics,
        gradio_run,
        reconnect_or_init,
        run_research_once_api_binding,
        stop_current_by_caller_api,
        stop_current_ui,
    )


def build_demo():
    api_client.get_backend_mode()
    logo_data_uri = _load_logo_data_uri()
    favicon_data_uri = _load_logo_data_uri("diting_mark.png")
    fallback_favicon_data_uri = _build_fallback_favicon_data_uri()

    # Theme CSS source: static/theme.css + static/css/* — /diting-static (HOT refresh).
    custom_css = static_assets.gradio_css_import()

    # 统一使用本地 logo，避免外部资源依赖；favicon 用方形标，横版锁合只用于页头。
    favicon_head = (
        f'<link rel="icon" href="{favicon_data_uri or fallback_favicon_data_uri}">'
    )
    hero_logo_src = logo_data_uri or fallback_favicon_data_uri
    hero_brand_name_html = (
        "" if logo_data_uri else '<span class="hero-brand-name">谛听 Diting</span>'
    )

    skills_download_url, _ = _resolve_skills_package_download()

    # Head scripts come from static/js/ via /diting-static (same HOT refresh as CSS).
    demo_head = static_assets.build_head_tags(favicon_head)

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
            # 停止/取消后仍允许再次触发同一事件（默认 once 会把后续点击吞掉）。
            trigger_mode="multiple",
        )
        # Enter 键同样触发研究
        submit_event = inp.submit(
            fn=gradio_run,
            inputs=run_inputs,
            outputs=run_outputs,
            api_name=False,
            show_progress="hidden",
            trigger_mode="multiple",
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
