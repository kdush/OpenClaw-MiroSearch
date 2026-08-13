"""API 请求/响应 Pydantic 数据模型。"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from settings import settings


VALID_MODES = (
    "balanced",
    "verified",
    "research",
    "production-web",
    "quota",
    "thinking",
)
VALID_SEARCH_PROFILES = (
    "searxng-first",
    "serp-first",
    "multi-route",
    "parallel",
    "parallel-trusted",
    "searxng-only",
)
VALID_OUTPUT_DETAIL_LEVELS = ("compact", "balanced", "detailed")
VALID_SEARCH_RESULT_NUMS = (10, 20, 30)
MIN_VERIFICATION_SEARCH_ROUNDS = 1
MAX_VERIFICATION_SEARCH_ROUNDS = 8


class ResearchRequest(BaseModel):
    """POST /v1/research 请求体。

    可选策略字段省略或显式传入 ``null`` 都表示使用部署环境中的 ``DEFAULT_*``
    默认值；只要传入非空值，就必须通过下方的显式范围校验。
    """

    query: str = Field(..., min_length=1, description="研究查询内容")
    mode: Optional[str] = Field(default=None, description="研究模式")
    search_profile: Optional[str] = Field(default=None, description="检索路由策略")
    search_result_num: Optional[int] = Field(
        default=None,
        strict=True,
        description="检索结果数量",
    )
    verification_min_search_rounds: Optional[int] = Field(
        default=None,
        strict=True,
        ge=MIN_VERIFICATION_SEARCH_ROUNDS,
        le=MAX_VERIFICATION_SEARCH_ROUNDS,
        description="最小检索轮次（仅 verified 模式生效）",
    )
    output_detail_level: Optional[str] = Field(
        default=None,
        description="输出篇幅档位",
    )
    caller_id: Optional[str] = Field(
        default=None, description="调用方 ID，用于定向取消"
    )

    @field_validator("query")
    @classmethod
    def validate_query(cls, v: str) -> str:
        """统一清理首尾空白，并拒绝没有实际内容的查询。"""
        normalized = v.strip()
        if not normalized:
            raise ValueError("query must contain non-whitespace characters")
        return normalized

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        normalized = v.strip().lower()
        if normalized not in VALID_MODES:
            raise ValueError(f"mode must be one of {VALID_MODES}, got '{v}'")
        return normalized

    @field_validator("search_profile")
    @classmethod
    def validate_search_profile(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        normalized = v.strip().lower()
        if normalized not in VALID_SEARCH_PROFILES:
            raise ValueError(
                f"search_profile must be one of {VALID_SEARCH_PROFILES}, got '{v}'"
            )
        return normalized

    @field_validator("search_result_num")
    @classmethod
    def validate_search_result_num(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return None
        if v not in VALID_SEARCH_RESULT_NUMS:
            raise ValueError(
                "search_result_num must be one of "
                f"{VALID_SEARCH_RESULT_NUMS}, got '{v}'"
            )
        return v

    @field_validator("output_detail_level")
    @classmethod
    def validate_output_detail_level(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        normalized = v.strip().lower()
        if normalized not in VALID_OUTPUT_DETAIL_LEVELS:
            raise ValueError(
                f"output_detail_level must be one of {VALID_OUTPUT_DETAIL_LEVELS}, got '{v}'"
            )
        return normalized

    @field_validator("caller_id")
    @classmethod
    def validate_caller_id(cls, v: Optional[str]) -> Optional[str]:
        """与取消端使用相同规则；空白调用方按未提供处理。"""
        if v is None:
            return None
        normalized = v.strip()
        return normalized or None


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
    version: str = Field(default_factory=lambda: settings.api_version)


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

    model_config = ConfigDict(strict=True)

    format_valid: bool = False
    fallback_used: bool = False
    issues: list[str] = Field(default_factory=lambda: ["quality_unavailable"])
    answer_available: bool = False


class ResearchTaskProgress(BaseModel):
    """任务进度信息。"""

    task_id: str
    status: str
    current_stage: str = ""
    started_at: Optional[float] = None
    elapsed_seconds: float = 0.0
