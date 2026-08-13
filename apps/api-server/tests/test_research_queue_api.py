"""研究队列 API 测试。"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from main import app
from services.task_store import TaskMeta, TaskStatus, TaskStore


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
    with (
        patch("routers.research.get_task_store", return_value=mock_task_store),
        patch("routers.research.get_task_queue", return_value=mock_task_queue),
    ):
        mock_task_store.create_task = AsyncMock()
        mock_task_store.get_task = AsyncMock(return_value=None)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/research",
                json={
                    "query": "test query",
                    "mode": "balanced",
                    "search_profile": "parallel-trusted",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert "task_id" in data
        assert data["status"] == "accepted"

        mock_task_store.create_task.assert_called_once()
        mock_task_queue.enqueue_research_job.assert_called_once()


@pytest.mark.asyncio
async def test_enqueue_failure_marks_created_task_failed(
    mock_task_store,
    mock_task_queue,
):
    """入队失败不得遗留一个永远 queued 且无法恢复的任务。"""
    mock_task_store.get_cached_result = AsyncMock(return_value=None)
    mock_task_store.create_task = AsyncMock()
    mock_task_store.update_task_status = AsyncMock()
    mock_task_store.append_event = AsyncMock()
    mock_task_queue.enqueue_research_job.side_effect = RuntimeError("queue unavailable")

    with (
        patch("routers.research.get_task_store", return_value=mock_task_store),
        patch("routers.research.get_task_queue", return_value=mock_task_queue),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v1/research",
                json={"query": "enqueue failure"},
            )

    assert response.status_code == 503
    task_id = mock_task_store.create_task.await_args.kwargs["task_id"]
    mock_task_store.update_task_status.assert_awaited_once_with(
        task_id,
        TaskStatus.FAILED,
        error="Failed to enqueue research task: queue unavailable",
    )
    mock_task_store.append_event.assert_awaited_once_with(
        task_id,
        "error",
        {"error": "Failed to enqueue research task: queue unavailable"},
    )


@pytest.mark.asyncio
async def test_create_research_normalizes_caller_id(
    mock_task_store,
    mock_task_queue,
):
    """提交端与取消端必须使用相同的 caller_id 规范化规则。"""
    with (
        patch("routers.research.get_task_store", return_value=mock_task_store),
        patch("routers.research.get_task_queue", return_value=mock_task_queue),
    ):
        mock_task_store.get_cached_result = AsyncMock(return_value=None)
        mock_task_store.create_task = AsyncMock()

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v1/research",
                json={
                    "query": "caller normalization",
                    "caller_id": "  caller-001  ",
                },
            )

    assert response.status_code == 200
    assert mock_task_store.create_task.await_args.kwargs["caller_id"] == "caller-001"
    payload = mock_task_queue.enqueue_research_job.await_args.args[0]
    assert payload.caller_id == "caller-001"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mode", "invalid-mode"),
        ("search_profile", "invalid-profile"),
        ("output_detail_level", "verbose"),
        ("search_result_num", 25),
        ("search_result_num", 100),
        ("search_result_num", "20"),
        ("search_result_num", 20.0),
        ("search_result_num", True),
        ("verification_min_search_rounds", 0),
        ("verification_min_search_rounds", 20),
        ("verification_min_search_rounds", "3"),
        ("verification_min_search_rounds", 3.0),
        ("verification_min_search_rounds", True),
    ],
)
async def test_create_research_rejects_invalid_explicit_depth_values(
    mock_task_store,
    mock_task_queue,
    field,
    value,
):
    """显式非法深度参数必须由 Pydantic 返回 422，不能进入 resolver 归一化。"""
    with (
        patch("routers.research.get_task_store", return_value=mock_task_store),
        patch("routers.research.get_task_queue", return_value=mock_task_queue),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v1/research",
                json={"query": "invalid explicit parameter", field: value},
                headers={
                    "Authorization": (
                        f"Bearer invalid-{field}-{type(value).__name__}-{value}"
                    )
                },
            )

    assert response.status_code == 422
    mock_task_store.create_task.assert_not_awaited()
    mock_task_queue.enqueue_research_job.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("search_result_num", [10, 20, 30])
@pytest.mark.parametrize("verification_min_search_rounds", [1, 8])
async def test_create_research_accepts_supported_depth_values(
    mock_task_store,
    mock_task_queue,
    search_result_num,
    verification_min_search_rounds,
):
    """受支持的结果数和 verified 轮次边界必须可正常入队。"""
    with (
        patch("routers.research.get_task_store", return_value=mock_task_store),
        patch("routers.research.get_task_queue", return_value=mock_task_queue),
        patch(
            "routers.research.ResultCache.make_key",
            return_value="valid-depth-cache-key",
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v1/research",
                json={
                    "query": (
                        f"valid-{search_result_num}-"
                        f"{verification_min_search_rounds}"
                    ),
                    "mode": "verified",
                    "search_result_num": search_result_num,
                    "verification_min_search_rounds": (verification_min_search_rounds),
                },
            )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_create_research_uses_one_effective_parameter_set(
    mock_task_store,
    mock_task_queue,
    monkeypatch,
):
    """显式 null 应按省略处理，并统一用于缓存、元数据、队列载荷和 GET。"""
    monkeypatch.setenv("DEFAULT_RESEARCH_MODE", "verified")
    monkeypatch.setenv("DEFAULT_SEARCH_PROFILE", "multi-route")
    monkeypatch.setenv("DEFAULT_SEARCH_RESULT_NUM", "30")
    monkeypatch.setenv("DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS", "7")
    monkeypatch.setenv("DEFAULT_OUTPUT_DETAIL_LEVEL", "compact")

    with (
        patch("routers.research.get_task_store", return_value=mock_task_store),
        patch("routers.research.get_task_queue", return_value=mock_task_queue),
        patch(
            "routers.research.ResultCache.make_key",
            return_value="effective-cache-key",
        ) as mock_make_key,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v1/research",
                json={
                    "query": "deployment defaults",
                    "mode": None,
                    "search_profile": None,
                    "search_result_num": None,
                    "verification_min_search_rounds": None,
                    "output_detail_level": None,
                },
            )

            assert response.status_code == 200
            task_id = response.json()["task_id"]

            create_kwargs = mock_task_store.create_task.await_args.kwargs
            payload = mock_task_queue.enqueue_research_job.await_args.args[0]
            effective = {
                "mode": "verified",
                "search_profile": "multi-route",
                "search_result_num": 30,
                "verification_min_search_rounds": 7,
                "output_detail_level": "compact",
            }
            for name, expected in effective.items():
                assert create_kwargs[name] == expected
                assert getattr(payload, name) == expected

            mock_make_key.assert_called_once_with(
                "deployment defaults",
                "verified",
                "multi-route",
                "compact",
                search_result_num=30,
                verification_min_search_rounds=7,
            )

            mock_task_store.get_task.return_value = TaskMeta(
                task_id=task_id,
                status=TaskStatus.QUEUED,
                query="deployment defaults",
                **effective,
            )
            mock_task_store.get_result.return_value = None
            mock_task_store.get_event_stream_length.return_value = 0
            mock_task_store.get_result_quality.return_value = None
            status_response = await client.get(f"/v1/research/{task_id}")

    assert status_response.status_code == 200
    assert {
        name: status_response.json()["meta"][name] for name in effective
    } == effective


@pytest.mark.asyncio
async def test_non_verified_round_default_does_not_fragment_cache_key(
    mock_task_store,
    mock_task_queue,
    monkeypatch,
):
    """非 verified 仍保存有效默认轮次，但轮次不参与真实缓存键。"""
    monkeypatch.setenv("DEFAULT_SEARCH_PROFILE", "searxng-first")
    monkeypatch.setenv("DEFAULT_SEARCH_RESULT_NUM", "20")
    monkeypatch.setenv("DEFAULT_OUTPUT_DETAIL_LEVEL", "detailed")

    with (
        patch("routers.research.get_task_store", return_value=mock_task_store),
        patch("routers.research.get_task_queue", return_value=mock_task_queue),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            for default_rounds in (4, 7):
                monkeypatch.setenv(
                    "DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS",
                    str(default_rounds),
                )
                response = await client.post(
                    "/v1/research",
                    json={
                        "query": "balanced rounds",
                        "mode": "balanced",
                    },
                )
                assert response.status_code == 200

    create_calls = mock_task_store.create_task.await_args_list
    payloads = [
        call.args[0] for call in mock_task_queue.enqueue_research_job.await_args_list
    ]
    assert [call.kwargs["verification_min_search_rounds"] for call in create_calls] == [
        4,
        7,
    ]
    assert [payload.verification_min_search_rounds for payload in payloads] == [4, 7]
    assert payloads[0].cache_key == payloads[1].cache_key


@pytest.mark.asyncio
async def test_create_research_cache_hit(
    mock_task_store,
    mock_task_queue,
    monkeypatch,
):
    """测试 POST /v1/research 缓存命中。"""
    monkeypatch.setenv("DEFAULT_SEARCH_RESULT_NUM", "20")
    monkeypatch.setenv("DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS", "3")
    monkeypatch.setenv("DEFAULT_OUTPUT_DETAIL_LEVEL", "detailed")

    with (
        patch("routers.research.get_task_store", return_value=mock_task_store),
        patch("routers.research.get_task_queue", return_value=mock_task_queue),
        patch(
            "routers.research.ResultCache.make_key",
            return_value="cache-key",
        ) as mock_make_key,
    ):
        mock_task_store.get_cached_result = AsyncMock(
            return_value={
                "result": "# Cached Result",
                "quality": {
                    "format_valid": True,
                    "fallback_used": False,
                    "issues": [],
                    "answer_available": True,
                },
            }
        )
        mock_task_store.create_task = AsyncMock()
        mock_task_store.append_event = AsyncMock()
        mock_task_store.store_result = AsyncMock()
        mock_task_store.store_result_quality = AsyncMock()
        mock_task_store.update_task_status = AsyncMock()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/research",
                json={
                    "query": "test query",
                    "mode": "balanced",
                    "search_profile": "parallel-trusted",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert "task_id" in data
        assert data["status"] == "cached"
        assert str(uuid.UUID(data["task_id"].removeprefix("cached-"))) == (
            data["task_id"].removeprefix("cached-")
        )

        mock_make_key.assert_called_once_with(
            "test query",
            "balanced",
            "parallel-trusted",
            "detailed",
            search_result_num=20,
            verification_min_search_rounds=None,
        )
        cached_kwargs = mock_task_store.create_task.await_args.kwargs
        assert cached_kwargs["mode"] == "balanced"
        assert cached_kwargs["search_profile"] == "parallel-trusted"
        assert cached_kwargs["search_result_num"] == 20
        assert cached_kwargs["verification_min_search_rounds"] == 3
        assert cached_kwargs["output_detail_level"] == "detailed"
        mock_task_queue.enqueue_research_job.assert_not_called()


@pytest.mark.asyncio
async def test_create_research_reads_worker_shared_cache_before_request_queue_lookup(
    mock_task_store,
    mock_task_queue,
):
    """共享缓存命中时，请求处理路径不应再次获取任务队列。"""
    quality = {
        "format_valid": True,
        "fallback_used": False,
        "issues": [],
        "answer_available": True,
    }
    mock_task_store.get_cached_result = AsyncMock(
        return_value={
            "result": "# Worker 缓存结果",
            "quality": quality,
        }
    )
    mock_task_store.create_task = AsyncMock()
    mock_task_store.append_event = AsyncMock()
    mock_task_store.store_result = AsyncMock()
    mock_task_store.store_result_quality = AsyncMock()
    mock_task_store.update_task_status = AsyncMock()

    with (
        patch("routers.research.get_task_store", return_value=mock_task_store),
        patch(
            "routers.research.get_task_queue",
            return_value=mock_task_queue,
        ) as mock_get_task_queue,
        patch(
            "routers.research.ResultCache.make_key",
            return_value="worker-shared-cache-key",
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v1/research",
                json={"query": "跨进程缓存"},
            )

    assert response.status_code == 200
    assert response.json()["status"] == "cached"
    mock_task_store.get_cached_result.assert_awaited_once_with(
        "worker-shared-cache-key"
    )
    mock_task_store.store_result_quality.assert_awaited_once_with(
        response.json()["task_id"],
        quality,
    )
    mock_get_task_queue.assert_not_called()
    mock_task_queue.enqueue_research_job.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "quality",
    [
        None,
        {
            "format_valid": "not-a-bool",
            "fallback_used": False,
            "issues": [],
            "answer_available": True,
        },
        {
            "format_valid": False,
            "fallback_used": False,
            "issues": ["no_answer_available"],
            "answer_available": False,
        },
    ],
)
async def test_create_research_rejects_inconsistent_shared_cache_quality(
    mock_task_store,
    mock_task_queue,
    quality,
):
    """缺失、损坏或明确不可用的质量元数据不得生成 CACHED 成功任务。"""
    mock_task_store.get_cached_result = AsyncMock(
        return_value={
            "result": "# 不可信缓存结果",
            "quality": quality,
        }
    )
    mock_task_store.delete_cached_result = AsyncMock()
    mock_task_store.create_task = AsyncMock()

    with (
        patch("routers.research.get_task_store", return_value=mock_task_store),
        patch("routers.research.get_task_queue", return_value=mock_task_queue),
        patch(
            "routers.research.ResultCache.make_key",
            return_value="inconsistent-quality-cache-key",
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v1/research",
                json={"query": "不一致缓存质量"},
            )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    mock_task_store.delete_cached_result.assert_awaited_once_with(
        "inconsistent-quality-cache-key"
    )
    mock_task_queue.enqueue_research_job.assert_awaited_once()


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
        mock_task_store.get_result_quality = AsyncMock(return_value=None)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/v1/research/test-task-001")

        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == "test-task-001"
        assert data["status"] == "running"
        assert data["meta"]["query"] == "test query"
        assert data["event_count"] == 5
        assert data["result_quality"]["format_valid"] is False
        assert data["result_quality"]["fallback_used"] is False
        assert data["result_quality"]["issues"] == ["quality_unavailable"]


@pytest.mark.asyncio
async def test_get_task_status_degrades_invalid_quality_metadata(
    mock_task_store,
):
    """损坏的历史质量元数据应降级为 unavailable，不能让状态接口返回 500。"""
    meta = TaskMeta(
        task_id="test-task-invalid-quality",
        status=TaskStatus.COMPLETED,
        query="test query",
    )
    mock_task_store.get_task = AsyncMock(return_value=meta)
    mock_task_store.get_result = AsyncMock(return_value="# existing result")
    mock_task_store.get_event_stream_length = AsyncMock(return_value=1)
    mock_task_store.get_result_quality = AsyncMock(
        return_value={
            "format_valid": "not-a-bool",
            "fallback_used": False,
            "issues": [],
            "answer_available": True,
        }
    )

    with patch("routers.research.get_task_store", return_value=mock_task_store):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/v1/research/test-task-invalid-quality")

    assert response.status_code == 200
    assert response.json()["result_quality"] == {
        "format_valid": False,
        "fallback_used": False,
        "issues": ["quality_unavailable"],
        "answer_available": True,
    }


@pytest.mark.asyncio
async def test_get_task_status_not_found(mock_task_store):
    """测试 GET /v1/research/{task_id} 任务不存在。"""
    with patch("routers.research.get_task_store", return_value=mock_task_store):
        mock_task_store.get_task = AsyncMock(return_value=None)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
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

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
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

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/v1/research/test-task-003/cancel")

        assert response.status_code == 400


@pytest.mark.asyncio
async def test_cancel_by_caller(mock_task_store):
    """测试 POST /v1/research/cancel。"""
    with patch("routers.research.get_task_store", return_value=mock_task_store):
        mock_task_store.cancel_tasks_by_caller = AsyncMock(
            return_value=["task-a", "task-b"]
        )

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/research/cancel", params={"caller_id": "caller-001"}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["cancelled"] == 2
        assert len(data["task_ids"]) == 2
        mock_task_store.cancel_tasks_by_caller.assert_awaited_once_with("caller-001")


@pytest.mark.asyncio
@pytest.mark.parametrize("caller_id", [None, "", "   "])
async def test_cancel_by_caller_rejects_missing_or_blank_id(
    mock_task_store,
    caller_id,
):
    """调用方取消接口不得因 caller_id 缺失而退化成全局取消。"""
    mock_task_store.cancel_tasks_by_caller = AsyncMock(return_value=["other-task"])
    params = {} if caller_id is None else {"caller_id": caller_id}

    with patch("routers.research.get_task_store", return_value=mock_task_store):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post("/v1/research/cancel", params=params)

    assert response.status_code == 422
    mock_task_store.cancel_tasks_by_caller.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_task_status_includes_result_quality(
    mock_task_store, mock_task_queue
):
    """GET /v1/research/{task_id} 应返回 result_quality 字段。"""
    meta = TaskMeta(
        task_id="task-1",
        status=TaskStatus.COMPLETED,
        caller_id="caller-1",
        query="test query",
        mode="balanced",
        search_profile="parallel-trusted",
        search_result_num=20,
        verification_min_search_rounds=3,
        output_detail_level="detailed",
        created_at=1234567890.0,
    )

    mock_task_store.create_task = AsyncMock()
    mock_task_store.get_task = AsyncMock(return_value=meta)
    mock_task_store.get_result = AsyncMock(return_value="Final result markdown")
    mock_task_store.get_event_stream_length = AsyncMock(return_value=5)
    mock_task_store.get_result_quality = AsyncMock(
        return_value={
            "format_valid": True,
            "fallback_used": False,
            "issues": [],
        }
    )

    with (
        patch("routers.research.get_task_store", return_value=mock_task_store),
        patch("routers.research.get_task_queue", return_value=mock_task_queue),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/v1/research/task-1")

    assert response.status_code == 200
    data = response.json()
    assert "result_quality" in data
    assert data["result_quality"]["format_valid"] is True
    assert data["result_quality"]["fallback_used"] is False
    assert data["result_quality"]["issues"] == []


def test_result_quality_default_does_not_claim_valid_format():
    """质量数据缺失时，API 模型不得默认声称格式有效。"""
    from models import ResultQuality

    quality = ResultQuality()

    assert quality.format_valid is False
    assert quality.fallback_used is False
    assert quality.issues == ["quality_unavailable"]
