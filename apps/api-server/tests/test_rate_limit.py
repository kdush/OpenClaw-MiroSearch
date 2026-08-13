"""限流中间件回归测试。"""

import hashlib
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

API_SERVER_DIR = Path(__file__).resolve().parents[1]
AGENT_DIR = API_SERVER_DIR.parent / "miroflow-agent"
if str(API_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(API_SERVER_DIR))
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


@contextmanager
def _make_client(rate_limit_enabled: bool = True, rpm: int = 5):
    """构造带限流配置的 TestClient。"""
    import middleware.rate_limit as rl

    test_limiter = rl.SlidingWindowCounter(max_requests=rpm, window_seconds=60)
    task_store = AsyncMock()
    task_store.get_last_run_metrics = AsyncMock(return_value=None)
    task_store.get_task = AsyncMock(return_value=None)
    task_queue = AsyncMock()

    with (
        patch.object(rl, "RATE_LIMIT_ENABLED", rate_limit_enabled),
        patch.object(rl, "RATE_LIMIT_RPM", rpm),
        patch.object(rl, "_limiter", test_limiter),
        patch("main.get_task_store", AsyncMock(return_value=task_store)),
        patch("main.get_task_queue", AsyncMock(return_value=task_queue)),
        patch("main.close_task_store", AsyncMock()),
        patch("main.close_task_queue", AsyncMock()),
        patch("routers.metrics.get_task_store", AsyncMock(return_value=task_store)),
        patch("routers.research.get_task_store", AsyncMock(return_value=task_store)),
        patch("routers.research.get_task_queue", AsyncMock(return_value=task_queue)),
    ):
        from main import app

        with TestClient(app) as client:
            yield client


def test_rate_limit_allows_within_quota():
    """配额内的请求应正常通过。"""
    with _make_client(rpm=10) as client:
        for _ in range(10):
            resp = client.get("/health")
            assert resp.status_code == 200


def test_rate_limit_rejects_over_quota():
    """超出配额的请求应返回 429。"""
    with _make_client(rpm=3) as client:
        for _ in range(3):
            resp = client.get("/v1/metrics/last")
            assert resp.status_code == 200
        resp = client.get("/v1/metrics/last")
        assert resp.status_code == 429
        assert "Rate limit exceeded" in resp.json()["detail"]


def test_rate_limit_health_bypassed():
    """/health 路径应跳过限流。"""
    with _make_client(rpm=2) as client:
        for _ in range(2):
            client.get("/v1/metrics/last")
        assert client.get("/v1/metrics/last").status_code == 429
        assert client.get("/health").status_code == 200


def test_rate_limit_disabled():
    """RATE_LIMIT_ENABLED=false 时不限流。"""
    with _make_client(rate_limit_enabled=False, rpm=1) as client:
        for _ in range(10):
            resp = client.get("/v1/metrics/last")
            assert resp.status_code == 200


def test_make_client_restores_rate_limit_module_state():
    """临时测试配置退出后必须完整恢复，不能污染同一 worker 的后续用例。"""
    import middleware.rate_limit as rl

    original_enabled = rl.RATE_LIMIT_ENABLED
    original_rpm = rl.RATE_LIMIT_RPM
    original_limiter = rl._limiter

    with _make_client(
        rate_limit_enabled=not original_enabled,
        rpm=original_rpm + 17,
    ):
        assert rl.RATE_LIMIT_ENABLED is not original_enabled
        assert rl.RATE_LIMIT_RPM == original_rpm + 17
        assert rl._limiter is not original_limiter

    assert rl.RATE_LIMIT_ENABLED is original_enabled
    assert rl.RATE_LIMIT_RPM == original_rpm
    assert rl._limiter is original_limiter


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


def _fake_request(headers: dict, client_host: str = "10.0.0.1"):
    from types import SimpleNamespace

    return SimpleNamespace(
        headers={k.lower(): v for k, v in headers.items()},
        client=SimpleNamespace(host=client_host),
    )


