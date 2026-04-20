"""Metrics 端点。"""

from typing import Optional

from fastapi import APIRouter, Depends

from middleware.auth import verify_bearer_token
from services.task_store import get_task_store

router = APIRouter(prefix="/v1/metrics", tags=["metrics"])


@router.get(
    "/last",
    summary="获取最近一次任务的结构化运行指标",
)
async def metrics_last(
    _token: Optional[str] = Depends(verify_bearer_token),
):
    task_store = await get_task_store()
    data = await task_store.get_last_run_metrics()
    if data is None:
        return {"status": "no_data", "message": "尚无已完成的任务"}
    return data
