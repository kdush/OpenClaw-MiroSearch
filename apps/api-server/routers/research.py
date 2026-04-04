"""研究任务相关端点：提交、流式输出、取消。"""

import asyncio
import logging
import os
import threading
import uuid
from typing import AsyncIterable, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.sse import EventSourceResponse, ServerSentEvent

from deps import (
    cancel_task,
    cancel_tasks_by_caller,
    finish_task,
    get_task,
    register_task,
    set_last_run_metrics,
)
from middleware.auth import verify_bearer_token
from models import CancelResponse, ErrorResponse, ResearchRequest, ResearchResponse
from src.cache.result_cache import ResultCache

logger = logging.getLogger("api-server")

router = APIRouter(prefix="/v1/research", tags=["research"])

_result_cache = ResultCache(
    max_size=int(os.getenv("RESULT_CACHE_MAX_SIZE", "128")),
    ttl_seconds=int(os.getenv("RESULT_CACHE_TTL_SECONDS", "3600")),
)

# ---- pipeline 预加载缓存 ----
_preload_cache = {}
_preload_lock = threading.Lock()


def _ensure_pipeline_loaded(req: ResearchRequest) -> dict:
    """懒加载 pipeline 组件（与 gradio-demo 类似的预加载逻辑）。"""
    from hydra import compose, initialize_config_dir
    from omegaconf import OmegaConf

    from src.core.pipeline import create_pipeline_components

    cache_key = f"{req.mode}|{req.search_profile}|{req.search_result_num}|{req.output_detail_level}"
    with _preload_lock:
        if cache_key in _preload_cache:
            return _preload_cache[cache_key]

    # 构建 Hydra 配置
    conf_dir = os.getenv("AGENT_CONF_DIR", "")
    if not conf_dir:
        from pathlib import Path
        conf_dir = str(Path(__file__).resolve().parents[2] / "miroflow-agent" / "conf")

    overrides = [
        f"agent={os.getenv('AGENT_CONFIG', 'mirothinker_v1.5_keep5_max200')}",
        f"llm={os.getenv('LLM_CONFIG', 'qwen-3')}",
    ]

    with initialize_config_dir(config_path=conf_dir, version_base=None):
        cfg = compose(config_name="config", overrides=overrides)

    main_tm, sub_tms, output_fmt = create_pipeline_components(cfg)

    # 获取工具定义
    async def _get_tool_defs():
        tool_defs = await main_tm.get_tools_definitions()
        sub_defs = {}
        for name, tm in sub_tms.items():
            sub_defs[name] = await tm.get_tools_definitions()
        return tool_defs, sub_defs

    loop = asyncio.new_event_loop()
    tool_definitions, sub_agent_tool_definitions = loop.run_until_complete(_get_tool_defs())
    loop.close()

    result = {
        "cfg": cfg,
        "main_agent_tool_manager": main_tm,
        "sub_agent_tool_managers": sub_tms,
        "output_formatter": output_fmt,
        "tool_definitions": tool_definitions,
        "sub_agent_tool_definitions": sub_agent_tool_definitions,
    }
    with _preload_lock:
        _preload_cache[cache_key] = result
    return result


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
    # 结果缓存检查
    cache_key = ResultCache.make_key(
        req.query, req.mode, req.search_profile, req.output_detail_level
    )
    cached = _result_cache.get(cache_key)
    if cached is not None:
        task_id = f"cached-{uuid.uuid4().hex[:8]}"
        logger.info("Cache hit | key=%s | query=%s", cache_key, req.query[:60])
        queue = register_task(task_id, caller_id=req.caller_id or "")
        queue.put_nowait({"event": "final_output", "data": {"markdown": cached}})
        queue.put_nowait(None)
        finish_task(task_id, "completed")
        return ResearchResponse(task_id=task_id, status="cached")

    task_id = str(uuid.uuid4())
    queue = register_task(task_id, caller_id=req.caller_id or "")

    # 后台启动 pipeline
    asyncio.get_event_loop().run_in_executor(
        None, _run_pipeline_background, task_id, req, queue, cache_key
    )

    return ResearchResponse(task_id=task_id)


def _run_pipeline_background(task_id: str, req: ResearchRequest, queue: asyncio.Queue, cache_key: str = ""):
    """在线程中运行 pipeline，事件写入 queue。"""
    from src.core.pipeline import execute_task_pipeline

    task_info = get_task(task_id)
    cancel_event = task_info["cancel_event"] if task_info else threading.Event()

    try:
        components = _ensure_pipeline_loaded(req)
    except Exception as e:
        logger.error("Pipeline 预加载失败: %s", e, exc_info=True)
        queue.put_nowait({"event": "error", "data": {"error": str(e)}})
        queue.put_nowait(None)
        finish_task(task_id, "failed")
        return

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    class QueueWrapper:
        def __init__(self, q, cancel_evt):
            self._q = q
            self._cancel = cancel_evt

        async def put(self, item):
            if self._cancel.is_set():
                return
            # run_metrics 事件特殊处理
            if isinstance(item, dict) and item.get("event") == "run_metrics":
                set_last_run_metrics(item.get("data"))
            self._q.put_nowait(item)

    wrapper = QueueWrapper(queue, cancel_event)

    async def _run():
        pipeline_task = asyncio.create_task(
            execute_task_pipeline(
                cfg=components["cfg"],
                task_id=task_id,
                task_description=req.query,
                task_file_name=None,
                main_agent_tool_manager=components["main_agent_tool_manager"],
                sub_agent_tool_managers=components["sub_agent_tool_managers"],
                output_formatter=components["output_formatter"],
                stream_queue=wrapper,
                log_dir=os.getenv("LOG_DIR", "logs/api-server"),
                tool_definitions=components["tool_definitions"],
                sub_agent_tool_definitions=components["sub_agent_tool_definitions"],
            )
        )

        async def _watch_cancel():
            while not cancel_event.is_set():
                await asyncio.sleep(0.5)
            pipeline_task.cancel()

        cancel_watcher = asyncio.create_task(_watch_cancel())
        try:
            done, pending = await asyncio.wait(
                [pipeline_task, cancel_watcher],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
            for t in done:
                if t == pipeline_task:
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass
        except Exception as e:
            logger.error("Pipeline 执行出错: %s", e, exc_info=True)
            queue.put_nowait({"event": "error", "data": {"error": str(e)}})

    try:
        loop.run_until_complete(_run())
        finish_task(task_id, "completed")
    except Exception as e:
        logger.error("Pipeline 线程异常: %s", e, exc_info=True)
        finish_task(task_id, "failed")
    finally:
        queue.put_nowait(None)
        loop.close()


@router.get(
    "/{task_id}/stream",
    summary="SSE 流式获取任务进度",
    description="通过 Server-Sent Events 实时获取研究任务的执行进度和结果。",
)
async def stream_research(
    task_id: str,
    _token: Optional[str] = Depends(verify_bearer_token),
):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    queue = task["queue"]

    async def _event_generator():
        while True:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=30)
                if message is None:
                    yield ServerSentEvent(data={"status": "completed"}, event="done")
                    break
                event_type = message.get("event", "message")
                yield ServerSentEvent(data=message.get("data", {}), event=event_type)
            except asyncio.TimeoutError:
                yield ServerSentEvent(data={}, event="heartbeat")

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
    success = cancel_task(task_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
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
    cancelled_ids = cancel_tasks_by_caller(caller_id)
    return CancelResponse(cancelled=len(cancelled_ids), task_ids=cancelled_ids)
