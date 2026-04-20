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

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from middleware.auth import verify_bearer_token
from models import (
    CancelResponse,
    ErrorResponse,
    ResearchRequest,
    ResearchResponse,
    ResearchTaskMeta,
    ResearchTaskStatusResponse,
)
from services.task_queue import TaskPayload, get_task_queue
from services.task_store import TaskStatus, get_task_store
from settings import settings
from src.cache.result_cache import ResultCache

logger = logging.getLogger("api-server")

router = APIRouter(prefix="/v1/research", tags=["research"])

# 结果缓存（保持内存实现）
_result_cache = ResultCache(
    max_size=settings.result_cache_max_size,
    ttl_seconds=settings.result_cache_ttl_seconds,
)


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
    task_queue = await get_task_queue()

    # 结果缓存检查
    cache_key = ResultCache.make_key(
        req.query, req.mode, req.search_profile, req.output_detail_level
    )
    cached = _result_cache.get(cache_key)
    if cached is not None:
        # 缓存命中：创建 cached 任务并写入事件
        task_id = f"cached-{uuid.uuid4().hex[:8]}"
        logger.info("Cache hit | key=%s | query=%s", cache_key, req.query[:60])

        await task_store.create_task(
            task_id=task_id,
            status=TaskStatus.CACHED,
            caller_id=req.caller_id or "",
            query=req.query,
            mode=req.mode,
            search_profile=req.search_profile,
            search_result_num=req.search_result_num,
            verification_min_search_rounds=req.verification_min_search_rounds,
            output_detail_level=req.output_detail_level,
        )
        await task_store.append_event(task_id, "final_output", {"markdown": cached})
        await task_store.store_result(task_id, cached)
        await task_store.update_task_status(task_id, TaskStatus.CACHED)

        return ResearchResponse(task_id=task_id, status="cached")

    # 创建任务
    task_id = str(uuid.uuid4())
    await task_store.create_task(
        task_id=task_id,
        status=TaskStatus.QUEUED,
        caller_id=req.caller_id or "",
        query=req.query,
        mode=req.mode,
        search_profile=req.search_profile,
        search_result_num=req.search_result_num,
        verification_min_search_rounds=req.verification_min_search_rounds,
        output_detail_level=req.output_detail_level,
    )

    # 入队
    payload = TaskPayload(
        task_id=task_id,
        query=req.query,
        mode=req.mode,
        search_profile=req.search_profile,
        search_result_num=req.search_result_num,
        verification_min_search_rounds=req.verification_min_search_rounds,
        output_detail_level=req.output_detail_level,
        caller_id=req.caller_id or "",
        cache_key=cache_key,
    )
    await task_queue.enqueue_research_job(payload)

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
        heartbeat_interval = 15  # 心跳间隔（秒）
        last_heartbeat = time.time()

        while True:
            # 读取事件
            events = await task_store.read_events(
                task_id,
                last_event_id=last_event_id,
                block_ms=5000,
                count=100,
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
                # 事件流读取完毕后输出 done
                if not events:
                    yield {
                        "event": "done",
                        "data": _json.dumps({"status": current.status.value}),
                    }
                    break

            # 发送心跳
            if time.time() - last_heartbeat > heartbeat_interval:
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
        raise HTTPException(status_code=400, detail=f"Task {task_id} is not cancellable (status: {meta.status.value})")

    await task_store.request_cancel(task_id)
    return CancelResponse(cancelled=1, task_ids=[task_id])


@router.post(
    "/cancel",
    response_model=CancelResponse,
    summary="按 caller_id 取消任务",
    description="不传 caller_id 则取消所有运行中任务。",
)
async def cancel_by_caller(
    caller_id: Optional[str] = None,
    _token: Optional[str] = Depends(verify_bearer_token),
):
    task_store = await get_task_store()
    cancelled_ids = await task_store.cancel_tasks_by_caller(caller_id)
    return CancelResponse(cancelled=len(cancelled_ids), task_ids=cancelled_ids)
