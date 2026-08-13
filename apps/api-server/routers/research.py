"""研究任务相关端点：提交、状态查询、流式输出、取消。

重构后执行模型:
- POST /v1/research: 参数校验 -> 缓存检查 -> 任务入队 -> 返回 task_id
- GET /v1/research/{task_id}: 返回任务快照
- GET /v1/research/{task_id}/stream: 从 Redis Stream 增量读取事件
- POST /v1/research/{task_id}/cancel: 写入共享取消标记
- POST /v1/research/cancel: 按 caller 批量设置取消标记
"""

import logging
import time
import uuid
from typing import Optional

import json as _json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError
from sse_starlette.sse import EventSourceResponse

from middleware.auth import verify_bearer_token
from models import (
    CancelResponse,
    ErrorResponse,
    ResearchRequest,
    ResearchResponse,
    ResearchTaskMeta,
    ResearchTaskStatusResponse,
    ResultQuality,
)
from services.task_queue import TaskPayload, get_task_queue
from services.task_store import TaskStatus, get_task_store
from services.profile_resolver import resolve_effective_research_params
from src.cache.result_cache import ResultCache

logger = logging.getLogger("api-server")

router = APIRouter(prefix="/v1/research", tags=["research"])

SSE_HEARTBEAT_INTERVAL_SECONDS = 15
SSE_EVENT_READ_BLOCK_MS = 5000
SSE_EVENT_READ_COUNT = 100
SSE_TERMINAL_DRAIN_BLOCK_MS = None
TASK_QUEUE_UNAVAILABLE_DETAIL = "Task queue unavailable"


# API 与 Worker 共享的结果缓存质量校验
def _validate_cached_quality(quality: object) -> Optional[dict]:
    """校验共享缓存的质量元数据；缺失或不可用时按未命中处理。"""
    if quality is None:
        return None
    try:
        normalized = ResultQuality.model_validate(quality)
    except ValidationError:
        return None
    if not normalized.answer_available:
        return None
    return normalized.model_dump()


@router.post(
    "",
    response_model=ResearchResponse,
    responses={401: {"model": ErrorResponse}},
    summary="提交研究任务",
    description="提交一个研究查询，返回 task_id 后通过 SSE 流获取实时进度。",
)
async def create_research(
    req: ResearchRequest,
    _token: Optional[str] = Depends(verify_bearer_token),
):
    task_store = await get_task_store()
    effective = resolve_effective_research_params(
        mode=req.mode,
        search_profile=req.search_profile,
        search_result_num=req.search_result_num,
        verification_min_search_rounds=req.verification_min_search_rounds,
        output_detail_level=req.output_detail_level,
    )

    # 结果缓存检查：纳入会改变检索深度（从而改变结论）的参数，避免误命中
    cache_key = ResultCache.make_key(
        req.query,
        effective.mode,
        effective.search_profile,
        effective.output_detail_level,
        search_result_num=effective.search_result_num,
        verification_min_search_rounds=(
            effective.verification_min_search_rounds
            if effective.mode == "verified"
            else None
        ),
    )
    cached = None
    cached_quality = None
    shared_cache_entry = await task_store.get_cached_result(cache_key)
    if isinstance(shared_cache_entry, dict):
        shared_result = shared_cache_entry.get("result")
        if isinstance(shared_result, str) and shared_result.strip():
            cached_quality = _validate_cached_quality(shared_cache_entry.get("quality"))
            if cached_quality is None:
                await task_store.delete_cached_result(cache_key)
            else:
                cached = shared_result

    if cached is not None:
        # 缓存命中：创建 cached 任务并写入事件
        task_id = f"cached-{uuid.uuid4()}"
        logger.info("Cache hit | key=%s | query=%s", cache_key, req.query[:60])

        await task_store.create_task(
            task_id=task_id,
            status=TaskStatus.CACHED,
            caller_id=req.caller_id or "",
            query=req.query,
            **effective.as_dict(),
        )
        await task_store.append_event(task_id, "final_output", {"markdown": cached})
        await task_store.store_result(task_id, cached)
        await task_store.store_result_quality(task_id, cached_quality)
        await task_store.update_task_status(task_id, TaskStatus.CACHED)

        return ResearchResponse(task_id=task_id, status="cached")

    task_queue = await get_task_queue()

    # 创建任务
    task_id = str(uuid.uuid4())
    await task_store.create_task(
        task_id=task_id,
        status=TaskStatus.QUEUED,
        caller_id=req.caller_id or "",
        query=req.query,
        **effective.as_dict(),
    )

    # 入队
    payload = TaskPayload(
        task_id=task_id,
        query=req.query,
        **effective.as_dict(),
        caller_id=req.caller_id or "",
        cache_key=cache_key,
    )
    try:
        await task_queue.enqueue_research_job(payload)
    except Exception as exc:
        error_message = f"Failed to enqueue research task: {exc}"
        logger.error(
            "Failed to enqueue task %s: %s",
            task_id,
            exc,
            exc_info=True,
        )
        try:
            try:
                await task_store.append_event(
                    task_id,
                    "error",
                    {"error": error_message},
                )
            finally:
                await task_store.update_task_status(
                    task_id,
                    TaskStatus.FAILED,
                    error=error_message,
                )
        except Exception:
            logger.error(
                "Failed to persist enqueue failure for task %s",
                task_id,
                exc_info=True,
            )
        raise HTTPException(
            status_code=503,
            detail=TASK_QUEUE_UNAVAILABLE_DETAIL,
        ) from exc

    return ResearchResponse(task_id=task_id, status="accepted")


