"""TaskStore 测试。"""

import pytest
import redis.asyncio as redis
import asyncio

from services.task_store import TaskStore, TaskStatus, TaskMeta


@pytest.fixture
async def task_store():
    """创建 TaskStore 实例。"""
    try:
        store = await TaskStore.create()
        await store._redis.ping()
    except Exception as exc:
        pytest.skip(f"需要可用的 Valkey/Redis 测试环境: {exc}")
    yield store
    await store.close()


@pytest.mark.asyncio
async def test_create_and_get_task(task_store: TaskStore):
    """测试创建和获取任务。"""
    task_id = "test-task-001"
    meta = await task_store.create_task(
        task_id=task_id,
        status=TaskStatus.QUEUED,
        caller_id="caller-001",
        query="test query",
        mode="balanced",
        search_profile="parallel-trusted",
    )

    assert meta.task_id == task_id
    assert meta.status == TaskStatus.QUEUED
    assert meta.caller_id == "caller-001"
    assert meta.query == "test query"

    # 获取任务
    fetched = await task_store.get_task(task_id)
    assert fetched is not None
    assert fetched.task_id == task_id
    assert fetched.status == TaskStatus.QUEUED
    assert isinstance(fetched.search_result_num, int)
    assert fetched.started_at is None
    assert fetched.finished_at is None

    # 清理
    await task_store.delete_task(task_id)


@pytest.mark.asyncio
async def test_update_task_status(task_store: TaskStore):
    """测试更新任务状态。"""
    task_id = "test-task-002"
    await task_store.create_task(task_id=task_id, status=TaskStatus.QUEUED)

    # 更新为 running
    await task_store.update_task_status(task_id, TaskStatus.RUNNING)
    meta = await task_store.get_task(task_id)
    assert meta.status == TaskStatus.RUNNING
    assert meta.started_at is not None

    # 更新为 completed
    await task_store.update_task_status(task_id, TaskStatus.COMPLETED)
    meta = await task_store.get_task(task_id)
    assert meta.status == TaskStatus.COMPLETED
    assert meta.finished_at is not None

    # 清理
    await task_store.delete_task(task_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("activity", ["status", "stage", "event"])
async def test_task_activity_refreshes_metadata_ttl(
    task_store: TaskStore, activity: str
):
    task_id = f"test-task-ttl-{activity}"
    meta_key = f"{task_store.KEY_TASK}:{task_id}"
    await task_store.create_task(task_id=task_id, status=TaskStatus.RUNNING)
    await task_store._redis.expire(meta_key, 2)

    await asyncio.sleep(1.1)
    if activity == "status":
        await task_store.update_task_status(task_id, TaskStatus.RUNNING)
    elif activity == "stage":
        await task_store.update_task_stage(task_id, "agent:main")
    else:
        await task_store.append_event(
            task_id,
            "stage_heartbeat",
            {"stage": "agent:main"},
        )

    await asyncio.sleep(1.1)
    meta = await task_store.get_task(task_id)

    assert meta is not None
    await task_store.delete_task(task_id)


@pytest.mark.asyncio
async def test_cached_task_sets_finished_at(task_store: TaskStore):
    """测试 cached 任务也会记录 finished_at。"""
    task_id = "test-task-cached"
    await task_store.create_task(task_id=task_id, status=TaskStatus.CACHED)

    await task_store.update_task_status(task_id, TaskStatus.CACHED)
    meta = await task_store.get_task(task_id)

    assert meta is not None
    assert meta.status == TaskStatus.CACHED
    assert meta.finished_at is not None

    await task_store.delete_task(task_id)


@pytest.mark.asyncio
async def test_append_and_read_events(task_store: TaskStore):
    """测试事件流写入和读取。"""
    task_id = "test-task-003"
    await task_store.create_task(task_id=task_id, status=TaskStatus.RUNNING)

    # 写入事件
    event_id1 = await task_store.append_event(task_id, "stage_heartbeat", {"stage": "search"})
    event_id2 = await task_store.append_event(task_id, "tool_call", {"tool": "search_web"})

    assert event_id1 is not None
    assert event_id2 is not None

    # 读取事件
    events = await task_store.read_events(task_id, last_event_id=None, block_ms=100, count=10)
    assert len(events) == 2
    assert events[0]["event"] == "stage_heartbeat"
    assert events[0]["data"]["stage"] == "search"
    assert events[1]["event"] == "tool_call"
    assert events[1]["data"]["tool"] == "search_web"

    # 增量读取
    events = await task_store.read_events(task_id, last_event_id=event_id1, block_ms=100, count=10)
    assert len(events) == 1
    assert events[0]["event"] == "tool_call"

    # 清理
    await task_store.delete_task(task_id)


@pytest.mark.asyncio
async def test_store_and_get_result(task_store: TaskStore):
    """测试结果存储。"""
    task_id = "test-task-004"
    await task_store.create_task(task_id=task_id, status=TaskStatus.COMPLETED)

    # 存储结果
    result = "# Research Result\n\nThis is the final output."
    await task_store.store_result(task_id, result)

    # 获取结果
    fetched = await task_store.get_result(task_id)
    assert fetched == result

    # 清理
    await task_store.delete_task(task_id)


@pytest.mark.asyncio
async def test_cancel_mechanism(task_store: TaskStore):
    """测试取消机制。"""
    task_id = "test-task-005"
    await task_store.create_task(task_id=task_id, status=TaskStatus.RUNNING)

    # 初始未取消
    assert not await task_store.is_cancel_requested(task_id)

    # 请求取消
    await task_store.request_cancel(task_id)
    assert await task_store.is_cancel_requested(task_id)

    # 清理
    await task_store.delete_task(task_id)


@pytest.mark.asyncio
async def test_cancel_by_caller(task_store: TaskStore):
    """测试按 caller 批量取消。"""
    caller_id = "caller-002"

    # 清理可能存在的旧数据
    await task_store.delete_task("task-a")
    await task_store.delete_task("task-b")
    await task_store.delete_task("task-c")

    # 创建多个任务
    await task_store.create_task("task-a", status=TaskStatus.RUNNING, caller_id=caller_id)
    await task_store.create_task("task-b", status=TaskStatus.QUEUED, caller_id=caller_id)
    await task_store.create_task("task-c", status=TaskStatus.QUEUED, caller_id="other-caller")

    # 按 caller 取消
    cancelled = await task_store.cancel_tasks_by_caller(caller_id)
    assert len(cancelled) == 1  # 只有 running 状态的会被取消
    assert "task-a" in cancelled

    # 验证取消标志
    assert await task_store.is_cancel_requested("task-a")
    assert not await task_store.is_cancel_requested("task-b")  # task-b 是 queued，不是 running

    # 清理
    await task_store.delete_task("task-a")
    await task_store.delete_task("task-b")
    await task_store.delete_task("task-c")


@pytest.mark.asyncio
async def test_last_run_metrics(task_store: TaskStore):
    """测试运行指标存储。"""
    metrics = {
        "total_duration_ms": 12345,
        "stage_durations": {"search": 5000, "reasoning": 7000},
    }

    await task_store.set_last_run_metrics(metrics)
    fetched = await task_store.get_last_run_metrics()

    assert fetched is not None
    assert fetched["total_duration_ms"] == 12345
    assert fetched["stage_durations"]["search"] == 5000
