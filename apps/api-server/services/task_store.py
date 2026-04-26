"""TaskStore: 持久化任务存储层。

用 Valkey 统一保存任务元数据、事件流、最终结果和取消标志，
替代 deps.py 中的 _TASKS 内存表。

Redis 键设计:
- miro:task:{task_id} — Hash，保存任务元数据与状态快照
- miro:task:{task_id}:events — Stream，保存流式事件
- miro:task:{task_id}:result — String，保存最终 markdown
- miro:caller:{caller_id}:tasks — Set，保存 caller 关联任务
- miro:metrics:last — String，保存最近一次 run_metrics
"""

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import redis.asyncio as redis

from settings import settings

logger = logging.getLogger("api-server.task_store")


def _parse_optional_float(value: Optional[str]) -> Optional[float]:
    if value in (None, "", "None"):
        return None
    return float(value)


def _parse_optional_str(value: Optional[str]) -> Optional[str]:
    if value in (None, "", "None"):
        return None
    return value


class TaskStatus(str, Enum):
    """任务状态枚举。"""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CACHED = "cached"  # 缓存命中，直接返回


@dataclass
class TaskMeta:
    """任务元数据。"""

    task_id: str
    status: TaskStatus
    caller_id: str = ""
    query: str = ""
    mode: str = "balanced"
    search_profile: str = "parallel-trusted"
    search_result_num: int = 20
    verification_min_search_rounds: int = 3
    output_detail_level: str = "detailed"
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    current_stage: str = ""
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典。"""
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "caller_id": self.caller_id,
            "query": self.query,
            "mode": self.mode,
            "search_profile": self.search_profile,
            "search_result_num": str(self.search_result_num),
            "verification_min_search_rounds": str(self.verification_min_search_rounds),
            "output_detail_level": self.output_detail_level,
            "created_at": str(self.created_at),
            "started_at": "" if self.started_at is None else str(self.started_at),
            "finished_at": "" if self.finished_at is None else str(self.finished_at),
            "current_stage": self.current_stage,
            "error": "" if self.error is None else self.error,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskMeta":
        """从字典创建。"""
        return cls(
            task_id=data["task_id"],
            status=TaskStatus(data["status"]),
            caller_id=data.get("caller_id", ""),
            query=data.get("query", ""),
            mode=data.get("mode", "balanced"),
            search_profile=data.get("search_profile", "parallel-trusted"),
            search_result_num=int(data.get("search_result_num", 20)),
            verification_min_search_rounds=int(
                data.get("verification_min_search_rounds", 3)
            ),
            output_detail_level=data.get("output_detail_level", "detailed"),
            created_at=float(data.get("created_at", time.time())),
            started_at=_parse_optional_float(data.get("started_at")),
            finished_at=_parse_optional_float(data.get("finished_at")),
            current_stage=data.get("current_stage", ""),
            error=_parse_optional_str(data.get("error")),
        )


class TaskStore:
    """任务存储层。"""

    # Redis 键前缀
    KEY_PREFIX = "miro"
    KEY_TASK = f"{KEY_PREFIX}:task"
    KEY_EVENTS = f"{KEY_PREFIX}:task:events"
    KEY_RESULT = f"{KEY_PREFIX}:task:result"
    KEY_CALLER = f"{KEY_PREFIX}:caller"
    KEY_METRICS = f"{KEY_PREFIX}:metrics:last"

    def __init__(self, redis_client: redis.Redis):
        self._redis = redis_client
        self._event_stream_maxlen = settings.task_queue.event_stream_maxlen
        self._result_ttl = settings.task_queue.result_ttl_seconds
        self._metadata_ttl = settings.task_queue.metadata_ttl_seconds

    @classmethod
    async def create(cls) -> "TaskStore":
        """工厂方法：创建 TaskStore 实例。"""
        client = redis.from_url(
            settings.valkey.store_url,
            decode_responses=True,
        )
        return cls(client)

    async def close(self) -> None:
        """关闭 Redis 连接。"""
        await self._redis.aclose()

    # ---- 任务元数据 ----

    async def create_task(
        self,
        task_id: str,
        status: TaskStatus = TaskStatus.QUEUED,
        caller_id: str = "",
        query: str = "",
        mode: str = "balanced",
        search_profile: str = "parallel-trusted",
        search_result_num: int = 20,
        verification_min_search_rounds: int = 3,
        output_detail_level: str = "detailed",
    ) -> TaskMeta:
        """创建新任务记录。"""
        meta = TaskMeta(
            task_id=task_id,
            status=status,
            caller_id=caller_id,
            query=query,
            mode=mode,
            search_profile=search_profile,
            search_result_num=search_result_num,
            verification_min_search_rounds=verification_min_search_rounds,
            output_detail_level=output_detail_level,
        )

        key = f"{self.KEY_TASK}:{task_id}"
        await self._redis.hset(key, mapping=meta.to_dict())
        await self._redis.expire(key, self._metadata_ttl)

        # 关联到 caller
        if caller_id:
            caller_key = f"{self.KEY_CALLER}:{caller_id}:tasks"
            await self._redis.sadd(caller_key, task_id)
            await self._redis.expire(caller_key, self._metadata_ttl)

        logger.debug("Created task %s with status %s", task_id, status.value)
        return meta

    async def get_task(self, task_id: str) -> Optional[TaskMeta]:
        """获取任务元数据。"""
        key = f"{self.KEY_TASK}:{task_id}"
        data = await self._redis.hgetall(key)
        if not data:
            return None
        return TaskMeta.from_dict(data)

    async def _refresh_task_metadata_ttl(self, task_id: str) -> None:
        """刷新任务元数据 TTL，避免长任务运行时快照先过期。"""
        key = f"{self.KEY_TASK}:{task_id}"
        await self._redis.expire(key, self._metadata_ttl)

    async def update_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        error: Optional[str] = None,
    ) -> bool:
        """更新任务状态。"""
        key = f"{self.KEY_TASK}:{task_id}"
        updates: Dict[str, str] = {"status": status.value}

        if status == TaskStatus.RUNNING:
            updates["started_at"] = str(time.time())
        elif status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.CACHED,
        ):
            updates["finished_at"] = str(time.time())

        if error:
            updates["error"] = error

        result = await self._redis.hset(key, mapping=updates)
        await self._refresh_task_metadata_ttl(task_id)
        return result >= 0

    async def update_task_stage(self, task_id: str, stage: str) -> bool:
        """更新任务当前阶段。"""
        key = f"{self.KEY_TASK}:{task_id}"
        result = await self._redis.hset(key, "current_stage", stage)
        await self._refresh_task_metadata_ttl(task_id)
        return result >= 0

    # ---- 事件流 ----

    async def append_event(
        self,
        task_id: str,
        event_type: str,
        data: Dict[str, Any],
    ) -> str:
        """追加事件到事件流，返回事件 ID。"""
        key = f"{self.KEY_TASK}:{task_id}:events"
        event_data = {
            "event": event_type,
            "data": json.dumps(data, ensure_ascii=False),
            "ts": str(time.time()),
        }
        event_id = await self._redis.xadd(key, event_data, maxlen=self._event_stream_maxlen)
        # 设置事件流 TTL
        await self._redis.expire(key, self._metadata_ttl)
        await self._refresh_task_metadata_ttl(task_id)
        return event_id

    async def read_events(
        self,
        task_id: str,
        last_event_id: Optional[str] = None,
        block_ms: int = 5000,
        count: int = 100,
    ) -> List[Dict[str, Any]]:
        """读取事件流。

        Args:
            task_id: 任务 ID
            last_event_id: 上次读取的事件 ID，None 表示从头读取
            block_ms: 阻塞等待时间（毫秒）
            count: 单次读取最大数量

        Returns:
            事件列表，每个事件包含 id, event, data, ts
        """
        key = f"{self.KEY_TASK}:{task_id}:events"

        if last_event_id:
            # 从上次位置之后读取
            results = await self._redis.xread(
                {key: last_event_id},
                block=block_ms,
                count=count,
            )
        else:
            # 从头读取
            results = await self._redis.xread(
                {key: "0"},
                block=block_ms,
                count=count,
            )

        if not results:
            return []

        events = []
        for stream_name, stream_events in results:
            for event_id, event_data in stream_events:
                events.append({
                    "id": event_id,
                    "event": event_data.get("event", "message"),
                    "data": json.loads(event_data.get("data", "{}")),
                    "ts": float(event_data.get("ts", 0)),
                })
        return events

    async def get_event_stream_length(self, task_id: str) -> int:
        """获取事件流长度。"""
        key = f"{self.KEY_TASK}:{task_id}:events"
        return await self._redis.xlen(key)

    # ---- 结果存储 ----

    async def store_result(self, task_id: str, result_markdown: str) -> bool:
        """存储最终结果。"""
        key = f"{self.KEY_TASK}:{task_id}:result"
        await self._redis.set(key, result_markdown, ex=self._result_ttl)
        return True

    async def get_result(self, task_id: str) -> Optional[str]:
        """获取最终结果。"""
        key = f"{self.KEY_TASK}:{task_id}:result"
        return await self._redis.get(key)

    # ---- 取消机制 ----

    async def request_cancel(self, task_id: str) -> bool:
        """请求取消任务。"""
        key = f"{self.KEY_TASK}:{task_id}"
        # 设置取消标志
        await self._redis.hset(key, "cancel_requested", "1")
        return True

    async def is_cancel_requested(self, task_id: str) -> bool:
        """检查是否请求取消。"""
        key = f"{self.KEY_TASK}:{task_id}"
        value = await self._redis.hget(key, "cancel_requested")
        return value == "1"

    async def cancel_tasks_by_caller(self, caller_id: Optional[str] = None) -> List[str]:
        """按 caller_id 批量取消任务。"""
        cancelled = []

        if caller_id:
            # 取消指定 caller 的任务
            caller_key = f"{self.KEY_CALLER}:{caller_id}:tasks"
            task_ids = await self._redis.smembers(caller_key)
        else:
            # 取消所有运行中的任务
            pattern = f"{self.KEY_TASK}:*"
            task_ids = []
            async for key in self._redis.scan_iter(match=pattern):
                task_id = key.split(":")[-1]
                meta = await self.get_task(task_id)
                if meta and meta.status == TaskStatus.RUNNING:
                    task_ids.append(task_id)

        for task_id in task_ids:
            meta = await self.get_task(task_id)
            if meta and meta.status == TaskStatus.RUNNING:
                await self.request_cancel(task_id)
                cancelled.append(task_id)

        return cancelled

    # ---- 运行指标 ----

    async def set_last_run_metrics(self, metrics: Dict[str, Any]) -> None:
        """保存最近一次任务的 run_metrics。"""
        await self._redis.set(
            self.KEY_METRICS,
            json.dumps(metrics, ensure_ascii=False),
            ex=self._metadata_ttl,
        )

    async def get_last_run_metrics(self) -> Optional[Dict[str, Any]]:
        """获取最近一次任务的 run_metrics。"""
        data = await self._redis.get(self.KEY_METRICS)
        if data:
            return json.loads(data)
        return None

    # ---- 清理 ----

    async def delete_task(self, task_id: str) -> bool:
        """删除任务相关所有数据。"""
        keys = [
            f"{self.KEY_TASK}:{task_id}",
            f"{self.KEY_TASK}:{task_id}:events",
            f"{self.KEY_TASK}:{task_id}:result",
        ]
        await self._redis.delete(*keys)
        return True

_task_store: Optional[TaskStore] = None

async def get_task_store() -> TaskStore:
    """获取 TaskStore 单例。"""
    global _task_store

    if _task_store is not None:
        return _task_store

    _task_store = await TaskStore.create()
    return _task_store

async def close_task_store() -> None:
    """关闭 TaskStore 单例。"""
    global _task_store

    if _task_store is not None:
        await _task_store.close()
        _task_store = None