@router.get(
    "/{task_id}",
    response_model=ResearchTaskStatusResponse,
    responses={404: {"model": ErrorResponse}},
    summary="获取任务状态",
    description="返回任务快照，支持不依赖 SSE 的轮询式查询。",
)
async def get_task_status(
    task_id: str,
    _token: Optional[str] = Depends(verify_bearer_token),
):
    task_store = await get_task_store()

    meta = await task_store.get_task(task_id)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    result = await task_store.get_result(task_id)
    event_count = await task_store.get_event_stream_length(task_id)
    quality = await task_store.get_result_quality(task_id)
    result_available = bool(str(result or "").strip())
    if quality is None:
        normalized_quality = ResultQuality(answer_available=result_available)
    else:
        try:
            normalized_quality = ResultQuality.model_validate(quality)
        except ValidationError:
            logger.warning("Task %s has invalid result quality metadata", task_id)
            normalized_quality = ResultQuality(answer_available=result_available)

    return ResearchTaskStatusResponse(
        task_id=task_id,
        status=meta.status.value,
        meta=ResearchTaskMeta(
            task_id=meta.task_id,
            status=meta.status.value,
            caller_id=meta.caller_id,
            query=meta.query,
            mode=meta.mode,
            search_profile=meta.search_profile,
            search_result_num=meta.search_result_num,
            verification_min_search_rounds=meta.verification_min_search_rounds,
            output_detail_level=meta.output_detail_level,
            created_at=meta.created_at,
            started_at=meta.started_at,
            finished_at=meta.finished_at,
            current_stage=meta.current_stage,
            error=meta.error,
        ),
        result=result,
        event_count=event_count,
        result_quality=normalized_quality,
    )


@router.get(
    "/{task_id}/stream",
    summary="SSE 流式获取任务进度",
    description="通过 Server-Sent Events 实时获取研究任务的执行进度和结果。",
)
async def stream_research(
    task_id: str,
    _token: Optional[str] = Depends(verify_bearer_token),
):
    task_store = await get_task_store()

    meta = await task_store.get_task(task_id)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    async def _event_generator():
        last_event_id = None
        last_heartbeat = time.time()

        while True:
            # 读取事件
            events = await task_store.read_events(
                task_id,
                last_event_id=last_event_id,
                block_ms=SSE_EVENT_READ_BLOCK_MS,
                count=SSE_EVENT_READ_COUNT,
            )
            for event in events:
                last_event_id = event["id"]
                yield {
                    "event": event["event"],
                    "data": _json.dumps(event["data"], ensure_ascii=False),
                }
                last_heartbeat = time.time()

            # 检查任务是否进入终态
            current = await task_store.get_task(task_id)
            if current and current.status in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
                TaskStatus.CACHED,
            ):
                # 状态可能在本轮首次读取之后才提交。终态写入约定是“事件先、
                # 状态后”，因此看到终态后再做一次非阻塞 drain，避免永久漏掉
                # 已写入但尚未读取的 error/cancelled/final_output。
                while True:
                    terminal_events = await task_store.read_events(
                        task_id,
                        last_event_id=last_event_id,
                        block_ms=SSE_TERMINAL_DRAIN_BLOCK_MS,
                        count=SSE_EVENT_READ_COUNT,
                    )
                    for event in terminal_events:
                        last_event_id = event["id"]
                        yield {
                            "event": event["event"],
                            "data": _json.dumps(
                                event["data"],
                                ensure_ascii=False,
                            ),
                        }
                    if len(terminal_events) < SSE_EVENT_READ_COUNT:
                        break

                done_data = {"status": current.status.value}
                if current.error:
                    done_data["error"] = current.error
                yield {
                    "event": "done",
                    "data": _json.dumps(done_data, ensure_ascii=False),
                }
                break

            # 发送心跳
            if time.time() - last_heartbeat > SSE_HEARTBEAT_INTERVAL_SECONDS:
                yield {"event": "heartbeat", "data": "{}"}
                last_heartbeat = time.time()

    return EventSourceResponse(_event_generator())


@router.post(
    "/{task_id}/cancel",
    response_model=CancelResponse,
    summary="取消指定任务",
)
async def cancel_research(
    task_id: str,
    _token: Optional[str] = Depends(verify_bearer_token),
):
    task_store = await get_task_store()

    meta = await task_store.get_task(task_id)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    if meta.status not in (TaskStatus.QUEUED, TaskStatus.RUNNING):
        raise HTTPException(
            status_code=400,
            detail=f"Task {task_id} is not cancellable (status: {meta.status.value})",
        )

    await task_store.request_cancel(task_id)
    return CancelResponse(cancelled=1, task_ids=[task_id])


@router.post(
    "/cancel",
    response_model=CancelResponse,
    summary="按 caller_id 取消任务",
    description="caller_id 为必填项，只取消该调用方排队中或运行中的任务。",
)
async def cancel_by_caller(
    caller_id: str = Query(..., min_length=1),
    _token: Optional[str] = Depends(verify_bearer_token),
):
    normalized_caller_id = caller_id.strip()
    if not normalized_caller_id:
        raise HTTPException(status_code=422, detail="caller_id must not be blank")

    task_store = await get_task_store()
    cancelled_ids = await task_store.cancel_tasks_by_caller(normalized_caller_id)
    return CancelResponse(cancelled=len(cancelled_ids), task_ids=cancelled_ids)
