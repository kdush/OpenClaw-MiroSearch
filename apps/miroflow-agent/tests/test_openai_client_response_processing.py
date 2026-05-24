import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from omegaconf import OmegaConf

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

OpenAIClient = importlib.import_module("src.llm.providers.openai_client").OpenAIClient


def _make_minimal_cfg(**overrides) -> Any:
    base = {
        "llm": {
            "provider": "openai",
            "model_name": "qwen/qwen3.6-35b-a3b",
            "temperature": 0.7,
            "top_p": 0.9,
            "min_p": 0.0,
            "top_k": 50,
            "max_context_length": 4096,
            "max_tokens": 1024,
            "async_client": True,
            "api_key": "test-key",
            "base_url": "http://localhost:9999/v1",
            "max_retries": 1,
            "retry_wait_seconds": 0.01,
            "model_summary_name": "qwen/qwen3.6-35b-a3b",
        },
        "agent": {
            "keep_tool_result": 5,
        },
    }
    cfg = OmegaConf.create(base)
    if overrides:
        cfg = OmegaConf.merge(cfg, overrides)
    return cfg


def _make_task_log():
    task_log = MagicMock()
    task_log.log_step = MagicMock()
    task_log.record_stage_timing = MagicMock()
    task_log.run_metrics = MagicMock()
    task_log.run_metrics.record_model_route = MagicMock()
    return task_log


def _make_client():
    client = object.__new__(OpenAIClient)
    client.task_log = MagicMock()
    return client


def _make_response(finish_reason, content=None, **message_fields):
    message = SimpleNamespace(content=content, **message_fields)
    choice = SimpleNamespace(finish_reason=finish_reason, message=message)
    return SimpleNamespace(choices=[choice])


def _make_success_response(content="好的"):
    usage = SimpleNamespace(
        prompt_tokens=1,
        completion_tokens=1,
        prompt_tokens_details=None,
        completion_tokens_details=None,
    )
    return SimpleNamespace(
        choices=[_make_response("stop", content=content).choices[0]],
        usage=usage,
        model="qwen/qwen3.6-35b-a3b-20260415",
    )


def test_process_response_uses_reasoning_when_length_response_has_no_content():
    client = _make_client()
    history = []
    response = _make_response(
        "length",
        content=None,
        reasoning="推理过程最后得到：1+1 等于 2。",
    )

    text, should_exit, updated_history = client.process_llm_response(response, history)

    assert text == "推理过程最后得到：1+1 等于 2。"
    assert should_exit is False
    assert updated_history == [
        {"role": "assistant", "content": "推理过程最后得到：1+1 等于 2。"}
    ]


def test_process_response_uses_deepseek_reasoning_content_when_content_is_empty():
    client = _make_client()
    history = []
    response = _make_response(
        "stop",
        content="",
        reasoning_content="最终答案：DeepSeek V4 系列返回 reasoning_content。",
    )

    text, should_exit, updated_history = client.process_llm_response(response, history)

    assert text == "最终答案：DeepSeek V4 系列返回 reasoning_content。"
    assert should_exit is False
    assert updated_history == [
        {
            "role": "assistant",
            "content": "最终答案：DeepSeek V4 系列返回 reasoning_content。",
        }
    ]


@pytest.mark.asyncio
async def test_summary_request_disables_openrouter_reasoning_tokens():
    mock_chat = MagicMock()
    mock_chat.completions.create = AsyncMock(return_value=_make_success_response())
    mock_client = MagicMock()
    mock_client.chat = mock_chat

    with patch.object(OpenAIClient, "_create_client", return_value=mock_client):
        client = OpenAIClient(
            task_id="summary-reasoning-test",
            cfg=_make_minimal_cfg(),
            task_log=_make_task_log(),
        )

        await client._create_message(
            "",
            [{"role": "user", "content": "总结：1+1"}],
            [],
            agent_type="final_summary",
        )

    params = mock_chat.completions.create.call_args.kwargs
    assert params["extra_body"]["reasoning"] == {
        "effort": "none",
        "exclude": True,
    }


@pytest.mark.asyncio
async def test_deepseek_v4_routed_model_enables_thinking():
    mock_chat = MagicMock()
    mock_chat.completions.create = AsyncMock(return_value=_make_success_response())
    mock_client = MagicMock()
    mock_client.chat = mock_chat

    cfg = _make_minimal_cfg(
        llm={
            "model_name": "qwen/qwen3.6-35b-a3b",
            "model_thinking_name": "deepseek/deepseek-v4-pro",
        }
    )

    with patch.object(OpenAIClient, "_create_client", return_value=mock_client):
        client = OpenAIClient(
            task_id="deepseek-v4-routing-test",
            cfg=cfg,
            task_log=_make_task_log(),
        )

        await client._create_message(
            "",
            [{"role": "user", "content": "测试 DeepSeek V4"}],
            [],
            agent_type="main",
        )

    params = mock_chat.completions.create.call_args.kwargs
    assert params["model"] == "deepseek/deepseek-v4-pro"
    assert params["extra_body"]["thinking"] == {"type": "enabled"}
