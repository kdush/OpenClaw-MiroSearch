import asyncio
import importlib.util
import os
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[3]
GRADIO_DEMO_DIR = PROJECT_ROOT / "apps" / "gradio-demo"
MIROFLOW_AGENT_DIR = PROJECT_ROOT / "apps" / "miroflow-agent"
MODULE_PATH = GRADIO_DEMO_DIR / "main.py"


def _load_demo_main():
    os.environ.setdefault("ENABLE_PROMPT_PATCH", "0")
    if str(GRADIO_DEMO_DIR) not in sys.path:
        sys.path.insert(0, str(GRADIO_DEMO_DIR))
    if str(MIROFLOW_AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(MIROFLOW_AGENT_DIR))
    module_name = "gradio_demo_main_run_once_routing_tests"
    spec = importlib.util.spec_from_file_location(module_name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("max_size", "ttl_seconds"),
    [
        ("not-an-integer", "not-an-integer"),
        ("-5", "-1"),
    ],
)
def test_invalid_local_cache_environment_values_fall_back(
    monkeypatch,
    max_size,
    ttl_seconds,
):
    """本地缓存配置写错时不应阻断 Gradio 模块启动。"""
    monkeypatch.setenv("RESULT_CACHE_MAX_SIZE", max_size)
    monkeypatch.setenv("RESULT_CACHE_TTL_SECONDS", ttl_seconds)

    module = _load_demo_main()

    assert module._result_cache.stats() == {
        "size": 0,
        "max_size": 128,
        "ttl_seconds": 3600,
    }


def test_invalid_research_depth_environment_values_use_api_defaults(
    monkeypatch,
):
    """非法部署默认值不得被夹到另一档，Gradio 应与 API 一致回退。"""
    monkeypatch.setenv("DEFAULT_SEARCH_RESULT_NUM", "25")
    monkeypatch.setenv("DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS", "99")

    module = _load_demo_main()

    assert module.DEFAULT_SEARCH_RESULT_NUM == 20
    assert module.DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS == 3


@pytest.fixture
def demo_main(monkeypatch):
    monkeypatch.setenv("BACKEND_MODE", "local")
    module = _load_demo_main()
    module._result_cache.clear()
    module._CANCEL_FLAGS.clear()
    module._ACTIVE_TASK_IDS.clear()
    yield module
    module._result_cache.clear()
    module._CANCEL_FLAGS.clear()
    module._ACTIVE_TASK_IDS.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "first_result_num", "second_result_num", "first_rounds", "second_rounds"),
    [
        ("balanced", 10, 20, 3, 3),
        ("verified", 20, 20, 2, 4),
    ],
)
async def test_local_cache_key_separates_effective_research_depth(
    demo_main,
    monkeypatch,
    mode,
    first_result_num,
    second_result_num,
    first_rounds,
    second_rounds,
):
    """检索条数或有效校验轮次不同，不得误命中同一份本地结果。"""
    executions = []

    async def fake_stream_events(
        _task_id,
        _query,
        _mode,
        _profile,
        search_result_num,
        verification_min_rounds,
        _detail_level,
        _cancel_check,
    ):
        executions.append((search_result_num, verification_min_rounds))
        conclusion = (
            f"# 研究结论 {search_result_num}/{verification_min_rounds}\n\n"
            + "这是用于验证真实缓存路由的完整结论。" * 12
        )
        yield {"event": "final_output", "data": {"markdown": conclusion}}
        yield {"event": "done", "data": {"status": "completed"}}

    monkeypatch.setattr(
        demo_main,
        "stream_events_optimized",
        fake_stream_events,
    )

    first = await demo_main.run_research_once(
        query="同一个缓存问题",
        mode=mode,
        search_profile="searxng-first",
        search_result_num=first_result_num,
        verification_min_search_rounds=first_rounds,
        output_detail_level="balanced",
    )
    second = await demo_main.run_research_once(
        query="同一个缓存问题",
        mode=mode,
        search_profile="searxng-first",
        search_result_num=second_result_num,
        verification_min_search_rounds=second_rounds,
        output_detail_level="balanced",
    )
    first_again = await demo_main.run_research_once(
        query="同一个缓存问题",
        mode=mode,
        search_profile="searxng-first",
        search_result_num=first_result_num,
        verification_min_search_rounds=first_rounds,
        output_detail_level="balanced",
    )

    assert len(executions) == 2
    assert first != second
    assert first_again == first


