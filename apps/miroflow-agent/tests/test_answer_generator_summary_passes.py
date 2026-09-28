# Copyright (c) 2025 MiroMind
"""Round 7: summary_passes metric + deep default single summary pass."""

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
from src.io.output_formatter import OutputFormatter  # noqa: E402
from src.logging.task_logger import RunMetrics  # noqa: E402


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


def test_deep_research_defaults_to_single_summary_pass():
    gen = _make_generator()
    assert gen.max_final_answer_retries == 1


def test_explicit_retries_override_deep_default():
    gen = _make_generator(max_final_answer_retries=3)
    assert gen.max_final_answer_retries == 3


@pytest.mark.asyncio
async def test_summary_passes_metric_increments_once():
    gen = _make_generator()
    boxed = (
        "\\boxed{\n"
        "## TL;DR\nok\n"
        "## Conflicts & Uncertainties\n双方说法冲突\n"
        "## Timeline\n- 2026-09-19 event\n"
        "## Evidence（证据）\n[1] source\n"
        "## Confirmed vs Unconfirmed\n可确认：拦截；不可确认：延布受损\n"
        "}"
    )

    async def _fake_llm(*_a, **_k):
        return boxed, True, None, [{"role": "user", "content": "q"}]

    gen.handle_llm_call = _fake_llm  # type: ignore[method-assign]
    await gen.generate_final_answer_with_retries(
        system_prompt="sys",
        message_history=[{"role": "user", "content": "q"}],
        tool_definitions=[],
        turn_count=3,
        task_description="test query",
    )
    assert gen.task_log.run_metrics.summary_passes == 1
