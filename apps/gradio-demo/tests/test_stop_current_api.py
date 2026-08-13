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
    module_name = "gradio_demo_main_for_tests"
    spec = importlib.util.spec_from_file_location(module_name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_stop_current_api_can_cancel_active_api_task(monkeypatch):
    demo_main = _load_demo_main()
    monkeypatch.setenv("BACKEND_MODE", "local")
    demo_main._CANCEL_FLAGS.clear()
    demo_main._ACTIVE_TASK_IDS.clear()

    started = asyncio.Event()

    async def fake_stream_events(task_id, *_args, **_kwargs):
        started.set()
        while not await demo_main._disconnect_check_for_task(task_id):
            await asyncio.sleep(0.01)
        yield {"event": "error", "data": {"error": "cancelled by test"}}

    demo_main.stream_events_optimized = fake_stream_events

    task = asyncio.create_task(
        demo_main.run_research_once(
            query="测试取消",
            mode="balanced",
            search_profile="searxng-only",
            search_result_num=10,
            verification_min_search_rounds=1,
        )
    )

    await asyncio.wait_for(started.wait(), timeout=1.0)
    cancel_result = demo_main.stop_current_api()

    assert cancel_result["cancelled"] >= 1

    result = await asyncio.wait_for(task, timeout=1.0)
    assert "cancelled by test" in result
    assert not demo_main._ACTIVE_TASK_IDS


@pytest.mark.asyncio
async def test_stop_current_api_caller_id_isolation(monkeypatch):
    """stop_current_api(caller_id=X) 只取消该 caller 的任务，不影响其他并发任务。"""
    demo_main = _load_demo_main()
    monkeypatch.setenv("BACKEND_MODE", "local")
    demo_main._CANCEL_FLAGS.clear()
    demo_main._ACTIVE_TASK_IDS.clear()

    started_a = asyncio.Event()
    started_b = asyncio.Event()

    async def fake_stream_a(task_id, *_args, **_kwargs):
        started_a.set()
        while not await demo_main._disconnect_check_for_task(task_id):
            await asyncio.sleep(0.01)
        yield {"event": "error", "data": {"error": "cancelled-A"}}

    async def fake_stream_b(task_id, *_args, **_kwargs):
        started_b.set()
        # B 不会被取消，正常结束
        await asyncio.sleep(0.05)
        yield {"event": "final_output", "data": {"markdown": "done-B"}}

    call_count = {"n": 0}

    async def dispatch_stream(task_id, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            async for msg in fake_stream_a(task_id, *args, **kwargs):
                yield msg
        else:
            async for msg in fake_stream_b(task_id, *args, **kwargs):
                yield msg

    demo_main.stream_events_optimized = dispatch_stream

    task_a = asyncio.create_task(
        demo_main.run_research_once(
            query="任务A",
            mode="balanced",
            caller_id=" user-A ",
        )
    )
    task_b = asyncio.create_task(
        demo_main.run_research_once(
            query="任务B",
            mode="balanced",
            caller_id="user-B",
        )
    )

    await asyncio.wait_for(started_a.wait(), timeout=1.0)
    await asyncio.wait_for(started_b.wait(), timeout=1.0)

    # 只取消 user-A 的任务
    cancel_result = demo_main.stop_current_api(caller_id="user-A")
    assert cancel_result["cancelled"] == 1

    result_a = await asyncio.wait_for(task_a, timeout=1.0)
    assert "cancelled-A" in result_a

    result_b = await asyncio.wait_for(task_b, timeout=2.0)
    # B 应该正常完成，不受 A 的取消影响
    assert "cancelled" not in result_b.lower() or "done-B" in result_b


@pytest.mark.asyncio
async def test_stop_current_api_cancels_only_selected_remote_caller(monkeypatch):
    """API 模式不仅停止本地 SSE，还必须定向取消服务端任务。"""
    demo_main = _load_demo_main()
    monkeypatch.setenv("BACKEND_MODE", "api")
    demo_main._CANCEL_FLAGS.clear()
    demo_main._ACTIVE_TASK_IDS.clear()
    demo_main._register_active_task("remote-A", caller_id="user-A")
    demo_main._register_active_task("remote-B", caller_id="user-B")
    remote_cancelled = []

    async def fake_cancel_task(task_id):
        remote_cancelled.append(task_id)
        return {"cancelled": 1, "task_ids": [task_id]}

    monkeypatch.setattr(demo_main.api_client, "cancel_task", fake_cancel_task)

    cancel_result = demo_main.stop_current_api(caller_id="user-A")
    await asyncio.sleep(0)

    assert cancel_result["cancelled"] == 1
    assert cancel_result["active_task_ids"] == ["remote-A"]
    assert remote_cancelled == ["remote-A"]
    assert await demo_main._disconnect_check_for_task("remote-A") is True
    assert await demo_main._disconnect_check_for_task("remote-B") is False


@pytest.mark.asyncio
async def test_stop_current_api_ignores_task_unregistered_before_atomic_mark(
    monkeypatch,
):
    """筛选与置位之间注销的任务不得产生孤立 flag 或远端取消。"""
    demo_main = _load_demo_main()
    monkeypatch.setenv("BACKEND_MODE", "api")
    demo_main._CANCEL_FLAGS.clear()
    demo_main._ACTIVE_TASK_IDS.clear()
    demo_main._register_active_task("race-task", caller_id="race-caller")
    original_get_active_task_ids = demo_main._get_active_task_ids
    remote_cancelled = []

    def unregister_during_selection(
        caller_id=None,
        *,
        mark_cancelled=False,
    ):
        if mark_cancelled:
            demo_main._unregister_active_task("race-task")
            return original_get_active_task_ids(
                caller_id=caller_id,
                mark_cancelled=True,
            )
        selected = original_get_active_task_ids(caller_id=caller_id)
        demo_main._unregister_active_task("race-task")
        return selected

    async def fake_cancel_task(task_id):
        remote_cancelled.append(task_id)
        return {"cancelled": 1, "task_ids": [task_id]}

    monkeypatch.setattr(
        demo_main,
        "_get_active_task_ids",
        unregister_during_selection,
    )
    monkeypatch.setattr(demo_main.api_client, "cancel_task", fake_cancel_task)

    result = demo_main.stop_current_api(caller_id="race-caller")
    await asyncio.sleep(0)

    assert result["cancelled"] == 0
    assert result["active_task_ids"] == []
    assert result["remote_cancel_requested"] == 0
    assert "race-task" not in demo_main._CANCEL_FLAGS
    assert remote_cancelled == []


def test_public_caller_stop_rejects_empty_caller_id(monkeypatch):
    """公开 caller 入口缺少标识时，不得退化为取消所有任务。"""
    demo_main = _load_demo_main()
    monkeypatch.setenv("BACKEND_MODE", "local")
    demo_main._CANCEL_FLAGS.clear()
    demo_main._ACTIVE_TASK_IDS.clear()
    demo_main._register_active_task("task-A", caller_id="user-A")
    demo_main._register_active_task("task-B", caller_id="user-B")

    result = demo_main.stop_current_by_caller_api("")

    assert result["cancelled"] == 0
    assert result["active_task_ids"] == []
    assert result["reason"] == "caller_id_required"
    assert demo_main._CANCEL_FLAGS["task-A"] is False
    assert demo_main._CANCEL_FLAGS["task-B"] is False


def test_stop_current_ui_accepts_missing_state(monkeypatch):
    demo_main = _load_demo_main()
    monkeypatch.setenv("BACKEND_MODE", "local")
    run_update, stop_update = demo_main.stop_current_ui(None)

    assert run_update["interactive"] is True
    assert stop_update["interactive"] is False
