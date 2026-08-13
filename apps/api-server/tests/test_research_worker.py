"""Worker 测试。"""

import asyncio

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from arq import Retry
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
    runtime.create_runtime_components = AsyncMock(
        return_value=(
            MagicMock(),  # cfg
            MagicMock(),  # main_tm
            {},  # sub_tms
            MagicMock(),  # output_fmt
            [],  # tool_defs
            {},  # sub_tool_defs
        )
    )
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

    with (
        patch("workers.research_worker.TaskStore.create", return_value=mock_task_store),
        patch(
            "workers.research_worker.get_pipeline_runtime",
            return_value=mock_pipeline_runtime,
        ),
        patch("workers.research_worker._execute_pipeline") as mock_execute,
    ):
        mock_task_store.update_task_status = AsyncMock()
        mock_task_store.is_cancel_requested = AsyncMock(return_value=False)
        mock_task_store.store_result = AsyncMock()
        mock_task_store.store_result_quality = AsyncMock()
        mock_task_store.append_event = AsyncMock()

        result_quality = {
            "format_valid": False,
            "fallback_used": True,
            "issues": ["missing_boxed"],
            "answer_available": True,
        }
        mock_execute.return_value = {
            "status": "completed",
            "final_summary": "Final result",
            "final_boxed_answer": "answer",
            "log_file_path": "/logs/task.log",
            "failure_experience_summary": None,
            "result_quality": result_quality,
            "error": None,
        }

        from workers.research_worker import run_research_job

        result = await run_research_job({}, payload.to_dict())

        assert result["status"] == "completed"
        assert result["task_id"] == "test-task-001"

        # 验证状态更新
        mock_task_store.update_task_status.assert_called()
        mock_task_store.store_result_quality.assert_awaited_once_with(
            "test-task-001",
            result_quality,
        )


@pytest.mark.asyncio
async def test_successful_worker_populates_shared_result_cache(
    mock_task_store,
    mock_pipeline_runtime,
):
    """Worker 与 API 分进程运行时，成功结果必须写入共享缓存而非进程内缓存。"""
    payload = _make_payload("test-task-shared-cache")
    payload.cache_key = "shared-cache-key"
    result_quality = {
        "format_valid": True,
        "fallback_used": False,
        "issues": [],
        "answer_available": True,
    }
    mock_task_store.update_task_status = AsyncMock()
    mock_task_store.is_cancel_requested = AsyncMock(return_value=False)
    mock_task_store.store_result = AsyncMock()
    mock_task_store.store_result_quality = AsyncMock()
    mock_task_store.store_cached_result = AsyncMock()
    mock_task_store.append_event = AsyncMock()

    with (
        patch(
            "workers.research_worker.TaskStore.create",
            return_value=mock_task_store,
        ),
        patch(
            "workers.research_worker.get_pipeline_runtime",
            return_value=mock_pipeline_runtime,
        ),
        patch(
            "workers.research_worker._execute_pipeline",
            new=AsyncMock(
                return_value={
                    "status": "completed",
                    "final_summary": "# 可复用结果",
                    "result_quality": result_quality,
                }
            ),
        ),
    ):
        from workers.research_worker import run_research_job

        result = await run_research_job({}, payload.to_dict())

    assert result["status"] == "completed"
    mock_task_store.store_cached_result.assert_awaited_once_with(
        "shared-cache-key",
        "# 可复用结果",
        result_quality,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result_quality",
    [
        None,
        {
            "format_valid": "not-a-bool",
            "fallback_used": False,
            "issues": [],
            "answer_available": True,
        },
        {
            "format_valid": True,
            "fallback_used": False,
            "issues": [],
            "answer_available": "false",
        },
        {
            "format_valid": True,
            "fallback_used": False,
            "issues": [],
            "answer_available": 1,
        },
    ],
)
async def test_completed_worker_without_valid_quality_does_not_populate_shared_cache(
    mock_task_store,
    mock_pipeline_runtime,
    result_quality,
):
    """旧结果可保持 completed，但缺失或非法质量时不得写入共享缓存。"""
    payload = _make_payload("test-task-missing-cache-quality")
    payload.cache_key = "missing-quality-cache-key"
    mock_task_store.update_task_status = AsyncMock()
    mock_task_store.is_cancel_requested = AsyncMock(return_value=False)
    mock_task_store.store_result = AsyncMock()
    mock_task_store.store_result_quality = AsyncMock()
    mock_task_store.store_cached_result = AsyncMock()
    mock_task_store.append_event = AsyncMock()
    pipeline_result = {
        "status": "completed",
        "final_summary": "# 旧版无有效质量结果",
    }
    if result_quality is not None:
        pipeline_result["result_quality"] = result_quality

    with (
        patch(
            "workers.research_worker.TaskStore.create",
            return_value=mock_task_store,
        ),
        patch(
            "workers.research_worker.get_pipeline_runtime",
            return_value=mock_pipeline_runtime,
        ),
        patch(
            "workers.research_worker._execute_pipeline",
            new=AsyncMock(return_value=pipeline_result),
        ),
    ):
        from workers.research_worker import run_research_job

        result = await run_research_job({}, payload.to_dict())

    assert result["status"] == "completed"
    mock_task_store.store_result_quality.assert_not_awaited()
    mock_task_store.store_cached_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_research_job_accepts_legacy_quality_key(
    mock_task_store,
    mock_pipeline_runtime,
):
    """迁移期间 worker 仍应兼容旧 quality 键。"""
    payload = _make_payload("test-task-legacy-quality")
    legacy_quality = {
        "format_valid": True,
        "fallback_used": False,
        "issues": [],
    }
    mock_task_store.update_task_status = AsyncMock()
    mock_task_store.is_cancel_requested = AsyncMock(return_value=False)
    mock_task_store.store_result = AsyncMock()
    mock_task_store.store_result_quality = AsyncMock()
    mock_task_store.append_event = AsyncMock()

    with (
        patch("workers.research_worker.TaskStore.create", return_value=mock_task_store),
        patch(
            "workers.research_worker.get_pipeline_runtime",
            return_value=mock_pipeline_runtime,
        ),
        patch(
            "workers.research_worker._execute_pipeline",
            new=AsyncMock(
                return_value={
                    "status": "completed",
                    "final_summary": "旧版结果",
                    "final_boxed_answer": "旧版结果",
                    "quality": legacy_quality,
                }
            ),
        ),
    ):
        from workers.research_worker import run_research_job

        result = await run_research_job({}, payload.to_dict())

    assert result["status"] == "completed"
    mock_task_store.store_result_quality.assert_awaited_once_with(
        "test-task-legacy-quality",
        {
            **legacy_quality,
            "answer_available": True,
        },
    )


