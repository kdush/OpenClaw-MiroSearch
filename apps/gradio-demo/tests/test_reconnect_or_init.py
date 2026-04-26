import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[3]
GRADIO_DEMO_DIR = PROJECT_ROOT / "apps" / "gradio-demo"
MIROFLOW_AGENT_DIR = PROJECT_ROOT / "apps" / "miroflow-agent"
MODULE_PATH = GRADIO_DEMO_DIR / "main.py"


def _load_demo_main():
    os.environ.setdefault("ENABLE_PROMPT_PATCH", "0")
    os.environ.setdefault("BACKEND_MODE", "api")
    if str(GRADIO_DEMO_DIR) not in sys.path:
        sys.path.insert(0, str(GRADIO_DEMO_DIR))
    if str(MIROFLOW_AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(MIROFLOW_AGENT_DIR))
    module_name = "gradio_demo_main_reconnect_tests"
    spec = importlib.util.spec_from_file_location(module_name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_reconnect_or_init_first_frame_uses_running_placeholder(monkeypatch):
    demo_main = _load_demo_main()

    async def fake_get_task(task_id: str):
        return {
            "task_id": task_id,
            "status": "running",
            "event_count": 5,
            "meta": {
                "task_id": task_id,
                "status": "running",
                "mode": "balanced",
                "search_profile": "parallel",
                "search_result_num": 10,
                "verification_min_search_rounds": 3,
                "output_detail_level": "balanced",
            },
        }

    async def fake_stream_task_events(task_id: str, cancel_check=None):
        yield {
            "event": "stage_heartbeat",
            "data": {
                "phase": "推理",
                "turn": 1,
                "detail": "主模型推理中",
                "agent_name": "main",
                "search_round": 0,
                "timestamp": 1.0,
            },
        }

    monkeypatch.setattr(demo_main.api_client, "get_task", fake_get_task)
    monkeypatch.setattr(
        demo_main.api_client,
        "stream_task_events",
        fake_stream_task_events,
    )

    request = SimpleNamespace(query_params={"task_id": "task-running-1"})
    agen = demo_main.reconnect_or_init({}, request)
    first_markdown, run_update, stop_update, ui_state, task_id_bridge = await agen.__anext__()
    await agen.aclose()

    assert "等待开始研究" not in first_markdown
    assert "当前任务已启动" in first_markdown
    assert "阶段:推理" in first_markdown
    assert run_update["interactive"] is False
    assert stop_update["interactive"] is True
    assert ui_state["task_id"] == "task-running-1"
    assert task_id_bridge == "task-running-1"
