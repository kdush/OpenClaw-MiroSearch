"""Pipeline 最终答案状态与质量透传测试。"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from omegaconf import OmegaConf

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core import pipeline  # noqa: E402
from src.io.output_formatter import OutputFormatter  # noqa: E402


def _make_pipeline_config():
    return OmegaConf.create(
        {
            "llm": {
                "provider": "fake",
                "model_name": "fake-model",
            },
            "agent": {},
        }
    )


def _make_tool_manager(*, close_error=None):
    manager = MagicMock()
    manager.aclose = AsyncMock(side_effect=close_error)
    return manager


def _install_pipeline_fakes(monkeypatch, outcome):
    fake_client = MagicMock()
    fake_client.aclose = AsyncMock()

    class FakeOrchestrator:
        def __init__(self, **kwargs):
            del kwargs

        async def run_main_agent(self, **kwargs):
            del kwargs
            if outcome == "failed":
                raise RuntimeError("pipeline failed")
            if outcome == "cancelled":
                raise asyncio.CancelledError
            return (
                "最终答案",
                "答案",
                None,
                {"answer_available": True},
            )

    monkeypatch.setattr(pipeline, "ClientFactory", lambda **kwargs: fake_client)
    monkeypatch.setattr(pipeline, "Orchestrator", FakeOrchestrator)
    monkeypatch.setattr(pipeline, "get_env_info", lambda cfg: {})
    return fake_client


@pytest.mark.asyncio
async def test_pipeline_marks_unavailable_final_answer_failed(
    monkeypatch,
    tmp_path,
):
    """无模型正文且无中间回退时，pipeline 必须返回 failed。"""
    result_quality = {
        "format_valid": False,
        "fallback_used": False,
        "issues": ["summary_generation_failed", "no_answer_available"],
        "answer_available": False,
    }
    fake_client = MagicMock()
    fake_client.aclose = AsyncMock()

    class FakeOrchestrator:
        def __init__(self, **kwargs):
            del kwargs

        async def run_main_agent(self, **kwargs):
            del kwargs
            return "", "", None, result_quality

    monkeypatch.setattr(pipeline, "ClientFactory", lambda **kwargs: fake_client)
    monkeypatch.setattr(pipeline, "Orchestrator", FakeOrchestrator)
    monkeypatch.setattr(pipeline, "get_env_info", lambda cfg: {})
    manager = MagicMock()

    result = await pipeline.execute_task_pipeline(
        cfg=_make_pipeline_config(),
        task_id="unavailable-answer",
        task_description="原始任务",
        task_file_name="",
        main_agent_tool_manager=manager,
        sub_agent_tool_managers={},
        output_formatter=OutputFormatter(),
        log_dir=str(tmp_path),
    )

    assert result["status"] == "failed"
    assert result["result_quality"] == result_quality
    assert result["final_summary"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected_status"),
    [
        ("completed", "completed"),
        ("failed", "failed"),
        ("cancelled", "cancelled"),
    ],
)
async def test_pipeline_closes_all_tool_managers_on_every_exit_path(
    monkeypatch,
    tmp_path,
    outcome,
    expected_status,
):
    """成功、异常和取消路径都必须关闭主/子 ToolManager。"""
    _install_pipeline_fakes(monkeypatch, outcome)
    main_manager = _make_tool_manager()
    first_sub_manager = _make_tool_manager()
    second_sub_manager = _make_tool_manager()

    result = await pipeline.execute_task_pipeline(
        cfg=_make_pipeline_config(),
        task_id=f"cleanup-{outcome}",
        task_description="验证资源清理",
        task_file_name="",
        main_agent_tool_manager=main_manager,
        sub_agent_tool_managers={
            "first": first_sub_manager,
            "second": second_sub_manager,
        },
        output_formatter=OutputFormatter(),
        log_dir=str(tmp_path),
    )

    assert result["status"] == expected_status
    main_manager.aclose.assert_awaited_once_with()
    first_sub_manager.aclose.assert_awaited_once_with()
    second_sub_manager.aclose.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_pipeline_tool_manager_cleanup_is_deduplicated_and_best_effort(
    monkeypatch,
    tmp_path,
):
    """重复 manager 只关一次；单个关闭失败不得阻断其余清理或改变结果。"""
    _install_pipeline_fakes(monkeypatch, "completed")
    repeated_manager = _make_tool_manager(close_error=RuntimeError("close failed"))
    healthy_manager = _make_tool_manager()

    result = await pipeline.execute_task_pipeline(
        cfg=_make_pipeline_config(),
        task_id="cleanup-best-effort",
        task_description="验证容错清理",
        task_file_name="",
        main_agent_tool_manager=repeated_manager,
        sub_agent_tool_managers={
            "repeated": repeated_manager,
            "healthy": healthy_manager,
        },
        output_formatter=OutputFormatter(),
        log_dir=str(tmp_path),
    )

    assert result["status"] == "completed"
    repeated_manager.aclose.assert_awaited_once_with()
    healthy_manager.aclose.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_pipeline_closes_managers_when_set_task_log_raises(
    monkeypatch,
    tmp_path,
):
    """早期注入 task_log 失败也必须返回结构化失败并关闭全部 manager。"""
    monkeypatch.setattr(pipeline, "get_env_info", lambda cfg: {})
    main_manager = _make_tool_manager()
    main_manager.set_task_log.side_effect = RuntimeError("set_task_log failed")
    sub_manager = _make_tool_manager()

    result = await pipeline.execute_task_pipeline(
        cfg=_make_pipeline_config(),
        task_id="early-set-task-log-failure",
        task_description="验证早期初始化清理",
        task_file_name="",
        main_agent_tool_manager=main_manager,
        sub_agent_tool_managers={"sub": sub_manager},
        output_formatter=OutputFormatter(),
        log_dir=str(tmp_path),
    )

    assert result["status"] == "failed"
    assert "set_task_log failed" in result["error"]
    main_manager.aclose.assert_awaited_once_with()
    sub_manager.aclose.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_pipeline_returns_failed_when_start_log_raises(
    monkeypatch,
    tmp_path,
):
    """TaskLog 已创建但起始日志失败时，仍应尽力返回结构化失败并完成清理。"""
    real_task_log = pipeline.TaskLog

    def make_failing_task_log(**kwargs):
        task_log = real_task_log(**kwargs)
        task_log.log_step = MagicMock(side_effect=RuntimeError("start log failed"))
        return task_log

    monkeypatch.setattr(pipeline, "TaskLog", make_failing_task_log)
    monkeypatch.setattr(pipeline, "get_env_info", lambda cfg: {})
    main_manager = _make_tool_manager()
    sub_manager = _make_tool_manager()

    result = await pipeline.execute_task_pipeline(
        cfg=_make_pipeline_config(),
        task_id="early-start-log-failure",
        task_description="验证起始日志失败清理",
        task_file_name="",
        main_agent_tool_manager=main_manager,
        sub_agent_tool_managers={"sub": sub_manager},
        output_formatter=OutputFormatter(),
        log_dir=str(tmp_path),
    )

    assert result["status"] == "failed"
    assert "start log failed" in result["error"]
    main_manager.aclose.assert_awaited_once_with()
    sub_manager.aclose.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_pipeline_closes_managers_when_task_log_initialization_raises(
    monkeypatch,
):
    """TaskLog 尚未创建时应清理 manager，并传播带任务上下文的明确错误。"""
    monkeypatch.setattr(
        pipeline,
        "TaskLog",
        MagicMock(side_effect=RuntimeError("task log init failed")),
    )
    monkeypatch.setattr(pipeline, "get_env_info", lambda cfg: {})
    main_manager = _make_tool_manager()
    sub_manager = _make_tool_manager()

    with pytest.raises(
        RuntimeError,
        match="early-task-log-failure.*task log init failed",
    ):
        await pipeline.execute_task_pipeline(
            cfg=_make_pipeline_config(),
            task_id="early-task-log-failure",
            task_description="验证 TaskLog 初始化清理",
            task_file_name="",
            main_agent_tool_manager=main_manager,
            sub_agent_tool_managers={"sub": sub_manager},
            output_formatter=OutputFormatter(),
        )

    main_manager.aclose.assert_awaited_once_with()
    sub_manager.aclose.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_close_tool_managers_aggregates_cleanup_and_preserves_cancellation():
    """重复取消不得中断任一 manager 清理，完成后仍向调用方传播取消。"""
    release_cleanup = asyncio.Event()
    started = []
    finished = []

    class BlockingManager:
        def __init__(self, name):
            self.name = name

        async def aclose(self):
            started.append(self.name)
            await release_cleanup.wait()
            finished.append(self.name)

    main_manager = BlockingManager("main")
    sub_manager = BlockingManager("sub")
    cleanup_task = asyncio.create_task(
        pipeline._close_tool_managers(
            main_manager,
            {"sub": sub_manager},
        )
    )

    for _ in range(10):
        if len(started) == 2:
            break
        await asyncio.sleep(0)
    assert sorted(started) == ["main", "sub"]

    cleanup_task.cancel()
    await asyncio.sleep(0)
    cleanup_task.cancel()
    release_cleanup.set()

    with pytest.raises(asyncio.CancelledError):
        await cleanup_task
    assert sorted(finished) == ["main", "sub"]


@pytest.mark.asyncio
async def test_close_tool_managers_is_best_effort_for_normal_errors():
    """普通关闭异常应被隔离，其他 manager 仍必须完成。"""
    healthy_manager = _make_tool_manager()
    failing_manager = _make_tool_manager(close_error=RuntimeError("close failed"))

    await pipeline._close_tool_managers(
        failing_manager,
        {"healthy": healthy_manager},
    )

    failing_manager.aclose.assert_awaited_once_with()
    healthy_manager.aclose.assert_awaited_once_with()
