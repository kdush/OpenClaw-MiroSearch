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
from typing import Any, Dict

# 确保 miroflow-agent 的 src 在 import 路径中
_AGENT_ROOT = Path(__file__).resolve().parents[2] / "miroflow-agent"
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from arq import Retry
from arq.connections import RedisSettings
from dotenv import load_dotenv

load_dotenv()

from services.pipeline_runtime import get_pipeline_runtime, RequestLike
from services.task_event_sink import TaskEventSink
from services.task_queue import TaskPayload
from services.task_store import TaskStore, TaskStatus
from settings import settings

logger = logging.getLogger("api-server.worker")


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
        _job_try: arq 注入的重试次数（忽略）

    Returns:
        执行结果
    """
    payload = TaskPayload.from_dict(payload_dict)
    task_id = payload.task_id

    # 创建 TaskStore
    task_store = await TaskStore.create()

    try:
        # 更新状态为 running
        await task_store.update_task_status(task_id, TaskStatus.RUNNING)

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
        cfg, main_tm, sub_tms, output_fmt, tool_defs, sub_tool_defs = await runtime.create_runtime_components(req)

        # 取消轮询任务
        cancel_poll_interval = settings.worker.cancel_poll_interval_seconds

        async def check_cancel():
            """检查取消标志。"""
            while True:
                await asyncio.sleep(cancel_poll_interval)
                if await task_store.is_cancel_requested(task_id):
                    logger.info("Task %s cancel requested", task_id)
                    return True

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

            # 取消未完成的任务
            for t in pending:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

            # 检查结果
            if cancel_task in done:
                # 取消
                event_sink.cancel()
                await task_store.update_task_status(task_id, TaskStatus.CANCELLED)
                await task_store.append_event(task_id, "cancelled", {"reason": "user_cancelled"})
                return {"status": "cancelled", "task_id": task_id}

            # pipeline 完成
            result = pipeline_task.result()
            final_summary, final_boxed_answer, log_file_path = result

            # 存储结果
            if final_summary:
                await task_store.store_result(task_id, final_summary)

            await task_store.update_task_status(task_id, TaskStatus.COMPLETED)
            return {"status": "completed", "task_id": task_id, "log_file": log_file_path}

        except asyncio.CancelledError:
            event_sink.cancel()
            await task_store.update_task_status(task_id, TaskStatus.CANCELLED)
            await task_store.append_event(task_id, "cancelled", {"reason": "worker_cancelled"})
            return {"status": "cancelled", "task_id": task_id}

        except Exception as e:
            logger.error("Pipeline execution failed: %s", e, exc_info=True)
            error_msg = str(e)
            await task_store.update_task_status(task_id, TaskStatus.FAILED, error=error_msg)
            await task_store.append_event(task_id, "error", {"error": error_msg})
            return {"status": "failed", "task_id": task_id, "error": error_msg}

    except Exception as e:
        logger.error("Worker setup failed: %s", e, exc_info=True)
        try:
            await task_store.update_task_status(task_id, TaskStatus.FAILED, error=str(e))
        except Exception:
            pass
        raise Retry(defer=5)  # 5 秒后重试

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
):
    """执行 pipeline 并返回结果。"""
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

    # result: (final_summary, final_boxed_answer, log_file_path, failure_experience_summary)
    return result[0], result[1], result[2]


class WorkerSettings:
    """arq Worker 配置。"""

    functions = [run_research_job]
    queue_name = settings.task_queue.queue_name
    max_jobs = settings.worker.max_jobs
    job_timeout = settings.worker.job_timeout_seconds
    keep_result = 3600  # 保留 arq 结果 1 小时（仅用于调试）
    
    redis_settings = RedisSettings(
        host=settings.valkey.host,
        port=settings.valkey.port,
        password=settings.valkey.password,
        database=settings.valkey.queue_db,
    )
