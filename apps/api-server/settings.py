"""统一配置入口：队列、存储和 worker 相关环境变量。

所有 Redis/Valkey 配置通过此模块导出，避免散落在 router / worker / service 中。
"""

import os
from typing import Optional

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings

load_dotenv()


class ValkeySettings(BaseSettings):
    """Valkey/Redis 连接配置。"""

    host: str = Field(default="localhost", alias="VALKEY_HOST")
    port: int = Field(default=6379, alias="VALKEY_PORT")
    password: Optional[str] = Field(default=None, alias="VALKEY_PASSWORD")

    # 队列使用的 Redis DB
    queue_db: int = Field(default=1, alias="TASK_QUEUE_REDIS_DB")
    # 任务存储使用的 Redis DB
    store_db: int = Field(default=2, alias="TASK_STORE_REDIS_DB")

    @property
    def queue_url(self) -> str:
        """构建队列 Redis URL。"""
        if self.password:
            return f"redis://:{self.password}@{self.host}:{self.port}/{self.queue_db}"
        return f"redis://{self.host}:{self.port}/{self.queue_db}"

    @property
    def store_url(self) -> str:
        """构建存储 Redis URL。"""
        if self.password:
            return f"redis://:{self.password}@{self.host}:{self.port}/{self.store_db}"
        return f"redis://{self.host}:{self.port}/{self.store_db}"


class TaskQueueSettings(BaseSettings):
    """任务队列配置。"""

    # 队列名称
    queue_name: str = Field(default="miro:research:queue", alias="TASK_QUEUE_NAME")
    # 事件流最大长度
    event_stream_maxlen: int = Field(default=1000, alias="TASK_EVENT_STREAM_MAXLEN")
    # 任务结果 TTL（秒）
    result_ttl_seconds: int = Field(default=3600, alias="TASK_RESULT_TTL_SECONDS")
    # 任务元数据 TTL（秒）
    metadata_ttl_seconds: int = Field(default=7200, alias="TASK_METADATA_TTL_SECONDS")


class WorkerSettings(BaseSettings):
    """Worker 配置。"""

    # 单个任务超时（秒）
    job_timeout_seconds: int = Field(default=1800, alias="ARQ_JOB_TIMEOUT_SECONDS")
    # Worker 最大并发任务数
    max_jobs: int = Field(default=1, alias="ARQ_WORKER_MAX_JOBS")
    # 取消轮询间隔（秒）
    cancel_poll_interval_seconds: float = Field(
        default=0.5, alias="TASK_CANCEL_POLL_INTERVAL_SECONDS"
    )
    force_async_llm_client: bool = Field(
        default=True, alias="WORKER_FORCE_ASYNC_LLM_CLIENT"
    )


class Settings(BaseSettings):
    """应用总配置。"""

    valkey: ValkeySettings = Field(default_factory=ValkeySettings)
    task_queue: TaskQueueSettings = Field(default_factory=TaskQueueSettings)
    worker: WorkerSettings = Field(default_factory=WorkerSettings)

    # API 相关配置
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8090, alias="API_PORT")

    # 结果缓存配置
    result_cache_max_size: int = Field(default=128, alias="RESULT_CACHE_MAX_SIZE")
    result_cache_ttl_seconds: int = Field(default=3600, alias="RESULT_CACHE_TTL_SECONDS")

    # Agent 配置目录
    agent_conf_dir: str = Field(default="", alias="AGENT_CONF_DIR")

    # 日志目录
    log_dir: str = Field(default="logs/api-server", alias="LOG_DIR")


# 全局单例
settings = Settings()
