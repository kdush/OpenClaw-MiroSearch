"""最终总结重试与结构化结果质量测试。"""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from omegaconf import OmegaConf

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.answer_generator import (  # noqa: E402
    DEFAULT_MAX_FINAL_ANSWER_RETRIES,
    AnswerGenerator,
)
from src.io.output_formatter import OutputFormatter  # noqa: E402


def test_invalid_summary_length_environment_value_falls_back_safely():
    """无效字数阈值不应导致总结模块在服务启动时导入失败。"""
    env = os.environ.copy()
    env["RESEARCH_DETAILED_TARGET_MIN_CHARS"] = "not-an-integer"

    completed = subprocess.run(
        [sys.executable, "-c", "import src.core.answer_generator"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def _make_generator(
    *,
    keep_tool_result: int = 5,
    max_final_answer_retries=None,
    output_detail_level: str = "balanced",
    research_report_mode: bool = False,
    verification_enabled: bool = False,
    intermediate_boxed_answers=None,
) -> AnswerGenerator:
    agent_config = {
        "keep_tool_result": keep_tool_result,
        "context_compress_limit": 0,
        "retry_with_summary": True,
        "output_detail_level": output_detail_level,
        "research_report_mode": research_report_mode,
        "verification": {
            "enabled": verification_enabled,
            "use_high_model_for_verification": verification_enabled,
        },
    }
    if max_final_answer_retries is not None:
        agent_config["max_final_answer_retries"] = max_final_answer_retries
    cfg = OmegaConf.create({"agent": agent_config})
    llm_client = MagicMock()
    llm_client.format_token_usage_summary.return_value = ([], "")
    stream = MagicMock()
    stream.update = AsyncMock()
    return AnswerGenerator(
        llm_client=llm_client,
        output_formatter=OutputFormatter(),
        task_log=MagicMock(),
        stream_handler=stream,
        cfg=cfg,
        intermediate_boxed_answers=list(intermediate_boxed_answers or []),
    )


def test_max_final_answer_retries_uses_independent_agent_config():
    """保留有限工具结果时，独立总结重试配置仍应生效。"""
    generator = _make_generator(
        keep_tool_result=5,
        max_final_answer_retries=4,
    )

    assert generator.max_final_answer_retries == 4


def test_max_final_answer_retries_defaults_and_clamps_to_one():
    """缺省使用统一默认值，非正配置最少仍执行一次。"""
    default_generator = _make_generator(keep_tool_result=5)
    clamped_generator = _make_generator(
        keep_tool_result=-1,
        max_final_answer_retries=0,
    )

    assert (
        default_generator.max_final_answer_retries == DEFAULT_MAX_FINAL_ANSWER_RETRIES
    )
    assert clamped_generator.max_final_answer_retries == 1


@pytest.mark.asyncio
async def test_balanced_retries_an_overly_short_summary():
    """适中档总结过短时应有机会扩写后再返回。"""
    generator = _make_generator(
        output_detail_level="balanced",
        research_report_mode=True,
        max_final_answer_retries=2,
    )
    expanded_body = "关键信息" * 500
    responses = iter(
        [
            "过短。\\boxed{短答案}",
            f"{expanded_body}\\boxed{{扩写后的答案}}",
        ]
    )
    received_prompts = []

    async def fake_llm_call(
        system_prompt,
        message_history,
        tool_definitions,
        step_id,
        purpose,
        agent_type="main",
    ):
        del system_prompt, tool_definitions, step_id, purpose, agent_type
        received_prompts.append(message_history[-1]["content"])
        answer = next(responses)
        return (
            answer,
            False,
            None,
            [*message_history, {"role": "assistant", "content": answer}],
        )

    generator.handle_llm_call = fake_llm_call

    result = await generator.generate_final_answer_with_retries(
        system_prompt="系统指令",
        message_history=[{"role": "user", "content": "原始任务"}],
        tool_definitions=[],
        turn_count=1,
        task_description="原始任务",
    )

    assert result[2] == "扩写后的答案"
    assert len(received_prompts) == 2
    assert "上一版总结" in received_prompts[1]


@pytest.mark.asyncio
async def test_thinking_content_does_not_satisfy_summary_length_threshold():
    """长推理块不能掩盖过短正文，篇幅门控必须只计算可展示内容。"""
    generator = _make_generator(
        output_detail_level="balanced",
        research_report_mode=True,
        max_final_answer_retries=2,
    )
    expanded_body = "可核验信息" * 500
    responses = iter(
        [
            f"<think>{'内部推理' * 1000}</think>过短。\\boxed{{短答案}}",
            f"{expanded_body}\\boxed{{扩写后的答案}}",
        ]
    )
    received_prompts = []

    async def fake_llm_call(
        system_prompt,
        message_history,
        tool_definitions,
        step_id,
        purpose,
        agent_type="main",
    ):
        del system_prompt, tool_definitions, step_id, purpose, agent_type
        received_prompts.append(message_history[-1]["content"])
        answer = next(responses)
        return (
            answer,
            False,
            None,
            [*message_history, {"role": "assistant", "content": answer}],
        )

    generator.handle_llm_call = fake_llm_call

    result = await generator.generate_final_answer_with_retries(
        system_prompt="系统指令",
        message_history=[{"role": "user", "content": "原始任务"}],
        tool_definitions=[],
        turn_count=1,
        task_description="原始任务",
    )

    assert result[2] == "扩写后的答案"
    assert len(received_prompts) == 2
    assert "上一版总结" in received_prompts[1]


@pytest.mark.asyncio
async def test_final_answer_returns_quality_for_displayable_body_fallback():
    """缺少 boxed 但有正文时，生成器应返回可贯通的降级质量。"""
    generator = _make_generator(max_final_answer_retries=1)

    async def fake_llm_call(
        system_prompt,
        message_history,
        tool_definitions,
        step_id,
        purpose,
        agent_type="main",
    ):
        del system_prompt, tool_definitions, step_id, purpose, agent_type
        answer = "这是可展示的总结正文。"
        return answer, False, None, message_history

    generator.handle_llm_call = fake_llm_call

    result = await generator.generate_final_answer_with_retries(
        system_prompt="系统指令",
        message_history=[{"role": "user", "content": "原始任务"}],
        tool_definitions=[],
        turn_count=1,
        task_description="原始任务",
    )

    result_quality = result[5]
    assert result_quality["format_valid"] is False
    assert result_quality["fallback_used"] is True
    assert result_quality["answer_available"] is True
    assert result_quality["issues"] == ["missing_boxed"]


@pytest.mark.asyncio
async def test_empty_summary_without_intermediate_answer_is_unavailable():
    """模型无正文且无中间答案时，必须显式返回不可用结果。"""
    generator = _make_generator(max_final_answer_retries=1)
    generator.handle_llm_call = AsyncMock(
        return_value=("", False, None, [{"role": "user", "content": "总结指令"}])
    )

    result = await generator.generate_and_finalize_answer(
        system_prompt="系统指令",
        message_history=[{"role": "user", "content": "原始任务"}],
        tool_definitions=[],
        turn_count=1,
        task_description="原始任务",
    )

    assert result[0] == ""
    assert result[5]["answer_available"] is False
    assert result[5]["fallback_used"] is False
    assert "summary_generation_failed" in result[5]["issues"]
    assert "no_answer_available" in result[5]["issues"]


@pytest.mark.asyncio
async def test_think_only_summary_preserves_failure_quality_reasons():
    """模型只返回推理块时，质量信息必须保留总结失败与无答案原因。"""
    generator = _make_generator(max_final_answer_retries=1)
    generator.handle_llm_call = AsyncMock(
        return_value=(
            "<think>仍在推理，没有最终正文</think>",
            False,
            None,
            [{"role": "user", "content": "总结指令"}],
        )
    )

    result = await generator.generate_final_answer_with_retries(
        system_prompt="系统指令",
        message_history=[{"role": "user", "content": "原始任务"}],
        tool_definitions=[],
        turn_count=1,
        task_description="原始任务",
    )

    assert result[5]["answer_available"] is False
    assert "summary_generation_failed" in result[5]["issues"]
    assert "no_answer_available" in result[5]["issues"]


@pytest.mark.asyncio
async def test_empty_summary_uses_intermediate_answer_as_explicit_fallback():
    """模型总结失败但有中间 boxed 时，应返回可用且明确标记的回退结果。"""
    generator = _make_generator(
        max_final_answer_retries=1,
        intermediate_boxed_answers=["中间阶段的可靠答案"],
    )
    generator.handle_llm_call = AsyncMock(
        return_value=("", False, None, [{"role": "user", "content": "总结指令"}])
    )

    result = await generator.generate_and_finalize_answer(
        system_prompt="系统指令",
        message_history=[{"role": "user", "content": "原始任务"}],
        tool_definitions=[],
        turn_count=1,
        task_description="原始任务",
    )

    assert result[0] == "中间阶段的可靠答案"
    assert result[1] == "中间阶段的可靠答案"
    assert result[5]["answer_available"] is True
    assert result[5]["format_valid"] is False
    assert result[5]["fallback_used"] is True
    assert "summary_generation_failed" in result[5]["issues"]
    assert "intermediate_answer_fallback" in result[5]["issues"]


@pytest.mark.asyncio
async def test_no_tool_assistant_exit_protects_context_before_summary_prompt():
    """主模型无工具退出后，应先保护临界上下文再追加最终总结指令。"""
    generator = _make_generator(max_final_answer_retries=1)
    protected_histories = []
    submitted_histories = []

    def ensure_summary_context(message_history, summary_prompt):
        protected_histories.append([message.copy() for message in message_history])
        assert summary_prompt
        return True, message_history

    generator.llm_client.ensure_summary_context.side_effect = ensure_summary_context

    async def fake_llm_call(
        system_prompt,
        message_history,
        tool_definitions,
        step_id,
        purpose,
        agent_type="main",
    ):
        del system_prompt, tool_definitions, step_id, purpose, agent_type
        submitted_histories.append([message.copy() for message in message_history])
        return "\\boxed{最终答案}", False, None, message_history

    generator.handle_llm_call = fake_llm_call
    history = [
        {"role": "user", "content": "原始任务"},
        {"role": "assistant", "content": "主模型最后一轮直接作答"},
    ]

    await generator.generate_final_answer_with_retries(
        system_prompt="系统指令",
        message_history=history,
        tool_definitions=[],
        turn_count=1,
        task_description="原始任务",
    )

    assert len(protected_histories) == 1
    assert protected_histories[0][-1]["role"] == "assistant"
    assert submitted_histories[0][-2]["role"] == "assistant"
    assert submitted_histories[0][-1]["role"] == "user"


@pytest.mark.asyncio
async def test_compact_retries_empty_verification_and_repairs_format():
    """精简档也应使用独立重试完成 verification 降级和格式修复。"""
    generator = _make_generator(
        keep_tool_result=1,
        output_detail_level="compact",
        research_report_mode=True,
        verification_enabled=True,
    )
    generator.generate_cross_verification_note = AsyncMock(
        side_effect=lambda **kwargs: kwargs["message_history"]
    )
    responses = iter(["", "已有正文但缺少 boxed", "\\boxed{修复后的答案}"])
    used_agent_types = []

    async def fake_llm_call(
        system_prompt,
        message_history,
        tool_definitions,
        step_id,
        purpose,
        agent_type="main",
    ):
        del system_prompt, tool_definitions, step_id, purpose
        used_agent_types.append(agent_type)
        return next(responses), False, None, message_history

    generator.handle_llm_call = fake_llm_call

    result = await generator.generate_final_answer_with_retries(
        system_prompt="系统指令",
        message_history=[{"role": "user", "content": "原始任务"}],
        tool_definitions=[],
        turn_count=1,
        task_description="原始任务",
    )

    assert result[2] == "修复后的答案"
    assert used_agent_types == ["verification", "final_summary", "final_summary"]
    assert result[5]["format_valid"] is True