def test_xff_ignored_by_default():
    """默认不信任 X-Forwarded-For：伪造该头不应改变限流 key（用真实对端 IP）。"""
    import middleware.rate_limit as rl

    prev = rl.TRUST_PROXY
    rl.TRUST_PROXY = False
    try:
        k1 = rl._extract_client_key(_fake_request({"X-Forwarded-For": "1.2.3.4"}))
        k2 = rl._extract_client_key(_fake_request({"X-Forwarded-For": "9.9.9.9"}))
        assert k1 == k2 == "ip:10.0.0.1"
    finally:
        rl.TRUST_PROXY = prev


def test_xff_trusted_uses_last_hop():
    """TRUST_PROXY=1 时按 XFF 最后一段（可信反代追加的最近一跳）。"""
    import middleware.rate_limit as rl

    prev = rl.TRUST_PROXY
    rl.TRUST_PROXY = True
    try:
        key = rl._extract_client_key(
            _fake_request({"X-Forwarded-For": "1.2.3.4, 5.6.7.8"})
        )
        assert key == "ip:5.6.7.8"
    finally:
        rl.TRUST_PROXY = prev


def test_token_key_uses_full_hash(monkeypatch):
    """限流 token 标识用全长哈希，前缀相同的不同 token 不应归并。"""
    import middleware.auth as auth_mod
    import middleware.rate_limit as rl

    token_a = "x" * 20 + "AAAA"
    token_b = "x" * 20 + "BBBB"
    monkeypatch.setenv("API_TOKENS", f"{token_a},{token_b}")
    auth_mod._API_TOKENS = None
    common = "Bearer " + "x" * 20
    k_a = rl._extract_client_key(_fake_request({"Authorization": common + "AAAA"}))
    k_b = rl._extract_client_key(_fake_request({"Authorization": common + "BBBB"}))

    assert k_a != k_b
    assert k_a == f"token:{hashlib.sha256(token_a.encode('utf-8')).hexdigest()}"
    assert k_b == f"token:{hashlib.sha256(token_b.encode('utf-8')).hexdigest()}"


def test_short_configured_bearer_uses_token_hash(monkeypatch):
    """合法短 Token 不应因请求头长度门槛而退化为共享 IP 限流。"""
    import middleware.auth as auth_mod
    import middleware.rate_limit as rl

    token = "a"
    monkeypatch.setenv("API_TOKENS", token)
    auth_mod._API_TOKENS = None

    key = rl._extract_client_key(_fake_request({"Authorization": f"Bearer {token}"}))

    assert key == f"token:{hashlib.sha256(token.encode('utf-8')).hexdigest()}"


def test_empty_bearer_uses_real_client_ip():
    """空 Bearer 候选不是有效 Token，必须回退真实对端 IP。"""
    import middleware.rate_limit as rl

    key = rl._extract_client_key(_fake_request({"Authorization": "Bearer    "}))

    assert key == "ip:10.0.0.1"


def test_invalid_bearer_uses_real_client_ip(monkeypatch):
    """无效 Bearer 不得创建独立限流桶，否则攻击者可换 Token 绕过 IP 限流。"""
    import middleware.auth as auth_mod
    import middleware.rate_limit as rl

    monkeypatch.setenv("API_TOKENS", "configured-valid-token")
    auth_mod._API_TOKENS = None

    first_key = rl._extract_client_key(
        _fake_request({"Authorization": "Bearer forged-token-a"})
    )
    second_key = rl._extract_client_key(
        _fake_request({"Authorization": "Bearer forged-token-b"})
    )

    assert first_key == second_key == "ip:10.0.0.1"


def test_bearer_uses_ip_when_auth_is_not_configured():
    """开发绕过模式下没有有效 Token 集合，Bearer 头仍按真实 IP 分桶。"""
    import middleware.rate_limit as rl

    key = rl._extract_client_key(
        _fake_request({"Authorization": "Bearer arbitrary-token"})
    )

    assert key == "ip:10.0.0.1"
