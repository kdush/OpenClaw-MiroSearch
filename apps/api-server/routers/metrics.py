"""Metrics 端点。"""

from typing import Optional

from fastapi import APIRouter, Depends

from deps import get_last_run_metrics
from middleware.auth import verify_bearer_token

router = APIRouter(prefix="/v1/metrics", tags=["metrics"])


@router.get(
    "/last",
    summary="获取最近一次任务的结构化运行指标",
)
async def metrics_last(
    _token: Optional[str] = Depends(verify_bearer_token),
):
    data = get_last_run_metrics()
    if data is None:
        return {"status": "no_data", "message": "尚无已完成的任务"}
    return data
