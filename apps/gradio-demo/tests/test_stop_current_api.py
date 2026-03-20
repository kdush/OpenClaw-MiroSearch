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
async def test_stop_current_api_can_cancel_active_api_task():
    demo_main = _load_demo_main()
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
async def test_stop_current_api_caller_id_isolation():
    """stop_current_api(caller_id=X) 只取消该 caller 的任务，不影响其他并发任务。"""
    demo_main = _load_demo_main()
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

    # 第一次调用用 fake_stream_a（caller_id="user-A"）
    original_stream = demo_main.stream_events_optimized

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
            caller_id="user-A",
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


def test_stop_current_ui_accepts_missing_state():
    demo_main = _load_demo_main()
    run_update, stop_update = demo_main.stop_current_ui(None)

    assert run_update["interactive"] is True
    assert stop_update["interactive"] is False
