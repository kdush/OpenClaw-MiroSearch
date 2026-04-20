"""研究队列 API 测试。"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import json

from httpx import AsyncClient, ASGITransport

from main import app
from services.task_store import TaskStore, TaskStatus, TaskMeta


@pytest.fixture
def mock_task_store():
    """Mock TaskStore。"""
    store = AsyncMock(spec=TaskStore)
    return store


@pytest.fixture
def mock_task_queue():
    """Mock TaskQueue。"""
    queue = AsyncMock()
    queue.enqueue_research_job = AsyncMock(return_value="test-job-id")
    return queue


@pytest.mark.asyncio
async def test_create_research_queues_task(mock_task_store, mock_task_queue):
    """测试 POST /v1/research 入队任务。"""
    with patch("routers.research.get_task_store", return_value=mock_task_store), \
         patch("routers.research.get_task_queue", return_value=mock_task_queue):

        mock_task_store.create_task = AsyncMock()
        mock_task_store.get_task = AsyncMock(return_value=None)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/v1/research",
                json={
                    "query": "test query",
                    "mode": "balanced",
                    "search_profile": "parallel-trusted",
                }
            )

        assert response.status_code == 200
        data = response.json()
        assert "task_id" in data
        assert data["status"] == "accepted"

        mock_task_store.create_task.assert_called_once()
        mock_task_queue.enqueue_research_job.assert_called_once()


@pytest.mark.asyncio
async def test_create_research_cache_hit(mock_task_store, mock_task_queue):
    """测试 POST /v1/research 缓存命中。"""
    with patch("routers.research.get_task_store", return_value=mock_task_store), \
         patch("routers.research.get_task_queue", return_value=mock_task_queue), \
         patch("routers.research._result_cache") as mock_cache:

        mock_cache.get = MagicMock(return_value="# Cached Result")
        mock_cache.make_key = MagicMock(return_value="cache-key")
        mock_task_store.create_task = AsyncMock()
        mock_task_store.append_event = AsyncMock()
        mock_task_store.store_result = AsyncMock()
        mock_task_store.update_task_status = AsyncMock()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/v1/research",
                json={
                    "query": "test query",
                    "mode": "balanced",
                    "search_profile": "parallel-trusted",
                }
            )

        assert response.status_code == 200
        data = response.json()
        assert "task_id" in data
        assert data["status"] == "cached"

        mock_task_queue.enqueue_research_job.assert_not_called()


@pytest.mark.asyncio
async def test_get_task_status(mock_task_store):
    """测试 GET /v1/research/{task_id}。"""
    meta = TaskMeta(
        task_id="test-task-001",
        status=TaskStatus.RUNNING,
        caller_id="caller-001",
        query="test query",
        mode="balanced",
        search_profile="parallel-trusted",
        search_result_num=20,
        verification_min_search_rounds=3,
        output_detail_level="detailed",
    )

    with patch("routers.research.get_task_store", return_value=mock_task_store):
        mock_task_store.get_task = AsyncMock(return_value=meta)
        mock_task_store.get_result = AsyncMock(return_value=None)
        mock_task_store.get_event_stream_length = AsyncMock(return_value=5)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/v1/research/test-task-001")

        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == "test-task-001"
        assert data["status"] == "running"
        assert data["meta"]["query"] == "test query"
        assert data["event_count"] == 5


@pytest.mark.asyncio
async def test_get_task_status_not_found(mock_task_store):
    """测试 GET /v1/research/{task_id} 任务不存在。"""
    with patch("routers.research.get_task_store", return_value=mock_task_store):
        mock_task_store.get_task = AsyncMock(return_value=None)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/v1/research/nonexistent")

        assert response.status_code == 404


@pytest.mark.asyncio
async def test_cancel_task(mock_task_store):
    """测试 POST /v1/research/{task_id}/cancel。"""
    meta = TaskMeta(
        task_id="test-task-002",
        status=TaskStatus.RUNNING,
    )

    with patch("routers.research.get_task_store", return_value=mock_task_store):
        mock_task_store.get_task = AsyncMock(return_value=meta)
        mock_task_store.request_cancel = AsyncMock()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/v1/research/test-task-002/cancel")

        assert response.status_code == 200
        data = response.json()
        assert data["cancelled"] == 1
        assert "test-task-002" in data["task_ids"]

        mock_task_store.request_cancel.assert_called_once_with("test-task-002")


@pytest.mark.asyncio
async def test_cancel_task_not_cancellable(mock_task_store):
    """测试取消已完成任务返回 400。"""
    meta = TaskMeta(
        task_id="test-task-003",
        status=TaskStatus.COMPLETED,
    )

    with patch("routers.research.get_task_store", return_value=mock_task_store):
        mock_task_store.get_task = AsyncMock(return_value=meta)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/v1/research/test-task-003/cancel")

        assert response.status_code == 400


@pytest.mark.asyncio
async def test_cancel_by_caller(mock_task_store):
    """测试 POST /v1/research/cancel。"""
    with patch("routers.research.get_task_store", return_value=mock_task_store):
        mock_task_store.cancel_tasks_by_caller = AsyncMock(
            return_value=["task-a", "task-b"]
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/v1/research/cancel",
                params={"caller_id": "caller-001"}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["cancelled"] == 2
        assert len(data["task_ids"]) == 2
