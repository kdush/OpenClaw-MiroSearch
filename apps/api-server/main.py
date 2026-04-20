"""FastAPI API Server 入口。

独立于 Gradio Demo 的标准 HTTP API 层，提供：
- POST /v1/research — 提交研究任务
- GET  /v1/research/{task_id} — 获取任务状态
- GET  /v1/research/{task_id}/stream — SSE 流式进度
- POST /v1/research/{task_id}/cancel — 取消指定任务
- POST /v1/research/cancel — 按 caller_id 批量取消
- GET  /v1/metrics/last — 最近任务运行指标
- GET  /health — 健康检查
"""

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI

# 确保 miroflow-agent 的 src 在 import 路径中
_AGENT_ROOT = Path(__file__).resolve().parents[1] / "miroflow-agent"
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

load_dotenv()

from middleware.rate_limit import check_rate_limit, cleanup_rate_limit_buckets
from models import HealthResponse
from routers import metrics, research
from services.task_queue import close_task_queue, get_task_queue
from services.task_store import close_task_store, get_task_store

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s][%(name)s][%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("api-server")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """应用生命周期：初始化/关闭 TaskStore 和 TaskQueue。"""
    import asyncio

    # 启动时初始化
    logger.info("Initializing TaskStore and TaskQueue...")
    await get_task_store()
    await get_task_queue()
    logger.info("TaskStore initialized")

    async def _cleanup_loop():
        while True:
            await asyncio.sleep(60)
            cleanup_rate_limit_buckets()

    task = asyncio.create_task(_cleanup_loop())

    yield

    # 关闭时清理
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    await close_task_store()
    await close_task_queue()
    logger.info("TaskStore and TaskQueue closed")


app = FastAPI(
    title="MiroSearch API",
    description="OpenClaw-MiroSearch 标准 HTTP API，独立于 Gradio Demo",
    version="0.2.0",
    docs_url="/docs",
    redoc_url="/redoc",
    dependencies=[Depends(check_rate_limit)],
    lifespan=_lifespan,
)

app.include_router(research.router)
app.include_router(metrics.router)


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health_check():
    return HealthResponse()


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("API_HOST", "0.0.0.0")
    port = int(os.getenv("API_PORT", "8090"))
    uvicorn.run("main:app", host=host, port=port, reload=False, log_level="info")