@pytest.mark.asyncio
async def test_run_research_job_rejects_completed_without_available_answer(
    mock_task_store,
    mock_pipeline_runtime,
):
    """防御性校验：不可用答案即使误报 completed 也必须落为 FAILED。"""
    payload = _make_payload("test-task-no-answer")
    mock_task_store.update_task_status = AsyncMock()
    mock_task_store.is_cancel_requested = AsyncMock(return_value=False)
    mock_task_store.store_result = AsyncMock()
    mock_task_store.store_result_quality = AsyncMock()
    mock_task_store.append_event = AsyncMock()
    unavailable_quality = {
        "format_valid": False,
        "fallback_used": False,
        "issues": [
            "summary_generation_failed",
            "no_answer_available",
        ],
        "answer_available": False,
    }

    with (
        patch("workers.research_worker.TaskStore.create", return_value=mock_task_store),
        patch(
            "workers.research_worker.get_pipeline_runtime",
            return_value=mock_pipeline_runtime,
        ),
        patch(
            "workers.research_worker._execute_pipeline",
            new=AsyncMock(
                return_value={
                    "status": "completed",
                    "final_summary": "",
                    "final_boxed_answer": "",
                    "result_quality": unavailable_quality,
                }
            ),
        ),
    ):
        from workers.research_worker import run_research_job

        result = await run_research_job({}, payload.to_dict())

    assert result["status"] == "failed"
    mock_task_store.store_result.assert_not_awaited()
    mock_task_store.store_result_quality.assert_awaited_once_with(
        "test-task-no-answer",
        unavailable_quality,
    )
    mock_task_store.update_task_status.assert_any_call(
        "test-task-no-answer",
        TaskStatus.FAILED,
        error="Final summary produced no usable answer.",
    )


