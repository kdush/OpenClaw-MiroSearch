# Copyright (c) 2025 MiroMind
# This source code is licensed under the Apache 2.0 License.

"""
Task execution pipeline module.

This module provides:
- execute_task_pipeline: Main function to run a complete task from start to finish
- create_pipeline_components: Factory function to initialize all pipeline components

The pipeline orchestrates the interaction between LLM clients, tool managers,
and the orchestrator to execute complex multi-turn agent tasks.
"""

import asyncio
import time
import traceback
import uuid
from typing import Any, Dict, List, Optional

from miroflow_tools.manager import ToolManager
from omegaconf import DictConfig

from ..config.settings import (
    create_mcp_server_parameters,
    get_env_info,
)
from ..io.output_formatter import OutputFormatter
from ..llm.factory import ClientFactory
from ..logging.task_logger import (
    TaskLog,
    get_utc_plus_8_time,
)
from .orchestrator import Orchestrator

FINAL_ANSWER_UNAVAILABLE_ERROR = "Final summary produced no usable answer."


def _build_pipeline_result(
    *,
    status: str,
    final_summary: str,
    final_boxed_answer: str,
    log_file_path: str,
    failure_experience_summary: Optional[str] = None,
    error: Optional[str] = None,
    result_quality: Optional[Dict[str, Any]] = None,
) -> dict:
    """构建结构化 pipeline 结果，供 worker 根据 status 决定落库状态。"""
    return {
        "status": status,
        "final_summary": final_summary,
        "final_boxed_answer": final_boxed_answer,
        "log_file_path": log_file_path,
        "failure_experience_summary": failure_experience_summary,
        "error": error,
        "result_quality": result_quality,
    }


def _safe_task_log_step(
    task_log: TaskLog,
    level: str,
    step_name: str,
    message: str,
) -> None:
    """日志组件自身异常不得掩盖 pipeline 原始失败或阻断资源清理。"""
    try:
        task_log.log_step(level, step_name, message)
    except Exception:
        pass


async def _close_tool_managers(
    main_agent_tool_manager: ToolManager,
    sub_agent_tool_managers: Dict[str, ToolManager],
) -> None:
    """并发关闭去重后的 ToolManager，清理完成后再传播外部取消。"""
    managers = [main_agent_tool_manager, *sub_agent_tool_managers.values()]
    closed_manager_ids = set()
    unique_managers = []
    for tool_manager in managers:
        manager_id = id(tool_manager)
        if manager_id in closed_manager_ids:
            continue
        closed_manager_ids.add(manager_id)
        unique_managers.append(tool_manager)

    async def close_one(tool_manager: ToolManager) -> None:
        close = getattr(tool_manager, "aclose", None)
        if not callable(close):
            return
        try:
            await close()
        except (Exception, asyncio.CancelledError):
            pass

    async def close_all() -> None:
        await asyncio.gather(
            *(close_one(tool_manager) for tool_manager in unique_managers)
        )

    cleanup_task = asyncio.create_task(close_all())
    cancellation_received = False
    while True:
        try:
            await asyncio.shield(cleanup_task)
            break
        except asyncio.CancelledError:
            cancellation_received = True
            if cleanup_task.done():
                break

    cleanup_task.result()
    if cancellation_received:
        raise asyncio.CancelledError


async def execute_task_pipeline(
    cfg: DictConfig,
    task_id: str,
    task_description: str,
    task_file_name: str,
    main_agent_tool_manager: ToolManager,
    sub_agent_tool_managers: Dict[str, ToolManager],
    output_formatter: OutputFormatter,
    ground_truth: Optional[Any] = None,
    log_dir: str = "logs",
    stream_queue: Optional[Any] = None,
    tool_definitions: Optional[List[Dict[str, Any]]] = None,
    sub_agent_tool_definitions: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    is_final_retry: bool = False,
):
    """
    Executes the full pipeline for a single task.

    Args:
        cfg: The Hydra configuration object.
        task_id: A unique identifier for this task run (used for logging).
        task_description: The description of the task for the LLM.
        task_file_name: The path to an associated file (empty string if none).
        main_agent_tool_manager: An initialized main agent ToolManager instance.
        sub_agent_tool_managers: Dictionary mapping sub-agent names to their ToolManager instances.
        output_formatter: An initialized OutputFormatter instance.
        ground_truth: The ground truth for the task (optional).
        log_dir: The directory to save the task log (default: "logs").
        stream_queue: A queue for streaming the task execution (optional).
        tool_definitions: The definitions of the tools for the main agent (optional).
        sub_agent_tool_definitions: The definitions of the tools for the sub-agents (optional).

    Returns:
        包含以下字段的 Pipeline 结果映射：
        - status: completed、failed 或 cancelled。
        - final_summary: 最终总结；失败时可能包含面向用户的错误说明。
        - final_boxed_answer: 从最终总结中提取的 boxed 答案。
        - log_file_path: 任务日志文件路径。
        - failure_experience_summary: 用于重试的失败经验总结。
        - error: 失败或取消原因。
        - result_quality: 最终总结的结构化质量信息。
    """
    total_start_time = time.perf_counter()
    task_log: Optional[TaskLog] = None
    llm_client = None
    try:
        # TaskLog 创建、起始日志与 manager 注入也必须位于资源清理保护范围内。
        task_log = TaskLog(
            log_dir=log_dir,
            task_id=task_id,
            start_time=get_utc_plus_8_time(),
            input={
                "task_description": task_description,
                "task_file_name": task_file_name,
            },
            env_info=get_env_info(cfg),
            ground_truth=ground_truth,
        )
        task_log.log_step(
            "info",
            "Main | Task Start",
            f"--- Starting Task Execution: {task_id} ---",
        )

        main_agent_tool_manager.set_task_log(task_log)
        for sub_agent_tool_manager in sub_agent_tool_managers.values():
            sub_agent_tool_manager.set_task_log(task_log)

        # Initialize LLM client
        llm_init_start_time = time.perf_counter()
        random_uuid = str(uuid.uuid4())
        unique_id = f"{task_id}-{random_uuid}"
        llm_client = ClientFactory(task_id=unique_id, cfg=cfg, task_log=task_log)
        task_log.record_stage_timing(
            "pipeline.llm_client_init",
            int((time.perf_counter() - llm_init_start_time) * 1000),
            metadata={
                "provider": cfg.llm.provider,
                "model_name": cfg.llm.model_name,
            },
        )

        # Initialize orchestrator
        orchestrator_init_start_time = time.perf_counter()
        orchestrator = Orchestrator(
            main_agent_tool_manager=main_agent_tool_manager,
            sub_agent_tool_managers=sub_agent_tool_managers,
            llm_client=llm_client,
            output_formatter=output_formatter,
            cfg=cfg,
            task_log=task_log,
            stream_queue=stream_queue,
            tool_definitions=tool_definitions,
            sub_agent_tool_definitions=sub_agent_tool_definitions,
        )
        task_log.record_stage_timing(
            "pipeline.orchestrator_init",
            int((time.perf_counter() - orchestrator_init_start_time) * 1000),
        )

        main_agent_run_start_time = time.perf_counter()
        (
            final_summary,
            final_boxed_answer,
            failure_experience_summary,
            result_quality,
        ) = await orchestrator.run_main_agent(
            task_description=task_description,
            task_file_name=task_file_name,
            task_id=task_id,
            is_final_retry=is_final_retry,
        )
        task_log.record_stage_timing(
            "pipeline.main_agent_run",
            int((time.perf_counter() - main_agent_run_start_time) * 1000),
        )

        # 连接释放统一交给 finally 的 aclose()，确保异常/取消路径也能清理

        task_log.final_boxed_answer = final_boxed_answer

        if not result_quality.get("answer_available", False):
            task_log.status = "failed"
            task_log.error = FINAL_ANSWER_UNAVAILABLE_ERROR
            log_file_path = task_log.save()
            return _build_pipeline_result(
                status="failed",
                final_summary=final_summary,
                final_boxed_answer=final_boxed_answer,
                log_file_path=log_file_path,
                failure_experience_summary=failure_experience_summary,
                error=FINAL_ANSWER_UNAVAILABLE_ERROR,
                result_quality=result_quality,
            )

        task_log.status = "success"

        # Store failure experience summary in task log if available
        if failure_experience_summary:
            task_log.trace_data["failure_experience_summary"] = (
                failure_experience_summary
            )

        log_file_path = task_log.save()
        return _build_pipeline_result(
            status="completed",
            final_summary=final_summary,
            final_boxed_answer=final_boxed_answer,
            log_file_path=log_file_path,
            failure_experience_summary=failure_experience_summary,
            result_quality=result_quality,
        )

    except asyncio.CancelledError:
        if task_log is None:
            raise
        cancel_message = (
            f"Task {task_id} was cancelled during execution.\n"
            f"Description: {task_description}\n"
            f"File: {task_file_name}"
        )
        _safe_task_log_step(
            task_log,
            "warning",
            "task_cancelled",
            cancel_message,
        )
        task_log.status = "cancelled"
        task_log.error = cancel_message
        log_file_path = task_log.save()
        return _build_pipeline_result(
            status="cancelled",
            final_summary=cancel_message,
            final_boxed_answer="",
            log_file_path=log_file_path,
            error=cancel_message,
        )

    except Exception as e:
        if task_log is None:
            raise RuntimeError(
                f"Task {task_id} failed before TaskLog initialization: "
                f"{type(e).__name__}: {e}"
            ) from e
        error_details = traceback.format_exc()
        _safe_task_log_step(
            task_log,
            "warning",
            "task_error_notification",
            f"An error occurred during task {task_id}",
        )
        _safe_task_log_step(
            task_log,
            "error",
            "task_error_details",
            error_details,
        )

        error_message = (
            f"Error executing task {task_id}:\n"
            f"Description: {task_description}\n"
            f"File: {task_file_name}\n"
            f"Error Type: {type(e).__name__}\n"
            f"Error Details:\n{error_details}"
        )

        task_log.status = "failed"
        task_log.error = error_details

        log_file_path = task_log.save()

        return _build_pipeline_result(
            status="failed",
            final_summary=error_message,
            final_boxed_answer="",
            log_file_path=log_file_path,
            error=error_details,
        )

    finally:
        # 每任务 ToolManager 可能持有持久浏览器会话，必须覆盖所有退出路径清理。
        tool_cleanup_cancelled = False
        try:
            await _close_tool_managers(
                main_agent_tool_manager,
                sub_agent_tool_managers,
            )
        except asyncio.CancelledError:
            tool_cleanup_cancelled = True

        # 释放 LLM client 的异步连接池，避免长跑 worker 累积未关闭的连接与 fd。
        if llm_client is not None:
            try:
                await llm_client.aclose()
            except Exception:
                pass

        if task_log is not None:
            # 保证状态机总是落到终态，避免出现 status=running 导致前端一直等待。
            if task_log.status == "running":
                task_log.status = "failed"
                if not task_log.error:
                    task_log.error = (
                        "Task exited pipeline without terminal status; "
                        "marked as failed to avoid hanging state."
                    )
                _safe_task_log_step(
                    task_log,
                    "warning",
                    "task_status_guard",
                    "Detected non-terminal task status 'running' at pipeline end; force set to 'failed'.",
                )

            total_ms = int((time.perf_counter() - total_start_time) * 1000)
            try:
                task_log.record_stage_timing("pipeline.total", total_ms)
            except Exception:
                pass
            task_log.end_time = get_utc_plus_8_time()

            # 聚合结构化 run_metrics
            task_log.run_metrics.total_duration_ms = total_ms
            stage_timing_summary = task_log.trace_data.get("stage_timing_summary", {})
            task_log.run_metrics.stage_durations = {
                name: data.get("total_duration_ms", 0)
                for name, data in stage_timing_summary.items()
            }

            timing_summary = task_log.format_stage_timing_summary()
            if timing_summary:
                _safe_task_log_step(
                    task_log,
                    "info",
                    "Timing | Summary",
                    timing_summary,
                )

            # 通过 stream_queue 发送结构化 run_metrics 事件供前端/API 消费
            if stream_queue is not None:
                try:
                    await stream_queue.put(
                        {
                            "event": "run_metrics",
                            "data": task_log.run_metrics.to_dict(),
                        }
                    )
                except Exception:
                    pass

            # Record task summary to structured log
            _safe_task_log_step(
                task_log,
                "info",
                "task_execution_finished",
                f"Task {task_id} execution completed with status: {task_log.status}",
            )
            task_log.save()
        if tool_cleanup_cancelled:
            raise asyncio.CancelledError


