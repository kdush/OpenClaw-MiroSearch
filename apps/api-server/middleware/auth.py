"""Bearer Token 认证中间件。"""

import os
from typing import Optional

from fastapi import HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer_scheme = HTTPBearer(auto_error=False)

# 从环境变量读取允许的 API Token（逗号分隔支持多 Token）
_API_TOKENS: Optional[set] = None


def _load_tokens() -> set:
    global _API_TOKENS
    if _API_TOKENS is not None:
        return _API_TOKENS
    raw = os.getenv("API_TOKENS", "").strip()
    if not raw:
        _API_TOKENS = set()
        return _API_TOKENS
    _API_TOKENS = {t.strip() for t in raw.split(",") if t.strip()}
    return _API_TOKENS


async def verify_bearer_token(request: Request) -> Optional[str]:
    """验证 Bearer Token。

    - 若未配置 API_TOKENS 环境变量，则跳过认证（开发模式）。
    - 若已配置，则要求请求携带有效的 Bearer Token。
    """
    allowed = _load_tokens()
    if not allowed:
        return None

    credentials: Optional[HTTPAuthorizationCredentials] = await _bearer_scheme(request)
    if credentials is None or credentials.credentials not in allowed:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials
