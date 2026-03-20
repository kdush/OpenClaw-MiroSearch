from pathlib import Path
import sys


# 确保测试可直接导入项目源码
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.tool_executor import ToolExecutor


def _build_executor() -> ToolExecutor:
    executor = ToolExecutor(
        main_agent_tool_manager=None,  # type: ignore[arg-type]
        sub_agent_tool_managers={},  # type: ignore[arg-type]
        output_formatter=None,  # type: ignore[arg-type]
        task_log=None,  # type: ignore[arg-type]
        stream_handler=None,  # type: ignore[arg-type]
    )
    executor.append_current_year_to_fresh_queries = True
    executor.current_year_anchor = 2026
    executor.search_freshness_keywords = (
        "最新",
        "当前",
        "近况",
        "情况",
        "战况",
        "进展",
        "战争",
        "冲突",
        "latest",
        "current",
        "news",
    )
    executor.search_historical_keywords = ("历史", "回顾", "history", "historical")
    return executor


def test_google_search_should_append_current_year_for_fresh_query_without_current_year():
    executor = _build_executor()
    fixed = executor.fix_tool_call_arguments(
        "google_search", {"q": "美国以色列伊朗冲突战争情况 2024 2025"}
    )
    assert fixed["q"].endswith("2026")


def test_google_search_should_not_modify_query_when_current_year_already_present():
    executor = _build_executor()
    original_query = "美国以色列伊朗冲突战争情况 2026"
    fixed = executor.fix_tool_call_arguments("google_search", {"q": original_query})
    assert fixed["q"] == original_query


def test_google_search_should_not_modify_non_fresh_query():
    executor = _build_executor()
    original_query = "阿里云百炼 API 文档"
    fixed = executor.fix_tool_call_arguments("google_search", {"q": original_query})
    assert fixed["q"] == original_query


def test_sogou_search_should_append_current_year_for_fresh_query():
    executor = _build_executor()
    fixed = executor.fix_tool_call_arguments("sogou_search", {"Query": "中东战况进展"})
    assert fixed["Query"].endswith("2026")
