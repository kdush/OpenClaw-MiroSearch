from pathlib import Path
import sys


# 确保测试可直接导入项目源码
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.orchestrator import Orchestrator  # noqa: E402
from src.logging.task_logger import TaskLog  # noqa: E402


def _build_orchestrator_stub() -> Orchestrator:
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.verification_enabled = True
    orchestrator.verification_min_search_rounds = 3
    orchestrator.verification_min_high_conf_sources = 2
    orchestrator.verification_max_guidance_attempts = 3
    orchestrator.verification_guidance_attempts = 1
    orchestrator.verification_search_rounds = 1
    orchestrator.verification_high_conf_source_domains = set()
    orchestrator.retrieval_confidence_passed = False
    orchestrator.verification_guidance_anchor_search_rounds = 1
    orchestrator.verification_guidance_anchor_high_conf_sources = 0
    orchestrator.verification_stagnant_guidance_attempts = 0
    orchestrator.verification_max_stagnant_guidance_attempts = 1
    orchestrator.task_log = TaskLog(
        task_id="verification-gate-test",
        start_time="2026-03-20 12:00:00",
    )
    return orchestrator


def test_verification_gate_stops_repeating_without_new_search_evidence():
    orchestrator = _build_orchestrator_stub()

    should_issue = orchestrator._should_issue_verification_guidance(
        turn_count=5,
        max_turns=9,
    )

    assert should_issue is False
    assert orchestrator.verification_stagnant_guidance_attempts == 1


def test_verification_gate_allows_retry_when_new_search_evidence_exists():
    orchestrator = _build_orchestrator_stub()
    orchestrator.verification_search_rounds = 2

    should_issue = orchestrator._should_issue_verification_guidance(
        turn_count=5,
        max_turns=9,
    )

    assert should_issue is True
    assert orchestrator.verification_stagnant_guidance_attempts == 0


def test_verification_requirements_met_when_retrieval_confidence_passed():
    orchestrator = _build_orchestrator_stub()
    orchestrator.verification_search_rounds = 3
    orchestrator.retrieval_confidence_passed = True

    # 高置信来源计数不足，但工具侧 confidence 已通过
    assert orchestrator._verification_requirements_met() is True

    # 检索轮次下限仍然必须满足
    orchestrator.verification_search_rounds = 2
    assert orchestrator._verification_requirements_met() is False
