from pathlib import Path
import sys
from unittest.mock import MagicMock

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class FakeStream:
    def __init__(self):
        self.events = []

    async def update(self, event_type, data):
        self.events.append({"event": event_type, "data": data})


@pytest.mark.asyncio
async def test_emit_final_output_sends_markdown_event():
    from src.core.orchestrator import Orchestrator

    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.stream = FakeStream()

    await orchestrator._emit_final_output("# Final result")

    assert orchestrator.stream.events == [
        {"event": "final_output", "data": {"markdown": "# Final result"}}
    ]


@pytest.mark.asyncio
async def test_emit_final_output_skips_unavailable_answer():
    """不可用总结不得发送 final_output，以免事件接收器提前标记完成。"""
    from src.core.orchestrator import Orchestrator

    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.stream = FakeStream()

    emitted = await orchestrator._emit_final_output(
        "",
        {"answer_available": False},
    )

    assert emitted is False
    assert orchestrator.stream.events == []


def test_log_final_outcome_marks_unavailable_answer_as_error():
    """不可用答案不得记录 completed successfully 终态。"""
    from src.core.orchestrator import Orchestrator

    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.task_log = MagicMock()

    orchestrator._log_final_outcome(
        "task-no-answer",
        {
            "answer_available": False,
            "issues": ["summary_generation_failed", "no_answer_available"],
        },
    )

    orchestrator.task_log.log_step.assert_called_once_with(
        "error",
        "Main Agent | Final Answer Unavailable",
        (
            "Main agent task task-no-answer ended without a usable final answer; "
            "issues=['summary_generation_failed', 'no_answer_available']"
        ),
    )
    logged_text = " ".join(
        str(value)
        for call in orchestrator.task_log.log_step.call_args_list
        for value in call.args
    )
    assert "completed successfully" not in logged_text
