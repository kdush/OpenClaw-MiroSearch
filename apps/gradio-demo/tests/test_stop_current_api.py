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


def test_stop_current_ui_accepts_missing_state():
    demo_main = _load_demo_main()
    run_update, stop_update = demo_main.stop_current_ui(None)

    assert run_update["interactive"] is True
    assert stop_update["interactive"] is False
