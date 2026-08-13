"""TaskStore 测试。"""

import asyncio
import json
import uuid
from unittest.mock import AsyncMock

import pytest

from services.task_store import TaskMeta, TaskStatus, TaskStore
from settings import settings


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
    event_id1 = await task_store.append_event(
        task_id, "stage_heartbeat", {"stage": "search"}
    )
    event_id2 = await task_store.append_event(
        task_id, "tool_call", {"tool": "search_web"}
    )

    assert event_id1 is not None
    assert event_id2 is not None

    # 读取事件
    events = await task_store.read_events(
        task_id, last_event_id=None, block_ms=100, count=10
    )
    assert len(events) == 2
    assert events[0]["event"] == "stage_heartbeat"
    assert events[0]["data"]["stage"] == "search"
    assert events[1]["event"] == "tool_call"
    assert events[1]["data"]["tool"] == "search_web"

    # 增量读取
    events = await task_store.read_events(
        task_id, last_event_id=event_id1, block_ms=100, count=10
    )
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
async def test_shared_result_cache_roundtrip_preserves_quality():
    """跨进程结果缓存必须同时保存正文和总结质量，并应用独立 TTL。"""
    redis_client = AsyncMock()
    store = TaskStore(redis_client)
    quality = {
        "format_valid": False,
        "fallback_used": True,
        "issues": ["missing_boxed"],
        "answer_available": True,
    }

    await store.store_cached_result("cache-key", "# 已缓存结果", quality)

    expected_payload = json.dumps(
        {
            "result": "# 已缓存结果",
            "quality": quality,
        },
        ensure_ascii=False,
    )
    redis_client.set.assert_awaited_once_with(
        f"{store.KEY_RESULT_CACHE}:cache-key",
        expected_payload,
        ex=settings.result_cache_ttl_seconds,
    )

    redis_client.get.return_value = expected_payload
    cached = await store.get_cached_result("cache-key")

    assert cached == {
        "result": "# 已缓存结果",
        "quality": quality,
    }


@pytest.mark.asyncio
async def test_shared_result_cache_deletes_incomplete_payload():
    """缺少可用正文的共享缓存应删除，避免每次请求重复读取损坏条目。"""
    redis_client = AsyncMock()
    redis_client.get.return_value = json.dumps(
        {"result": "   ", "quality": {}},
        ensure_ascii=False,
    )
    store = TaskStore(redis_client)

    cached = await store.get_cached_result("broken-cache-key")

    assert cached is None
    redis_client.delete.assert_awaited_once_with(
        f"{store.KEY_RESULT_CACHE}:broken-cache-key"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("quality", ["corrupt", [], 1])
async def test_shared_result_cache_deletes_non_mapping_quality(quality):
    """非空 quality 必须是映射，不能伪装成旧版缺失质量。"""
    redis_client = AsyncMock()
    redis_client.get.return_value = json.dumps(
        {"result": "# cached", "quality": quality},
        ensure_ascii=False,
    )
    store = TaskStore(redis_client)

    assert await store.get_cached_result("invalid-quality-type") is None
    redis_client.delete.assert_awaited_once_with(
        f"{store.KEY_RESULT_CACHE}:invalid-quality-type"
    )


@pytest.mark.asyncio
async def test_shared_result_cache_zero_ttl_means_no_expiration(monkeypatch):
    """TTL=0 的明确语义是永久缓存，Redis SET 不应传入非法 ex=0。"""
    redis_client = AsyncMock()
    store = TaskStore(redis_client)
    monkeypatch.setattr(settings, "result_cache_ttl_seconds", 0)
    quality = {
        "format_valid": True,
        "fallback_used": False,
        "issues": [],
        "answer_available": True,
    }

    await store.store_cached_result("no-expiry", "# 结果", quality)

    redis_client.set.assert_awaited_once()
    assert "ex" not in redis_client.set.await_args.kwargs


@pytest.mark.asyncio
async def test_shared_result_cache_is_visible_across_connections_and_expires(
    monkeypatch,
):
    """真实 Redis 可用时验证两个独立连接共享缓存并按 TTL 过期。"""
    writer = None
    reader = None
    try:
        writer = await TaskStore.create()
        reader = await TaskStore.create()
        await writer._redis.ping()
        await reader._redis.ping()
    except Exception as exc:
        if writer is not None:
            await writer.close()
        if reader is not None:
            await reader.close()
        pytest.skip(f"需要可用的 Valkey/Redis 测试环境: {exc}")

    cache_key = f"integration-{uuid.uuid4()}"
    monkeypatch.setattr(settings, "result_cache_ttl_seconds", 1)
    quality = {
        "format_valid": True,
        "fallback_used": False,
        "issues": [],
        "answer_available": True,
    }
    try:
        await writer.store_cached_result(cache_key, "# 跨连接结果", quality)

        assert await reader.get_cached_result(cache_key) == {
            "result": "# 跨连接结果",
            "quality": quality,
        }
        await asyncio.sleep(1.1)
        assert await reader.get_cached_result(cache_key) is None
    finally:
        await writer.delete_cached_result(cache_key)
        await writer.close()
        await reader.close()


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
    await task_store.create_task(
        "task-a", status=TaskStatus.RUNNING, caller_id=caller_id
    )
    await task_store.create_task(
        "task-b", status=TaskStatus.QUEUED, caller_id=caller_id
    )
    await task_store.create_task(
        "task-c", status=TaskStatus.QUEUED, caller_id="other-caller"
    )

    # 按 caller 取消
    cancelled = await task_store.cancel_tasks_by_caller(caller_id)
    assert len(cancelled) == 2
    assert "task-a" in cancelled
    assert "task-b" in cancelled

    # 验证取消标志
    assert await task_store.is_cancel_requested("task-a")
    assert await task_store.is_cancel_requested("task-b")

    # 清理
    await task_store.delete_task("task-a")
    await task_store.delete_task("task-b")
    await task_store.delete_task("task-c")


@pytest.mark.asyncio
async def test_cancel_by_caller_includes_queued_tasks_without_redis():
    """批量取消应与单任务端点一致，同时覆盖 queued 与 running。"""
    store = object.__new__(TaskStore)
    store._redis = AsyncMock()
    store._redis.smembers.return_value = {"queued-task", "running-task", "done-task"}
    tasks = {
        "queued-task": TaskMeta(
            task_id="queued-task",
            status=TaskStatus.QUEUED,
        ),
        "running-task": TaskMeta(
            task_id="running-task",
            status=TaskStatus.RUNNING,
        ),
        "done-task": TaskMeta(
            task_id="done-task",
            status=TaskStatus.COMPLETED,
        ),
    }
    store.get_task = AsyncMock(side_effect=lambda task_id: tasks[task_id])
    store.request_cancel = AsyncMock(return_value=True)

    cancelled = await store.cancel_tasks_by_caller("caller-queued")

    assert set(cancelled) == {"queued-task", "running-task"}
    assert {call.args[0] for call in store.request_cancel.await_args_list} == {
        "queued-task",
        "running-task",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("caller_id", ["", "   ", None])
async def test_cancel_by_caller_rejects_blank_identifier(caller_id):
    """存储层也不得把空 caller_id 解释成“取消全部”。"""
    store = object.__new__(TaskStore)

    with pytest.raises(ValueError, match="caller_id"):
        await store.cancel_tasks_by_caller(caller_id)


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
