"""api-server 回归测试。

仅测试 API 层逻辑（认证、路由、模型校验），不依赖真实 pipeline。
"""

import os
import sys
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch
from typing import Optional

import pytest
from fastapi.testclient import TestClient

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
    assert "version" in data


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
    """POST /v1/research/cancel 无运行任务时应返回空列表。
    
    注意：此测试在新异步架构下需要 Valkey 连接，标记为跳过。
    完整的取消测试见 test_research_queue_api.py。
    """
    pytest.skip("取消测试需要 Valkey 连接，见 test_research_queue_api.py")


class TestBearerAuth:
    """Bearer Token 认证测试。"""

    def test_auth_required_when_tokens_configured(self):
        """配置了 API_TOKENS 后，无 Token 请求应返回 401。"""
        os.environ["API_TOKENS"] = "test-token-123"
        import middleware.auth as auth_mod
        auth_mod._API_TOKENS = None  # 重置缓存

        with build_test_client() as test_client:
            resp = test_client.get("/v1/metrics/last")
            assert resp.status_code == 401

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

        with build_test_client() as test_client:
            resp = test_client.get("/health")
            assert resp.status_code == 200
