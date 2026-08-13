"""Bearer Token 认证中间件。"""

import hmac
import os
from typing import Optional

from fastapi import HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer_scheme = HTTPBearer(auto_error=False)

# 从环境变量读取允许的 API Token（逗号分隔支持多 Token）
_API_TOKENS: Optional[set[str]] = None


def _load_tokens() -> set[str]:
    global _API_TOKENS
    if _API_TOKENS is not None:
        return _API_TOKENS
    raw = os.getenv("API_TOKENS", "").strip()
    if not raw:
        _API_TOKENS = set()
        return _API_TOKENS
    _API_TOKENS = {t.strip() for t in raw.split(",") if t.strip()}
    return _API_TOKENS


def _auth_disabled() -> bool:
    """仅当显式设置 AUTH_DISABLED=1/true/yes 时，才允许无 Token 放行（开发模式）。"""
    return os.getenv("AUTH_DISABLED", "").strip().lower() in ("1", "true", "yes")


def _is_allowed_token(candidate: str, allowed: set[str]) -> bool:
    """常量时间比较，避免短路带来的时序侧信道。

    统一编码为 UTF-8 bytes 再比较：hmac.compare_digest 对 str 要求纯 ASCII，
    含非 ASCII 字符的伪造 token 会抛 TypeError 冒泡成 500；转 bytes 无此限制，
    可让非法 token 稳定落到 401，同时保留常量时间比较特性。
    """
    candidate_bytes = candidate.encode("utf-8")
    matched = False
    for token in allowed:
        # 对所有候选都执行 compare_digest，不因提前命中而短路
        if hmac.compare_digest(candidate_bytes, token.encode("utf-8")):
            matched = True
    return matched


def is_configured_token(candidate: str) -> bool:
    """判断候选 Token 是否属于当前配置的允许集合。"""
    allowed = _load_tokens()
    return bool(allowed) and _is_allowed_token(candidate, allowed)


async def verify_bearer_token(request: Request) -> Optional[str]:
    """验证 Bearer Token（默认拒绝）。

    - 未配置 API_TOKENS 时：默认拒绝所有请求；仅在显式 AUTH_DISABLED=1
      的开发模式下才放行。避免"忘记配置即对外裸奔"。
    - 已配置时：要求请求携带有效的 Bearer Token（常量时间比较）。
    """
    allowed = _load_tokens()
    if not allowed:
        if _auth_disabled():
            return None
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "API authentication is not configured. Set API_TOKENS, "
                "or set AUTH_DISABLED=1 to explicitly run without auth (dev only)."
            ),
        )

    credentials: Optional[HTTPAuthorizationCredentials] = await _bearer_scheme(request)
    if credentials is None or not _is_allowed_token(credentials.credentials, allowed):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials
