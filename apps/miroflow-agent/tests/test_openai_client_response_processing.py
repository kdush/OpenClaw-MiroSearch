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

_openai_client_module = importlib.import_module("src.llm.providers.openai_client")
OpenAIClient = _openai_client_module.OpenAIClient
is_summary_or_fast_stage = _openai_client_module.is_summary_or_fast_stage
is_unsupported_request_param_error = (
    _openai_client_module.is_unsupported_request_param_error
)


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


class TestThinkingOptOutAdaptation:
    """「关闭思考」是可选加速参数，不支持它的模型不该让报告生成失败。

    回归来源：某模型作为 summary 模型时返回 400（不接受关闭思考参数），而
    summary 阶段 max_retries=1 不重试，导致 final_summary 必然失败、整份报告
    出不来，下游（如 M3 核验）永远没有执行机会。

    修复保持提供商中立：不判断模型是谁，只在下发后被拒时剥离该参数重试一次。
    """

    def test_stage_classification_is_model_agnostic(self):
        assert is_summary_or_fast_stage("final_summary") is True
        assert is_summary_or_fast_stage("failure_summary") is True
        assert is_summary_or_fast_stage("main") is False
        assert is_summary_or_fast_stage("verification") is False

    def test_param_rejection_detection_covers_any_provider(self):
        """只看 400 状态码，不匹配任何提供商文案或模型名。"""
        assert (
            is_unsupported_request_param_error(
                Exception("Error code: 400 - {'error': {'code': '1210'}}")
            )
            is True
        )
        assert (
            is_unsupported_request_param_error(
                Exception("Error code: 400 - unsupported parameter: thinking")
            )
            is True
        )

    def test_context_length_400_is_not_treated_as_param_rejection(self):
        """上下文超长有专门路径，不能被这里抢走。"""
        assert (
            is_unsupported_request_param_error(
                Exception("Error code: 400 - prompt is longer than the model context")
            )
            is False
        )

    def test_other_status_codes_are_not_param_rejection(self):
        for text in ("Error code: 429", "Error code: 500", "Error code: 401", "boom"):
            assert is_unsupported_request_param_error(Exception(text)) is False


@pytest.mark.asyncio
async def test_param_rejection_strips_thinking_and_retries_without_spending_retry():
    """核心回归：summary 阶段 max_retries=1，仍必须拿到第二次机会。

    第一次带 thinking 被 400 拒；剥离后第二次成功。若降级逻辑消耗了
    max_retries，这里就会直接抛错、报告出不来。
    """
    mock_chat = MagicMock()
    mock_chat.completions.create = AsyncMock(
        side_effect=[
            Exception("Error code: 400 - unsupported parameter: thinking"),
            _make_success_response(),
        ]
    )
    mock_client = MagicMock()
    mock_client.chat = mock_chat

    cfg = _make_minimal_cfg()

    with patch.object(OpenAIClient, "_create_client", return_value=mock_client):
        client = OpenAIClient(
            task_id="thinking-optout-retry-test",
            cfg=cfg,
            task_log=_make_task_log(),
        )
        await client._create_message(
            "",
            [{"role": "user", "content": "总结：1+1"}],
            [],
            agent_type="final_summary",
        )

    assert mock_chat.completions.create.await_count == 2
    calls = mock_chat.completions.create.await_args_list
    assert calls[0].kwargs["extra_body"]["thinking"] == {"type": "disabled"}
    # 第二次必须已经剥离，且与模型名无关
    assert "thinking" not in calls[1].kwargs["extra_body"]
    assert "reasoning" not in calls[1].kwargs["extra_body"]


@pytest.mark.asyncio
async def test_model_that_accepts_opt_out_keeps_the_speedup():
    """支持该参数的模型仍保留加速——降级只在被拒时发生，不影响正常路径。"""
    mock_chat = MagicMock()
    mock_chat.completions.create = AsyncMock(return_value=_make_success_response())
    mock_client = MagicMock()
    mock_client.chat = mock_chat

    cfg = _make_minimal_cfg()

    with patch.object(OpenAIClient, "_create_client", return_value=mock_client):
        client = OpenAIClient(
            task_id="thinking-optout-kept-test",
            cfg=cfg,
            task_log=_make_task_log(),
        )
        await client._create_message(
            "",
            [{"role": "user", "content": "总结：1+1"}],
            [],
            agent_type="final_summary",
        )

    assert mock_chat.completions.create.await_count == 1
    params = mock_chat.completions.create.call_args.kwargs
    assert params["extra_body"]["thinking"] == {"type": "disabled"}
    assert params["extra_body"]["reasoning"] == {"effort": "none", "exclude": True}
