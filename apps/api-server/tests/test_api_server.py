"""api-server 回归测试。

仅测试 API 层逻辑（认证、路由、模型校验），不依赖真实 pipeline。
"""

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# 确保 api-server 目录在 import 路径中
API_SERVER_DIR = Path(__file__).resolve().parents[1]
AGENT_DIR = API_SERVER_DIR.parent / "miroflow-agent"
if str(API_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(API_SERVER_DIR))
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


@pytest.fixture(scope="module")
def client():
    # 确保认证跳过（开发模式）
    os.environ.pop("API_TOKENS", None)
    from middleware.auth import _load_tokens
    # 重置 token 缓存
    import middleware.auth as auth_mod
    auth_mod._API_TOKENS = None

    from main import app
    return TestClient(app)


def test_health_endpoint(client):
    """GET /health 应返回 200 + status=ok。"""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data


def test_metrics_last_no_data(client):
    """GET /v1/metrics/last 无数据时应返回 no_data。"""
    resp = client.get("/v1/metrics/last")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "no_data"


def test_create_research_missing_query(client):
    """POST /v1/research 缺少 query 时应返回 422。"""
    resp = client.post("/v1/research", json={})
    assert resp.status_code == 422


def test_create_research_empty_query(client):
    """POST /v1/research query 为空字符串应返回 422。"""
    resp = client.post("/v1/research", json={"query": ""})
    assert resp.status_code == 422


def test_cancel_nonexistent_task(client):
    """POST /v1/research/{task_id}/cancel 不存在的任务应返回 404。"""
    resp = client.post("/v1/research/nonexistent-id/cancel")
    assert resp.status_code == 404


def test_stream_nonexistent_task(client):
    """GET /v1/research/{task_id}/stream 不存在的任务应返回 404。"""
    resp = client.get("/v1/research/nonexistent-id/stream")
    assert resp.status_code == 404


def test_cancel_by_caller_empty(client):
    """POST /v1/research/cancel 无运行任务时应返回空列表。"""
    resp = client.post("/v1/research/cancel")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cancelled"] == 0
    assert data["task_ids"] == []


class TestBearerAuth:
    """Bearer Token 认证测试。"""

    def test_auth_required_when_tokens_configured(self):
        """配置了 API_TOKENS 后，无 Token 请求应返回 401。"""
        os.environ["API_TOKENS"] = "test-token-123"
        import middleware.auth as auth_mod
        auth_mod._API_TOKENS = None  # 重置缓存

        from main import app
        test_client = TestClient(app)

        resp = test_client.get("/v1/metrics/last")
        assert resp.status_code == 401

        # 使用正确 Token 应通过
        resp = test_client.get(
            "/v1/metrics/last",
            headers={"Authorization": "Bearer test-token-123"},
        )
        assert resp.status_code == 200

        # 清理
        os.environ.pop("API_TOKENS", None)
        auth_mod._API_TOKENS = None

    def test_auth_skipped_when_no_tokens(self):
        """未配置 API_TOKENS 时，请求应直接通过。"""
        os.environ.pop("API_TOKENS", None)
        import middleware.auth as auth_mod
        auth_mod._API_TOKENS = None

        from main import app
        test_client = TestClient(app)

        resp = test_client.get("/health")
        assert resp.status_code == 200
