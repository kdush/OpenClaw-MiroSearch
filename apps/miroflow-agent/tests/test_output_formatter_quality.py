"""输出格式化质量标记测试 —— Task 2: 拆分答案解析与格式有效性判定。"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.io.output_formatter import OutputFormatter  # noqa: E402


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


def test_format_final_summary_payload_never_exposes_thinking_with_boxed():
    """即使 boxed 有效，最终展示与提取结果也不得包含模型内部推理。"""
    formatter = OutputFormatter()

    payload = formatter.format_final_summary_payload(
        "<think>SECRET reasoning</think>可见正文。\\boxed{正确答案}",
        client=None,
    )

    assert "SECRET reasoning" not in payload["summary"]
    assert "<think>" not in payload["summary"]
    assert payload["boxed_answer"] == "正确答案"
    assert payload["quality"]["format_valid"] is True


def test_format_final_summary_payload_marks_unclosed_boxed_as_invalid():
    """未闭合的 \\boxed{ 不能被误判为有效格式。"""
    formatter = OutputFormatter()

    payload = formatter.format_final_summary_payload(
        "已有可展示正文。\\boxed{尚未闭合",
        client=None,
    )

    assert payload["quality"]["format_valid"] is False
    assert payload["quality"]["fallback_used"] is True
    assert "missing_boxed" in payload["quality"]["issues"]
    assert "已有可展示正文" in payload["boxed_answer"]


def test_unclosed_boxed_marker_without_content_is_not_an_available_fallback():
    """只有未闭合格式标记时不能被当作可展示答案。"""
    formatter = OutputFormatter()

    payload = formatter.format_final_summary_payload(
        "\\boxed{",
        client=None,
    )

    from src.utils.prompt_utils import FORMAT_ERROR_MESSAGE

    assert payload["boxed_answer"] == FORMAT_ERROR_MESSAGE
    assert payload["quality"]["format_valid"] is False
    assert payload["quality"]["fallback_used"] is False


def test_unclosed_think_block_without_answer_is_not_an_available_fallback():
    """未闭合的推理块不能在没有最终正文时冒充答案。"""
    formatter = OutputFormatter()

    payload = formatter.format_final_summary_payload(
        "<think>仍在推理，尚未给出最终答案",
        client=None,
    )

    from src.utils.prompt_utils import FORMAT_ERROR_MESSAGE

    assert payload["boxed_answer"] == FORMAT_ERROR_MESSAGE
    assert payload["quality"]["fallback_used"] is False
    assert "no_answer_available" in payload["quality"]["issues"]


def test_format_error_sentinel_is_not_an_available_fallback():
    """项目自己的格式错误哨兵不能被再次清理后误判为有效答案。"""
    from src.utils.prompt_utils import FORMAT_ERROR_MESSAGE

    formatter = OutputFormatter()
    payload = formatter.format_final_summary_payload(
        FORMAT_ERROR_MESSAGE,
        client=None,
    )

    assert payload["boxed_answer"] == FORMAT_ERROR_MESSAGE
    assert payload["quality"]["format_valid"] is False
    assert payload["quality"]["fallback_used"] is False
    assert "no_answer_available" in payload["quality"]["issues"]


def test_boxed_placeholder_is_not_a_valid_answer():
    """boxed 内的大小写变体占位符也不能被判定为格式有效答案。"""
    formatter = OutputFormatter()
    payload = formatter.format_final_summary_payload(
        "\\boxed{Unknown}",
        client=None,
    )

    from src.utils.prompt_utils import FORMAT_ERROR_MESSAGE

    assert payload["boxed_answer"] == FORMAT_ERROR_MESSAGE
    assert payload["quality"]["format_valid"] is False
    assert payload["quality"]["fallback_used"] is False
    assert "no_answer_available" in payload["quality"]["issues"]


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
