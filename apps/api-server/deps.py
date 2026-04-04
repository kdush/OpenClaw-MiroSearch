"""依赖注入：pipeline 组件预加载与任务状态管理。"""

import asyncio
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# ---- 任务状态管理 ----

_TASKS: Dict[str, Dict[str, Any]] = {}  # {task_id: {caller_id, cancel_event, queue, status, result}}
_TASKS_LOCK = threading.Lock()

# 最近一次完成任务的 run_metrics
_last_run_metrics: Optional[dict] = None
_last_run_metrics_lock = threading.Lock()


def register_task(task_id: str, caller_id: str = "") -> asyncio.Queue:
    """注册一个新任务，返回事件队列。"""
    queue: asyncio.Queue = asyncio.Queue()
    cancel_event = threading.Event()
    with _TASKS_LOCK:
        _TASKS[task_id] = {
            "caller_id": caller_id,
            "cancel_event": cancel_event,
            "queue": queue,
            "status": "running",
            "result": None,
            "created_at": time.time(),
        }
    return queue


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    """获取任务信息。"""
    with _TASKS_LOCK:
        return _TASKS.get(task_id)


def cancel_task(task_id: str) -> bool:
    """取消指定任务。"""
    with _TASKS_LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return False
        task["cancel_event"].set()
        return True


def cancel_tasks_by_caller(caller_id: Optional[str] = None) -> List[str]:
    """按 caller_id 取消任务，返回被取消的任务 ID 列表。"""
    cancelled = []
    with _TASKS_LOCK:
        for tid, task in _TASKS.items():
            if task["status"] != "running":
                continue
            if caller_id is None or task["caller_id"] == caller_id:
                task["cancel_event"].set()
                cancelled.append(tid)
    return cancelled


def finish_task(task_id: str, status: str, result: Optional[str] = None) -> None:
    """标记任务完成。"""
    with _TASKS_LOCK:
        task = _TASKS.get(task_id)
        if task:
            task["status"] = status
            task["result"] = result
            task["finished_at"] = time.time()


_TASK_RETAIN_SECONDS = 300  # 已完成任务保留 5 分钟供查询


def cleanup_stale_tasks() -> int:
    """清理已完成且超过保留期的任务，返回清除数量。"""
    now = time.time()
    removed = 0
    with _TASKS_LOCK:
        stale_ids = [
            tid for tid, t in _TASKS.items()
            if t["status"] != "running"
            and now - t.get("finished_at", t["created_at"]) > _TASK_RETAIN_SECONDS
        ]
        for tid in stale_ids:
            del _TASKS[tid]
            removed += 1
    return removed


def set_last_run_metrics(metrics: dict) -> None:
    """更新最近一次任务的 run_metrics。"""
    global _last_run_metrics
    with _last_run_metrics_lock:
        _last_run_metrics = metrics


def get_last_run_metrics() -> Optional[dict]:
    """获取最近一次任务的 run_metrics。"""
    with _last_run_metrics_lock:
        return _last_run_metrics
