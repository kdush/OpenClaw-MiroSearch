"""api-server 回归测试。

仅测试 API 层逻辑（认证、路由、模型校验），不依赖真实 pipeline。
"""

import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

# 确保 api-server 目录在 import 路径中
API_SERVER_DIR = Path(__file__).resolve().parents[1]
AGENT_DIR = API_SERVER_DIR.parent / "miroflow-agent"
if str(API_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(API_SERVER_DIR))
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


@contextmanager
def build_test_client(
    mock_task_store: Optional[AsyncMock] = None,
    mock_task_queue: Optional[AsyncMock] = None,
):
    task_store = mock_task_store or AsyncMock()
    if mock_task_store is None:
        task_store.get_last_run_metrics = AsyncMock(return_value=None)
        task_store.get_task = AsyncMock(return_value=None)

    task_queue = mock_task_queue or AsyncMock()

    import middleware.auth as auth_mod

    auth_mod._API_TOKENS = None

    with (
        patch("main.get_task_store", AsyncMock(return_value=task_store)),
        patch("main.get_task_queue", AsyncMock(return_value=task_queue)),
        patch("main.close_task_store", AsyncMock()),
        patch("main.close_task_queue", AsyncMock()),
        patch("routers.metrics.get_task_store", AsyncMock(return_value=task_store)),
        patch("routers.research.get_task_store", AsyncMock(return_value=task_store)),
        patch("routers.research.get_task_queue", AsyncMock(return_value=task_queue)),
    ):
        from main import app

        with TestClient(app) as test_client:
            yield test_client


@pytest.fixture
def client():
    with build_test_client() as test_client:
        yield test_client


def test_health_endpoint(client):
    """GET /health 应返回 200 + status=ok。"""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.2.0"


def test_default_api_host_is_loopback(monkeypatch):
    """未配置 API_HOST 时，本地启动默认只监听回环地址。"""
    monkeypatch.delenv("API_HOST", raising=False)
    from settings import Settings

    assert Settings().api_host == "127.0.0.1"


def test_negative_shared_result_cache_ttl_is_rejected():
    """负 TTL 不能静默退化成永久缓存。"""
    from settings import Settings

    with pytest.raises(ValidationError):
        Settings(RESULT_CACHE_TTL_SECONDS=-1)


def test_metrics_last_no_data(client):
    """GET /v1/metrics/last 无数据时应返回 no_data。"""
    resp = client.get("/v1/metrics/last")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "no_data"


def test_metrics_last_returns_persisted_data():
    """GET /v1/metrics/last 有数据时应返回持久化指标。"""
    task_store = AsyncMock()
    task_store.get_last_run_metrics = AsyncMock(
        return_value={"total_duration_ms": 12345}
    )
    task_store.get_task = AsyncMock(return_value=None)

    with build_test_client(mock_task_store=task_store) as test_client:
        resp = test_client.get("/v1/metrics/last")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total_duration_ms"] == 12345


def test_create_research_missing_query(client):
    """POST /v1/research 缺少 query 时应返回 422。"""
    resp = client.post("/v1/research", json={})
    assert resp.status_code == 422


def test_create_research_empty_query(client):
    """POST /v1/research query 为空字符串应返回 422。"""
    resp = client.post("/v1/research", json={"query": ""})
    assert resp.status_code == 422


def test_create_research_whitespace_query(client):
    """只包含空白的 query 也必须拒绝，不能进入缓存或任务队列。"""
    resp = client.post("/v1/research", json={"query": " \t\n "})
    assert resp.status_code == 422


def test_research_request_normalizes_surrounding_query_whitespace():
    """有效 query 应在缓存、落库和执行前统一去除首尾空白。"""
    from models import ResearchRequest

    assert ResearchRequest(query="  有效问题 \n").query == "有效问题"


def test_cancel_nonexistent_task(client):
    """POST /v1/research/{task_id}/cancel 不存在的任务应返回 404。"""
    resp = client.post("/v1/research/nonexistent-id/cancel")
    assert resp.status_code == 404


def test_stream_nonexistent_task(client):
    """GET /v1/research/{task_id}/stream 不存在的任务应返回 404。

    注意：此测试在新异步架构下需要 Valkey 连接，标记为跳过。
    完整的 SSE 测试见 test_research_queue_api.py。
    """
    pytest.skip("SSE 测试需要 Valkey 连接，见 test_research_queue_api.py")


def test_cancel_by_caller_empty(client):
    """缺少 caller_id 时应在访问 Valkey 前拒绝，不能退化为全局取消。"""
    resp = client.post("/v1/research/cancel")
    assert resp.status_code == 422


class TestBearerAuth:
    """Bearer Token 认证测试。"""

    def test_auth_required_when_tokens_configured(self, monkeypatch):
        """配置了 API_TOKENS 后，无 Token 请求应返回 401。"""
        monkeypatch.setenv("API_TOKENS", "test-token-123")
        monkeypatch.delenv("AUTH_DISABLED", raising=False)
        import middleware.auth as auth_mod

        auth_mod._API_TOKENS = None  # 重置缓存

        with build_test_client() as test_client:
            resp = test_client.get("/v1/metrics/last")
            assert resp.status_code == 401

            resp = test_client.get(
                "/v1/metrics/last",
                headers={"Authorization": "Bearer invalid-token"},
            )
            assert resp.status_code == 401

            resp = test_client.get(
                "/v1/metrics/last",
                headers={"Authorization": "Bearer test-token-123"},
            )
            assert resp.status_code == 200

    def test_non_ascii_token_not_matched_without_raising(self):
        """含非 ASCII 字符的伪造 Token 应判为不匹配（返回 False），而非 hmac.compare_digest 抛 TypeError。

        真实攻击者可用裸 socket 发送 latin-1 头字节，Starlette 以 latin-1 解码成
        非 ASCII str 传入 _is_allowed_token；此处直接单测该函数（HTTP 客户端会在
        传输层强制 ASCII，无法复现该路径）。
        """
        from middleware.auth import _is_allowed_token

        # 旧实现在此会抛 TypeError（str 含非 ASCII），修复后应稳定返回 False
        assert _is_allowed_token("héllo-非法-ÿ", {"real-token"}) is False
        # 正常匹配路径不受影响
        assert _is_allowed_token("real-token", {"real-token"}) is True

    def test_protected_endpoint_denied_when_no_tokens(self, monkeypatch):
        """未配置 API_TOKENS 且未显式禁用鉴权时，受保护端点应默认拒绝（503）。"""
        monkeypatch.delenv("API_TOKENS", raising=False)
        monkeypatch.delenv("AUTH_DISABLED", raising=False)
        import middleware.auth as auth_mod

        auth_mod._API_TOKENS = None

        with build_test_client() as test_client:
            # 公共端点不受影响
            assert test_client.get("/health").status_code == 200
            # 受保护端点默认拒绝
            assert test_client.get("/v1/metrics/last").status_code == 503

        auth_mod._API_TOKENS = None

    def test_auth_disabled_dev_mode_passes(self, monkeypatch):
        """显式 AUTH_DISABLED=1 时（开发模式），受保护端点放行。"""
        monkeypatch.delenv("API_TOKENS", raising=False)
        monkeypatch.setenv("AUTH_DISABLED", "1")
        import middleware.auth as auth_mod

        auth_mod._API_TOKENS = None

        with build_test_client() as test_client:
            assert test_client.get("/v1/metrics/last").status_code == 200
