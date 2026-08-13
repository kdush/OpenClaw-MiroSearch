"""API 测试的公共环境隔离。"""

import sys
from pathlib import Path

import pytest

API_SERVER_DIR = Path(__file__).resolve().parents[1]
AGENT_DIR = API_SERVER_DIR.parent / "miroflow-agent"
for import_path in (API_SERVER_DIR, AGENT_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))


@pytest.fixture(autouse=True)
def _isolate_api_environment(monkeypatch):
    """隔离每个 API 用例的认证缓存、环境变量和限流模块状态。

    鉴权专项测试可在用例内覆盖这两个环境变量；``monkeypatch`` 会在用例
    结束后恢复调用 pytest 前的真实环境。限流配置和计数桶也在用例结束后
    恢复，避免测试顺序和宿主机配置互相影响。
    """
    import middleware.auth as auth_mod
    import middleware.rate_limit as rate_limit_mod

    monkeypatch.delenv("API_TOKENS", raising=False)
    monkeypatch.setenv("AUTH_DISABLED", "1")
    auth_mod._API_TOKENS = None

    original_rate_limit_enabled = rate_limit_mod.RATE_LIMIT_ENABLED
    original_rate_limit_rpm = rate_limit_mod.RATE_LIMIT_RPM
    original_trust_proxy = rate_limit_mod.TRUST_PROXY
    original_limiter = rate_limit_mod._limiter
    rate_limit_mod._limiter = rate_limit_mod.SlidingWindowCounter(
        max_requests=rate_limit_mod.RATE_LIMIT_RPM,
        window_seconds=60,
    )

    try:
        yield
    finally:
        auth_mod._API_TOKENS = None
        rate_limit_mod.RATE_LIMIT_ENABLED = original_rate_limit_enabled
        rate_limit_mod.RATE_LIMIT_RPM = original_rate_limit_rpm
        rate_limit_mod.TRUST_PROXY = original_trust_proxy
        rate_limit_mod._limiter = original_limiter
