"""研究任务 Worker。

从队列取出任务后，创建任务运行时并执行 execute_task_pipeline()，
同时监听取消标志。

Worker 职责:
1. 更新任务状态：queued -> running
2. 创建 TaskEventSink
3. 调用 execute_task_pipeline()
4. 保存最终 markdown 结果
5. 根据执行结果写入 completed / failed / cancelled
6. 轮询 TaskStore.is_cancel_requested(task_id)，触发协作式取消
"""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# 确保 miroflow-agent 的 src 在 import 路径中
_AGENT_ROOT = Path(__file__).resolve().parents[2] / "miroflow-agent"
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from arq import Retry  # noqa: E402
from arq.connections import RedisSettings  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from pydantic import ValidationError  # noqa: E402

load_dotenv()

from models import ResultQuality  # noqa: E402
from services.pipeline_runtime import RequestLike, get_pipeline_runtime  # noqa: E402
from services.task_event_sink import TaskEventSink  # noqa: E402
from services.task_queue import TaskPayload  # noqa: E402
from services.task_store import TaskStatus, TaskStore  # noqa: E402
from settings import settings  # noqa: E402

logger = logging.getLogger("api-server.worker")
FINAL_ANSWER_UNAVAILABLE_ERROR = "Final summary produced no usable answer."


def _validated_result_quality(quality: object) -> Optional[Dict[str, Any]]:
    """严格校验质量元数据，并返回可安全持久化的标准字典。"""
    if quality is None:
        return None
    try:
        normalized = ResultQuality.model_validate(quality)
    except ValidationError:
        return None
    return normalized.model_dump()


def _validated_cache_quality(quality: object) -> Optional[Dict[str, Any]]:
    """只允许严格校验且答案可用的质量元数据进入共享缓存。"""
    normalized = _validated_result_quality(quality)
    if normalized is None or not normalized["answer_available"]:
        return None
    return normalized


async def _commit_terminal_state(
    task_store: TaskStore,
    task_id: str,
    status: TaskStatus,
    event_type: str,
    event_data: Dict[str, Any],
    *,
    error: Optional[str] = None,
) -> None:
    """先持久化业务终态事件，再提交快照终态，避免 SSE 提前结束漏事件。"""
    try:
        await task_store.append_event(
            task_id,
            event_type,
            event_data,
        )
    except Exception:
        # 即使事件流暂时不可写，也必须提交稳定终态，避免任务永久挂起。
        logger.error(
            "Failed to persist terminal event %s for task %s",
            event_type,
            task_id,
            exc_info=True,
        )
    await task_store.update_task_status(
        task_id,
        status,
        error=error,
    )