@pytest.mark.asyncio
@pytest.mark.parametrize("done_status", ["failed", "cancelled"])
async def test_local_cache_rejects_non_success_terminal_results(
    demo_main,
    monkeypatch,
    done_status,
):
    """失败或取消结果即使正文很长，也不得污染下一次本地请求。"""
    executions = 0

    async def fake_stream_events(*_args, **_kwargs):
        nonlocal executions
        executions += 1
        yield {
            "event": "final_output",
            "data": {
                "markdown": "# 未完成结果\n\n"
                + "这段较长内容用于证明不能只按渲染长度判断缓存资格。" * 12
            },
        }
        yield {
            "event": "done",
            "data": {"status": done_status, "error": "测试终态"},
        }

    monkeypatch.setattr(
        demo_main,
        "stream_events_optimized",
        fake_stream_events,
    )

    first = await demo_main.run_research_once(
        query=f"本地缓存终态-{done_status}",
        mode="balanced",
    )
    second = await demo_main.run_research_once(
        query=f"本地缓存终态-{done_status}",
        mode="balanced",
    )

    assert first == second
    assert executions == 2
    assert demo_main._result_cache.size == 0


@pytest.mark.asyncio
async def test_api_mode_run_once_uses_only_remote_backend_and_forwards_effective_params(
    demo_main,
    monkeypatch,
):
    """API 模式的非流式公开入口不得回落到本地 Pipeline。"""
    monkeypatch.setenv("BACKEND_MODE", " API ")
    create_calls = []

    async def fake_create_task(**kwargs):
        create_calls.append(kwargs)
        return {"task_id": "remote-task-1", "status": "accepted"}

    async def fake_stream_task_events(task_id, cancel_check=None):
        assert task_id == "remote-task-1"
        assert cancel_check is not None
        yield {
            "event": "final_output",
            "data": {"markdown": "# 远端研究结论\n\n服务端返回的最终 Markdown。"},
        }
        yield {"event": "done", "data": {"status": "completed"}}

    async def fail_local_pipeline(*_args, **_kwargs):
        raise AssertionError("API 模式不应调用本地 stream_events_optimized")
        yield

    monkeypatch.setattr(demo_main.api_client, "safe_create_task", fake_create_task)
    monkeypatch.setattr(
        demo_main.api_client,
        "stream_task_events",
        fake_stream_task_events,
    )
    monkeypatch.setattr(
        demo_main,
        "stream_events_optimized",
        fail_local_pipeline,
    )

    result = await demo_main.run_research_once(
        query="远端路由测试",
        mode="verified",
        search_profile="searxng-first",
        search_result_num="20",
        verification_min_search_rounds="4",
        output_detail_level="detailed",
        caller_id="caller-A",
    )

    assert "远端研究结论" in result
    assert create_calls == [
        {
            "query": "远端路由测试",
            "mode": "verified",
            "search_profile": "searxng-first",
            "search_result_num": 20,
            "verification_min_search_rounds": 4,
            "output_detail_level": "detailed",
            "caller_id": "caller-A",
        }
    ]
    assert demo_main._ACTIVE_TASK_IDS == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("done_status", "expected_text"),
    [
        ("failed", "任务执行失败"),
        ("cancelled", "任务已取消"),
    ],
)
async def test_api_mode_run_once_returns_clear_terminal_failure(
    demo_main,
    monkeypatch,
    done_status,
    expected_text,
):
    monkeypatch.setenv("BACKEND_MODE", "api")

    async def fake_create_task(**_kwargs):
        return {"task_id": f"remote-{done_status}", "status": "accepted"}

    async def fake_stream_task_events(_task_id, cancel_check=None):
        assert cancel_check is not None
        yield {
            "event": "done",
            "data": {"status": done_status, "error": "上游终态说明"},
        }

    monkeypatch.setattr(demo_main.api_client, "safe_create_task", fake_create_task)
    monkeypatch.setattr(
        demo_main.api_client,
        "stream_task_events",
        fake_stream_task_events,
    )

    result = await demo_main.run_research_once(
        query="终态测试",
        mode="balanced",
        caller_id="caller-terminal",
    )

    assert expected_text in result
    assert "上游终态说明" in result
    assert demo_main._ACTIVE_TASK_IDS == {}


