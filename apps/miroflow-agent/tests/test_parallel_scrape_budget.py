# Copyright (c) 2025 MiroMind
"""Parallel scrape budget must reserve slots before concurrent execution."""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.orchestrator import Orchestrator  # noqa: E402
from src.logging.task_logger import RunMetrics  # noqa: E402


def _bare_orchestrator(*, max_scrape: int = 8, scrape_count: int = 7) -> Orchestrator:
    obj = Orchestrator.__new__(Orchestrator)
    obj.max_scrape_per_task = max_scrape
    obj._scrape_budget_lock = threading.Lock()
    obj._scrape_slots_reserved = 0
    obj.task_log = MagicMock()
    obj.task_log.run_metrics = RunMetrics()
    obj.task_log.run_metrics.scrape_count = scrape_count
    obj.main_agent_tool_manager = MagicMock()
    return obj


@pytest.mark.asyncio
async def test_parallel_scrapes_cannot_exceed_budget():
    """Limit 8, used 7, two parallel scrapes → only one executes."""
    orch = _bare_orchestrator(max_scrape=8, scrape_count=7)
    started = []
    tool_name = "scrape_webpage"

    async def fake_execute(*, server_name, tool_name, arguments):
        started.append(tool_name)
        await asyncio.sleep(0.01)
        return {"server_name": server_name, "tool_name": tool_name, "result": "ok"}

    orch.main_agent_tool_manager.execute_tool_call = AsyncMock(side_effect=fake_execute)

    results = await asyncio.gather(
        orch._execute_regular_tool_call("search", tool_name, {"url": "https://a"}, 1),
        orch._execute_regular_tool_call("search", tool_name, {"url": "https://b"}, 1),
    )

    executed = [
        r for r in results if "[scrape_budget]" not in str(r.get("result") or "")
    ]
    skipped = [r for r in results if "[scrape_budget]" in str(r.get("result") or "")]
    assert len(executed) == 1
    assert len(skipped) == 1
    assert len(started) == 1
    assert orch._scrape_slots_reserved == 1  # held until metric commit

    for r in results:
        orch._record_scrape_metric(tool_name, r, 1)

    assert orch.task_log.run_metrics.scrape_count == 8
    assert orch._scrape_slots_reserved == 0


def test_early_stop_requires_high_conf_not_raw_domains():
    orch = _bare_orchestrator()
    orch.deep_early_stop_enabled = True
    orch.deep_early_stop_min_sources = 2
    orch.verification_min_search_rounds = 2
    orch.task_log.run_metrics.search_rounds = 3
    orch.independent_source_domains = {"opposing-a.com", "opposing-b.com"}
    orch.early_stop_high_conf_domains = set()
    orch.retrieval_confidence_passed = False
    assert orch._should_early_stop_clue_chase() is False

    orch.early_stop_high_conf_domains = {"reuters.com", "apnews.com"}
    assert orch._should_early_stop_clue_chase() is True
