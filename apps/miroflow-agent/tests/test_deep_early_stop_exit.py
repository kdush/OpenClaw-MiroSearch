# Copyright (c) 2025 MiroMind
"""Round 7: early-stop turn cap forces summary exit.

另含证据一致性裁决（agreement gate）回归：高可信域名数值门满足后，
只有裁决为 agree 且已裁决当前证据版本才允许早停；新证据使旧裁决失效，
conflict / unknown / 次数耗尽均 fail-closed。
"""

from __future__ import annotations

import json
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
    # 基线：已在证据版本 2 上裁决 agree（当前有效）
    obj.evidence_agreement = "agree"
    obj.evidence_agreement_attempts = 0
    obj.evidence_revision = 2
    obj.agreement_checked_revision = 2
    obj.verification_min_search_rounds = 3
    obj.verification_enabled = False
    obj.verification_high_conf_domains = {"reuters.com", "bbc.com"}
    obj.max_scrape_per_task = 0
    obj.task_log = MagicMock()
    obj.task_log.run_metrics = RunMetrics()
    obj.task_log.run_metrics.search_rounds = 3
    for key, value in kwargs.items():
        setattr(obj, key, value)
    return obj


def _search_result(url: str) -> dict:
    """google_search 工具结果载荷（_extract_search_links 可解析）。"""
    return {"result": json.dumps({"organic": [{"link": url}]})}


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


def test_early_stop_blocked_when_agree_verdict_stale():
    """裁决为 agree 但落后于当前证据版本时同样 fail-closed。"""
    orch = _bare_orchestrator(agreement_checked_revision=1)
    assert orch.agreement_checked_revision < orch.evidence_revision
    assert orch._should_early_stop_clue_chase() is False
    assert orch._should_force_summary_after_early_stop(10) is False


@pytest.mark.asyncio
async def test_new_search_conflict_invalidates_stale_agree():
    """场景①：AGREE 后新搜索带来关键冲突 → 撤销倒计时并复评为 conflict。"""
    orch = _bare_orchestrator()
    orch.deep_early_stop_triggered = True
    orch.deep_early_stop_turn = 3
    orch.answer_generator = MagicMock()
    orch.answer_generator.generate_agreement_check = AsyncMock(return_value="conflict")

    orch._record_search_evidence(
        "google_search", _search_result("https://www.apnews.com/contradicts")
    )

    # 新证据已使旧 AGREE 无法通过早停门（复评之前也 fail-closed）
    assert orch.evidence_revision == 3
    assert orch._should_early_stop_clue_chase() is False

    await orch._maybe_evaluate_evidence_agreement("sys", [], 4, "question")

    assert orch.evidence_agreement == "conflict"
    assert orch.agreement_checked_revision == orch.evidence_revision == 3
    assert orch.evidence_agreement_attempts == 1
    assert orch.deep_early_stop_triggered is False
    assert orch.deep_early_stop_turn == 0
    assert orch._should_early_stop_clue_chase() is False
    assert orch._should_force_summary_after_early_stop(4) is False
    assert orch._should_force_summary_after_early_stop(99) is False
    orch.answer_generator.generate_agreement_check.assert_awaited_once()


@pytest.mark.asyncio
async def test_new_scrape_conflict_invalidates_stale_agree():
    """场景②：仅新抓取带来冲突证据，同样使旧 AGREE 失效。"""
    orch = _bare_orchestrator()
    orch.deep_early_stop_triggered = True
    orch.deep_early_stop_turn = 3
    orch.answer_generator = MagicMock()
    orch.answer_generator.generate_agreement_check = AsyncMock(return_value="conflict")

    orch._record_scrape_metric("scrape_url", {"result": "页面正文与核心结论相矛盾"}, 4)

    assert orch.evidence_revision == 3
    assert orch.task_log.run_metrics.scrape_count == 1
    assert orch._should_early_stop_clue_chase() is False

    await orch._maybe_evaluate_evidence_agreement("sys", [], 4, "question")

    assert orch.evidence_agreement == "conflict"
    assert orch.agreement_checked_revision == orch.evidence_revision
    assert orch.deep_early_stop_triggered is False
    assert orch._should_early_stop_clue_chase() is False
    assert orch._should_force_summary_after_early_stop(99) is False
    orch.answer_generator.generate_agreement_check.assert_awaited_once()


