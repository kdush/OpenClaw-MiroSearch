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