@pytest.mark.asyncio
async def test_api_mode_run_once_handles_missing_task_id_without_registering_task(
    demo_main,
    monkeypatch,
):
    monkeypatch.setenv("BACKEND_MODE", "api")

    async def fake_create_task(**_kwargs):
        return {"status": "accepted"}

    monkeypatch.setattr(demo_main.api_client, "safe_create_task", fake_create_task)

    result = await demo_main.run_research_once(
        query="缺少任务编号",
        mode="balanced",
        caller_id="caller-missing",
    )

    assert "未返回 task_id" in result
    assert demo_main._ACTIVE_TASK_IDS == {}


@pytest.mark.asyncio
async def test_api_mode_run_once_cleans_active_task_after_stream_error(
    demo_main,
    monkeypatch,
):
    monkeypatch.setenv("BACKEND_MODE", "api")

    async def fake_create_task(**_kwargs):
        return {"task_id": "remote-error", "status": "accepted"}

    async def fake_stream_task_events(_task_id, cancel_check=None):
        assert cancel_check is not None
        if False:
            yield {}
        raise demo_main.api_client.ApiClientError("SSE 已断开")

    monkeypatch.setattr(demo_main.api_client, "safe_create_task", fake_create_task)
    monkeypatch.setattr(
        demo_main.api_client,
        "stream_task_events",
        fake_stream_task_events,
    )

    result = await demo_main.run_research_once(
        query="流错误",
        mode="balanced",
        caller_id="caller-error",
    )

    assert "连接 api-server 失败" in result
    assert "SSE 已断开" in result
    assert demo_main._ACTIVE_TASK_IDS == {}


@pytest.mark.asyncio
async def test_api_mode_run_once_reports_local_stop_when_stream_ends_before_done(
    demo_main,
    monkeypatch,
):
    """Stop 先结束本地 SSE 时，公开调用仍应返回明确的取消终态。"""
    monkeypatch.setenv("BACKEND_MODE", "api")
    stream_started = asyncio.Event()

    async def fake_create_task(**_kwargs):
        return {"task_id": "remote-stopped", "status": "accepted"}

    async def fake_stream_task_events(_task_id, cancel_check=None):
        assert cancel_check is not None
        stream_started.set()
        while not await cancel_check():
            await asyncio.sleep(0)
        if False:
            yield {}

    async def fake_cancel_task(task_id):
        return {"cancelled": 1, "task_ids": [task_id]}

    monkeypatch.setattr(demo_main.api_client, "safe_create_task", fake_create_task)
    monkeypatch.setattr(
        demo_main.api_client,
        "stream_task_events",
        fake_stream_task_events,
    )
    monkeypatch.setattr(demo_main.api_client, "cancel_task", fake_cancel_task)

    running = asyncio.create_task(
        demo_main.run_research_once(
            query="主动停止",
            mode="balanced",
            caller_id="caller-stop",
        )
    )
    await stream_started.wait()
    demo_main.stop_current_by_caller_api("caller-stop")

    result = await running

    assert "任务已取消" in result
    assert demo_main._ACTIVE_TASK_IDS == {}


@pytest.mark.asyncio
async def test_api_mode_run_once_rejects_stream_without_terminal_event(
    demo_main,
    monkeypatch,
):
    """远端流静默结束且没有 done 时，不得伪装成空成功。"""
    monkeypatch.setenv("BACKEND_MODE", "api")

    async def fake_create_task(**_kwargs):
        return {"task_id": "remote-no-done", "status": "accepted"}

    async def fake_stream_task_events(_task_id, cancel_check=None):
        assert cancel_check is not None
        if False:
            yield {}

    monkeypatch.setattr(demo_main.api_client, "safe_create_task", fake_create_task)
    monkeypatch.setattr(
        demo_main.api_client,
        "stream_task_events",
        fake_stream_task_events,
    )

    result = await demo_main.run_research_once(
        query="缺少终态",
        mode="balanced",
        caller_id="caller-no-done",
    )

    assert "未收到终态" in result
    assert demo_main._ACTIVE_TASK_IDS == {}


