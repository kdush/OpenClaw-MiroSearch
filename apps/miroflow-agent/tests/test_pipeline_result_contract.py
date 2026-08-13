"""Pipeline 映射返回协议的调用方回归测试。"""

import json
import sys
from pathlib import Path
from typing import Optional, get_type_hints
from unittest.mock import AsyncMock, MagicMock

import pytest
from hydra.core.global_hydra import GlobalHydra
from omegaconf import OmegaConf

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_ROOT = PROJECT_ROOT / "benchmarks"
for import_root in (PROJECT_ROOT, BENCHMARK_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

import common_benchmark  # noqa: E402
import main as cli_main  # noqa: E402


def _pipeline_result(
    *,
    status: str,
    final_summary: str = "",
    final_boxed_answer: str = "",
    error: Optional[str] = None,
    failure_experience_summary: Optional[str] = None,
    result_quality: Optional[dict] = None,
) -> dict:
    """构造完整的 Pipeline 映射结果。"""
    return {
        "status": status,
        "final_summary": final_summary,
        "final_boxed_answer": final_boxed_answer,
        "log_file_path": "/tmp/task.json",
        "failure_experience_summary": failure_experience_summary,
        "error": error,
        "result_quality": result_quality
        or {
            "format_valid": bool(final_boxed_answer),
            "fallback_used": False,
            "issues": [],
            "answer_available": bool(final_summary),
        },
    }


def _make_evaluator(tmp_path, *, pass_at_k: int = 1):
    """构造不初始化真实 Pipeline 组件的 Benchmark evaluator。"""
    evaluator = common_benchmark.GenericEvaluator.__new__(
        common_benchmark.GenericEvaluator
    )
    evaluator.pass_at_k = pass_at_k
    evaluator.context_compress_limit = 0
    evaluator.cfg = OmegaConf.create({})
    evaluator.main_agent_tool_manager = MagicMock()
    evaluator.sub_agent_tool_managers = {}
    evaluator.output_formatter = MagicMock()
    evaluator.data_dir = tmp_path
    evaluator.benchmark_name = "contract-test"
    evaluator.get_log_dir = lambda: tmp_path
    return evaluator


@pytest.mark.asyncio
async def test_cli_uses_mapping_result_and_reports_pipeline_failure(
    monkeypatch,
    tmp_path,
):
    """CLI 必须按键读取失败结果，并报告 Pipeline 的真实错误。"""
    pipeline_result = _pipeline_result(
        status="failed",
        final_summary="无法生成最终答案",
        error="provider timeout",
    )
    logger = MagicMock()
    monkeypatch.setattr(
        cli_main,
        "create_pipeline_components",
        lambda cfg: (MagicMock(), {}, MagicMock()),
    )
    monkeypatch.setattr(
        cli_main,
        "execute_task_pipeline",
        AsyncMock(return_value=pipeline_result),
    )
    monkeypatch.setattr(cli_main, "logger", logger)

    result = await cli_main.amain(OmegaConf.create({"debug_dir": str(tmp_path)}))

    assert result == pipeline_result
    logger.error.assert_called_once_with(
        "Task %s failed: %s",
        "task_example",
        "provider timeout",
    )


@pytest.mark.parametrize(
    ("pipeline_result", "expected_exit_code"),
    [
        (_pipeline_result(status="completed"), 0),
        (_pipeline_result(status="failed", error="failed"), 1),
        (_pipeline_result(status="cancelled", error="cancelled"), 1),
        (_pipeline_result(status="unexpected"), 1),
        ({"error": "missing status"}, 1),
    ],
)
def test_cli_process_exit_code_follows_pipeline_status(
    monkeypatch,
    tmp_path,
    pipeline_result,
    expected_exit_code,
):
    """Hydra 包装的 CLI 入口必须把 Pipeline 状态映射为进程退出码。"""
    monkeypatch.setattr(
        cli_main,
        "amain",
        AsyncMock(return_value=pipeline_result),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "miroflow-agent",
            f"hydra.run.dir={tmp_path}",
            "hydra.output_subdir=null",
            "hydra.job.chdir=false",
        ],
    )
    global_hydra = GlobalHydra.instance()
    global_hydra.clear()

    try:
        with pytest.raises(SystemExit) as exit_info:
            cli_main.main()
    finally:
        global_hydra.clear()

    assert exit_info.value.code == expected_exit_code


