# Copyright (c) 2025 MiroMind
# This source code is licensed under the Apache 2.0 License.

"""AnswerGenerator.handle_llm_call 总墙时超时回归测试。

防止回退场景：openai SDK / httpx 在异常网络条件下静默死锁，
导致 main agent loop 在工具调用结束后无任何错误日志地永久卡死。

契约：
- 当底层 LLM 调用超过 LLM_CALL_WALL_TIMEOUT_SECONDS 时，
  handle_llm_call 必须返回 ("", False, None, original_history)，
  让上层 main loop 累计 consecutive_llm_failures 并允许触发 failback。
- 必须打印 "LLM Wall Timeout" 错误日志，便于后续定位。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from omegaconf import OmegaConf

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.answer_generator import AnswerGenerator  # noqa: E402


class _StallingLLMClient:
    """模拟一个永远不返回的 LLM 客户端，复现 SDK 静默卡死。"""

    async def create_message(self, *args: Any, **kwargs: Any):
        await asyncio.sleep(60)
        return None, []


def _make_answer_generator() -> AnswerGenerator:
    """绕过完整 __init__，仅注入 handle_llm_call 实际依赖的属性。"""
    cfg = OmegaConf.create({"agent": {"keep_tool_result": -1}})
    gen = AnswerGenerator.__new__(AnswerGenerator)
    gen.llm_client = _StallingLLMClient()
    gen.cfg = cfg
    gen.task_log = MagicMock()
    gen.stream = MagicMock()
    gen.output_formatter = MagicMock()
    gen.intermediate_boxed_answers = []
    return gen


@pytest.mark.asyncio
async def test_handle_llm_call_returns_failure_on_wall_timeout(monkeypatch):
    """LLM 调用超过墙时间应返回失败结果且保留原 history。"""
    monkeypatch.setenv("LLM_CALL_WALL_TIMEOUT_SECONDS", "0.05")

    gen = _make_answer_generator()
    original_history = [{"role": "user", "content": "hello"}]

    response_text, should_break, tool_calls, returned_history = await gen.handle_llm_call(
        system_prompt="sys",
        message_history=original_history,
        tool_definitions=[],
        step_id=1,
        purpose="Test agent | Turn: 2",
    )

    assert response_text == ""
    assert should_break is False
    assert tool_calls is None
    assert returned_history is original_history


@pytest.mark.asyncio
async def test_handle_llm_call_emits_wall_timeout_log(monkeypatch):
    """墙时间超时必须记录可观测的错误日志，方便日后排查。"""
    monkeypatch.setenv("LLM_CALL_WALL_TIMEOUT_SECONDS", "0.05")

    gen = _make_answer_generator()
    original_history = [{"role": "user", "content": "hello"}]

    await gen.handle_llm_call(
        system_prompt="sys",
        message_history=original_history,
        tool_definitions=[],
        step_id=1,
        purpose="Test agent | Turn: 2",
    )

    log_steps = [call.args for call in gen.task_log.log_step.call_args_list]
    assert any(
        len(args) >= 2 and "LLM Wall Timeout" in str(args[1]) for args in log_steps
    ), f"expected LLM Wall Timeout log, got: {log_steps}"


@pytest.mark.asyncio
async def test_handle_llm_call_emits_start_log(monkeypatch):
    """每次 LLM 调用入口必须打印 LLM Call Start，避免后续静默卡死场景再次难以定位。"""
    monkeypatch.setenv("LLM_CALL_WALL_TIMEOUT_SECONDS", "0.05")

    gen = _make_answer_generator()
    original_history = [{"role": "user", "content": "hello"}]

    await gen.handle_llm_call(
        system_prompt="sys",
        message_history=original_history,
        tool_definitions=[],
        step_id=1,
        purpose="Test agent | Turn: 2",
    )

    log_steps = [call.args for call in gen.task_log.log_step.call_args_list]
    assert any(
        len(args) >= 2 and "LLM Call Start" in str(args[1]) for args in log_steps
    ), f"expected LLM Call Start log, got: {log_steps}"