@pytest.mark.asyncio
async def test_api_mode_direct_task_cancel_notifies_remote_before_reraising(
    demo_main,
    monkeypatch,
):
    """Gradio 直接取消协程时，也必须先通知 api-server。"""
    monkeypatch.setenv("BACKEND_MODE", "api")
    stream_started = asyncio.Event()
    remote_cancelled = []

    async def fake_create_task(**_kwargs):
        return {"task_id": "remote-direct-cancel", "status": "accepted"}

    async def fake_stream_task_events(_task_id, cancel_check=None):
        assert cancel_check is not None
        stream_started.set()
        await asyncio.Event().wait()
        if False:
            yield {}

    async def fake_cancel_task(task_id):
        await asyncio.sleep(0)
        remote_cancelled.append(task_id)
        return {"cancelled": 1, "task_ids": [task_id]}

    monkeypatch.setattr(demo_main.api_client, "safe_create_task", fake_create_task)
    monkeypatch.setattr(
        demo_main.api_client,
        "stream_task_events",
        fake_stream_task_events,
    )
    monkeypatch.setattr(demo_main.api_client, "cancel_task", fake_cancel_task)

    running = asyncio.create_task(
        demo_main.run_research_once(
            query="直接取消协程",
            mode="balanced",
            caller_id="caller-direct-cancel",
        )
    )
    await stream_started.wait()
    running.cancel()

    with pytest.raises(asyncio.CancelledError):
        await running

    assert remote_cancelled == ["remote-direct-cancel"]
    assert demo_main._ACTIVE_TASK_IDS == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "done_data",
    [
        {},
        {"status": "unknown"},
        "completed",
    ],
)
async def test_api_mode_rejects_invalid_done_status_protocol(
    demo_main,
    monkeypatch,
    done_data,
):
    """缺失、未知或非对象的 done.status 都不能伪装成成功。"""
    monkeypatch.setenv("BACKEND_MODE", "api")

    async def fake_create_task(**_kwargs):
        return {"task_id": "remote-invalid-done", "status": "accepted"}

    async def fake_stream_task_events(_task_id, cancel_check=None):
        assert cancel_check is not None
        yield {
            "event": "final_output",
            "data": {"markdown": "# 这段正文不能掩盖非法终态"},
        }
        yield {"event": "done", "data": done_data}

    monkeypatch.setattr(demo_main.api_client, "safe_create_task", fake_create_task)
    monkeypatch.setattr(
        demo_main.api_client,
        "stream_task_events",
        fake_stream_task_events,
    )

    result = await demo_main.run_research_once(
        query="非法终态",
        mode="balanced",
        caller_id="caller-invalid-done",
    )

    assert "终态协议错误" in result
    assert "这段正文不能掩盖非法终态" not in result
    assert demo_main._ACTIVE_TASK_IDS == {}


@pytest.mark.asyncio
async def test_original_seventh_positional_argument_remains_render_mode(
    demo_main,
    monkeypatch,
):
    """历史位置参数第 7 项仍是 render_mode，不能被解释成 caller_id。"""
    monkeypatch.setenv("BACKEND_MODE", "api")
    create_calls = []

    async def fake_create_task(**kwargs):
        create_calls.append(kwargs)
        return {"task_id": "remote-positional", "status": "accepted"}

    async def fake_stream_task_events(_task_id, cancel_check=None):
        assert cancel_check is not None
        yield {
            "event": "final_output",
            "data": {"markdown": "# 位置参数兼容"},
        }
        yield {"event": "done", "data": {"status": "completed"}}

    monkeypatch.setattr(demo_main.api_client, "safe_create_task", fake_create_task)
    monkeypatch.setattr(
        demo_main.api_client,
        "stream_task_events",
        fake_stream_task_events,
    )

    result = await demo_main.run_research_once(
        "位置参数",
        "balanced",
        "searxng-first",
        20,
        3,
        "detailed",
        "summary_only",
    )

    assert "位置参数兼容" in result
    assert create_calls[0]["caller_id"] is None


@pytest.mark.asyncio
async def test_public_binding_adapter_maps_seventh_argument_to_caller_id(
    demo_main,
    monkeypatch,
):
    """公开 Gradio 适配器将第 7 项映射为 caller_id。"""
    monkeypatch.setenv("BACKEND_MODE", "api")
    create_calls = []

    async def fake_create_task(**kwargs):
        create_calls.append(kwargs)
        return {"task_id": "remote-adapter", "status": "accepted"}

    async def fake_stream_task_events(_task_id, cancel_check=None):
        assert cancel_check is not None
        yield {
            "event": "final_output",
            "data": {"markdown": "# 适配器调用"},
        }
        yield {"event": "done", "data": {"status": "completed"}}

    monkeypatch.setattr(demo_main.api_client, "safe_create_task", fake_create_task)
    monkeypatch.setattr(
        demo_main.api_client,
        "stream_task_events",
        fake_stream_task_events,
    )

    result = await demo_main.run_research_once_api_binding(
        "适配器参数",
        "balanced",
        "searxng-first",
        20,
        3,
        "detailed",
        "caller-adapter",
    )

    assert "适配器调用" in result
    assert create_calls[0]["caller_id"] == "caller-adapter"
