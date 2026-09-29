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


class TestLeadChainTrace:
    """M4：记录搜索/追问/跳转与结果，展示「为什么继续查」。"""

    def test_snapshot_records_why_we_continued(self):
        mgr = LeadTrackingManager(enabled=True, max_follow_ups=2)
        mgr.initialize("原始问题")
        leads = mgr.process_turn_response(
            "We need to investigate: what bridged A to B?", turn=1
        )
        assert leads

        mgr.record_follow_up(
            leads[0], turn=2, findings="已找到线索", reason="early-stop-not-met"
        )

        trace = mgr.get_trace()
        assert trace["original_query"] == "原始问题"
        assert trace["follow_up_count"] == 1
        node = trace["leads"][0]
        assert node["followed_up"] is True
        assert node["follow_up_turn"] == 2
        assert node["follow_up_reason"] == "early-stop-not-met"
        assert node["findings"] == "已找到线索"

    def test_trail_section_surfaces_the_reason(self):
        mgr = LeadTrackingManager(enabled=True, max_follow_ups=2)
        mgr.initialize("原始问题")
        leads = mgr.process_turn_response(
            "We need to investigate: what bridged A to B?", turn=1
        )
        mgr.record_follow_up(leads[0], turn=2, reason="priority")

        assert "为何继续查" in mgr.get_trail_section()

    def test_trace_is_json_serializable(self):
        import json

        mgr = LeadTrackingManager(enabled=True, max_follow_ups=2)
        mgr.initialize("原始问题")
        mgr.process_turn_response(
            "We need to investigate: what bridged A to B?", turn=1
        )

        json.dumps(mgr.get_trace())

    def test_disabled_manager_returns_empty_trace(self):
        mgr = LeadTrackingManager(enabled=False)
        assert mgr.get_trace() == {
            "original_query": "",
            "follow_up_count": 0,
            "leads": [],
        }
