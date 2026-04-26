"""Worker 测试。"""

import asyncio

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from services.task_queue import TaskPayload
from services.task_store import TaskStatus


@pytest.fixture
def mock_task_store():
    """Mock TaskStore。"""
    store = AsyncMock()
    return store


@pytest.fixture
def mock_pipeline_runtime():
    """Mock PipelineRuntime。"""
    runtime = MagicMock()
    runtime.create_runtime_components = AsyncMock(return_value=(
        MagicMock(),  # cfg
        MagicMock(),  # main_tm
        {},  # sub_tms
        MagicMock(),  # output_fmt
        [],  # tool_defs
        {},  # sub_tool_defs
    ))
    runtime.get_log_dir = MagicMock(return_value="logs")
    return runtime


@pytest.mark.asyncio
async def test_run_research_job_success(mock_task_store, mock_pipeline_runtime):
    """测试 worker 成功执行任务。"""
    payload = TaskPayload(
        task_id="test-task-001",
        query="test query",
        mode="balanced",
        search_profile="parallel-trusted",
        search_result_num=20,
        verification_min_search_rounds=3,
        output_detail_level="detailed",
        caller_id="caller-001",
    )

    with patch("workers.research_worker.TaskStore.create", return_value=mock_task_store), \
         patch("workers.research_worker.get_pipeline_runtime", return_value=mock_pipeline_runtime), \
         patch("workers.research_worker._execute_pipeline") as mock_execute:

        mock_task_store.update_task_status = AsyncMock()
        mock_task_store.is_cancel_requested = AsyncMock(return_value=False)
        mock_task_store.store_result = AsyncMock()
        mock_task_store.append_event = AsyncMock()

        mock_execute.return_value = ("Final result", "answer", "/logs/task.log")

        from workers.research_worker import run_research_job

        result = await run_research_job({}, payload.to_dict())

        assert result["status"] == "completed"
        assert result["task_id"] == "test-task-001"

        # 验证状态更新
        mock_task_store.update_task_status.assert_called()


@pytest.mark.asyncio
async def test_run_research_job_cancelled(mock_task_store, mock_pipeline_runtime):
    """测试 worker 任务被取消。"""
    payload = TaskPayload(
        task_id="test-task-002",
        query="test query",
        mode="balanced",
        search_profile="parallel-trusted",
        search_result_num=20,
        verification_min_search_rounds=3,
        output_detail_level="detailed",
        caller_id="caller-001",
    )

    with patch("workers.research_worker.TaskStore.create", return_value=mock_task_store), \
         patch("workers.research_worker.get_pipeline_runtime", return_value=mock_pipeline_runtime), \
         patch("workers.research_worker._execute_pipeline") as mock_execute:

        mock_task_store.update_task_status = AsyncMock()
        mock_task_store.is_cancel_requested = AsyncMock(return_value=True)  # 已取消
        mock_task_store.append_event = AsyncMock()

        # 让 pipeline 永远不完成
        async def never_complete(*args, **kwargs):
            import asyncio
            await asyncio.sleep(100)
            return ("result", "answer", "/logs/task.log")

        mock_execute.side_effect = never_complete

        from workers.research_worker import run_research_job

        result = await run_research_job({}, payload.to_dict())

        assert result["status"] == "cancelled"
        assert result["task_id"] == "test-task-002"


@pytest.mark.asyncio
async def test_run_research_job_failed(mock_task_store, mock_pipeline_runtime):
    """测试 worker 任务失败。"""
    payload = TaskPayload(
        task_id="test-task-003",
        query="test query",
        mode="balanced",
        search_profile="parallel-trusted",
        search_result_num=20,
        verification_min_search_rounds=3,
        output_detail_level="detailed",
        caller_id="caller-001",
    )

    with patch("workers.research_worker.TaskStore.create", return_value=mock_task_store), \
         patch("workers.research_worker.get_pipeline_runtime", return_value=mock_pipeline_runtime), \
         patch("workers.research_worker._execute_pipeline") as mock_execute:

        mock_task_store.update_task_status = AsyncMock()
        mock_task_store.is_cancel_requested = AsyncMock(return_value=False)
        mock_task_store.append_event = AsyncMock()

        mock_execute.side_effect = Exception("Pipeline error")

        from workers.research_worker import run_research_job

        result = await run_research_job({}, payload.to_dict())

        assert result["status"] == "failed"
        assert result["task_id"] == "test-task-003"
        assert "Pipeline error" in result["error"]