@pytest.mark.asyncio
async def test_conflict_resolved_rearms_countdown_from_new_turn():
    """场景③：新证据化解冲突重获 AGREE 后，从当前回合重新计时。"""
    orch = _bare_orchestrator()
    orch.deep_early_stop_triggered = True
    orch.deep_early_stop_turn = 3
    orch.task_log.run_metrics.record_early_stop(3)
    orch.answer_generator = MagicMock()
    orch.answer_generator.generate_agreement_check = AsyncMock(
        side_effect=["conflict", "agree"]
    )

    # 回合 4：新搜索推翻旧结论，复评为 conflict，撤销倒计时
    orch._record_search_evidence(
        "google_search", _search_result("https://www.apnews.com/x")
    )
    await orch._maybe_evaluate_evidence_agreement("sys", [], 4, "question")
    assert orch.evidence_agreement == "conflict"
    assert orch.deep_early_stop_triggered is False
    assert orch._should_force_summary_after_early_stop(4) is False

    # 回合 6：又一轮新证据化解矛盾，复评为 agree
    orch._record_search_evidence(
        "google_search", _search_result("https://www.bbc.com/corroborates")
    )
    await orch._maybe_evaluate_evidence_agreement("sys", [], 6, "question")
    assert orch.evidence_agreement == "agree"
    assert orch._should_early_stop_clue_chase() is True

    # 从新回合重新计时：回合 7 未到阈值，回合 8 强制总结
    assert orch._should_force_summary_after_early_stop(6) is False
    assert orch.deep_early_stop_turn == 6
    assert orch._should_force_summary_after_early_stop(7) is False
    assert orch._should_force_summary_after_early_stop(8) is True

    # 运行指标保留“曾触发”的首次记录
    assert orch.task_log.run_metrics.early_stop_triggered is True
    assert orch.task_log.run_metrics.early_stop_turn == 3


@pytest.mark.asyncio
async def test_cap_exhausted_with_new_evidence_stays_fail_closed():
    """场景④：次数耗尽后新证据到来，旧 AGREE 仍被撤销且不早停。"""
    orch = _bare_orchestrator(evidence_agreement_attempts=3)
    orch.deep_early_stop_triggered = True
    orch.deep_early_stop_turn = 3
    orch.answer_generator = MagicMock()
    orch.answer_generator.generate_agreement_check = AsyncMock(return_value="agree")

    orch._record_search_evidence(
        "google_search", _search_result("https://www.apnews.com/x")
    )
    await orch._maybe_evaluate_evidence_agreement("sys", [], 4, "question")

    assert orch.evidence_agreement == "unknown"  # 旧 AGREE 已撤销
    assert orch.evidence_agreement_attempts == 3  # 不再消耗次数
    assert orch.agreement_checked_revision < orch.evidence_revision
    orch.answer_generator.generate_agreement_check.assert_not_awaited()
    assert orch._should_early_stop_clue_chase() is False
    assert orch._should_force_summary_after_early_stop(4) is False
    assert orch._should_force_summary_after_early_stop(99) is False


@pytest.mark.asyncio
async def test_unparseable_verdict_stays_fail_closed():
    """场景④：裁决失败按 unknown 处理（generate_agreement_check 已解析）；
    同一版本不重复裁决。"""
    orch = _bare_orchestrator(
        evidence_agreement="unknown", agreement_checked_revision=0
    )
    orch.answer_generator = MagicMock()
    orch.answer_generator.generate_agreement_check = AsyncMock(return_value="unknown")

    await orch._maybe_evaluate_evidence_agreement("sys", [], 3, "question")
    assert orch.evidence_agreement == "unknown"
    assert orch.evidence_agreement_attempts == 1
    assert orch.agreement_checked_revision == orch.evidence_revision
    assert orch._should_early_stop_clue_chase() is False

    await orch._maybe_evaluate_evidence_agreement("sys", [], 4, "question")
    orch.answer_generator.generate_agreement_check.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_readjudication_without_new_evidence():
    """场景⑤：无新证据不重复裁决；同批多份证据最多裁决一次。"""
    orch = _bare_orchestrator()
    orch.answer_generator = MagicMock()
    orch.answer_generator.generate_agreement_check = AsyncMock(return_value="conflict")

    # 当前版本已裁决 agree：不重复调用
    await orch._maybe_evaluate_evidence_agreement("sys", [], 4, "question")
    orch.answer_generator.generate_agreement_check.assert_not_awaited()
    assert orch._should_early_stop_clue_chase() is True

    # 并行一批两条搜索结果（版本连升两次）后仍只裁决一次
    orch.evidence_agreement = "unknown"
    orch.evidence_revision = 0
    orch.agreement_checked_revision = 0
    orch._record_search_evidence("google_search", _search_result("https://a.com/1"))
    orch._record_search_evidence("google_search", _search_result("https://b.com/2"))
    await orch._maybe_evaluate_evidence_agreement("sys", [], 5, "question")
    orch.answer_generator.generate_agreement_check.assert_awaited_once()
    assert orch.agreement_checked_revision == orch.evidence_revision == 2


