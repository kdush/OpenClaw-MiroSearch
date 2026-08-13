# Copyright (c) 2025 MiroMind
# This source code is licensed under the Apache 2.0 License.

"""Anthropic 分阶段模型与令牌预算路由回归测试。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from omegaconf import OmegaConf

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.llm.base_client import BaseClient  # noqa: E402
from src.llm.providers.anthropic_client import AnthropicClient  # noqa: E402
from src.llm.providers.openai_client import OpenAIClient  # noqa: E402


def _make_cfg(
    *,
    async_client: bool,
    max_tokens: int = 1_000,
) -> Any:
    """构造可实例化 Anthropic 客户端的最小配置。"""
    llm_config = {
        "provider": "anthropic",
        "model_name": "base-model",
        "model_tool_name": "tool-model",
        "model_fast_name": "fast-model",
        "model_thinking_name": "thinking-model",
        "model_summary_name": "summary-model",
        "temperature": 0.3,
        "top_p": 1.0,
        "min_p": 0.0,
        "top_k": -1,
        "max_context_length": 8_192,
        "max_tokens": max_tokens,
        "summary_max_tokens": 600,
        "verification_max_tokens": 400,
        "async_client": async_client,
        "api_key": "test-key",
        "base_url": "https://example.invalid",
    }
    return OmegaConf.create(
        {
            "llm": llm_config,
            "agent": {"keep_tool_result": -1},
        }
    )


def _make_task_log() -> MagicMock:
    """构造仅记录调用、不产生真实日志的 TaskLog。"""
    task_log = MagicMock()
    task_log.log_step = MagicMock()
    task_log.record_stage_timing = MagicMock()
    task_log.run_metrics = MagicMock()
    task_log.run_metrics.record_model_route = MagicMock()
    return task_log


def _make_response(
    model: Optional[str] = "provider-response-model",
) -> SimpleNamespace:
    """构造 Anthropic SDK 成功响应。"""
    response = SimpleNamespace(usage=None, stop_reason="end_turn")
    if model is not None:
        response.model = model
    return response


def _make_sdk_client(
    async_client: bool,
    *,
    response_model: Optional[str] = "provider-response-model",
) -> tuple[MagicMock, Any]:
    """构造同步或异步 Anthropic SDK 调用捕获器。"""
    create = (
        AsyncMock(return_value=_make_response(response_model))
        if async_client
        else MagicMock(return_value=_make_response(response_model))
    )
    sdk_client = MagicMock()
    sdk_client.messages.create = create
    return sdk_client, create


@pytest.mark.parametrize("async_client", [True, False], ids=["async", "sync"])
@pytest.mark.parametrize(
    ("agent_type", "tools", "expected_model", "expected_max_tokens"),
    [
        ("verification", [], "thinking-model", 400),
        ("failure_summary", [], "fast-model", 600),
        ("final_summary", [], "summary-model", 600),
        ("main", [{"name": "search", "tools": []}], "tool-model", 1_000),
        ("main", [], "thinking-model", 1_000),
    ],
)
@pytest.mark.asyncio
async def test_stage_routes_model_and_token_cap_for_both_sdk_paths(
    async_client: bool,
    agent_type: str,
    tools: list[dict[str, Any]],
    expected_model: str,
    expected_max_tokens: int,
) -> None:
    """同步与异步 SDK 都必须收到相同的分阶段模型和令牌上限。"""
    sdk_client, create = _make_sdk_client(async_client)
    with patch.object(AnthropicClient, "_create_client", return_value=sdk_client):
        client = AnthropicClient(
            task_id=f"{agent_type}-{async_client}",
            cfg=_make_cfg(async_client=async_client),
            task_log=_make_task_log(),
        )

    await client._create_message(
        "系统指令",
        [{"role": "user", "content": "测试请求"}],
        tools,
        agent_type=agent_type,
    )

    params = create.call_args.kwargs
    assert params["model"] == expected_model
    assert params["max_tokens"] == expected_max_tokens
    assert "extra_body" not in params


@pytest.mark.parametrize(
    ("agent_type", "expected_model"),
    [
        ("verification", "thinking-model"),
        ("failure_summary", "fast-model"),
        ("final_summary", "summary-model"),
    ],
)
@pytest.mark.asyncio
async def test_stage_cap_never_raises_mode_global_budget(
    agent_type: str,
    expected_model: str,
) -> None:
    """compact/quota 等模式给出的全局硬上限不能被阶段配置抬高。"""
    sdk_client, create = _make_sdk_client(async_client=True)
    with patch.object(AnthropicClient, "_create_client", return_value=sdk_client):
        client = AnthropicClient(
            task_id=f"hard-budget-{agent_type}",
            cfg=_make_cfg(async_client=True, max_tokens=128),
            task_log=_make_task_log(),
        )

    await client._create_message(
        "",
        [{"role": "user", "content": "测试硬预算"}],
        [],
        agent_type=agent_type,
    )

    params = create.await_args.kwargs
    assert params["model"] == expected_model
    assert params["max_tokens"] == 128


def test_stage_configuration_prefers_cfg_over_environment(monkeypatch) -> None:
    """显式 Hydra 配置必须覆盖同名环境变量。"""
    monkeypatch.setenv("MODEL_TOOL_NAME", "env-tool")
    monkeypatch.setenv("MODEL_FAST_NAME", "env-fast")
    monkeypatch.setenv("MODEL_THINKING_NAME", "env-thinking")
    monkeypatch.setenv("MODEL_SUMMARY_NAME", "env-summary")
    monkeypatch.setenv("LLM_SUMMARY_MAX_TOKENS", "60")
    monkeypatch.setenv("LLM_VERIFICATION_MAX_TOKENS", "40")

    with patch.object(AnthropicClient, "_create_client", return_value=MagicMock()):
        client = AnthropicClient(
            task_id="configured-routing",
            cfg=_make_cfg(async_client=True),
            task_log=_make_task_log(),
        )

    assert client.model_tool_name == "tool-model"
    assert client.model_fast_name == "fast-model"
    assert client.model_thinking_name == "thinking-model"
    assert client.model_summary_name == "summary-model"
    assert client.summary_max_tokens == 600
    assert client.verification_max_tokens == 400


def test_stage_configuration_falls_back_to_environment(monkeypatch) -> None:
    """缺少 Hydra 字段时应使用与 OpenAI 客户端一致的环境变量。"""
    monkeypatch.setenv("MODEL_TOOL_NAME", "env-tool")
    monkeypatch.setenv("MODEL_FAST_NAME", "env-fast")
    monkeypatch.setenv("MODEL_THINKING_NAME", "env-thinking")
    monkeypatch.setenv("MODEL_SUMMARY_NAME", "env-summary")
    monkeypatch.setenv("LLM_SUMMARY_MAX_TOKENS", "60")
    monkeypatch.setenv("LLM_VERIFICATION_MAX_TOKENS", "40")

    cfg = _make_cfg(async_client=True)
    for field_name in (
        "model_tool_name",
        "model_fast_name",
        "model_thinking_name",
        "model_summary_name",
        "summary_max_tokens",
        "verification_max_tokens",
    ):
        del cfg.llm[field_name]

    with patch.object(AnthropicClient, "_create_client", return_value=MagicMock()):
        client = AnthropicClient(
            task_id="environment-routing",
            cfg=cfg,
            task_log=_make_task_log(),
        )

    assert client.model_tool_name == "env-tool"
    assert client.model_fast_name == "env-fast"
    assert client.model_thinking_name == "env-thinking"
    assert client.model_summary_name == "env-summary"
    assert client.summary_max_tokens == 60
    assert client.verification_max_tokens == 40


def test_empty_model_environment_values_fall_back_to_safe_models(
    monkeypatch,
) -> None:
    """空白 MODEL_* 不能覆盖安全的基础模型回退值。"""
    for env_name in (
        "MODEL_TOOL_NAME",
        "MODEL_FAST_NAME",
        "MODEL_THINKING_NAME",
        "MODEL_SUMMARY_NAME",
    ):
        monkeypatch.setenv(env_name, "   ")

    cfg = _make_cfg(async_client=True)
    for field_name in (
        "model_tool_name",
        "model_fast_name",
        "model_thinking_name",
        "model_summary_name",
    ):
        del cfg.llm[field_name]

    with patch.object(AnthropicClient, "_create_client", return_value=MagicMock()):
        client = AnthropicClient(
            task_id="empty-model-environment",
            cfg=cfg,
            task_log=_make_task_log(),
        )

    assert client.model_tool_name == "base-model"
    assert client.model_fast_name == "base-model"
    assert client.model_thinking_name == "base-model"
    assert client.model_summary_name == "base-model"


@pytest.mark.parametrize("invalid_value", ["", "not-an-int", "1.5", "0", "-9"])
@pytest.mark.asyncio
async def test_invalid_token_environment_values_use_positive_defaults(
    monkeypatch,
    invalid_value: str,
) -> None:
    """非法阶段 token 环境变量应回退，且 SDK 只能收到正整数。"""
    monkeypatch.setenv("LLM_SUMMARY_MAX_TOKENS", invalid_value)
    monkeypatch.setenv("LLM_VERIFICATION_MAX_TOKENS", invalid_value)
    cfg = _make_cfg(async_client=True, max_tokens=4_096)
    del cfg.llm["summary_max_tokens"]
    del cfg.llm["verification_max_tokens"]
    sdk_client, create = _make_sdk_client(async_client=True)

    with patch.object(AnthropicClient, "_create_client", return_value=sdk_client):
        client = AnthropicClient(
            task_id=f"invalid-token-environment-{invalid_value}",
            cfg=cfg,
            task_log=_make_task_log(),
        )

    await client._create_message(
        "",
        [{"role": "user", "content": "总结"}],
        [],
        agent_type="final_summary",
    )
    await client._create_message(
        "",
        [{"role": "user", "content": "校验"}],
        [],
        agent_type="verification",
    )

    assert client.summary_max_tokens == 3_072
    assert client.verification_max_tokens == 2_048
    assert [
        current_call.kwargs["max_tokens"] for current_call in create.await_args_list
    ] == [3_072, 2_048]


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("model_tool_name", ""),
        ("model_fast_name", "   "),
        ("model_thinking_name", None),
        ("model_summary_name", 42),
        ("summary_max_tokens", "not-an-int"),
        ("summary_max_tokens", 0),
        ("summary_max_tokens", -1),
        ("verification_max_tokens", 1.5),
        ("verification_max_tokens", True),
        ("verification_max_tokens", 0),
        ("verification_max_tokens", -1),
    ],
)
def test_invalid_explicit_stage_configuration_fails_fast(
    field_name: str,
    invalid_value: Any,
) -> None:
    """显式 cfg 非法时应在初始化阶段给出包含字段名的错误。"""
    cfg = _make_cfg(async_client=True)
    cfg.llm[field_name] = invalid_value

    with (
        patch.object(AnthropicClient, "_create_client", return_value=MagicMock()),
        pytest.raises(ValueError, match=rf"llm\.{field_name}"),
    ):
        AnthropicClient(
            task_id=f"invalid-config-{field_name}",
            cfg=cfg,
            task_log=_make_task_log(),
        )


@pytest.mark.parametrize(
    ("async_client", "response_model", "expected_response_model"),
    [
        (True, "claude-routed-20260731", "claude-routed-20260731"),
        (False, None, "N/A"),
    ],
    ids=["async-reported-model", "sync-missing-model"],
)
@pytest.mark.asyncio
async def test_route_observability_records_model_and_request_timing(
    async_client: bool,
    response_model: Optional[str],
    expected_response_model: str,
) -> None:
    """同步与异步成功请求都要记录真实模型路由和阶段耗时。"""
    sdk_client, _ = _make_sdk_client(
        async_client,
        response_model=response_model,
    )
    task_log = _make_task_log()
    tools = [
        {
            "name": "search",
            "tools": [{"name": "query", "description": "", "schema": {}}],
        }
    ]

    with (
        patch.object(AnthropicClient, "_create_client", return_value=sdk_client),
        patch("time.perf_counter", side_effect=[1.0, 1.125]),
    ):
        client = AnthropicClient(
            task_id=f"route-observability-{async_client}",
            cfg=_make_cfg(async_client=async_client),
            task_log=task_log,
        )
        await client._create_message(
            "",
            [{"role": "user", "content": "检索请求"}],
            tools,
            agent_type="main",
        )

    task_log.run_metrics.record_model_route.assert_called_once_with(
        "tool-model",
        expected_response_model,
    )
    assert (
        call(
            "info",
            "LLM | Model Route",
            (
                "agent_type=main, requested=tool-model, "
                f"responded={expected_response_model}"
            ),
        )
        in task_log.log_step.call_args_list
    )
    task_log.record_stage_timing.assert_called_once_with(
        "llm.request.main",
        125,
        message="LLM request completed in 125ms",
        metadata={
            "agent_type": "main",
            "requested_model": "tool-model",
            "responded_model": expected_response_model,
            "tool_count": 1,
            "message_count": 1,
        },
    )


def test_openai_and_anthropic_share_stage_routing_implementation() -> None:
    """两家 provider 应继承同一套路由和预算实现，避免规则漂移。"""
    assert OpenAIClient._resolve_model_name is BaseClient._resolve_model_name
    assert AnthropicClient._resolve_model_name is BaseClient._resolve_model_name
    assert (
        OpenAIClient._resolve_stage_max_tokens is BaseClient._resolve_stage_max_tokens
    )
    assert (
        AnthropicClient._resolve_stage_max_tokens
        is BaseClient._resolve_stage_max_tokens
    )
