"""API 请求/响应 Pydantic 数据模型。"""

from typing import Optional

from pydantic import BaseModel, Field


class ResearchRequest(BaseModel):
    """POST /v1/research 请求体。"""

    query: str = Field(..., min_length=1, description="研究查询内容")
    mode: str = Field(default="balanced", description="研究模式")
    search_profile: str = Field(default="parallel-trusted", description="检索路由策略")
    search_result_num: int = Field(default=20, ge=1, le=100, description="检索结果数量")
    verification_min_search_rounds: int = Field(
        default=3, ge=0, le=20, description="最小检索轮次（仅 verified 模式生效）"
    )
    output_detail_level: str = Field(default="detailed", description="输出篇幅档位")
    caller_id: Optional[str] = Field(default=None, description="调用方 ID，用于定向取消")


class ResearchResponse(BaseModel):
    """POST /v1/research 同步响应。"""

    task_id: str
    status: str = "accepted"


class ResearchResult(BaseModel):
    """任务完成后的结果。"""

    task_id: str
    status: str
    result: Optional[str] = None
    error: Optional[str] = None


class CancelResponse(BaseModel):
    """POST /v1/research/{task_id}/cancel 响应。"""

    cancelled: int
    task_ids: list[str]


class HealthResponse(BaseModel):
    """GET /health 响应。"""

    status: str = "ok"
    version: str = "0.1.0"


class ErrorResponse(BaseModel):
    """通用错误响应。"""

    detail: str