@pytest.mark.asyncio
async def test_agreement_adjudication_updates_state_and_gates():
    """数值门满足且首次裁决：conflict 结果写入状态并挡住早停。"""
    orch = _bare_orchestrator(
        evidence_agreement="unknown", agreement_checked_revision=0
    )
    orch.answer_generator = MagicMock()
    orch.answer_generator.generate_agreement_check = AsyncMock(return_value="conflict")

    await orch._maybe_evaluate_evidence_agreement("sys", [], 3, "question")

    assert orch.evidence_agreement == "conflict"
    assert orch.evidence_agreement_attempts == 1
    assert orch.agreement_checked_revision == orch.evidence_revision == 2
    assert orch._should_early_stop_clue_chase() is False
    orch.answer_generator.generate_agreement_check.assert_awaited_once()


@pytest.mark.asyncio
async def test_agreement_adjudication_skipped_when_numeric_gate_unmet():
    orch = _bare_orchestrator(
        evidence_agreement="unknown", agreement_checked_revision=0
    )
    orch.task_log.run_metrics.search_rounds = 1  # 低于 min_rounds=3
    orch.answer_generator = MagicMock()
    orch.answer_generator.generate_agreement_check = AsyncMock()

    await orch._maybe_evaluate_evidence_agreement("sys", [], 3, "question")

    orch.answer_generator.generate_agreement_check.assert_not_awaited()
    assert orch.evidence_agreement_attempts == 0
    assert orch.agreement_checked_revision == 0


@pytest.mark.asyncio
async def test_agreement_attempts_capped_and_unknown_stays_fail_closed():
    """unknown 也绑定版本；跨新证据累计 3 次后不再裁决，保持 fail-closed。"""
    orch = _bare_orchestrator(
        evidence_agreement="unknown", agreement_checked_revision=0
    )
    orch.answer_generator = MagicMock()
    orch.answer_generator.generate_agreement_check = AsyncMock(return_value="unknown")

    for turn in (3, 4, 5):
        orch.evidence_revision += 1
        await orch._maybe_evaluate_evidence_agreement("sys", [], turn, "question")
    assert orch.evidence_agreement_attempts == 3

    # 次数耗尽后即使有新证据也不再裁决，保持 unknown（不早停）
    orch.evidence_revision += 1
    await orch._maybe_evaluate_evidence_agreement("sys", [], 6, "question")
    assert orch.evidence_agreement_attempts == 3
    assert orch.evidence_agreement == "unknown"
    assert orch.agreement_checked_revision < orch.evidence_revision
    assert orch._should_early_stop_clue_chase() is False
    assert orch.answer_generator.generate_agreement_check.await_count == 3


@pytest.mark.asyncio
async def test_stale_agree_revocation_overrides_convergence_nudge():
    """旧 AGREE 失效时撤销收敛提示，并向会话写入覆盖指令。"""
    orch = _bare_orchestrator()
    orch.deep_early_stop_triggered = True
    orch.deep_early_stop_turn = 3
    orch._deep_convergence_nudge_sent = True
    history = [
        {
            "role": "user",
            "content": "已有足够独立来源与检索轮次，请停止继续检索/抓取，"
            "立即基于现有证据撰写完整研究报告。",
        }
    ]
    orch.answer_generator = MagicMock()
    orch.answer_generator.generate_agreement_check = AsyncMock(return_value="conflict")

    orch._record_search_evidence(
        "google_search", _search_result("https://www.apnews.com/x")
    )
    await orch._maybe_evaluate_evidence_agreement("sys", history, 4, "question")

    assert orch._deep_convergence_nudge_sent is False
    assert orch.deep_early_stop_triggered is False
    assert orch.deep_early_stop_turn == 0
    assert len(history) == 2
    assert history[-1]["role"] == "user"
    assert "作废" in history[-1]["content"]


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
