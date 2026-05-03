"""API 请求/响应 Pydantic 数据模型。"""

from typing import Optional

from pydantic import BaseModel, Field, field_validator


VALID_MODES = ("balanced", "verified", "research", "production-web", "quota", "thinking")
VALID_SEARCH_PROFILES = (
    "searxng-first", "serp-first", "multi-route",
    "parallel", "parallel-trusted", "searxng-only",
)
VALID_OUTPUT_DETAIL_LEVELS = ("compact", "balanced", "detailed")


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

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        normalized = v.strip().lower()
        if normalized not in VALID_MODES:
            raise ValueError(f"mode must be one of {VALID_MODES}, got '{v}'")
        return normalized

    @field_validator("search_profile")
    @classmethod
    def validate_search_profile(cls, v: str) -> str:
        normalized = v.strip().lower()
        if normalized not in VALID_SEARCH_PROFILES:
            raise ValueError(f"search_profile must be one of {VALID_SEARCH_PROFILES}, got '{v}'")
        return normalized

    @field_validator("output_detail_level")
    @classmethod
    def validate_output_detail_level(cls, v: str) -> str:
        normalized = v.strip().lower()
        if normalized not in VALID_OUTPUT_DETAIL_LEVELS:
            raise ValueError(f"output_detail_level must be one of {VALID_OUTPUT_DETAIL_LEVELS}, got '{v}'")
        return normalized


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


# ---- 新增任务状态模型 ----


class ResearchTaskMeta(BaseModel):
    """任务元数据。"""

    task_id: str
    status: str
    caller_id: str = ""
    query: str = ""
    mode: str = "balanced"
    search_profile: str = "parallel-trusted"
    search_result_num: int = 20
    verification_min_search_rounds: int = 3
    output_detail_level: str = "detailed"
    created_at: float = 0.0
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    current_stage: str = ""
    error: Optional[str] = None


class ResearchTaskStatusResponse(BaseModel):
    """GET /v1/research/{task_id} 响应。"""

    task_id: str
    status: str
    meta: ResearchTaskMeta
    result: Optional[str] = None
    event_count: int = 0
    result_quality: "ResultQuality" = Field(default_factory=lambda: ResultQuality())


class ResultQuality(BaseModel):
    """最终答案的格式和质量信息。"""

    format_valid: bool = True
    fallback_used: bool = False
    issues: list[str] = Field(default_factory=list)


class ResearchTaskProgress(BaseModel):
    """任务进度信息。"""

    task_id: str
    status: str
    current_stage: str = ""
    started_at: Optional[float] = None
    elapsed_seconds: float = 0.0
