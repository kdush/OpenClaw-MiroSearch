import importlib.util
import json
import os
import sys
from pathlib import Path


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
    assert 'Search: "海拉鲁大陆历史 塞尔达传说"' in markdown
    assert "Found 1 results" in markdown
    assert "检索模式: fallback" in markdown
    assert "<details class=\"process-details\">" in markdown
    assert "## 📋 研究总结" in markdown

    search_steps_pos = markdown.index('<div class="search-step-board">')
    summary_pos = markdown.index("## 📋 研究总结")
    details_pos = markdown.index("<details class=\"process-details\">")
    search_card_pos = markdown.index('<div class="search-card">')
    assert search_steps_pos < summary_pos < details_pos
    assert search_card_pos > details_pos


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
                        "output": {"result": json.dumps({"Pages": []}, ensure_ascii=False)},
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
    assert "<details class=\"process-details\">" not in markdown
    assert markdown.count('<div class="search-card">') == 2


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
    assert (
        'class="ref-citation">[11]</a>'
    ) in linked
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
        "print(\"[1] not a citation\")\n"
        "```\n\n"
        "## 参考文献\n\n"
        "[1] 示例. https://example.com/article\n"
    )
    linked = demo_main._linkify_reference_citations(summary)
    assert 'class="ref-citation">[1]</a>' in linked
    # 代码块内部的 [1] 保持原样，不被替换。
    assert "print(\"[1] not a citation\")" in linked


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
    assert "## 📋 研究总结" in rendered
    assert "未能" in rendered
    # 原始字符串不应直接展示
    assert "No \\boxed{} content found in the final answer." not in rendered


def test_summary_section_has_blank_line_after_html_block():
    """避免回归：search-step-board </div> 与 ## 📋 研究总结 之间必须有空行，
    否则 CommonMark 会把 `##` 视为 HTML block 的延续，标题无法渲染。
    """
    demo_main = _load_demo_main()
    state = demo_main._init_render_state()
    events = [
        {"event": "start_of_agent", "data": {"agent_id": "a1", "agent_name": "main"}},
        {"event": "tool_call", "data": {"tool_call_id": "t1", "tool_name": "google_search", "tool_input": {"q": "demo"}}},
        {"event": "tool_call", "data": {"tool_call_id": "t1", "tool_name": "google_search", "tool_input": {"q": "demo", "result": {"organic": []}}}},
        {"event": "start_of_agent", "data": {"agent_id": "a2", "agent_name": "Final Summary"}},
        {"event": "tool_call", "data": {"tool_call_id": "fs1", "tool_name": "show_text", "tool_input": {"text": "# 关键结论\n本研究表明..."}}},
    ]
    for e in events:
        state = demo_main._update_state_with_event(state, e)
    md = demo_main._render_markdown(state)
    # `</div>` 与 `## 📋 研究总结` 之间应有至少一个空行（即 `\n\n`）
    idx_div = md.rfind("</div>", 0, md.find("## 📋 研究总结"))
    idx_h2 = md.find("## 📋 研究总结")
    between = md[idx_div + len("</div>"):idx_h2]
    assert "\n\n" in between, (
        f"HTML block 与下一个 markdown 标题之间必须空行，实际 between={between!r}"
    )
