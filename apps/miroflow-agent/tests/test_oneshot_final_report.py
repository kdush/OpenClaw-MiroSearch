# Copyright (c) 2025 MiroMind
"""Round 8: oneshot final report + summary-stage tool retention."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from omegaconf import OmegaConf

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.answer_generator import AnswerGenerator  # noqa: E402
from src.core.deep_efficiency import (  # noqa: E402
    DEFAULT_DEEP_POST_EARLY_STOP_TURNS,
    DEFAULT_DEEP_SUMMARY_KEEP_TOOL_RESULT,
    DEFAULT_DEEP_SUMMARY_MAX_TOKENS,
    resolve_exit_on_early_stop,
    resolve_oneshot_final_report,
    resolve_summary_keep_tool_result,
    resolve_summary_max_tokens_cap,
)
from src.io.output_formatter import OutputFormatter  # noqa: E402
from src.llm.base_client import OMITTED_TOOL_RESULT_TEXT  # noqa: E402
from src.logging.task_logger import RunMetrics  # noqa: E402


class _CfgDict(dict):
    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError as exc:
            raise AttributeError(item) from exc


def _make_generator(**agent_overrides) -> AnswerGenerator:
    agent = {
        "keep_tool_result": 5,
        "context_compress_limit": 0,
        "retry_with_summary": True,
        "output_detail_level": "detailed",
        "research_report_mode": True,
        "research_intensity": "deep",
        "verification": {"enabled": False},
    }
    agent.update(agent_overrides)
    cfg = OmegaConf.create({"agent": agent})
    llm_client = MagicMock()
    llm_client.format_token_usage_summary.return_value = ([], "")
    llm_client.ensure_summary_context.side_effect = lambda hist, _p: (True, hist)
    llm_client.summary_max_tokens = 8192
    stream = MagicMock()
    stream.update = AsyncMock()
    task_log = MagicMock()
    task_log.run_metrics = RunMetrics()
    return AnswerGenerator(
        llm_client=llm_client,
        output_formatter=OutputFormatter(),
        task_log=task_log,
        stream_handler=stream,
        cfg=cfg,
        intermediate_boxed_answers=[],
    )


def test_deep_post_early_stop_turns_default_is_one():
    assert DEFAULT_DEEP_POST_EARLY_STOP_TURNS == 1
    agent = _CfgDict(research_intensity="deep")
    cfg = _CfgDict(agent=agent)
    enabled, post = resolve_exit_on_early_stop(cfg)
    assert enabled is True
    assert post == 1


def test_oneshot_final_report_defaults_on_for_deep():
    agent = _CfgDict(research_intensity="deep")
    cfg = _CfgDict(agent=agent)
    assert resolve_oneshot_final_report(cfg) is True


def test_summary_keep_tool_result_deep_oneshot_defaults_to_two():
    agent = _CfgDict(research_intensity="deep")
    cfg = _CfgDict(agent=agent)
    assert (
        resolve_summary_keep_tool_result(cfg, research_keep=5)
        == DEFAULT_DEEP_SUMMARY_KEEP_TOOL_RESULT
    )
    assert DEFAULT_DEEP_SUMMARY_KEEP_TOOL_RESULT == 2


def test_summary_max_tokens_cap_deep_default():
    agent = _CfgDict(research_intensity="deep")
    cfg = _CfgDict(agent=agent)
    assert resolve_summary_max_tokens_cap(cfg) == DEFAULT_DEEP_SUMMARY_MAX_TOKENS


def test_oneshot_prompt_uses_skeleton_not_no_compress():
    gen = _make_generator()
    assert gen.oneshot_final_report is True
    assert gen.summary_keep_tool_result == 2
    prompt = gen._build_main_summary_prompt("测试任务：胡塞与沙特分歧")
    assert "ONE-SHOT" in prompt
    assert "禁止压缩" not in prompt
    assert "Conflicts" in prompt or "冲突" in prompt
    assert "\\boxed{" in prompt or "boxed" in prompt.lower()


def test_oneshot_skips_length_expand():
    gen = _make_generator()
    short = "## TL;DR\nok\n## Conflicts\n分歧\n## Evidence\nx\n## Confirmed\ny"
    assert gen._is_summary_too_short(short) is False


def test_strip_omitted_tool_stubs():
    gen = _make_generator()
    history = [
        {"role": "user", "content": "q"},
        {"role": "user", "content": OMITTED_TOOL_RESULT_TEXT},
        {"role": "user", "content": "real dump"},
    ]
    cleaned = gen._strip_omitted_tool_stubs(history)
    assert len(cleaned) == 2
    assert cleaned[1]["content"] == "real dump"


def test_resolve_keep_tool_result_for_summary_vs_main():
    gen = _make_generator()
    assert gen._resolve_keep_tool_result_for_call("final_summary") == 2
    assert gen._resolve_keep_tool_result_for_call("main") == 5


@pytest.mark.asyncio
async def test_oneshot_skips_verification_llm_pass():
    gen = _make_generator(
        verification={
            "enabled": True,
            "use_high_model_for_verification": True,
            "min_search_rounds": 3,
            "min_high_conf_sources": 2,
            "high_conf_domains": [],
        }
    )
    assert gen.oneshot_final_report is True
    assert gen.verification_enabled is True

    called_agent_types: list[str] = []
    boxed = (
        "\\boxed{\n## TL;DR\nok confidence medium\n"
        "## Conflicts & Uncertainties\n双方冲突说法足够长以通过结构校验门槛字符数要求\n"
        "## Timeline\n- 2026-09-19 event happened here with enough chars for min length\n"
        "## Evidence\n[1] https://example.com/a source dated 2026-09-19 and more\n"
        "## Confirmed vs Unconfirmed\n可确认：声明发出；不可确认：是否命中目标设施\n"
        "## References\n1. https://example.com/a\n"
        "}"
    )

    async def _capture(
        system_prompt,
        message_history,
        tool_definitions,
        step_id,
        purpose,
        agent_type="main",
    ):
        called_agent_types.append(agent_type)
        return boxed, True, None, message_history

    gen.handle_llm_call = _capture  # type: ignore[method-assign]
    gen.generate_cross_verification_note = AsyncMock(  # type: ignore[method-assign]
        side_effect=AssertionError("verification note should be skipped")
    )

    await gen.generate_final_answer_with_retries(
        system_prompt="sys",
        message_history=[{"role": "user", "content": "q"}],
        tool_definitions=[],
        turn_count=3,
        task_description="task",
    )
    assert called_agent_types == ["final_summary"]