async def run_research_job(
    ctx: Dict[str, Any],
    payload_dict: Dict[str, Any],
    _job_timeout: float | None = None,
    _job_try: int | None = None,
) -> Dict[str, Any]:
    """执行研究任务。

    Args:
        ctx: arq 上下文
        payload_dict: 任务载荷字典
        _job_timeout: arq 注入的任务超时（忽略，使用 settings 配置）
        _job_try: 兼容直接调用方的重试次数；arq 正式运行时以 ctx.job_try 为准

    Returns:
        执行结果
    """
    payload = TaskPayload.from_dict(payload_dict)
    task_id = payload.task_id

    # 创建 TaskStore
    task_store = await TaskStore.create()

    try:
        # 排队期间收到的取消必须在构建运行时/启动 Pipeline 前生效。
        try:
            cancelled_before_start = await task_store.is_cancel_requested(task_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # 预检只是低延迟优化；Redis 短暂抖动时仍应启动运行时，并由持续
            # watcher 继续容错轮询，不能把正常任务误判成 setup failure。
            cancelled_before_start = False
            logger.warning(
                "Task %s initial cancel check failed; continuing: %s",
                task_id,
                exc,
            )

        if cancelled_before_start:
            reason = "cancelled_before_start"
            await _commit_terminal_state(
                task_store,
                task_id,
                TaskStatus.CANCELLED,
                "cancelled",
                {"reason": reason},
                error=reason,
            )
            return {"status": "cancelled", "task_id": task_id}

        # 更新状态为 running
        # 新一轮执行开始时清除上一轮可重试初始化错误，避免成功任务残留错误文案。
        await task_store.update_task_status(
            task_id,
            TaskStatus.RUNNING,
            error="",
        )

        # 创建事件接收器
        event_sink = TaskEventSink(task_store, task_id)

        # 构建请求对象
        req = RequestLike(
            query=payload.query,
            mode=payload.mode,
            search_profile=payload.search_profile,
            search_result_num=payload.search_result_num,
            verification_min_search_rounds=payload.verification_min_search_rounds,
            output_detail_level=payload.output_detail_level,
        )

        # 获取运行时
        runtime = get_pipeline_runtime()

        # 创建运行时组件（每任务新建）
        (
            cfg,
            main_tm,
            sub_tms,
            output_fmt,
            tool_defs,
            sub_tool_defs,
        ) = await runtime.create_runtime_components(req)
        logger.info(
            "Task %s runtime config: llm.provider=%s llm.async_client=%s",
            task_id,
            getattr(cfg.llm, "provider", "unknown"),
            getattr(cfg.llm, "async_client", "unknown"),
        )

        # 取消轮询任务
        cancel_poll_interval = settings.worker.cancel_poll_interval_seconds

        async def check_cancel():
            """协作式取消监听器：定期轮询 redis 中 cancel_requested 标志。

            异常处理纪律：单次 redis 读取失败（连接抖动 / 超时）不应让 watcher
            静默退出，否则之后 cancel 信号永远收不到。捕获 ``Exception`` 后只
            打 warning 日志，继续下一次轮询；asyncio.CancelledError 仍要传播
            （pipeline 完成后由外层显式 cancel 该 watcher）。

            Heartbeat 日志：前 3 次轮询打 INFO 日志，后续每 60 秒打一次。便于
            诊断 watcher 是否被 event loop 调度到（历史排查中曾出现 watcher
            启动后长时间不轮询的问题）。
            """
            logger.info(
                "Cancel watcher started for task %s (poll interval=%.2fs)",
                task_id,
                cancel_poll_interval,
            )
            iteration = 0
            heartbeat_every = max(1, int(60.0 / cancel_poll_interval))
            while True:
                await asyncio.sleep(cancel_poll_interval)
                iteration += 1
                if iteration <= 3 or iteration % heartbeat_every == 0:
                    logger.info(
                        "Cancel watcher heartbeat: task=%s iter=%d",
                        task_id,
                        iteration,
                    )
                try:
                    if await task_store.is_cancel_requested(task_id):
                        logger.info(
                            "Task %s cancel requested, watcher exits (iter=%d)",
                            task_id,
                            iteration,
                        )
                        return True
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - 必须吞下保证 watcher 不死
                    logger.warning(
                        "Task %s cancel watcher redis error (continue polling): %s",
                        task_id,
                        exc,
                    )

        # 执行 pipeline
        pipeline_task = asyncio.create_task(
            _execute_pipeline(
                cfg=cfg,
                task_id=task_id,
                query=payload.query,
                main_tm=main_tm,
                sub_tms=sub_tms,
                output_fmt=output_fmt,
                event_sink=event_sink,
                tool_defs=tool_defs,
                sub_tool_defs=sub_tool_defs,
                log_dir=runtime.get_log_dir(),
            )
        )

        cancel_task = asyncio.create_task(check_cancel())

        try:
            done, pending = await asyncio.wait(
                [pipeline_task, cancel_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            # 取消未完成的任务（含响应窗口，超时则放弃等待，避免被吞掉 CancelledError 的
            # 下游代码导致 await pending 永远 hang）。pipeline 内部对 CancelledError
            # 已捕获（pipeline.py），通常会在拦截后短时间内 return；watcher 是 sleep
            # 循环，cancel 几乎立即生效。10s 是一个保守上限。
            for t in pending:
                t.cancel()
                try:
                    await asyncio.wait_for(t, timeout=10.0)
                except asyncio.TimeoutError:
                    logger.warning(
                        "Task %s background coroutine did not respond to cancel within 10s, abandoning",
                        task_id,
                    )
                except asyncio.CancelledError:
                    pass
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Task %s cancel cleanup raised unexpected error: %s",
                        task_id,
                        exc,
                    )

            # 检查结果
            if cancel_task in done:
                # 取消
                event_sink.cancel()
                reason = "user_cancelled"
                await _commit_terminal_state(
                    task_store,
                    task_id,
                    TaskStatus.CANCELLED,
                    "cancelled",
                    {"reason": reason},
                    error=reason,
                )
                return {"status": "cancelled", "task_id": task_id}

            # pipeline 完成
            pipeline_result = pipeline_task.result()
            status = (pipeline_result or {}).get("status", "")
            error = (pipeline_result or {}).get("error")
            final_summary = (pipeline_result or {}).get("final_summary", "")
            result_quality = (pipeline_result or {}).get("result_quality")
            if result_quality is None:
                # 兼容迁移前的 pipeline 键名。
                result_quality = (pipeline_result or {}).get("quality")
            if isinstance(result_quality, dict):
                result_quality = dict(result_quality)
                summary_available = bool(str(final_summary or "").strip())
                if "answer_available" not in result_quality:
                    result_quality["answer_available"] = (
                        summary_available if status == "completed" else False
                    )
                result_quality = _validated_result_quality(result_quality)
            else:
                result_quality = None

            if result_quality is not None:
                if status == "completed":
                    result_quality["answer_available"] = (
                        result_quality["answer_available"] and summary_available
                    )
                    if not result_quality["answer_available"]:
                        if not summary_available:
                            result_quality["format_valid"] = False
                        raw_issues = result_quality.get("issues")
                        issues = (
                            list(raw_issues) if isinstance(raw_issues, list) else []
                        )
                        if "no_answer_available" not in issues:
                            issues.append("no_answer_available")
                        result_quality["issues"] = issues
                else:
                    result_quality["answer_available"] = False
                # 质量信息属于最终结果的一部分，成功和失败终态都必须持久化。
                await task_store.store_result_quality(task_id, result_quality)

            # 根据结构化状态决定落库
            if status == "completed":
                if (
                    isinstance(result_quality, dict)
                    and "answer_available" in result_quality
                ):
                    answer_available = bool(result_quality["answer_available"])
                else:
                    answer_available = bool(str(final_summary or "").strip())

                if not answer_available:
                    await _commit_terminal_state(
                        task_store,
                        task_id,
                        TaskStatus.FAILED,
                        "error",
                        {"error": FINAL_ANSWER_UNAVAILABLE_ERROR},
                        error=FINAL_ANSWER_UNAVAILABLE_ERROR,
                    )
                    return {
                        "status": "failed",
                        "task_id": task_id,
                        "error": FINAL_ANSWER_UNAVAILABLE_ERROR,
                    }

                if final_summary:
                    await task_store.store_result(task_id, final_summary)
                await task_store.update_task_status(task_id, TaskStatus.COMPLETED)
                cache_quality = _validated_cache_quality(result_quality)
                if final_summary and payload.cache_key and cache_quality is not None:
                    try:
                        await task_store.store_cached_result(
                            payload.cache_key,
                            final_summary,
                            cache_quality,
                        )
                    except Exception as exc:  # noqa: BLE001
                        # 缓存是优化路径，写入失败不能把已完成的研究降级为失败。
                        logger.warning(
                            "Task %s shared result cache write failed: %s",
                            task_id,
                            exc,
                        )
                return {
                    "status": "completed",
                    "task_id": task_id,
                    "log_file": pipeline_result.get("log_file_path", ""),
                }
            elif status == "failed":
                error_msg = str(error or "unknown error")
                await _commit_terminal_state(
                    task_store,
                    task_id,
                    TaskStatus.FAILED,
                    "error",
                    {"error": error_msg},
                    error=error_msg,
                )
                return {"status": "failed", "task_id": task_id, "error": error_msg}
            elif status == "cancelled":
                reason = "pipeline_cancelled"
                await _commit_terminal_state(
                    task_store,
                    task_id,
                    TaskStatus.CANCELLED,
                    "cancelled",
                    {"reason": reason},
                    error=reason,
                )
                return {"status": "cancelled", "task_id": task_id}
            else:
                # 未知状态：按 failed 处理
                unknown_msg = f"Unknown pipeline status: {status}"
                await _commit_terminal_state(
                    task_store,
                    task_id,
                    TaskStatus.FAILED,
                    "error",
                    {"error": unknown_msg},
                    error=unknown_msg,
                )
                return {"status": "failed", "task_id": task_id, "error": unknown_msg}

        except asyncio.CancelledError:
            event_sink.cancel()
            reason = "worker_cancelled"
            await _commit_terminal_state(
                task_store,
                task_id,
                TaskStatus.CANCELLED,
                "cancelled",
                {"reason": reason},
                error=reason,
            )
            return {"status": "cancelled", "task_id": task_id}

        except Exception as e:
            logger.error("Pipeline execution failed: %s", e, exc_info=True)
            error_msg = str(e)
            await _commit_terminal_state(
                task_store,
                task_id,
                TaskStatus.FAILED,
                "error",
                {"error": error_msg},
                error=error_msg,
            )
            return {"status": "failed", "task_id": task_id, "error": error_msg}

    except asyncio.CancelledError:
        reason = "worker_cancelled_during_setup"
        logger.info("Worker setup cancelled for task %s", task_id)
        await _commit_terminal_state(
            task_store,
            task_id,
            TaskStatus.CANCELLED,
            "cancelled",
            {"reason": reason},
            error=reason,
        )
        return {"status": "cancelled", "task_id": task_id}

    except Exception as e:
        logger.error("Worker setup failed: %s", e, exc_info=True)
        error_msg = str(e)
        raw_job_try = ctx.get("job_try", _job_try or 1)
        try:
            current_try = max(1, int(raw_job_try))
        except (TypeError, ValueError):
            current_try = 1
        max_tries = settings.worker.max_tries

        if current_try >= max_tries:
            try:
                await _commit_terminal_state(
                    task_store,
                    task_id,
                    TaskStatus.FAILED,
                    "error",
                    {"error": error_msg},
                    error=error_msg,
                )
            except Exception:
                logger.error(
                    "Failed to persist final setup failure for task %s",
                    task_id,
                    exc_info=True,
                )
            return {
                "status": "failed",
                "task_id": task_id,
                "error": error_msg,
            }

        try:
            await task_store.update_task_status(
                task_id,
                TaskStatus.QUEUED,
                error=error_msg,
            )
            await task_store.append_event(
                task_id,
                "retrying",
                {
                    "error": error_msg,
                    "attempt": current_try,
                    "max_tries": max_tries,
                },
            )
        except Exception:
            logger.error(
                "Failed to persist retry state for task %s",
                task_id,
                exc_info=True,
            )
        raise Retry(defer=settings.worker.retry_defer_seconds)

    finally:
        await task_store.close()


async def _execute_pipeline(
    cfg,
    task_id: str,
    query: str,
    main_tm,
    sub_tms,
    output_fmt,
    event_sink: TaskEventSink,
    tool_defs,
    sub_tool_defs,
    log_dir: str,
) -> dict:
    """执行 pipeline 并返回结构化结果 dict。

    兼容旧 tuple 和新 dict 两种返回格式，统一规范化为 dict。
    """
    from src.core.pipeline import execute_task_pipeline

    result = await execute_task_pipeline(
        cfg=cfg,
        task_id=task_id,
        task_description=query,
        task_file_name="",  # API 模式无文件
        main_agent_tool_manager=main_tm,
        sub_agent_tool_managers=sub_tms,
        output_formatter=output_fmt,
        stream_queue=event_sink,
        log_dir=log_dir,
        tool_definitions=tool_defs,
        sub_agent_tool_definitions=sub_tool_defs,
    )

    if isinstance(result, dict):
        return result

    # 兼容旧 tuple: (final_summary, final_boxed_answer, log_file_path, failure_experience_summary)
    return {
        "status": "completed",
        "final_summary": result[0],
        "final_boxed_answer": result[1],
        "log_file_path": result[2],
        "failure_experience_summary": result[3] if len(result) > 3 else None,
        "error": None,
    }


class WorkerSettings:
    """arq Worker 配置。"""

    functions = [run_research_job]
    queue_name = settings.task_queue.queue_name
    max_jobs = settings.worker.max_jobs
    max_tries = settings.worker.max_tries
    job_timeout = settings.worker.job_timeout_seconds
    keep_result = 3600  # 保留 arq 结果 1 小时（仅用于调试）

    redis_settings = RedisSettings(
        host=settings.valkey.host,
        port=settings.valkey.port,
        password=settings.valkey.password,
        database=settings.valkey.queue_db,
    )
