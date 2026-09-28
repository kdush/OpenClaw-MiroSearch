# Copyright (c) 2025 MiroMind
"""Round 7: timeout fail-fast + degrade-once + metrics."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from omegaconf import OmegaConf

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.llm.providers.openai_client import OpenAIClient  # noqa: E402
from src.logging.task_logger import RunMetrics  # noqa: E402


def _make_client(**llm_overrides) -> OpenAIClient:
    llm = {
        "provider": "openai",
        "model_name": "glm-5.3-flash",
        "api_key": "test-key",
        "base_url": "https://example.invalid/v1",
        "async_client": True,
        "temperature": 0.3,
        "top_p": 1.0,
        "min_p": 0.0,
        "top_k": 50,
        "max_tokens": 1024,
        "max_context_length": 128000,
        "max_retries": 4,
        "retry_wait_seconds": 0.01,
        "timeout_fail_fast": True,
        "timeout_degrade_keep_tool_results": 2,
    }
    llm.update(llm_overrides)
    cfg = OmegaConf.create(
        {
            "llm": llm,
            "agent": {"keep_tool_result": 5},
        }
    )
    task_log = MagicMock()
    task_log.run_metrics = RunMetrics()
    task_log.log_step = MagicMock()
    task_log.record_stage_timing = MagicMock()
    client = OpenAIClient(task_id="t-timeout", cfg=cfg, task_log=task_log)
    client._remove_tool_result_from_messages = MagicMock(
        side_effect=lambda msgs, keep: [dict(m) for m in msgs]
    )
    return client


def test_is_timeout_error_detects_openai_and_httpx_names():
    assert OpenAIClient._is_timeout_error(TimeoutError("boom"))
    assert OpenAIClient._is_timeout_error(
        type("APITimeoutError", (Exception,), {})("timed out")
    )
    assert OpenAIClient._is_timeout_error(Exception("Request timed out"))
    assert not OpenAIClient._is_timeout_error(Exception("rate limited"))


@pytest.mark.asyncio
async def test_timeout_fail_fast_degrades_once_then_raises():
    client = _make_client()
    calls = {"n": 0}

    class _FakeAPITimeout(Exception):
        pass

    async def _boom(**_kwargs):
        calls["n"] += 1
        raise _FakeAPITimeout("Request timed out after 90s")

    client.async_client = True
    client.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_boom))
    )
    client.convert_tool_definition_to_tool_call = MagicMock(return_value=[])

    with pytest.raises(_FakeAPITimeout):
        await client._create_message(
            system_prompt="sys",
            messages_history=[
                {"role": "user", "content": "q"},
                {"role": "tool", "content": "x" * 5000, "tool_call_id": "1"},
            ],
            tools_definitions=[],
            keep_tool_result=5,
            agent_type="main",
        )

    assert calls["n"] == 2  # original + one degrade retry
    assert client.task_log.run_metrics.timeout_count == 2
    assert client.task_log.run_metrics.http_timeout_count == 2
    assert client.task_log.run_metrics.llm_retry_count == 1
    client._remove_tool_result_from_messages.assert_called()


@pytest.mark.asyncio
async def test_timeout_fail_fast_disabled_uses_normal_retries():
    client = _make_client(timeout_fail_fast=False, max_retries=3)
    calls = {"n": 0}

    async def _boom(**_kwargs):
        calls["n"] += 1
        raise TimeoutError("asyncio timeout")

    client.async_client = True
    client.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_boom))
    )

    with pytest.raises(TimeoutError):
        await client._create_message(
            system_prompt="sys",
            messages_history=[{"role": "user", "content": "q"}],
            tools_definitions=[],
            keep_tool_result=5,
            agent_type="main",
        )

    assert calls["n"] == 3
    assert client.task_log.run_metrics.timeout_count == 3
