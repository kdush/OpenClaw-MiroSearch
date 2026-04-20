"""TaskQueue: 任务入队服务。

统一封装 arq 连接池和 enqueue_job() 调用，API 层不直接操作 arq。

入队策略:
1. API 层生成 task_id
2. 先创建 queued 任务记录
3. 使用 _job_id=task_id 提交 run_research_job
4. task_id 与 arq job_id 保持一致，便于排查和取消
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from settings import settings

logger = logging.getLogger("api-server.task_queue")


@dataclass
class TaskPayload:
    """任务载荷。"""

    task_id: str
    query: str
    mode: str
    search_profile: str
    search_result_num: int
    verification_min_search_rounds: int
    output_detail_level: str
    caller_id: str
    cache_key: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典。"""
        return {
            "task_id": self.task_id,
            "query": self.query,
            "mode": self.mode,
            "search_profile": self.search_profile,
            "search_result_num": self.search_result_num,
            "verification_min_search_rounds": self.verification_min_search_rounds,
            "output_detail_level": self.output_detail_level,
            "caller_id": self.caller_id,
            "cache_key": self.cache_key,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskPayload":
        """从字典创建。"""
        return cls(
            task_id=data["task_id"],
            query=data["query"],
            mode=data["mode"],
            search_profile=data["search_profile"],
            search_result_num=data["search_result_num"],
            verification_min_search_rounds=data["verification_min_search_rounds"],
            output_detail_level=data["output_detail_level"],
            caller_id=data.get("caller_id", ""),
            cache_key=data.get("cache_key", ""),
        )


class TaskQueue:
    """任务队列服务。"""

    def __init__(self, arq_pool: ArqRedis):
        self._pool = arq_pool

    @classmethod
    async def create(cls) -> "TaskQueue":
        """工厂方法：创建 TaskQueue 实例。"""
        redis_settings = RedisSettings(
            host=settings.valkey.host,
            port=settings.valkey.port,
            password=settings.valkey.password,
            database=settings.valkey.queue_db,
        )
        pool = await create_pool(redis_settings, default_queue_name=settings.task_queue.queue_name)
        return cls(pool)

    async def close(self) -> None:
        """关闭连接池。"""
        await self._pool.close()

    async def enqueue_research_job(self, payload: TaskPayload) -> str:
        """入队研究任务。

        Args:
            payload: 任务载荷

        Returns:
            job_id（与 task_id 一致）
        """
        from workers.research_worker import run_research_job

        job = await self._pool.enqueue_job(
            "run_research_job",
            payload.to_dict(),
            _job_id=payload.task_id,  # 使用 task_id 作为 job_id
            _job_timeout=settings.worker.job_timeout_seconds,
        )

        if job is None:
            raise RuntimeError(f"Failed to enqueue job for task {payload.task_id}")

        logger.info("Enqueued job %s for task %s", job.job_id, payload.task_id)
        return job.job_id

    async def get_job_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        """获取任务状态。"""
        from arq.jobs import Job

        job = Job(job_id, self._pool)
        status = await job.status()
        result = await job.result()

        return {
            "job_id": job_id,
            "status": status.value if hasattr(status, "value") else str(status),
            "result": result,
        }

    async def abort_job(self, job_id: str) -> bool:
        """中止任务（注意：arq abort 是强制终止，不推荐使用）。"""
        from arq.jobs import Job

        job = Job(job_id, self._pool)
        await job.abort()
        return True


# 全局单例
_task_queue: Optional[TaskQueue] = None


async def get_task_queue() -> TaskQueue:
    """获取 TaskQueue 单例。"""
    global _task_queue

    if _task_queue is not None:
        return _task_queue

    _task_queue = await TaskQueue.create()
    return _task_queue


async def close_task_queue() -> None:
    """关闭 TaskQueue。"""
    global _task_queue

    if _task_queue is not None:
        await _task_queue.close()
        _task_queue = None
