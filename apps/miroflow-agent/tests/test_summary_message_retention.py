# Copyright (c) 2025 MiroMind
# This source code is licensed under the Apache 2.0 License.

"""最终总结消息与真实工具结果保留规则的回归测试。"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from omegaconf import OmegaConf

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.answer_generator import AnswerGenerator  # noqa: E402
from src.core.orchestrator import Orchestrator  # noqa: E402
from src.llm.base_client import (  # noqa: E402
    INTERNAL_MESSAGE_TYPE_KEY,
    OMITTED_TOOL_RESULT_TEXT,
    TOOL_RESULT_MESSAGE_TYPE,
    BaseClient,
)
from src.llm.providers.anthropic_client import AnthropicClient  # noqa: E402
from src.llm.providers.openai_client import OpenAIClient  # noqa: E402


def _tool_result_message(content: Any) -> dict[str, Any]:
    """构造带内部类型标记的真实工具结果消息。"""
    return {
        "role": "user",
        "content": content,
        INTERNAL_MESSAGE_TYPE_KEY: TOOL_RESULT_MESSAGE_TYPE,
    }


def _make_base_client() -> BaseClient:
    """绕过完整初始化，仅注入消息过滤所需依赖。"""
    client = object.__new__(BaseClient)
    client.task_log = MagicMock()
    return client


def _make_real_summary_context_client(client_type):
    """构造会真实执行 provider 上下文裁剪逻辑的最小客户端。"""

    class LocalEncoding:
        """避免测试依赖 tiktoken 首次运行时联网下载词表。"""

        @staticmethod
        def encode(text: str) -> list[str]:
            return list(text)

    client = object.__new__(client_type)
    client.task_log = MagicMock()
    client.encoding = LocalEncoding()
    client.last_call_tokens = {
        "prompt_tokens": 100,
        "completion_tokens": 100,
        "input_tokens": 100,
        "output_tokens": 100,
    }
    client.max_tokens = 100
    client.max_context_length = 1
    return client


@pytest.mark.parametrize("client_type", [OpenAIClient, AnthropicClient])
def test_provider_context_trimming_preserves_final_assistant_answer(
    client_type,
) -> None:
    """超限时只能裁剪更早工具调用对，不能删除末尾直接回答。"""
    client = _make_real_summary_context_client(client_type)
    history = [
        {"role": "user", "content": "原始任务"},
        {"role": "assistant", "content": "准备调用工具"},
        _tool_result_message("工具结果"),
        {"role": "assistant", "content": "主模型最后的直接回答"},
    ]

    _, trimmed = client.ensure_summary_context(
        copy.deepcopy(history),
        "最终总结指令",
    )

    assert trimmed == [history[0], history[-1]]


@pytest.mark.parametrize("client_type", [OpenAIClient, AnthropicClient])
def test_provider_context_trimming_keeps_history_without_tool_result_pair(
    client_type,
) -> None:
    """没有可裁剪工具结果对时，原始任务和末尾回答必须全部保留。"""
    client = _make_real_summary_context_client(client_type)
    history = [
        {"role": "user", "content": "原始任务"},
        {"role": "assistant", "content": "主模型最后的直接回答"},
    ]

    _, retained = client.ensure_summary_context(
        copy.deepcopy(history),
        "最终总结指令",
    )

    assert retained == history


@pytest.mark.parametrize(
    ("keep_tool_result", "expected_tool_contents"),
    [
        (0, [OMITTED_TOOL_RESULT_TEXT, OMITTED_TOOL_RESULT_TEXT]),
        (1, [OMITTED_TOOL_RESULT_TEXT, "工具结果二"]),
        (2, ["工具结果一", "工具结果二"]),
        (-1, ["工具结果一", "工具结果二"]),
    ],
)
def test_retention_only_omits_marked_tool_results(
    keep_tool_result: int,
    expected_tool_contents: list[str],
) -> None:
    """保留数量只计算真实工具结果，普通 user 指令必须原样保留。"""
    client = _make_base_client()
    history = [
        {"role": "user", "content": "原始任务"},
        {"role": "assistant", "content": "准备调用工具"},
        _tool_result_message("工具结果一"),
        {"role": "assistant", "content": "继续调用工具"},
        _tool_result_message("工具结果二"),
        {"role": "user", "content": "请基于全部证据生成最终总结"},
        {"role": "user", "content": "请修复最终答案格式"},
    ]

    filtered = client._remove_tool_result_from_messages(history, keep_tool_result)

    assert [filtered[2]["content"], filtered[4]["content"]] == expected_tool_contents
    assert filtered[5]["content"] == "请基于全部证据生成最终总结"
    assert filtered[6]["content"] == "请修复最终答案格式"
    assert all(INTERNAL_MESSAGE_TYPE_KEY not in message for message in filtered)
    assert history[2][INTERNAL_MESSAGE_TYPE_KEY] == TOOL_RESULT_MESSAGE_TYPE
    assert history[4][INTERNAL_MESSAGE_TYPE_KEY] == TOOL_RESULT_MESSAGE_TYPE


def test_openai_update_message_history_marks_tool_result() -> None:
    """OpenAI 格式写入历史时应显式标记真实工具结果。"""
    client = object.__new__(OpenAIClient)
    client.tool_result_max_chars = 4_000
    history: list[dict[str, Any]] = []

    updated = client.update_message_history(
        history,
        [("call-1", {"type": "text", "text": "OpenAI 工具结果"})],
    )

    assert updated is history
    assert history[-1][INTERNAL_MESSAGE_TYPE_KEY] == TOOL_RESULT_MESSAGE_TYPE


def test_anthropic_update_message_history_marks_tool_result() -> None:
    """Anthropic 格式写入历史时应显式标记真实工具结果。"""
    client = object.__new__(AnthropicClient)
    history: list[dict[str, Any]] = []

    updated = client.update_message_history(
        history,
        [("call-1", {"type": "text", "text": "Anthropic 工具结果"})],
    )

    assert updated is history
    assert history[-1][INTERNAL_MESSAGE_TYPE_KEY] == TOOL_RESULT_MESSAGE_TYPE


def _configure_openai_client_for_sdk_capture() -> tuple[OpenAIClient, AsyncMock]:
    """构造只执行真实发送路径的 OpenAI 客户端。"""
    response = SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="stop", text="")],
        usage=None,
        model="test-model",
    )
    create = AsyncMock(return_value=response)
    sdk_client = MagicMock()
    sdk_client.chat.completions.create = create

    client = object.__new__(OpenAIClient)
    client.task_log = MagicMock()
    client.client = sdk_client
    client.async_client = True
    client.model_name = "test-model"
    client.model_tool_name = "test-model"
    client.model_fast_name = "test-model"
    client.model_thinking_name = "test-model"
    client.model_summary_name = "test-model"
    client.temperature = 0.0
    client.top_p = 1.0
    client.max_tokens = 128
    client.summary_max_tokens = 128
    client.verification_max_tokens = 128
    client.max_retries = 1
    client.retry_wait_seconds = 0.0
    client.repetition_penalty = 1.0
    return client, create


@pytest.mark.asyncio
async def test_openai_sdk_receives_copy_without_internal_marker() -> None:
    """OpenAI SDK 只能收到去除内部标记的副本。"""
    client, create = _configure_openai_client_for_sdk_capture()
    tool_result = _tool_result_message("OpenAI 工具结果")
    history = [
        {"role": "user", "content": "原始任务"},
        {"role": "assistant", "content": "已调用工具"},
        tool_result,
        {"role": "user", "content": "最终总结指令"},
    ]

    _, returned_history = await client._create_message(
        "",
        history,
        [],
        keep_tool_result=-1,
    )

    sent_messages = create.await_args.kwargs["messages"]
    assert all(INTERNAL_MESSAGE_TYPE_KEY not in message for message in sent_messages)
    assert returned_history is history
    assert tool_result[INTERNAL_MESSAGE_TYPE_KEY] == TOOL_RESULT_MESSAGE_TYPE


def _configure_anthropic_client_for_sdk_capture() -> tuple[AnthropicClient, AsyncMock]:
    """构造只执行真实发送路径的 Anthropic 客户端。"""
    response = SimpleNamespace(usage=None, stop_reason="end_turn")
    create = AsyncMock(return_value=response)
    sdk_client = MagicMock()
    sdk_client.messages.create = create

    client = object.__new__(AnthropicClient)
    client.task_log = MagicMock()
    client.client = sdk_client
    client.async_client = True
    client.model_name = "test-model"
    client.model_tool_name = "test-model"
    client.model_fast_name = "test-model"
    client.model_thinking_name = "test-model"
    client.model_summary_name = "test-model"
    client.temperature = 0.0
    client.top_p = 1.0
    client.top_k = -1
    client.max_tokens = 128
    client.summary_max_tokens = 128
    client.verification_max_tokens = 128
    return client, create


@pytest.mark.asyncio
async def test_anthropic_sdk_receives_copy_without_internal_marker() -> None:
    """Anthropic SDK 只能收到去除内部标记的副本。"""
    client, create = _configure_anthropic_client_for_sdk_capture()
    tool_result = _tool_result_message([{"type": "text", "text": "Anthropic 工具结果"}])
    history = [
        {"role": "user", "content": [{"type": "text", "text": "原始任务"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "已调用工具"}]},
        tool_result,
        {"role": "user", "content": [{"type": "text", "text": "最终总结指令"}]},
    ]

    _, returned_history = await client._create_message(
        "",
        history,
        [],
        keep_tool_result=-1,
    )

    sent_messages = create.await_args.kwargs["messages"]
    assert all(INTERNAL_MESSAGE_TYPE_KEY not in message for message in sent_messages)
    assert returned_history is history
    assert tool_result[INTERNAL_MESSAGE_TYPE_KEY] == TOOL_RESULT_MESSAGE_TYPE


@pytest.mark.asyncio
async def test_final_summary_keeps_last_user_tool_result() -> None:
    """追加最终总结指令前不得无条件删除最后一条 user 工具结果。"""
    generator = AnswerGenerator.__new__(AnswerGenerator)
    generator.verification_enabled = False
    generator.verification_use_high_model = False
    generator.max_final_answer_retries = 1
    generator.task_log = MagicMock()
    generator.output_formatter = MagicMock()
    generator._build_main_summary_prompt = MagicMock(return_value="最终总结指令")
    generator._emit_stage_heartbeat = AsyncMock()
    captured_history: list[dict[str, Any]] = []

    async def _capture_llm_call(
        system_prompt: str,
        message_history: list[dict[str, Any]],
        tool_definitions: list[dict[str, Any]],
        step_id: int,
        purpose: str,
        agent_type: str = "main",
    ) -> tuple[str, bool, None, list[dict[str, Any]]]:
        del system_prompt, tool_definitions, step_id, purpose, agent_type
        captured_history.extend(message.copy() for message in message_history)
        return "", False, None, message_history

    generator.handle_llm_call = _capture_llm_call
    tool_result = _tool_result_message("最后一条工具结果")
    history = [
        {"role": "user", "content": "原始任务"},
        {"role": "assistant", "content": "已调用工具"},
        tool_result,
    ]

    await generator.generate_final_answer_with_retries(
        system_prompt="系统指令",
        message_history=history,
        tool_definitions=[],
        turn_count=2,
        task_description="原始任务",
    )

    assert captured_history[-2]["content"] == "最后一条工具结果"
    assert captured_history[-2][INTERNAL_MESSAGE_TYPE_KEY] == TOOL_RESULT_MESSAGE_TYPE
    assert captured_history[-1] == {"role": "user", "content": "最终总结指令"}


@pytest.mark.asyncio
async def test_sub_agent_summary_keeps_latest_tool_evidence_and_disables_tools() -> (
    None
):
    """子代理总结必须保留最后工具证据，并使用无工具的 summary 阶段路由。"""
    sub_agent_name = "agent-browsing"
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.task_log = MagicMock()
    orchestrator.task_log.current_sub_agent_session_id = "session-1"
    orchestrator.task_log.sub_agent_message_history_sessions = {}
    orchestrator.stream = SimpleNamespace(
        start_agent=AsyncMock(return_value="sub-agent-id"),
        start_llm=AsyncMock(),
        tool_call=AsyncMock(return_value="stream-tool-id"),
        end_llm=AsyncMock(),
        end_agent=AsyncMock(),
    )
    orchestrator.sub_agent_tool_definitions = {
        sub_agent_name: [{"name": "search", "description": "检索"}]
    }
    orchestrator.sub_agent_tool_managers = {
        sub_agent_name: SimpleNamespace(
            execute_tool_call=AsyncMock(
                return_value={
                    "server_name": "search-server",
                    "tool_name": "search",
                    "result": "最新工具证据",
                }
            )
        )
    }
    orchestrator.llm_client = MagicMock()
    orchestrator.llm_client.generate_agent_system_prompt.return_value = "系统指令"
    orchestrator.llm_client.ensure_summary_context.side_effect = (
        lambda history, _prompt: (True, history)
    )

    def update_message_history(history, _results):
        return [*history, _tool_result_message("最新工具证据")]

    orchestrator.llm_client.update_message_history.side_effect = update_message_history
    orchestrator.output_formatter = MagicMock()
    orchestrator.output_formatter.format_tool_result_for_user.return_value = {
        "type": "text",
        "text": "最新工具证据",
    }
    orchestrator.tool_executor = MagicMock()
    orchestrator.tool_executor.fix_tool_call_arguments.return_value = {"query": "测试"}
    orchestrator.tool_executor.post_process_tool_call_result.side_effect = (
        lambda _tool_name, result: result
    )
    orchestrator.tool_executor.should_rollback_result.return_value = False
    orchestrator.tool_executor.get_query_str_from_tool_call.return_value = None
    orchestrator.used_queries = {}
    orchestrator.cfg = OmegaConf.create(
        {
            "agent": {
                "sub_agents": {
                    sub_agent_name: {
                        "max_turns": 1,
                    }
                }
            }
        }
    )
    orchestrator.MAX_CONSECUTIVE_ROLLBACKS = 5
    orchestrator.max_consecutive_llm_failures = 2
    orchestrator.llm_failure_sleep_seconds = 0

    summary_call = {}
    call_count = 0

    async def fake_llm_call(
        system_prompt,
        message_history,
        tool_definitions,
        step_id,
        purpose,
        agent_type="main",
    ):
        nonlocal call_count
        del system_prompt, step_id, purpose
        call_count += 1
        if call_count == 1:
            return (
                "",
                False,
                [
                    {
                        "server_name": "search-server",
                        "tool_name": "search",
                        "arguments": {"query": "测试"},
                        "id": "tool-call-1",
                    }
                ],
                [
                    *message_history,
                    {"role": "assistant", "content": "准备检索"},
                ],
            )
        summary_call.update(
            {
                "history": copy.deepcopy(message_history),
                "tool_definitions": tool_definitions,
                "agent_type": agent_type,
            }
        )
        return "子代理总结", False, None, message_history

    orchestrator.answer_generator = SimpleNamespace(
        handle_llm_call=fake_llm_call,
    )

    result = await orchestrator.run_sub_agent(
        sub_agent_name,
        "调查一个问题",
    )

    assert result == "子代理总结"
    assert summary_call["history"][-2][INTERNAL_MESSAGE_TYPE_KEY] == (
        TOOL_RESULT_MESSAGE_TYPE
    )
    assert summary_call["history"][-2]["content"] == "最新工具证据"
    assert summary_call["history"][-1]["role"] == "user"
    assert summary_call["tool_definitions"] == []
    assert summary_call["agent_type"] == "final_summary"
