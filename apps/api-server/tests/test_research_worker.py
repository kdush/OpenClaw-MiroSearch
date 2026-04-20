"""Worker 测试。"""

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
