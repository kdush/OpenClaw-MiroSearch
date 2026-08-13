"""TaskEventSink 阶段字段映射测试 —— Task 4。"""

import pytest
from unittest.mock import AsyncMock

from services.task_event_sink import TaskEventSink


@pytest.mark.asyncio
async def test_task_event_sink_maps_actual_event_fields_to_stage():
    """stage_heartbeat/start_of_agent/tool_call 应使用实际字段名映射阶段。"""
    store = AsyncMock()
    sink = TaskEventSink(store, "task-1")

    await sink.put(
        {"event": "stage_heartbeat", "data": {"phase": "检索", "detail": "第 1 轮"}}
    )
    await sink.put({"event": "start_of_agent", "data": {"agent_name": "Final Summary"}})
    await sink.put({"event": "tool_call", "data": {"tool_name": "google_search"}})

    store.update_task_stage.assert_any_call("task-1", "检索:第 1 轮")
    store.update_task_stage.assert_any_call("task-1", "agent:Final Summary")
    store.update_task_stage.assert_any_call("task-1", "tool:google_search")


@pytest.mark.asyncio
async def test_task_event_sink_stores_final_output_without_claiming_terminal_status():
    """内容事件可先落结果，但最终状态必须由 Worker 的结构化结果决定。"""
    store = AsyncMock()
    sink = TaskEventSink(store, "task-final")

    await sink.put({"event": "final_output", "data": {"markdown": "# Final result"}})

    store.store_result.assert_awaited_once_with("task-final", "# Final result")
    store.update_task_status.assert_not_awaited()
