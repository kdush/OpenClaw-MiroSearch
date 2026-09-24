# Copyright (c) 2025 MiroMind
"""Round 7: early-stop turn cap forces summary exit.

另含证据一致性裁决（agreement gate）回归：高可信域名数值门满足后，
只有裁决为 agree 才允许早停；conflict / unknown 均 fail-closed。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.orchestrator import Orchestrator  # noqa: E402
from src.logging.task_logger import RunMetrics  # noqa: E402
from src.utils.prompt_utils import parse_agreement_verdict  # noqa: E402


def _bare_orchestrator(**kwargs) -> Orchestrator:
    """Build a partially-initialized orchestrator for helper unit tests."""
    obj = Orchestrator.__new__(Orchestrator)
    obj.deep_early_stop_enabled = True
    obj.deep_early_stop_min_sources = 2
    obj.deep_exit_on_early_stop = True
    obj.deep_post_early_stop_turns = 2
    obj.deep_early_stop_triggered = False
    obj.deep_early_stop_turn = 0
    obj._deep_convergence_nudge_sent = False
    obj.independent_source_domains = {"a.com", "b.com"}
    obj.early_stop_high_conf_domains = {"reuters.com", "bbc.com"}
    obj.retrieval_confidence_passed = False
    obj.evidence_agreement = "agree"
    obj.evidence_agreement_attempts = 0
    obj.evidence_agreement_last_eval_rounds = 0
    obj.verification_min_search_rounds = 3
    obj.task_log = MagicMock()
    obj.task_log.run_metrics = RunMetrics()
    obj.task_log.run_metrics.search_rounds = 3
    for key, value in kwargs.items():
        setattr(obj, key, value)
    return obj


def test_force_summary_after_post_early_stop_turns():
    orch = _bare_orchestrator()
    assert orch._should_force_summary_after_early_stop(3) is False
    assert orch.deep_early_stop_triggered is True
    assert orch.deep_early_stop_turn == 3
    assert orch.task_log.run_metrics.early_stop_triggered is True
    assert orch.task_log.run_metrics.early_stop_turn == 3
    # post_turns=2 → force at turn >= 5
    assert orch._should_force_summary_after_early_stop(4) is False
    assert orch._should_force_summary_after_early_stop(5) is True


def test_force_summary_disabled_when_exit_flag_off():
    orch = _bare_orchestrator(deep_exit_on_early_stop=False)
    assert orch._should_force_summary_after_early_stop(20) is False
    assert orch.deep_early_stop_triggered is False


def test_nudge_sent_flag_allows_failure_path_exit_semantics():
    """After nudge, orchestrator should prefer summary over more empty retries."""
    orch = _bare_orchestrator()
    orch._deep_convergence_nudge_sent = True
    assert orch._deep_convergence_nudge_sent is True
    # force condition still true once past budget
    orch.deep_early_stop_triggered = True
    orch.deep_early_stop_turn = 7
    assert orch._should_force_summary_after_early_stop(10) is True


def test_early_stop_blocked_when_high_conf_sources_contradict():
    """回归：两个高可信域名观点相反时不得提前结束研究。"""
    orch = _bare_orchestrator(
        early_stop_high_conf_domains={"reuters.com", "apnews.com"},
        evidence_agreement="conflict",
    )
    assert orch._should_early_stop_clue_chase() is False
    # 任意回合都不应触发强制总结，也不应记录早停
    for turn in range(1, 12):
        assert orch._should_force_summary_after_early_stop(turn) is False
    assert orch.deep_early_stop_triggered is False


def test_early_stop_blocked_when_agreement_unknown():
    """裁决未出/不可解析时 fail-closed，同样不早停。"""
    orch = _bare_orchestrator(
        early_stop_high_conf_domains={"reuters.com", "apnews.com"},
        evidence_agreement="unknown",
    )
    assert orch._should_early_stop_clue_chase() is False
    assert orch._should_force_summary_after_early_stop(10) is False
    assert orch.deep_early_stop_triggered is False


def test_early_stop_proceeds_when_agreement_confirmed():
    orch = _bare_orchestrator(
        early_stop_high_conf_domains={"reuters.com", "apnews.com"},
        evidence_agreement="agree",
    )
    assert orch._should_early_stop_clue_chase() is True


@pytest.mark.asyncio
async def test_agreement_adjudication_updates_state_and_gates():
    """数值门满足时触发裁决；conflict 结果写入状态并挡住早停。"""
    orch = _bare_orchestrator(evidence_agreement="unknown")
    orch.answer_generator = MagicMock()
    orch.answer_generator.generate_agreement_check = AsyncMock(return_value="conflict")

    await orch._maybe_evaluate_evidence_agreement("sys", [], 3, "question")

    assert orch.evidence_agreement == "conflict"
    assert orch.evidence_agreement_attempts == 1
    assert orch.evidence_agreement_last_eval_rounds == 3
    assert orch._should_early_stop_clue_chase() is False
    orch.answer_generator.generate_agreement_check.assert_awaited_once()


@pytest.mark.asyncio
async def test_agreement_adjudication_skipped_when_numeric_gate_unmet():
    orch = _bare_orchestrator(evidence_agreement="unknown")
    orch.task_log.run_metrics.search_rounds = 1  # 低于 min_rounds=3
    orch.answer_generator = MagicMock()
    orch.answer_generator.generate_agreement_check = AsyncMock()

    await orch._maybe_evaluate_evidence_agreement("sys", [], 3, "question")

    orch.answer_generator.generate_agreement_check.assert_not_awaited()
    assert orch.evidence_agreement_attempts == 0


@pytest.mark.asyncio
async def test_agreement_re_evaluated_only_on_new_search_rounds():
    """conflict 后出现新检索轮才复评；矛盾化解为 agree 后恢复早停。"""
    orch = _bare_orchestrator(evidence_agreement="unknown")
    orch.answer_generator = MagicMock()
    orch.answer_generator.generate_agreement_check = AsyncMock(
        side_effect=["conflict", "agree"]
    )

    await orch._maybe_evaluate_evidence_agreement("sys", [], 3, "question")
    assert orch.evidence_agreement == "conflict"
    assert orch.evidence_agreement_attempts == 1

    # 无新检索轮：不重复裁决
    await orch._maybe_evaluate_evidence_agreement("sys", [], 4, "question")
    assert orch.evidence_agreement_attempts == 1

    # 新检索轮到来：复评，矛盾化解后允许早停
    orch.task_log.run_metrics.search_rounds = 4
    await orch._maybe_evaluate_evidence_agreement("sys", [], 5, "question")
    assert orch.evidence_agreement == "agree"
    assert orch.evidence_agreement_attempts == 2
    assert orch._should_early_stop_clue_chase() is True


@pytest.mark.asyncio
async def test_agreement_attempts_capped_and_unknown_stays_fail_closed():
    orch = _bare_orchestrator(evidence_agreement="unknown")
    orch.answer_generator = MagicMock()
    orch.answer_generator.generate_agreement_check = AsyncMock(return_value="unknown")

    for rounds in (3, 4, 5):
        orch.task_log.run_metrics.search_rounds = rounds
        await orch._maybe_evaluate_evidence_agreement("sys", [], 3, "question")
    assert orch.evidence_agreement_attempts == 3

    # 达到上限后即使有新检索轮也不再裁决，保持 unknown（不早停）
    orch.task_log.run_metrics.search_rounds = 6
    await orch._maybe_evaluate_evidence_agreement("sys", [], 4, "question")
    assert orch.evidence_agreement_attempts == 3
    assert orch.evidence_agreement == "unknown"
    assert orch._should_early_stop_clue_chase() is False


def test_parse_agreement_verdict_variants():
    corroboration_note = "VERDICT: AGREE\n- reuters 与 apnews 相互印证"
    assert parse_agreement_verdict(corroboration_note) == "agree"
    assert parse_agreement_verdict("verdict: conflict") == "conflict"
    # 模型先写说明再给结论、全角冒号均需可解析
    assert parse_agreement_verdict("先说明…\nVERDICT：CONFLICT") == "conflict"
    # 词边界：agreed / agreement 等前缀不算有效裁决
    assert parse_agreement_verdict("verdict: agreed") == "unknown"
    assert parse_agreement_verdict("") == "unknown"
    assert parse_agreement_verdict("没有给出结论") == "unknown"
