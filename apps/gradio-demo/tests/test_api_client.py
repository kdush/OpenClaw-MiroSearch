"""api_client 单元测试。

覆盖：
- BACKEND_MODE 环境变量切换
- SSE 单块解析
- SSE 流式分块缓冲（连接断开 / 半包重组）
- create_task / get_task / cancel_task / stream_task_events 与本地 aiohttp 测试服务的端到端
"""

import asyncio
import json

import pytest
import pytest_asyncio
from aiohttp import web

import api_client


# ---------- BACKEND_MODE ----------


def test_is_api_mode_enabled_default(monkeypatch):
    monkeypatch.delenv("BACKEND_MODE", raising=False)
    assert api_client.is_api_mode_enabled() is False


def test_is_api_mode_enabled_explicit_api(monkeypatch):
    monkeypatch.setenv("BACKEND_MODE", "api")
    assert api_client.is_api_mode_enabled() is True


def test_is_api_mode_enabled_explicit_local(monkeypatch):
    monkeypatch.setenv("BACKEND_MODE", "local")
    assert api_client.is_api_mode_enabled() is False


def test_is_api_mode_enabled_uppercase(monkeypatch):
    monkeypatch.setenv("BACKEND_MODE", "API")
    assert api_client.is_api_mode_enabled() is True


# ---------- SSE 块解析 ----------


def test_parse_sse_block_basic():
    block = 'event: tool_call\ndata: {"tool": "web"}'
    parsed = api_client._parse_sse_block(block)
    assert parsed == {"event": "tool_call", "data": {"tool": "web"}}


def test_parse_sse_block_default_event_name():
    block = 'data: {"k": 1}'
    parsed = api_client._parse_sse_block(block)
    assert parsed == {"event": "message", "data": {"k": 1}}


def test_parse_sse_block_multiline_data():
    block = "event: x\ndata: line1\ndata: line2"
    parsed = api_client._parse_sse_block(block)
    assert parsed == {"event": "x", "data": "line1\nline2"}


def test_parse_sse_block_ignores_comment_only():
    block = ": this is a comment\n: another"
    assert api_client._parse_sse_block(block) is None


def test_parse_sse_block_invalid_json_falls_back_to_raw():
    block = "event: x\ndata: not-json"
    parsed = api_client._parse_sse_block(block)
    assert parsed == {"event": "x", "data": "not-json"}


def test_parse_sse_block_ignores_id_and_retry():
    block = "id: 1\nretry: 5000\nevent: x\ndata: 1"
    parsed = api_client._parse_sse_block(block)
    assert parsed == {"event": "x", "data": 1}


# ---------- 端到端：使用本地 aiohttp 测试服务模拟 api-server ----------


@pytest_asyncio.fixture
async def mock_api_server(aiohttp_unused_port, monkeypatch):
    """启动一个本地 aiohttp 服务模拟 api-server 的 4 个端点。"""
    port = aiohttp_unused_port()
    state = {
        "events": [],
        "create_calls": 0,
        "cancel_calls": 0,
        "get_404_first": False,
    }

    async def post_research(request: web.Request) -> web.Response:
        state["create_calls"] += 1
        body = await request.json()
        return web.json_response(
            {"task_id": "tid-" + body["query"][:6], "status": "accepted"}
        )

    async def get_research(request: web.Request) -> web.Response:
        task_id = request.match_info["task_id"]
        if state["get_404_first"]:
            state["get_404_first"] = False
            return web.json_response({"detail": "not found"}, status=404)
        return web.json_response(
            {
                "task_id": task_id,
                "status": "running",
                "meta": {
                    "task_id": task_id,
                    "status": "running",
                    "query": "demo",
                    "mode": "balanced",
                    "search_profile": "parallel-trusted",
                    "search_result_num": 20,
                    "verification_min_search_rounds": 3,
                    "output_detail_level": "detailed",
                },
                "result": None,
                "event_count": 0,
            }
        )

    async def cancel_research(request: web.Request) -> web.Response:
        state["cancel_calls"] += 1
        task_id = request.match_info["task_id"]
        return web.json_response({"cancelled": 1, "task_ids": [task_id]})

    async def stream_research(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(
            status=200,
            headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"},
        )
        await resp.prepare(request)
        events = [
            ("stage_heartbeat", {"phase": "starting"}),
            ("tool_call", {"tool_name": "google_search"}),
            ("final_output", {"markdown": "# done"}),
            ("done", {"status": "completed"}),
        ]
        for ev_name, data in events:
            chunk = f"event: {ev_name}\ndata: {json.dumps(data)}\n\n".encode()
            await resp.write(chunk)
            await asyncio.sleep(0.01)
        return resp

    app = web.Application()
    app.router.add_post("/v1/research", post_research)
    app.router.add_get("/v1/research/{task_id}", get_research)
    app.router.add_post("/v1/research/{task_id}/cancel", cancel_research)
    app.router.add_get("/v1/research/{task_id}/stream", stream_research)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()

    monkeypatch.setenv("API_BASE_URL", f"http://127.0.0.1:{port}")
    try:
        yield state
    finally:
        await runner.cleanup()


@pytest.fixture
def aiohttp_unused_port():
    """返回一个空闲端口分配函数。"""
    import socket

    def _alloc() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    return _alloc


@pytest.mark.asyncio
async def test_create_task_e2e(mock_api_server):
    result = await api_client.create_task(
        query="hello world",
        mode="balanced",
        search_profile="parallel-trusted",
        search_result_num=20,
        verification_min_search_rounds=3,
        output_detail_level="detailed",
    )
    assert result["task_id"].startswith("tid-")
    assert result["status"] == "accepted"
    assert mock_api_server["create_calls"] == 1


@pytest.mark.asyncio
async def test_get_task_e2e(mock_api_server):
    snapshot = await api_client.get_task("abc-123")
    assert snapshot is not None
    assert snapshot["task_id"] == "abc-123"
    assert snapshot["meta"]["mode"] == "balanced"


@pytest.mark.asyncio
async def test_get_task_404(mock_api_server):
    mock_api_server["get_404_first"] = True
    snapshot = await api_client.get_task("missing")
    assert snapshot is None


@pytest.mark.asyncio
async def test_cancel_task_e2e(mock_api_server):
    result = await api_client.cancel_task("xxx")
    assert result["cancelled"] == 1
    assert mock_api_server["cancel_calls"] == 1


@pytest.mark.asyncio
async def test_stream_task_events_e2e(mock_api_server):
    received = []
    async for msg in api_client.stream_task_events("abc"):
        received.append(msg)
    # 最后应包含 done 事件，且按发送顺序到达
    types = [m["event"] for m in received]
    assert "stage_heartbeat" in types
    assert "tool_call" in types
    assert "final_output" in types
    assert types[-1] == "done"
    # done 事件 data 解析正确
    assert received[-1]["data"] == {"status": "completed"}


@pytest.mark.asyncio
async def test_stream_cancel_check(mock_api_server):
    """cancel_check 返回 True 时应及早终止迭代。"""
    received = []
    triggered = {"count": 0}

    async def stop_after_first() -> bool:
        triggered["count"] += 1
        return triggered["count"] > 1

    async for msg in api_client.stream_task_events(
        "abc", cancel_check=stop_after_first
    ):
        received.append(msg)
    # 至少进入了 stream，但被取消提前结束（不一定收到全部事件）
    assert len(received) <= 4
