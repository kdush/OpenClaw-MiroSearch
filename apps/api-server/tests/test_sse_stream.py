"""SSE 流式端点 /v1/research/{task_id}/stream 测试。

测试策略：
- 使用 httpx AsyncClient + mock TaskStore
- 验证事件流的完整生命周期：事件推送 → 终态检测 → done 事件
- 验证 404、心跳、取消等边界场景
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from main import app
from services.task_store import TaskMeta, TaskStatus


@pytest.fixture
def mock_task_store():
    """Mock TaskStore。"""
    return AsyncMock()


def _make_meta(task_id: str, status: TaskStatus, **kwargs) -> TaskMeta:
    """快捷构建 TaskMeta。"""
    return TaskMeta(task_id=task_id, status=status, **kwargs)


def _make_event(event_id: str, event_type: str, data: dict) -> dict:
    """快捷构建事件。"""
    return {"id": event_id, "event": event_type, "data": data, "ts": time.time()}


def _parse_sse_events(text: str) -> list[dict]:
    """解析 SSE 文本为事件列表。"""
    events = []
    current = {}
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            if current:
                events.append(current)
                current = {}
            continue
        if line.startswith("event:"):
            current["event"] = line[len("event:"):].strip()
        elif line.startswith("data:"):
            raw = line[len("data:"):].strip()
            try:
                current["data"] = json.loads(raw)
            except json.JSONDecodeError:
                current["data"] = raw
    if current:
        events.append(current)
    return events


@pytest.mark.asyncio
async def test_stream_nonexistent_task_returns_404(mock_task_store):
    """GET /v1/research/{task_id}/stream 任务不存在应返回 404。"""
    with patch("routers.research.get_task_store", return_value=mock_task_store):
        mock_task_store.get_task = AsyncMock(return_value=None)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/v1/research/nonexistent/stream")

        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_stream_completed_task_emits_events_then_done(mock_task_store):
    """已完成任务：先返回已有事件，再发 done。"""
    task_id = "stream-test-001"
    meta_running = _make_meta(task_id, TaskStatus.RUNNING)
    meta_completed = _make_meta(task_id, TaskStatus.COMPLETED)

    events_batch = [
        _make_event("1-0", "stage_heartbeat", {"stage": "searching"}),
        _make_event("1-1", "tool_call", {"tool": "web_search"}),
        _make_event("1-2", "final_output", {"markdown": "# Result"}),
    ]

    # 第一次 get_task -> RUNNING（进入循环）
    # 第一次 read_events -> 返回 3 个事件
    # 第二次 get_task -> COMPLETED
    # 第二次 read_events -> 空（没有新事件）
    # 第三次 get_task -> COMPLETED + 无事件 -> 发 done 并退出
    call_count = {"get_task": 0, "read_events": 0}

    async def mock_get_task(tid):
        call_count["get_task"] += 1
        if call_count["get_task"] <= 1:
            return meta_running
        return meta_completed

    async def mock_read_events(tid, last_event_id=None, block_ms=5000, count=100):
        call_count["read_events"] += 1
        if call_count["read_events"] == 1:
            return events_batch
        return []  # 无更多事件

    with patch("routers.research.get_task_store", return_value=mock_task_store):
        mock_task_store.get_task = AsyncMock(side_effect=mock_get_task)
        mock_task_store.read_events = AsyncMock(side_effect=mock_read_events)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/v1/research/{task_id}/stream",
                headers={"Accept": "text/event-stream"},
            )

        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

        parsed = _parse_sse_events(resp.text)
        event_types = [e.get("event") for e in parsed]

        # 应包含 3 个业务事件 + 1 个 done 事件
        assert "stage_heartbeat" in event_types
        assert "tool_call" in event_types
        assert "final_output" in event_types
        assert "done" in event_types

        # done 事件应包含 completed 状态
        done_event = [e for e in parsed if e.get("event") == "done"][0]
        assert done_event["data"]["status"] == "completed"


@pytest.mark.asyncio
async def test_stream_cancelled_task_emits_done_cancelled(mock_task_store):
    """任务被取消：流应发 done + cancelled 状态。"""
    task_id = "stream-test-002"
    meta_running = _make_meta(task_id, TaskStatus.RUNNING)
    meta_cancelled = _make_meta(task_id, TaskStatus.CANCELLED)

    call_count = {"get_task": 0}

    async def mock_get_task(tid):
        call_count["get_task"] += 1
        if call_count["get_task"] <= 1:
            return meta_running
        return meta_cancelled

    async def mock_read_events(tid, last_event_id=None, block_ms=5000, count=100):
        return []

    with patch("routers.research.get_task_store", return_value=mock_task_store):
        mock_task_store.get_task = AsyncMock(side_effect=mock_get_task)
        mock_task_store.read_events = AsyncMock(side_effect=mock_read_events)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/v1/research/{task_id}/stream")

        parsed = _parse_sse_events(resp.text)
        done_events = [e for e in parsed if e.get("event") == "done"]
        assert len(done_events) == 1
        assert done_events[0]["data"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_stream_failed_task_emits_done_failed(mock_task_store):
    """任务失败：流应发 done + failed 状态。"""
    task_id = "stream-test-003"
    meta_running = _make_meta(task_id, TaskStatus.RUNNING)
    meta_failed = _make_meta(task_id, TaskStatus.FAILED, error="LLM timeout")

    call_count = {"get_task": 0}

    async def mock_get_task(tid):
        call_count["get_task"] += 1
        if call_count["get_task"] <= 1:
            return meta_running
        return meta_failed

    async def mock_read_events(tid, last_event_id=None, block_ms=5000, count=100):
        return []

    with patch("routers.research.get_task_store", return_value=mock_task_store):
        mock_task_store.get_task = AsyncMock(side_effect=mock_get_task)
        mock_task_store.read_events = AsyncMock(side_effect=mock_read_events)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/v1/research/{task_id}/stream")

        parsed = _parse_sse_events(resp.text)
        done_events = [e for e in parsed if e.get("event") == "done"]
        assert len(done_events) == 1
        assert done_events[0]["data"]["status"] == "failed"


@pytest.mark.asyncio
async def test_stream_cached_task_returns_immediately(mock_task_store):
    """缓存命中的任务：流直接返回 final_output + done。"""
    task_id = "cached-abc12345"
    meta_cached = _make_meta(task_id, TaskStatus.CACHED)

    events_batch = [
        _make_event("1-0", "final_output", {"markdown": "# Cached"}),
    ]

    call_count = {"read_events": 0}

    async def mock_read_events(tid, last_event_id=None, block_ms=5000, count=100):
        call_count["read_events"] += 1
        if call_count["read_events"] == 1:
            return events_batch
        return []

    with patch("routers.research.get_task_store", return_value=mock_task_store):
        mock_task_store.get_task = AsyncMock(return_value=meta_cached)
        mock_task_store.read_events = AsyncMock(side_effect=mock_read_events)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/v1/research/{task_id}/stream")

        parsed = _parse_sse_events(resp.text)
        event_types = [e.get("event") for e in parsed]

        assert "final_output" in event_types
        assert "done" in event_types


@pytest.mark.asyncio
async def test_stream_incremental_events(mock_task_store):
    """增量读取：验证 last_event_id 正确传递。"""
    task_id = "stream-test-incr"
    meta_running = _make_meta(task_id, TaskStatus.RUNNING)
    meta_completed = _make_meta(task_id, TaskStatus.COMPLETED)

    batch_1 = [_make_event("1-0", "stage_heartbeat", {"stage": "init"})]
    batch_2 = [_make_event("2-0", "final_output", {"markdown": "# Done"})]

    call_count = {"read_events": 0, "get_task": 0}
    captured_last_ids = []

    async def mock_read_events(tid, last_event_id=None, block_ms=5000, count=100):
        call_count["read_events"] += 1
        captured_last_ids.append(last_event_id)
        if call_count["read_events"] == 1:
            return batch_1
        if call_count["read_events"] == 2:
            return batch_2
        return []

    async def mock_get_task(tid):
        call_count["get_task"] += 1
        if call_count["get_task"] <= 2:
            return meta_running
        return meta_completed

    with patch("routers.research.get_task_store", return_value=mock_task_store):
        mock_task_store.get_task = AsyncMock(side_effect=mock_get_task)
        mock_task_store.read_events = AsyncMock(side_effect=mock_read_events)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/v1/research/{task_id}/stream")

        parsed = _parse_sse_events(resp.text)
        event_types = [e.get("event") for e in parsed]

        assert "stage_heartbeat" in event_types
        assert "final_output" in event_types
        assert "done" in event_types

        # 验证增量读取：第一次 last_event_id=None，第二次应传入 "1-0"
        assert captured_last_ids[0] is None
        assert captured_last_ids[1] == "1-0"