@pytest.mark.asyncio
async def test_benchmark_records_failed_pipeline_result_as_failed_attempt(
    monkeypatch,
    tmp_path,
):
    """Benchmark 不得把 Pipeline 的 failed 映射当作成功样本。"""
    pipeline_result = _pipeline_result(
        status="failed",
        final_summary="执行失败",
        error="search backend unavailable",
    )
    monkeypatch.setattr(
        common_benchmark,
        "execute_task_pipeline",
        AsyncMock(return_value=pipeline_result),
    )

    evaluator = _make_evaluator(tmp_path)

    result = await evaluator.run_single_task(
        common_benchmark.BenchmarkTask(
            task_id="failed-task",
            task_question="测试问题",
            ground_truth=None,
        )
    )

    assert result.status == "failed"
    assert result.model_boxed_answer == ""
    assert result.error_message == "search backend unavailable"
    assert result.attempts[0]["status"] == "failed"
    assert result.attempts[0]["error_message"] == "search backend unavailable"


@pytest.mark.asyncio
async def test_benchmark_cancelled_pipeline_stops_all_pass_at_k_attempts(
    monkeypatch,
    tmp_path,
):
    """取消态必须立即停止格式重试和后续 pass@k 尝试。"""
    execute_pipeline = AsyncMock(
        return_value=_pipeline_result(
            status="cancelled",
            final_summary="用户取消任务",
            error="cancelled by user",
        )
    )
    monkeypatch.setattr(
        common_benchmark,
        "execute_task_pipeline",
        execute_pipeline,
    )
    evaluator = _make_evaluator(tmp_path, pass_at_k=3)

    result = await evaluator.run_single_task(
        common_benchmark.BenchmarkTask(
            task_id="cancelled-task",
            task_question="测试取消",
            ground_truth=None,
        )
    )

    assert execute_pipeline.await_count == 1
    assert result.status == "cancelled"
    assert len(result.attempts) == 1
    assert result.attempts[0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_benchmark_retries_a_summary_generation_failure(
    monkeypatch,
    tmp_path,
):
    """有失败经验的总结不可用应进入格式重试，而不是立即终止。"""
    retryable_failure = _pipeline_result(
        status="failed",
        error="Final summary produced no usable answer.",
        failure_experience_summary="上一轮遗漏了最终结论。",
        result_quality={
            "format_valid": False,
            "fallback_used": False,
            "issues": ["summary_generation_failed", "no_answer_available"],
            "answer_available": False,
        },
    )
    recovered = _pipeline_result(
        status="completed",
        final_summary="恢复后的总结",
        final_boxed_answer="恢复后的答案",
    )
    execute_pipeline = AsyncMock(side_effect=[retryable_failure, recovered])
    monkeypatch.setattr(
        common_benchmark,
        "execute_task_pipeline",
        execute_pipeline,
    )
    evaluator = _make_evaluator(tmp_path)
    evaluator.context_compress_limit = 1

    result = await evaluator.run_single_task(
        common_benchmark.BenchmarkTask(
            task_id="summary-retry-task",
            task_question="测试总结失败重试",
            ground_truth=None,
        )
    )

    assert execute_pipeline.await_count == 2
    second_description = execute_pipeline.await_args_list[1].kwargs["task_description"]
    assert "上一轮遗漏了最终结论" in second_description
    assert result.status == "success"
    assert result.model_boxed_answer == "恢复后的答案"


@pytest.mark.asyncio
async def test_benchmark_promotes_later_correct_pass_at_k_attempt(
    monkeypatch,
    tmp_path,
):
    """后续正确尝试必须成为主结果，不能继续暴露首个错误答案。"""
    execute_pipeline = AsyncMock(
        side_effect=[
            _pipeline_result(
                status="completed",
                final_summary="第一次总结",
                final_boxed_answer="错误答案",
            ),
            _pipeline_result(
                status="completed",
                final_summary="第二次总结",
                final_boxed_answer="正确答案",
            ),
        ]
    )

    async def verify_answer(**kwargs):
        predicted = kwargs["predicted_answer"]
        return (
            "CORRECT" if predicted == "正确答案" else "INCORRECT",
            "test-judge",
            None,
        )

    monkeypatch.setattr(
        common_benchmark,
        "execute_task_pipeline",
        execute_pipeline,
    )
    monkeypatch.setattr(
        common_benchmark,
        "verify_answer_for_datasets",
        verify_answer,
    )
    evaluator = _make_evaluator(tmp_path, pass_at_k=2)

    result = await evaluator.run_single_task(
        common_benchmark.BenchmarkTask(
            task_id="pass-at-k-promotion",
            task_question="测试后续正确答案提升",
            ground_truth="正确答案",
        )
    )

    assert len(result.attempts) == 2
    assert result.attempts[0]["is_correct"] is False
    assert result.attempts[1]["is_correct"] is True
    assert result.pass_at_k_success is True
    assert result.status == "success"
    assert result.model_boxed_answer == "正确答案"


@pytest.mark.asyncio
@pytest.mark.parametrize("restored_status", ["failed", "cancelled"])
async def test_benchmark_does_not_verify_restored_unsuccessful_answer(
    monkeypatch,
    tmp_path,
    restored_status,
):
    """恢复出的失败或取消答案即使非空，也不得进入判题器。"""
    task_id = f"restored-{restored_status}"
    log_file = tmp_path / (
        f"task_{task_id}_attempt-1_format-retry-0_" "2026-07-31-00-00-00.json"
    )
    log_file.write_text(
        json.dumps(
            {
                "status": restored_status,
                "final_boxed_answer": "旧答案",
            }
        ),
        encoding="utf-8",
    )
    verifier = AsyncMock(return_value=("INCORRECT", "fake", None))
    monkeypatch.setattr(
        common_benchmark,
        "verify_answer_for_datasets",
        verifier,
    )
    evaluator = _make_evaluator(tmp_path)

    result = await evaluator.run_single_task(
        common_benchmark.BenchmarkTask(
            task_id=task_id,
            task_question="测试恢复状态",
            ground_truth="正确答案",
        )
    )

    verifier.assert_not_awaited()
    assert result.status == restored_status


@pytest.mark.asyncio
async def test_benchmark_does_not_verify_format_error_answer(
    monkeypatch,
    tmp_path,
):
    """格式错误占位文本不得作为模型答案送入判题器。"""
    monkeypatch.setattr(
        common_benchmark,
        "execute_task_pipeline",
        AsyncMock(
            return_value=_pipeline_result(
                status="completed",
                final_summary="缺少 boxed 答案",
                final_boxed_answer=common_benchmark.FORMAT_ERROR_MESSAGE,
            )
        ),
    )
    verifier = AsyncMock(return_value=("INCORRECT", "fake", None))
    monkeypatch.setattr(
        common_benchmark,
        "verify_answer_for_datasets",
        verifier,
    )
    evaluator = _make_evaluator(tmp_path)

    await evaluator.run_single_task(
        common_benchmark.BenchmarkTask(
            task_id="format-error-task",
            task_question="测试格式错误",
            ground_truth="正确答案",
        )
    )

    verifier.assert_not_awaited()


@pytest.mark.parametrize(
    "result_type",
    [
        common_benchmark.BenchmarkTask,
        common_benchmark.BenchmarkResult,
    ],
)
def test_benchmark_ground_truth_is_optional(result_type):
    """Benchmark 输入和输出类型都必须允许无 ground truth。"""
    type_hints = get_type_hints(result_type)

    assert type_hints["ground_truth"] == Optional[str]
