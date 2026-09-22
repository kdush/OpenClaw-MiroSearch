# Copyright (c) 2025 MiroMind
"""Round 7: early-stop turn cap forces summary exit."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.orchestrator import Orchestrator  # noqa: E402
from src.logging.task_logger import RunMetrics  # noqa: E402


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
