"""限流中间件回归测试。"""

import os
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

API_SERVER_DIR = Path(__file__).resolve().parents[1]
AGENT_DIR = API_SERVER_DIR.parent / "miroflow-agent"
if str(API_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(API_SERVER_DIR))
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


@pytest.fixture(autouse=True)
def _reset_rate_limit_state():
    """每个测试前重置限流器状态和环境变量。"""
    import middleware.rate_limit as rl
    import middleware.auth as auth_mod
    os.environ.pop("API_TOKENS", None)
    auth_mod._API_TOKENS = None
    rl._limiter = rl.SlidingWindowCounter(max_requests=rl.RATE_LIMIT_RPM, window_seconds=60)
    yield


def _make_client(rate_limit_enabled: bool = True, rpm: int = 5):
    """构造带限流配置的 TestClient。"""
    import middleware.rate_limit as rl
    rl.RATE_LIMIT_ENABLED = rate_limit_enabled
    rl.RATE_LIMIT_RPM = rpm
    rl._limiter = rl.SlidingWindowCounter(max_requests=rpm, window_seconds=60)
    from main import app
    return TestClient(app)


def test_rate_limit_allows_within_quota():
    """配额内的请求应正常通过。"""
    client = _make_client(rpm=10)
    for _ in range(10):
        resp = client.get("/health")
        assert resp.status_code == 200


def test_rate_limit_rejects_over_quota():
    """超出配额的请求应返回 429。"""
    client = _make_client(rpm=3)
    for _ in range(3):
        resp = client.get("/v1/metrics/last")
        assert resp.status_code == 200
    resp = client.get("/v1/metrics/last")
    assert resp.status_code == 429
    assert "Rate limit exceeded" in resp.json()["detail"]


def test_rate_limit_health_bypassed():
    """/health 路径应跳过限流。"""
    client = _make_client(rpm=2)
    # 先耗尽配额
    for _ in range(2):
        client.get("/v1/metrics/last")
    # /v1/metrics/last 应被拒绝
    assert client.get("/v1/metrics/last").status_code == 429
    # /health 仍然通过
    assert client.get("/health").status_code == 200


def test_rate_limit_disabled():
    """RATE_LIMIT_ENABLED=false 时不限流。"""
    client = _make_client(rate_limit_enabled=False, rpm=1)
    for _ in range(10):
        resp = client.get("/v1/metrics/last")
        assert resp.status_code == 200


def test_sliding_window_counter_basic():
    """SlidingWindowCounter 基本功能。"""
    from middleware.rate_limit import SlidingWindowCounter
    counter = SlidingWindowCounter(max_requests=3, window_seconds=60)
    assert counter.is_allowed("test") is True
    assert counter.is_allowed("test") is True
    assert counter.is_allowed("test") is True
    assert counter.is_allowed("test") is False
    assert counter.remaining("test") == 0


def test_sliding_window_counter_different_keys():
    """不同 key 应独立计数。"""
    from middleware.rate_limit import SlidingWindowCounter
    counter = SlidingWindowCounter(max_requests=2, window_seconds=60)
    assert counter.is_allowed("a") is True
    assert counter.is_allowed("a") is True
    assert counter.is_allowed("a") is False
    # key "b" 不受影响
    assert counter.is_allowed("b") is True
