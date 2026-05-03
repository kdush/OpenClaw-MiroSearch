"""输出格式化质量标记测试 —— Task 2: 拆分答案解析与格式有效性判定。"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.io.output_formatter import OutputFormatter


def test_format_final_summary_payload_marks_missing_boxed_as_format_invalid():
    """没有 \\boxed{} 但正文非空时，format_valid 应为 False。"""
    formatter = OutputFormatter()

    payload = formatter.format_final_summary_payload(
        "这是完整正文，但没有 boxed。",
        client=None,
    )

    assert payload["boxed_answer"] == "这是完整正文，但没有 boxed。"
    assert payload["quality"]["format_valid"] is False
    assert payload["quality"]["fallback_used"] is True
    assert "model did not use \\boxed{} format" in payload["summary"]


def test_format_final_summary_payload_with_boxed_is_format_valid():
    """有 \\boxed{} 时，format_valid 应为 True。"""
    formatter = OutputFormatter()

    payload = formatter.format_final_summary_payload(
        "结果如下：\\boxed{正确答案}",
        client=None,
    )

    assert payload["boxed_answer"] == "正确答案"
    assert payload["quality"]["format_valid"] is True
    assert payload["quality"]["fallback_used"] is False


def test_format_final_summary_payload_empty_text():
    """完全空文本时，boxed_answer 应为 FORMAT_ERROR_MESSAGE 字符串。"""
    formatter = OutputFormatter()

    payload = formatter.format_final_summary_payload(
        "",
        client=None,
    )

    from src.utils.prompt_utils import FORMAT_ERROR_MESSAGE

    assert payload["boxed_answer"] == FORMAT_ERROR_MESSAGE
    assert payload["quality"]["format_valid"] is False
    assert payload["quality"]["fallback_used"] is False
