# Copyright (c) 2025 MiroMind
# This source code is licensed under the Apache 2.0 License.

"""Unit tests for lead tracking enable resolution and extraction."""

from types import SimpleNamespace

from src.core.lead_tracker import (
    LeadTrackingManager,
    LeadTrail,
    resolve_lead_tracking_config,
)


class _CfgDict(dict):
    """Dict that also supports OmegaConf-like .get usage."""

    pass


def test_resolve_enable_from_main_agent_path():
    agent = _CfgDict()
    agent["main_agent"] = _CfgDict(enable_lead_tracking=True, max_lead_follow_ups=2)
    cfg = SimpleNamespace(agent=agent)
    enabled, max_fu = resolve_lead_tracking_config(cfg)
    assert enabled is True
    assert max_fu == 2


def test_resolve_auto_enable_on_deep_intensity():
    agent = _CfgDict(research_intensity="deep")
    agent["main_agent"] = _CfgDict()
    cfg = SimpleNamespace(agent=agent)
    enabled, _ = resolve_lead_tracking_config(cfg)
    assert enabled is True


def test_resolve_light_disabled_without_explicit_flag():
    agent = _CfgDict(research_intensity="light")
    agent["main_agent"] = _CfgDict()
    cfg = SimpleNamespace(agent=agent)
    enabled, _ = resolve_lead_tracking_config(cfg)
    assert enabled is False


def test_english_extraction_need_to_investigate():
    text = (
        "Based on initial research, we found quantum computing developments.\n"
        "However, we need to investigate: what are the specific breakthroughs by IBM?\n"
        "There is also uncertainty regarding Google's claimed quantum supremacy metrics.\n"
    )
    leads = LeadTrail.extract_leads_from_text(text, turn=1)
    assert len(leads) >= 1


def test_query_seed_for_chained_how_did():
    seeds = LeadTrail.seed_leads_from_query(
        "How did the discovery of DNA structure lead to CRISPR technology?"
    )
    assert len(seeds) >= 1
    assert any("CRISPR" in q or "intermediate" in q.lower() for q, _ in seeds)


def test_trail_includes_pending_leads():
    mgr = LeadTrackingManager(enabled=True, max_follow_ups=2)
    mgr.initialize("How did A lead to B?")
    assert mgr.get_stats()["total_leads"] >= 1
    trail = mgr.get_trail_section()
    assert "Lead Trail" in trail
    assert ("未跟进" in trail) or ("pending" in trail) or ("followed" in trail)


def test_follow_up_marks_trail_and_stats():
    mgr = LeadTrackingManager(enabled=True, max_follow_ups=2)
    mgr.initialize("How did A lead to B?")
    leads = mgr.process_turn_response(
        "We need to investigate: what tools bridged A to B?", turn=1
    )
    assert leads
    mgr.record_follow_up(leads[0], turn=2, findings="Found restriction enzymes.")
    trail = mgr.get_trail_section()
    assert "followed" in trail
    assert mgr.trail.follow_up_count >= 1