@pytest.mark.asyncio
async def test_worker_rejects_empty_summary_even_if_quality_claims_available(
    mock_task_store,
    mock_pipeline_runtime,
):
    """结构化质量标记不能把空总结伪装成已完成结果。"""
    payload = _make_payload("test-task-empty-summary-quality-mismatch")
    mock_task_store.update_task_status = AsyncMock()
    mock_task_store.is_cancel_requested = AsyncMock(return_value=False)
    mock_task_store.store_result = AsyncMock()
    mock_task_store.store_result_quality = AsyncMock()
    mock_task_store.store_cached_result = AsyncMock()
    mock_task_store.append_event = AsyncMock()

    with (
        patch(
            "workers.research_worker.TaskStore.create",
            return_value=mock_task_store,
        ),
        patch(
            "workers.research_worker.get_pipeline_runtime",
            return_value=mock_pipeline_runtime,
        ),
        patch(
            "workers.research_worker._execute_pipeline",
            new=AsyncMock(
                return_value={
                    "status": "completed",
                    "final_summary": "   ",
                    "result_quality": {
                        "format_valid": True,
                        "fallback_used": False,
                        "issues": [],
                        "answer_available": True,
                    },
                }
            ),
        ),
    ):
        from workers.research_worker import run_research_job

        result = await run_research_job({}, payload.to_dict())

    assert result["status"] == "failed"
    mock_task_store.store_result.assert_not_awaited()
    mock_task_store.store_cached_result.assert_not_awaited()
    stored_quality = mock_task_store.store_result_quality.await_args.args[1]
    assert stored_quality["answer_available"] is False
    assert stored_quality["format_valid"] is False
    assert "no_answer_available" in stored_quality["issues"]


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

    with (
        patch("workers.research_worker.TaskStore.create", return_value=mock_task_store),
        patch(
            "workers.research_worker.get_pipeline_runtime",
            return_value=mock_pipeline_runtime,
        ),
        patch("workers.research_worker._execute_pipeline") as mock_execute,
    ):
        mock_task_store.update_task_status = AsyncMock()
        mock_task_store.is_cancel_requested = AsyncMock(return_value=True)  # 已取消
        mock_task_store.append_event = AsyncMock()

        from workers.research_worker import run_research_job

        result = await run_research_job({}, payload.to_dict())

        assert result["status"] == "cancelled"
        assert result["task_id"] == "test-task-002"
        mock_pipeline_runtime.create_runtime_components.assert_not_awaited()
        mock_execute.assert_not_awaited()


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

    with (
        patch("workers.research_worker.TaskStore.create", return_value=mock_task_store),
        patch(
            "workers.research_worker.get_pipeline_runtime",
            return_value=mock_pipeline_runtime,
        ),
        patch("workers.research_worker._execute_pipeline") as mock_execute,
    ):
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
async def test_worker_setup_failure_stays_non_terminal_before_final_retry(
    mock_task_store,
    mock_pipeline_runtime,
    monkeypatch,
):
    """运行时初始化失败但仍可重试时，任务不能提前落为 FAILED。"""
    payload = _make_payload("test-task-setup-retry")
    mock_task_store.is_cancel_requested = AsyncMock(return_value=False)
    mock_task_store.update_task_status = AsyncMock()
    mock_task_store.append_event = AsyncMock()
    mock_pipeline_runtime.create_runtime_components.side_effect = RuntimeError(
        "runtime unavailable"
    )
    monkeypatch.setattr(
        "workers.research_worker.settings.worker.max_tries",
        3,
    )
    monkeypatch.setattr(
        "workers.research_worker.settings.worker.retry_defer_seconds",
        0.0,
    )

    with (
        patch(
            "workers.research_worker.TaskStore.create",
            return_value=mock_task_store,
        ),
        patch(
            "workers.research_worker.get_pipeline_runtime",
            return_value=mock_pipeline_runtime,
        ),
    ):
        from workers.research_worker import run_research_job

        with pytest.raises(Retry):
            await run_research_job(
                {"job_try": 1},
                payload.to_dict(),
            )

    mock_task_store.update_task_status.assert_any_await(
        payload.task_id,
        TaskStatus.QUEUED,
        error="runtime unavailable",
    )
    failed_updates = [
        call
        for call in mock_task_store.update_task_status.await_args_list
        if len(call.args) >= 2 and call.args[1] == TaskStatus.FAILED
    ]
    assert failed_updates == []
    mock_task_store.append_event.assert_awaited_once_with(
        payload.task_id,
        "retrying",
        {
            "error": "runtime unavailable",
            "attempt": 1,
            "max_tries": 3,
        },
    )


@pytest.mark.asyncio
async def test_worker_setup_failure_becomes_failed_on_final_retry(
    mock_task_store,
    mock_pipeline_runtime,
    monkeypatch,
):
    """最后一次初始化仍失败时，必须形成稳定 FAILED 终态且不再 Retry。"""
    payload = _make_payload("test-task-setup-final-failure")
    mock_task_store.is_cancel_requested = AsyncMock(return_value=False)
    mock_task_store.update_task_status = AsyncMock()
    mock_task_store.append_event = AsyncMock()
    mock_pipeline_runtime.create_runtime_components.side_effect = RuntimeError(
        "runtime unavailable"
    )
    monkeypatch.setattr(
        "workers.research_worker.settings.worker.max_tries",
        3,
    )

    with (
        patch(
            "workers.research_worker.TaskStore.create",
            return_value=mock_task_store,
        ),
        patch(
            "workers.research_worker.get_pipeline_runtime",
            return_value=mock_pipeline_runtime,
        ),
    ):
        from workers.research_worker import run_research_job

        result = await run_research_job(
            {"job_try": 3},
            payload.to_dict(),
        )

    assert result == {
        "status": "failed",
        "task_id": payload.task_id,
        "error": "runtime unavailable",
    }
    mock_task_store.update_task_status.assert_any_await(
        payload.task_id,
        TaskStatus.FAILED,
        error="runtime unavailable",
    )
    mock_task_store.append_event.assert_awaited_once_with(
        payload.task_id,
        "error",
        {"error": "runtime unavailable"},
    )


