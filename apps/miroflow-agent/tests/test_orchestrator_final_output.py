from pathlib import Path
import sys

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
