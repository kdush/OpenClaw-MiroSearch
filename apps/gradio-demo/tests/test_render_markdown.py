import importlib.util
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[3]
GRADIO_DEMO_DIR = PROJECT_ROOT / "apps" / "gradio-demo"
MIROFLOW_AGENT_DIR = PROJECT_ROOT / "apps" / "miroflow-agent"
MODULE_PATH = GRADIO_DEMO_DIR / "main.py"


def _load_demo_main():
    os.environ.setdefault("ENABLE_PROMPT_PATCH", "0")
    if str(GRADIO_DEMO_DIR) not in sys.path:
        sys.path.insert(0, str(GRADIO_DEMO_DIR))
    if str(MIROFLOW_AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(MIROFLOW_AGENT_DIR))
    module_name = "gradio_demo_main_render_tests"
    spec = importlib.util.spec_from_file_location(module_name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_render_markdown_collapses_full_process_after_final_summary():
    demo_main = _load_demo_main()
    state = {
        "errors": [],
        "agent_order": ["main-agent", "final-agent"],
        "agents": {
            "main-agent": {
                "agent_name": "Main Agent",
                "tool_call_order": ["search-1", "scrape-1"],
                "tools": {
                    "search-1": {
                        "tool_name": "google_search",
                        "input": {"q": "海拉鲁大陆历史 塞尔达传说"},
                        "output": {
                            "result": json.dumps(
                                {
                                    "organic": [
                                        {
                                            "title": "样例结果",
                                            "link": "https://example.com/result",
                                        }
                                    ],
                                    "searchParameters": {
                                        "provider_mode": "fallback",
                                        "providers_with_results": ["searxng"],
                                    },
                                },
                                ensure_ascii=False,
                            )
                        },
                    },
                    "scrape-1": {
                        "tool_name": "scrape_webpage",
                        "input": {"url": "https://example.com/result"},
                        "output": {"result": {"text": "抓取成功"}},
                    },
                },
            },
            "final-agent": {
                "agent_name": "Final Summary",
                "tool_call_order": ["final-1"],
                "tools": {
                    "final-1": {
                        "tool_name": "message",
                        "content": "这是最终研究总结。",
                    }
                },
            },
        },
    }

    markdown = demo_main._render_markdown(
        state,
        render_mode="full",
        final_summary_merge_strategy="latest",
    )

    assert '<div class="search-step-board">' in markdown
    assert markdown.count('class="search-step-item"') == 1
    assert '检索: "海拉鲁大陆历史 塞尔达传说"' in markdown
    assert "找到 1 条结果" in markdown
    assert "检索模式: fallback" in markdown
    assert '<details class="process-details"' in markdown
    assert "### 研究报告" in markdown

    search_steps_pos = markdown.index('<div class="search-step-board">')
    summary_pos = markdown.index("### 研究报告")
    details_pos = markdown.index('<details class="process-details"')
    search_card_pos = markdown.index('<div class="search-card">')
    # Answer-first: summary above folded process; search steps live inside the fold.
    assert summary_pos < details_pos < search_steps_pos
    assert search_card_pos > details_pos
    assert (
        "思考与检索过程（已完成，点击展开）" in markdown
        or "Thinking & search process" in markdown
    )


def test_render_markdown_does_not_show_step_board_without_final_summary():
    demo_main = _load_demo_main()
    state = {
        "errors": [],
        "agent_order": ["main-agent"],
        "agents": {
            "main-agent": {
                "agent_name": "Main Agent",
                "tool_call_order": ["search-1", "search-2"],
                "tools": {
                    "search-1": {
                        "tool_name": "google_search",
                        "input": {"q": "问题一"},
                        "output": {
                            "result": json.dumps({"organic": []}, ensure_ascii=False)
                        },
                    },
                    "search-2": {
                        "tool_name": "sogou_search",
                        "input": {"q": "问题二"},
                        "output": {
                            "result": json.dumps({"Pages": []}, ensure_ascii=False)
                        },
                    },
                },
            }
        },
    }

    markdown = demo_main._render_markdown(
        state,
        render_mode="full",
        final_summary_merge_strategy="latest",
    )

    assert '<div class="search-step-board">' not in markdown
    assert '<details class="process-details">' not in markdown
    assert markdown.count('<div class="search-card">') == 2


def _state_with_process_and_summary():
    return {
        "errors": [],
        "agent_order": ["main-agent", "final-agent"],
        "agents": {
            "main-agent": {
                "agent_name": "Main Agent",
                "tool_call_order": ["search-1"],
                "tools": {
                    "search-1": {
                        "tool_name": "google_search",
                        "input": {"q": "海拉鲁大陆历史 塞尔达传说"},
                        "output": {
                            "result": json.dumps(
                                {
                                    "organic": [
                                        {
                                            "title": "样例结果",
                                            "link": "https://example.com/result",
                                        }
                                    ],
                                    "searchParameters": {
                                        "provider_mode": "fallback",
                                        "providers_with_results": ["searxng"],
                                    },
                                },
                                ensure_ascii=False,
                            )
                        },
                    }
                },
            },
            "final-agent": {
                "agent_name": "Final Summary",
                "tool_call_order": ["final-1"],
                "tools": {
                    "final-1": {
                        "tool_name": "message",
                        "content": "## 结论\n\n这是最终研究总结。",
                    }
                },
            },
        },
    }


@pytest.mark.parametrize("render_mode", ["full", "summary_with_details"])
def test_prepare_export_markdown_drops_process_from_rendered_output(render_mode):
    demo_main = _load_demo_main()
    markdown = demo_main._render_markdown(
        _state_with_process_and_summary(),
        render_mode=render_mode,
        final_summary_merge_strategy="latest",
    )

    exported = demo_main._prepare_export_markdown(markdown)

    assert "这是最终研究总结。" in exported
    assert "海拉鲁大陆历史" not in exported
    assert "找到 1 条结果" not in exported
    assert "search-step-board" not in exported
    assert "思考与检索过程" not in exported


def test_linkify_reference_citations_replaces_inline_markers():
    demo_main = _load_demo_main()
    summary = (
        "SpaceX 已宣布有权收购 Cursor[2][5][9][11]。\n"
        "北京时间上午 6 时许，消息引发广泛关注[11]。\n\n"
        "---\n\n"
        "**References**\n\n"
        "[2] 600亿美元!SpaceX拿下AI编程公司Cursor收购权. "
        "https://baijiahao.baidu.com/s?id=1863127511634299122&wfr=spider&for=pc\n\n"
        "[5] 追不上就直接买，马斯克600亿美元收购Cursor. "
        "http://app.myzaker.com/news/article.php?pk=69e85e838e9f096c0b135fa2\n\n"
        "[9] SpaceX、Cursor达成合作意向. "
        "https://baijiahao.baidu.com/s?id=1863144438361763046&wfr=spider&for=pc\n\n"
        "[11] SpaceX宣布有权以600亿美元收购Cursor. "
        "http://www.sohu.com/a/1012767485_120988576\n"
    )

    linked = demo_main._linkify_reference_citations(summary)

    # 正文里每一处 [N] 都被替换为指向对应 URL 的 HTML 锚点。
    assert (
        '<a href="https://baijiahao.baidu.com/s?id=1863127511634299122&amp;wfr=spider&amp;for=pc" '
        'target="_blank" rel="noopener noreferrer" class="ref-citation">[2]</a>'
    ) in linked
    assert (
        '<a href="http://app.myzaker.com/news/article.php?pk=69e85e838e9f096c0b135fa2" '
        'target="_blank" rel="noopener noreferrer" class="ref-citation">[5]</a>'
    ) in linked
    assert ('class="ref-citation">[11]</a>') in linked
    # 连续出现的引用应各自独立生成链接。
    assert linked.count('class="ref-citation">[11]</a>') == 2
    # References 章节本身保持原样，内部的 [N] 标号不被改写。
    ref_index = linked.index("**References**")
    assert "[2]" in linked[ref_index:]
    assert "ref-citation" not in linked[ref_index:]


def test_linkify_reference_citations_no_references_section():
    demo_main = _load_demo_main()
    text = "普通段落里的 [1] 和 [2]，但没有参考文献章节。"
    assert demo_main._linkify_reference_citations(text) == text


def test_linkify_reference_citations_skips_code_blocks():
    demo_main = _load_demo_main()
    summary = (
        "正文引用 [1]。\n\n"
        "```\n"
        'print("[1] not a citation")\n'
        "```\n\n"
        "## 参考文献\n\n"
        "[1] 示例. https://example.com/article\n"
    )
    linked = demo_main._linkify_reference_citations(summary)
    assert 'class="ref-citation">[1]</a>' in linked
    # 代码块内部的 [1] 保持原样，不被替换。
    assert 'print("[1] not a citation")' in linked


def test_humanize_pipeline_fallback_rewrites_format_error():
    demo_main = _load_demo_main()
    out = demo_main._humanize_pipeline_fallback(
        "No \\boxed{} content found in the final answer."
    )
    assert "\\boxed{}" in out  # 重写后仍然解释了原因
    assert "未能" in out and "降级" in out


def test_humanize_pipeline_fallback_rewrites_incomplete_marker():
    demo_main = _load_demo_main()
    out = demo_main._humanize_pipeline_fallback(
        "Task incomplete - reached maximum turns. Will retry with failure experience."
    )
    assert "未能" in out


def test_humanize_pipeline_fallback_keeps_normal_summary():
    demo_main = _load_demo_main()
    text = "## 关键结论\n本研究表明...证据 [1] [2]"
    assert demo_main._humanize_pipeline_fallback(text) == text


def test_build_summary_section_humanizes_fallback():
    demo_main = _load_demo_main()
    blocks = ["No \\boxed{} content found in the final answer."]
    rendered = "".join(demo_main._build_summary_section(blocks))
    assert "### 研究报告" in rendered
    assert "未能" in rendered
    # 原始字符串不应直接展示
    assert "No \\boxed{} content found in the final answer." not in rendered


def test_build_summary_section_strips_output_formatter_diagnostics():
    """OutputFormatter 注入的 Final Answer / Extracted Result / Token Usage 分段
    属于面向 CLI/评测日志的调试信息，前端展示时必须剔除，避免在 UI 出现重复正文、
    token 统计和 'Pricing is disabled' 等噪声（回归 demo 页面的截图问题）。"""
    demo_main = _load_demo_main()
    raw_block = (
        "============================== Final Answer ==============================\n"
        "腾讯魔方工作室近期核心动态为《暗区突围》与《三角洲行动》双产品线成功运营。\n"
        "\n"
        "-------------------- Extracted Result --------------------\n"
        "腾讯魔方工作室近期核心动态为《暗区突围》与《三角洲行动》双产品线成功运营。\n"
        "\n"
        "-------------------- Token Usage --------------------\n"
        "Total Input Tokens: 10217\n"
        "Total Cache Input Tokens: 0\n"
        "Total Output Tokens: 4306\n"
        "-----------------------------------------\n"
        "Pricing is disabled - no cost information available\n"
        "-----------------------------------------\n"
    )
    rendered = "".join(demo_main._build_summary_section([raw_block]))
    assert "### 研究报告" in rendered
    assert "腾讯魔方工作室近期核心动态" in rendered
    # 调试分段标记与配套噪声不应出现在 UI 渲染输出中
    for marker in (
        "Final Answer",
        "Extracted Result",
        "Token Usage",
        "Total Input Tokens",
        "Pricing is disabled",
    ):
        assert marker not in rendered, f"诊断标记残留: {marker}"


def test_render_markdown_deduplicates_sanitized_final_summary_blocks():
    """流式 message 与刷新回放 final_output 可能分别携带同一份总结：
    一份是纯正文，一份带 OutputFormatter 诊断分段。UI 应先清洗再去重，
    保证流式输出和刷新后的最终渲染一致。"""
    demo_main = _load_demo_main()
    conclusion = "腾讯魔方工作室近期核心动态为双产品线成功运营。"
    diagnostic_block = (
        "============================== Final Answer ==============================\n"
        f"{conclusion}\n"
        "\n"
        "-------------------- Extracted Result --------------------\n"
        f"{conclusion}\n"
        "\n"
        "-------------------- Token Usage --------------------\n"
        "Total Input Tokens: 10217\n"
        "Pricing is disabled - no cost information available\n"
    )
    state = {
        "errors": [],
        "agent_order": ["final-stream", "final-output"],
        "agents": {
            "final-stream": {
                "agent_name": "Final Summary",
                "tool_call_order": ["stream-message"],
                "tools": {
                    "stream-message": {
                        "tool_name": "message",
                        "content": conclusion,
                    }
                },
            },
            "final-output": {
                "agent_name": "Final Summary",
                "tool_call_order": ["final-output-message"],
                "tools": {
                    "final-output-message": {
                        "tool_name": "message",
                        "content": diagnostic_block,
                    }
                },
            },
        },
    }

    rendered = demo_main._render_markdown(
        state,
        render_mode="summary_with_details",
        final_summary_merge_strategy="all_unique",
    )

    assert rendered.count(conclusion) == 1
    assert "Extracted Result" not in rendered
    assert "Token Usage" not in rendered


def test_format_search_results_shows_structured_search_failure():
    demo_main = _load_demo_main()
    rendered = demo_main._format_search_results(
        {"q": "test query"},
        {
            "result": json.dumps(
                {
                    "success": False,
                    "error": "searxng: timeout",
                    "organic": [],
                    "provider_fallback": ["searxng: timeout"],
                },
                ensure_ascii=False,
            )
        },
    )

    assert "检索失败" in rendered
    assert "searxng: timeout" in rendered


def test_default_model_family_uses_qwen_when_env_missing(monkeypatch):
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    for env_name in (
        "DEFAULT_MODEL_NAME",
        "MODEL_TOOL_NAME",
        "MODEL_FAST_NAME",
        "MODEL_THINKING_NAME",
        "MODEL_SUMMARY_NAME",
    ):
        monkeypatch.delenv(env_name, raising=False)

    demo_main = _load_demo_main()

    assert demo_main.DEFAULT_MODEL_NAME.startswith("qwen")
    assert demo_main.DEFAULT_MODEL_TOOL_NAME.startswith("qwen")
    assert demo_main.DEFAULT_MODEL_FAST_NAME.startswith("qwen")
    assert demo_main.DEFAULT_MODEL_THINKING_NAME.startswith("qwen")
    assert demo_main.DEFAULT_MODEL_SUMMARY_NAME.startswith("qwen")


def test_create_export_file_writes_markdown_pdf_and_docx(tmp_path):
    demo_main = _load_demo_main()
    markdown = (
        "# 研究结论\n\n"
        "腾讯魔方工作室近期核心动态为双产品线成功运营。\n\n"
        "## References\n\n"
        "[1] https://example.com/report"
    )

    md_path = Path(
        demo_main._create_export_file(
            markdown,
            "md",
            output_dir=tmp_path,
            task_id="task-123",
        )
    )
    pdf_path = Path(
        demo_main._create_export_file(
            markdown,
            "pdf",
            output_dir=tmp_path,
            task_id="task-123",
        )
    )
    docx_path = Path(
        demo_main._create_export_file(
            markdown,
            "docx",
            output_dir=tmp_path,
            task_id="task-123",
        )
    )

    assert md_path.suffix == ".md"
    assert "腾讯魔方工作室" in md_path.read_text(encoding="utf-8")
    assert pdf_path.suffix == ".pdf"
    assert pdf_path.read_bytes().startswith(b"%PDF-")
    assert b"STSong-Light" in pdf_path.read_bytes()
    assert docx_path.suffix == ".docx"
    assert zipfile.is_zipfile(docx_path)
    with zipfile.ZipFile(docx_path) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert "腾讯魔方工作室" in document_xml


def test_export_conclusion_returns_visible_file_update(tmp_path, monkeypatch):
    demo_main = _load_demo_main()
    monkeypatch.setattr(demo_main, "EXPORT_OUTPUT_DIR", tmp_path)

    update = demo_main._export_conclusion(
        "# 研究结论\n\n这是导出正文。",
        "md",
        {"task_id": "task-abc"},
    )

    assert update["visible"] is True
    exported_path = Path(update["value"])
    assert exported_path.exists()
    assert "task-abc" in exported_path.name
    assert "这是导出正文" in exported_path.read_text(encoding="utf-8")


def test_create_export_file_uses_unique_filename_with_same_second(
    tmp_path, monkeypatch
):
    demo_main = _load_demo_main()
    monkeypatch.setattr(demo_main.time, "strftime", lambda _format: "20260524-231500")

    first_path = Path(
        demo_main._create_export_file(
            "# 研究结论\n\n第一次导出。",
            "md",
            output_dir=tmp_path,
            task_id="task-same-second",
        )
    )
    second_path = Path(
        demo_main._create_export_file(
            "# 研究结论\n\n第二次导出。",
            "md",
            output_dir=tmp_path,
            task_id="task-same-second",
        )
    )

    assert first_path != second_path
    assert first_path.exists()
    assert second_path.exists()
    assert "第一次导出" in first_path.read_text(encoding="utf-8")
    assert "第二次导出" in second_path.read_text(encoding="utf-8")


_DECORATED_REPORT_SAMPLE = """### 研究报告

<div class="report-glance"><div class="report-glance-meta"><span class="report-glance-kicker">结论</span><span class="confidence-badge confidence-high">置信度：高</span></div><p class="report-glance-body">腾讯魔方工作室聚焦两条产品线。<br>下半年仍以流水为纲。</p></div>

## 关键发现

- 新品上线 <a href="https://example.com/a?x=1&amp;y=2" target="_blank" rel="noopener noreferrer" class="ref-citation ref-chip">[1]</a>

<details class="report-fold">
<summary>证据与来源（3）</summary>

### 来源清单

- https://example.com/a

<div class="mermaid-card">

### 关系拓扑

```mermaid
graph TD;A-->B;
```

</div>

</details>

## 争议与不确定 <span class="conflict-tag">冲突/不确定</span>

样本量偏小。

## References

[1] https://example.com/a?x=1&y=2

<details class="process-details" data-collapsed="1" data-fp="42">
<summary>🧭 思考与检索过程 · 3</summary>

<details class="thought-card"><summary>思考</summary>

不应出现在导出里的过程正文

</details>

</details>
"""


def test_prepare_export_markdown_keeps_conclusion_and_plainifies_html():
    demo_main = _load_demo_main()

    exported = demo_main._prepare_export_markdown(_DECORATED_REPORT_SAMPLE)

    assert "不应出现在导出里的过程正文" not in exported
    assert "process-details" not in exported
    assert "thought-card" not in exported
    assert "report-glance" not in exported
    assert "conflict-tag" not in exported
    assert "mermaid-card" not in exported
    assert "## 结论" in exported
    assert "置信度：高" in exported
    assert "腾讯魔方工作室聚焦两条产品线。\n下半年仍以流水为纲。" in exported
    assert "[1](https://example.com/a?x=1&y=2)" in exported
    assert "## 证据与来源" in exported
    assert "```mermaid" in exported
    assert "## 争议与不确定" in exported
    assert "样本量偏小。" in exported


def test_prepare_export_markdown_drops_progress_before_report_heading():
    demo_main = _load_demo_main()
    rendered = (
        '<div class="spinner">研究进行中…</div>\n\n## 检索步骤\n\n正在检索…\n\n---\n\n'
        + _DECORATED_REPORT_SAMPLE
    )

    exported = demo_main._prepare_export_markdown(rendered)

    assert "spinner" not in exported
    assert "正在检索" not in exported
    assert exported.startswith("### 研究报告")


def test_prepare_export_markdown_uses_placeholder_when_conclusion_missing():
    demo_main = _load_demo_main()

    assert "没有可导出的研究结论" in demo_main._prepare_export_markdown("")


def test_prepare_export_markdown_falls_back_to_visible_text_mid_run():
    demo_main = _load_demo_main()

    exported = demo_main._prepare_export_markdown(
        '<div class="spinner">研究进行中…</div>\n\n思考片段…'
    )

    assert "研究进行中…" in exported
    assert "spinner" not in exported


def test_export_conclusion_returns_hidden_file_update_when_export_fails(monkeypatch):
    demo_main = _load_demo_main()

    def _raise_export_error(*_args, **_kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(demo_main, "_create_export_file", _raise_export_error)

    update = demo_main._export_conclusion(
        "# 研究结论\n\n这是导出正文。",
        "md",
        {"task_id": "task-error"},
    )

    assert update["visible"] is False
    assert update["value"] is None
    assert "导出失败" in update["label"]
    assert "permission denied" in update["label"]


def test_normalize_latex_noop_without_backslash():
    demo_main = _load_demo_main()
    text = "普通 Markdown：**加粗**、*斜体*、- 列表项\n\n## 二级标题"
    assert demo_main._normalize_latex_like_markup(text) == text


def test_normalize_latex_unwraps_outer_boxed():
    demo_main = _load_demo_main()
    text = "\\boxed{\\textbf{核心结论}：示例内容。}"
    out = demo_main._normalize_latex_like_markup(text)
    # 最外层 \boxed{} 被剥掉，内部 \textbf 转 Markdown
    assert "\\boxed" not in out
    assert "**核心结论**：示例内容。" == out


def test_normalize_latex_keeps_embedded_boxed_when_not_wrapping_whole():
    demo_main = _load_demo_main()
    text = "前置说明。\\boxed{短结论}\n\n后置备注。"
    out = demo_main._normalize_latex_like_markup(text)
    # 整段不只包含 \boxed{...}，不做外层剥离；但内部 \boxed{X} 会替换为 **X**
    assert "**短结论**" in out
    assert "前置说明" in out and "后置备注" in out
    assert "\\boxed" not in out


def test_normalize_latex_preserves_empty_boxed_marker():
    """fallback 文案依赖 `No \\boxed{} content found` 字面匹配，空 \\boxed{} 必须保留。"""
    demo_main = _load_demo_main()
    text = "No \\boxed{} content found in the final answer."
    out = demo_main._normalize_latex_like_markup(text)
    assert "\\boxed{}" in out
    assert "No \\boxed{} content found in the final answer." == out


def test_normalize_latex_inline_commands_to_markdown():
    demo_main = _load_demo_main()
    text = (
        "\\textbf{粗体} \\emph{斜体1} \\textit{斜体2} "
        "\\underline{下划线} \\texttt{代码}"
    )
    out = demo_main._normalize_latex_like_markup(text)
    assert "**粗体**" in out
    assert "*斜体1*" in out
    assert "*斜体2*" in out
    assert "<u>下划线</u>" in out
    assert "`代码`" in out
    assert "\\text" not in out and "\\emph" not in out


def test_normalize_latex_section_commands_to_headings():
    demo_main = _load_demo_main()
    text = (
        "\\section*{一、 执行摘要}\n正文一。\n"
        "\\subsection*{1.1 背景}\n正文二。\n"
        "\\subsubsection*{1.1.1 细节}\n正文三。"
    )
    out = demo_main._normalize_latex_like_markup(text)
    assert "## 一、 执行摘要" in out
    assert "### 1.1 背景" in out
    assert "#### 1.1.1 细节" in out
    assert "\\section" not in out


def test_normalize_latex_itemize_to_markdown_list():
    demo_main = _load_demo_main()
    text = (
        "\\begin{itemize} "
        "\\item \\textbf{项一}：说明一。 "
        "\\item \\textbf{项二}：说明二。 "
        "\\end{itemize}"
    )
    out = demo_main._normalize_latex_like_markup(text)
    assert "- **项一**：说明一" in out
    assert "- **项二**：说明二" in out
    assert "\\begin" not in out and "\\item" not in out and "\\end" not in out


def test_normalize_latex_nested_commands():
    demo_main = _load_demo_main()
    text = "\\section*{\\textbf{嵌套标题}}"
    out = demo_main._normalize_latex_like_markup(text)
    assert "## **嵌套标题**" in out


def test_normalize_latex_escaped_chars():
    demo_main = _load_demo_main()
    text = "利润率 70\\% 与 \\$100 及 A\\&B \\#1"
    out = demo_main._normalize_latex_like_markup(text)
    assert "70%" in out and "$100" in out and "A&B" in out and "#1" in out


def test_normalize_latex_line_break_command():
    demo_main = _load_demo_main()
    text = "第一行\\\\ 第二行"
    out = demo_main._normalize_latex_like_markup(text)
    # \\ 后紧跟空白，应替换为 Markdown 硬换行（两个空格 + \n）
    assert "  \n" in out
    assert "\\\\" not in out


def test_normalize_latex_end_to_end_report_shape():
    """回归 demo 页问题场景：整份报告被 \\boxed{} 包裹且夹杂多种 LaTeX 命令。"""
    demo_main = _load_demo_main()
    text = (
        "\\boxed{ \\textbf{DeepSeek-V4 模型水平调研报告}\n"
        "\\textbf{报告日期}：2026年5月2日\n\n"
        "\\section*{一、 执行摘要}\n"
        "这是摘要正文。\n\n"
        "\\section*{二、 模型矩阵}\n"
        "\\begin{itemize}\n"
        "\\item \\textbf{Pro}：1.6T 参数。\n"
        "\\item \\textbf{Flash}：284B 参数。\n"
        "\\end{itemize}\n"
        "}"
    )
    out = demo_main._normalize_latex_like_markup(text)
    # 最外层 \boxed 被剥离
    assert not out.startswith("\\boxed")
    assert "\\boxed" not in out
    # 所有 LaTeX 控制命令都被转义掉
    for marker in ("\\textbf", "\\section", "\\begin", "\\end", "\\item"):
        assert marker not in out
    # 生成了预期的 Markdown 结构
    assert "**DeepSeek-V4 模型水平调研报告**" in out
    assert "**报告日期**：2026年5月2日" in out
    assert "## 一、 执行摘要" in out
    assert "## 二、 模型矩阵" in out
    assert "- **Pro**：1.6T 参数。" in out
    assert "- **Flash**：284B 参数。" in out


def test_build_summary_section_normalizes_latex_heavy_block():
    demo_main = _load_demo_main()
    blocks = [
        "\\boxed{\\textbf{结论标题}\\section*{要点}\\begin{itemize}"
        "\\item 要点一 \\item 要点二\\end{itemize}}"
    ]
    rendered = "".join(demo_main._build_summary_section(blocks))
    assert "### 研究报告" in rendered
    assert "**结论标题**" in rendered
    assert "## 要点" in rendered
    assert "- 要点一" in rendered and "- 要点二" in rendered
    # 原始 LaTeX 命令不应直接出现在渲染结果中
    for marker in ("\\boxed", "\\textbf", "\\section", "\\begin", "\\item", "\\end"):
        assert marker not in rendered


def test_summary_section_comes_before_folded_process():
    """答案优先：研究总结在折叠过程区之前；过程区内 HTML 与后续内容保持可展开。"""
    demo_main = _load_demo_main()
    state = demo_main._init_render_state()
    events = [
        {"event": "start_of_agent", "data": {"agent_id": "a1", "agent_name": "main"}},
        {
            "event": "tool_call",
            "data": {
                "tool_call_id": "t1",
                "tool_name": "google_search",
                "tool_input": {"q": "demo"},
            },
        },
        {
            "event": "tool_call",
            "data": {
                "tool_call_id": "t1",
                "tool_name": "google_search",
                "tool_input": {"q": "demo", "result": {"organic": []}},
            },
        },
        {
            "event": "start_of_agent",
            "data": {"agent_id": "a2", "agent_name": "Final Summary"},
        },
        {
            "event": "tool_call",
            "data": {
                "tool_call_id": "fs1",
                "tool_name": "show_text",
                "tool_input": {"text": "# 最终结论\n本研究表明..."},
            },
        },
    ]
    for e in events:
        state = demo_main._update_state_with_event(state, e)
    md = demo_main._render_markdown(state)
    idx_h2 = md.find("### 研究报告")
    idx_details = md.find('<details class="process-details"')
    assert idx_h2 != -1 and idx_details != -1
    assert idx_h2 < idx_details
    assert "思考与检索过程（已完成，点击展开）" in md
    # Folded panel must not start open
    assert '<details class="process-details" open>' not in md


def test_update_state_with_final_output_renders_summary():
    """final_output 事件应创建 Final Summary agent 并在渲染时展示内容。"""
    demo_main = _load_demo_main()
    state = demo_main._init_render_state()

    state = demo_main._update_state_with_event(
        state,
        {"event": "final_output", "data": {"markdown": "# 缓存结果\n\n正文"}},
    )
    markdown = demo_main._render_markdown(state)

    assert "### 研究报告" in markdown
    # Consumer reshape turns the H1 into the glance conclusion body
    assert "缓存结果" in markdown or "正文" in markdown
    assert "等待开始研究" not in markdown


def test_prepare_safe_threads_detail_level_compact_skips_auto_analysis():
    """compact 档位不得自动注入内容分析 / Mermaid；detailed 可注入。"""
    demo_main = _load_demo_main()
    conflict_report = (
        "## TL;DR\n传闻待核实。\n\n"
        "## 冲突与不确定\n"
        "- 说法 A 与官方通报矛盾\n"
        "- 缺少权威信源二次确认\n\n"
        "## References\n"
        "[1] https://example.com/a\n"
    )
    compact = demo_main._prepare_user_facing_report_safe(
        conflict_report, detail_level="compact"
    )
    assert "```mermaid" not in compact
    assert "内容分析" not in compact

    detailed = demo_main._prepare_user_facing_report_safe(
        conflict_report, detail_level="detailed"
    )
    assert "内容分析" in detailed or "```mermaid" in detailed


def test_build_summary_section_passes_compact_detail_level():
    demo_main = _load_demo_main()
    conflict_report = (
        "## 结论\n短结论。\n\n"
        "## 冲突与不确定\n"
        "- 双方说法不一致\n\n"
        "## References\n"
        "[1] https://example.com/b\n"
    )
    rendered = "".join(
        demo_main._build_summary_section(
            [conflict_report], output_detail_level="compact"
        )
    )
    assert "### 研究报告" in rendered
    assert "```mermaid" not in rendered
    assert (
        "report-glance" in rendered or "report-tldr" in rendered or "结论" in rendered
    )


def test_decorate_folds_evidence_and_deep():
    demo_main = _load_demo_main()
    md = (
        "## 结论\n短结论。\n\n"
        "<!-- confidence:high -->\n"
        "**置信度：高**\n\n"
        "## 要点\n- 要点一\n\n"
        "## 证据与来源\n1. https://example.com/a\n\n"
        "## 深入了解\n### 详细分析\n很长。\n"
    )
    out = demo_main._decorate_report_for_web(md)
    assert "report-glance" in out
    assert out.count('<details class="report-fold">') == 2
    assert "证据与来源" in out
    assert "深入了解" in out


def test_build_summary_section_keeps_single_glance_from_stream_dupes():
    """Balanced all_unique merge can keep mid then high confidence reshapes."""
    demo_main = _load_demo_main()
    mid = (
        "## 结论\n"
        "1+1=2\n\n"
        "<!-- confidence:mid -->\n"
        "**置信度：中**\n\n"
        "## 深入了解\n### 详细分析\n补充说明。\n"
    )
    high = (
        "## 结论\n"
        "1+1=2\n\n"
        "<!-- confidence:high -->\n"
        "**置信度：高**\n\n"
        "## 深入了解\n### 详细分析\n补充说明。\n"
    )
    rendered = "".join(
        demo_main._build_summary_section([mid, high], output_detail_level="balanced")
    )
    assert rendered.count('class="report-glance"') == 1
    assert "置信度：高" in rendered
    assert "置信度：中" not in rendered
    assert "深入了解" in rendered


def test_normalize_output_detail_level_accepts_cn_labels():
    demo_main = _load_demo_main()
    assert demo_main._normalize_output_detail_level("精简") == "compact"
    assert demo_main._normalize_output_detail_level("适中") == "balanced"
    assert demo_main._normalize_output_detail_level("详细") == "detailed"
