"""api-server 调用客户端。

封装 apps/api-server 暴露的 HTTP 端点，提供：
- 创建任务（POST /v1/research）
- 轮询任务状态（GET /v1/research/{task_id}）
- 订阅任务进度（GET /v1/research/{task_id}/stream，SSE 流）
- 取消任务（POST /v1/research/{task_id}/cancel）

后端 SSE 端点会从 Redis Stream 头部回放历史事件 + 阻塞订阅新事件，
因此任意时刻用同一个 task_id 调用 stream，都能拿到完整事件序列，
天然支持页面刷新 / 断电重连场景。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, AsyncGenerator, Dict, Optional

import aiohttp

logger = logging.getLogger(__name__)


# ====== 配置 ======

DEFAULT_API_BASE_URL = "http://127.0.0.1:8090"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_SSE_READ_TIMEOUT_SECONDS = 600  # SSE 单次读取超时（含阻塞等待）


def get_api_base_url() -> str:
    """读取 api-server 基础地址。"""
    return os.getenv("API_BASE_URL", DEFAULT_API_BASE_URL).rstrip("/")


def get_api_bearer_token() -> Optional[str]:
    """读取 api-server Bearer Token（可选）。"""
    return os.getenv("API_BEARER_TOKEN") or None


def is_api_mode_enabled() -> bool:
    """是否启用 api-server 后端模式。

    - 显式设置 BACKEND_MODE=api：启用
    - 显式设置 BACKEND_MODE=local：禁用
    - 未设置：默认禁用（保持向后兼容）
    """
    raw = (os.getenv("BACKEND_MODE") or "").strip().lower()
    if raw == "api":
        return True
    if raw == "local":
        return False
    return False


# ====== 异常 ======


class ApiClientError(RuntimeError):
    """api-server 客户端通用错误。"""


class TaskNotFoundError(ApiClientError):
    """task_id 在 api-server 上不存在。"""


# ====== 客户端实现 ======


def _build_headers() -> Dict[str, str]:
    headers = {"Accept": "application/json"}
    token = get_api_bearer_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def create_task(
    query: str,
    mode: str,
    search_profile: str,
    search_result_num: int,
    verification_min_search_rounds: int,
    output_detail_level: str,
    caller_id: Optional[str] = None,
) -> Dict[str, Any]:
    """提交研究任务到 api-server。

    Returns:
        {"task_id": str, "status": "accepted" | "cached"}
    """
    url = f"{get_api_base_url()}/v1/research"
    payload = {
        "query": query,
        "mode": mode,
        "search_profile": search_profile,
        "search_result_num": search_result_num,
        "verification_min_search_rounds": verification_min_search_rounds,
        "output_detail_level": output_detail_level,
    }
    if caller_id:
        payload["caller_id"] = caller_id

    timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload, headers=_build_headers()) as resp:
            if resp.status >= 400:
                body = await resp.text()
                raise ApiClientError(
                    f"create_task 失败 status={resp.status} body={body[:300]}"
                )
            data = await resp.json()
            return data


async def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    """获取任务快照。任务不存在返回 None。"""
    url = f"{get_api_base_url()}/v1/research/{task_id}"
    timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=_build_headers()) as resp:
            if resp.status == 404:
                return None
            if resp.status >= 400:
                body = await resp.text()
                raise ApiClientError(
                    f"get_task 失败 status={resp.status} body={body[:300]}"
                )
            return await resp.json()


async def cancel_task(task_id: str) -> Dict[str, Any]:
    """请求取消任务（不抛异常，错误转 dict）。"""
    url = f"{get_api_base_url()}/v1/research/{task_id}/cancel"
    timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_SECONDS)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=_build_headers()) as resp:
                if resp.status == 404:
                    return {"cancelled": 0, "task_ids": [], "reason": "not_found"}
                if resp.status >= 400:
                    body = await resp.text()
                    return {
                        "cancelled": 0,
                        "task_ids": [],
                        "reason": f"http_{resp.status}",
                        "detail": body[:300],
                    }
                return await resp.json()
    except aiohttp.ClientError as exc:
        logger.warning("cancel_task 网络错误: %s", exc)
        return {"cancelled": 0, "task_ids": [], "reason": "network_error"}


# ====== SSE 解析 ======


def _parse_sse_block(block: str) -> Optional[Dict[str, Any]]:
    """解析单个 SSE 事件块（行间用 \\n 分隔，块间空行分隔）。

    返回 {"event": str, "data": Any}；忽略只有注释的块。
    """
    event_name = "message"
    data_lines = []
    for raw_line in block.split("\n"):
        line = raw_line.rstrip("\r")
        if not line or line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())
        # 其他字段（id、retry）忽略
    if not data_lines:
        return None
    data_raw = "\n".join(data_lines)
    try:
        data_obj: Any = json.loads(data_raw)
    except json.JSONDecodeError:
        data_obj = data_raw
    return {"event": event_name, "data": data_obj}


async def stream_task_events(
    task_id: str,
    cancel_check=None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """订阅 api-server 的 SSE 流，按出现顺序产出 {"event", "data"} 字典。

    api-server 端会从 Redis Stream 起点回放历史事件，然后阻塞等待新事件，
    因此该函数同时承担"刷新重连 + 实时订阅"两种角色。

    Args:
        task_id: 任务 ID
        cancel_check: 可选，async () -> bool；为 True 时停止订阅
    """
    url = f"{get_api_base_url()}/v1/research/{task_id}/stream"
    headers = _build_headers()
    headers["Accept"] = "text/event-stream"
    headers["Cache-Control"] = "no-cache"

    timeout = aiohttp.ClientTimeout(
        total=None,
        sock_connect=DEFAULT_TIMEOUT_SECONDS,
        sock_read=DEFAULT_SSE_READ_TIMEOUT_SECONDS,
    )

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 404:
                raise TaskNotFoundError(f"task_id={task_id} 不存在")
            if resp.status >= 400:
                body = await resp.text()
                raise ApiClientError(
                    f"stream 失败 status={resp.status} body={body[:300]}"
                )

            buffer = ""
            async for chunk in resp.content.iter_any():
                if cancel_check is not None and await cancel_check():
                    logger.info("stream_task_events cancelled by caller")
                    return
                if not chunk:
                    continue
                buffer += chunk.decode("utf-8", errors="replace")
                # SSE 块以空行（\n\n）分隔；兼容 \r\n\r\n
                while True:
                    matched_idx = -1
                    matched_len = 0
                    for sep in ("\r\n\r\n", "\n\n"):
                        idx = buffer.find(sep)
                        if idx != -1 and (matched_idx == -1 or idx < matched_idx):
                            matched_idx = idx
                            matched_len = len(sep)
                    if matched_idx == -1:
                        break
                    block = buffer[:matched_idx]
                    buffer = buffer[matched_idx + matched_len :]
                    parsed = _parse_sse_block(block)
                    if parsed is None:
                        continue
                    yield parsed
                    # done 事件后立即结束
                    if parsed.get("event") == "done":
                        return


# ====== 兼容性辅助 ======


async def safe_create_task(
    query: str,
    mode: str,
    search_profile: str,
    search_result_num: int,
    verification_min_search_rounds: int,
    output_detail_level: str,
    caller_id: Optional[str] = None,
    retries: int = 1,
) -> Dict[str, Any]:
    """带轻量重试的任务创建。仅对网络/5xx 错误重试。"""
    attempt = 0
    last_exc: Optional[BaseException] = None
    while attempt <= retries:
        try:
            return await create_task(
                query=query,
                mode=mode,
                search_profile=search_profile,
                search_result_num=search_result_num,
                verification_min_search_rounds=verification_min_search_rounds,
                output_detail_level=output_detail_level,
                caller_id=caller_id,
            )
        except ApiClientError as exc:
            last_exc = exc
            if attempt >= retries:
                break
            await asyncio.sleep(0.5 * (attempt + 1))
            attempt += 1
    assert last_exc is not None
    raise last_exc