# ---- cancel 链路鲁棒性 ----------------------------------------------------


def _make_payload(task_id: str = "test-task-cancel") -> TaskPayload:
    return TaskPayload(
        task_id=task_id,
        query="test query",
        mode="balanced",
        search_profile="searxng-first",
        search_result_num=20,
        verification_min_search_rounds=3,
        output_detail_level="detailed",
        caller_id="caller-x",
    )


@pytest.mark.asyncio
async def test_cancel_watcher_survives_redis_errors(
    mock_task_store, mock_pipeline_runtime, monkeypatch
):
    """cancel watcher 在 redis 抖动时不应静默退出，必须继续轮询。"""
    monkeypatch.setattr(
        "settings.settings.worker.cancel_poll_interval_seconds", 0.01, raising=False
    )

    # 让 is_cancel_requested 前两次抛错，第三次返回 True
    call_count = {"n": 0}

    async def flaky_is_cancel_requested(_task_id):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            raise ConnectionError("redis temporarily unavailable")
        return True

    mock_task_store.is_cancel_requested = flaky_is_cancel_requested
    mock_task_store.update_task_status = AsyncMock()
    mock_task_store.append_event = AsyncMock()

    async def never_complete(*args, **kwargs):
        await asyncio.sleep(60)
        return ("result", "answer", "/logs/task.log")

    payload = _make_payload("test-task-cancel-flaky")

    with patch("workers.research_worker.TaskStore.create", return_value=mock_task_store), \
         patch("workers.research_worker.get_pipeline_runtime", return_value=mock_pipeline_runtime), \
         patch("workers.research_worker._execute_pipeline", side_effect=never_complete):
        from workers.research_worker import run_research_job

        result = await asyncio.wait_for(
            run_research_job({}, payload.to_dict()), timeout=5.0
        )

    assert result["status"] == "cancelled"
    assert call_count["n"] >= 3  # 经过两次错误才读到 True


@pytest.mark.asyncio
async def test_cancel_path_with_unresponsive_pipeline(
    mock_task_store, mock_pipeline_runtime, monkeypatch
):
    """pipeline 不响应 cancel 时，worker 应在 10s 内 abandon 并继续返回 cancelled。

    历史 bug：``await pipeline_task`` 不带超时，遇到下游代码吞掉 CancelledError
    就会让 worker 永远 hang。修复后改为 ``asyncio.wait_for(timeout=10s)``。
    """
    monkeypatch.setattr(
        "settings.settings.worker.cancel_poll_interval_seconds", 0.01, raising=False
    )
    mock_task_store.update_task_status = AsyncMock()
    mock_task_store.is_cancel_requested = AsyncMock(return_value=True)
    mock_task_store.append_event = AsyncMock()

    cancelled_but_ignored = asyncio.Event()

    async def unresponsive_pipeline(*args, **kwargs):
        # 模拟某些下游代码捕获 CancelledError 但拒绝结束（极端 bug 场景），
        # 这里直接进入永久 sleep，不响应外部 cancel。
        try:
            await asyncio.sleep(120)
        except asyncio.CancelledError:
            cancelled_but_ignored.set()
            # 故意吞掉 CancelledError 并继续 sleep
            await asyncio.sleep(120)
        return ("result", "answer", "/logs/task.log")

    payload = _make_payload("test-task-cancel-unresponsive")

    with patch("workers.research_worker.TaskStore.create", return_value=mock_task_store), \
         patch("workers.research_worker.get_pipeline_runtime", return_value=mock_pipeline_runtime), \
         patch("workers.research_worker._execute_pipeline", side_effect=unresponsive_pipeline), \
         patch("workers.research_worker.asyncio.wait_for", wraps=_fast_wait_for):
        from workers.research_worker import run_research_job

        # 整体不应超过 5 秒（10s 超时被我们 monkey-patch 缩短）
        result = await asyncio.wait_for(
            run_research_job({}, payload.to_dict()), timeout=5.0
        )

    assert result["status"] == "cancelled"
    assert cancelled_but_ignored.is_set()


# 在模块加载时保存原 wait_for 引用，避免被 patch 后 _fast_wait_for 自递归
_REAL_WAIT_FOR = asyncio.wait_for


async def _fast_wait_for(awaitable, timeout):
    """把 worker 内 ``asyncio.wait_for(timeout=10)`` 缩到 0.5 秒，加速测试。"""
    return await _REAL_WAIT_FOR(awaitable, timeout=min(timeout, 0.5))