def create_pipeline_components(cfg: DictConfig):
    """
    Creates and initializes the core components of the agent pipeline.

    Args:
        cfg: The Hydra configuration object.

    Returns:
        Tuple of (main_agent_tool_manager, sub_agent_tool_managers, output_formatter)
    """
    # Create ToolManagers for main agent and sub-agents
    main_agent_mcp_server_configs, main_agent_blacklist = create_mcp_server_parameters(
        cfg, cfg.agent.main_agent
    )
    main_agent_tool_manager = ToolManager(
        main_agent_mcp_server_configs,
        tool_blacklist=main_agent_blacklist,
    )

    # Create OutputFormatter
    output_formatter = OutputFormatter()
    sub_agent_tool_managers = {}

    # For single agent mode
    if not cfg.agent.sub_agents:
        return main_agent_tool_manager, {}, output_formatter

    for sub_agent in cfg.agent.sub_agents:
        sub_agent_mcp_server_configs, sub_agent_blacklist = (
            create_mcp_server_parameters(cfg, cfg.agent.sub_agents[sub_agent])
        )
        sub_agent_tool_manager = ToolManager(
            sub_agent_mcp_server_configs,
            tool_blacklist=sub_agent_blacklist,
        )
        sub_agent_tool_managers[sub_agent] = sub_agent_tool_manager

    return main_agent_tool_manager, sub_agent_tool_managers, output_formatter