@pytest.mark.asyncio
async def test_worker_setup_cancellation_commits_cancelled_terminal_state(
    mock_task_store,
    mock_pipeline_runtime,
):
    """setup 阶段被取消也必须落事件与 CANCELLED，不能遗留 RUNNING。"""
    payload = _make_payload("test-task-setup-cancelled")
    call_order = []

    async def update_status(task_id, status, error=None):
        call_order.append(("status", task_id, status, error))

    async def append_event(task_id, event_type, data):
        call_order.append(("event", task_id, event_type, data))

    mock_task_store.is_cancel_requested = AsyncMock(return_value=False)
    mock_task_store.update_task_status = AsyncMock(side_effect=update_status)
    mock_task_store.append_event = AsyncMock(side_effect=append_event)
    mock_pipeline_runtime.create_runtime_components.side_effect = (
        asyncio.CancelledError()
    )

    with (
        patch(
            "workers.research_worker.TaskStore.create",
            return_value=mock_task_store,
        ),
        patch(
            "workers.research_worker.get_pipeline_runtime",
            return_value=mock_pipeline_runtime,
        ),
    ):
        from workers.research_worker import run_research_job

        result = await run_research_job({}, payload.to_dict())

    assert result == {
        "status": "cancelled",
        "task_id": payload.task_id,
    }
    terminal_event_index = call_order.index(
        (
            "event",
            payload.task_id,
            "cancelled",
            {"reason": "worker_cancelled_during_setup"},
        )
    )
    terminal_status_index = call_order.index(
        (
            "status",
            payload.task_id,
            TaskStatus.CANCELLED,
            "worker_cancelled_during_setup",
        )
    )
    assert terminal_event_index < terminal_status_index


@pytest.mark.asyncio
async def test_run_research_job_marks_pipeline_failed_result_as_failed(
    mock_task_store, mock_pipeline_runtime
):
    """pipeline 返回 status="failed" 的 dict 时，worker 应落库 FAILED 而非 COMPLETED。"""
    payload = _make_payload("test-task-pipeline-failed-result")
    failed_quality = {
        "format_valid": False,
        "fallback_used": False,
        "issues": ["summary_generation_failed", "no_answer_available"],
        "answer_available": False,
    }

    async def failed_pipeline(*_args, **_kwargs):
        return {
            "status": "failed",
            "final_summary": "Error executing task test-task-pipeline-failed-result",
            "final_boxed_answer": "",
            "log_file_path": "/logs/task.log",
            "failure_experience_summary": None,
            "error": "LLM timeout",
            "quality": failed_quality,
        }

    mock_task_store.update_task_status = AsyncMock()
    mock_task_store.is_cancel_requested = AsyncMock(return_value=False)
    mock_task_store.store_result = AsyncMock()
    mock_task_store.store_result_quality = AsyncMock()
    mock_task_store.append_event = AsyncMock()

    with (
        patch("workers.research_worker.TaskStore.create", return_value=mock_task_store),
        patch(
            "workers.research_worker.get_pipeline_runtime",
            return_value=mock_pipeline_runtime,
        ),
        patch("workers.research_worker._execute_pipeline", side_effect=failed_pipeline),
    ):
        from workers.research_worker import run_research_job

        result = await run_research_job({}, payload.to_dict())

    assert result["status"] == "failed"
    mock_task_store.store_result_quality.assert_awaited_once_with(
        "test-task-pipeline-failed-result",
        failed_quality,
    )
    mock_task_store.update_task_status.assert_any_call(
        "test-task-pipeline-failed-result",
        TaskStatus.FAILED,
        error="LLM timeout",
    )
    mock_task_store.append_event.assert_any_call(
        "test-task-pipeline-failed-result",
        "error",
        {"error": "LLM timeout"},
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

    with (
        patch("workers.research_worker.TaskStore.create", return_value=mock_task_store),
        patch(
            "workers.research_worker.get_pipeline_runtime",
            return_value=mock_pipeline_runtime,
        ),
        patch("workers.research_worker._execute_pipeline", side_effect=never_complete),
    ):
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
    mock_task_store.is_cancel_requested = AsyncMock(side_effect=[False, True])
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

    with (
        patch("workers.research_worker.TaskStore.create", return_value=mock_task_store),
        patch(
            "workers.research_worker.get_pipeline_runtime",
            return_value=mock_pipeline_runtime,
        ),
        patch(
            "workers.research_worker._execute_pipeline",
            side_effect=unresponsive_pipeline,
        ),
        patch("workers.research_worker.asyncio.wait_for", wraps=_fast_wait_for),
    ):
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
