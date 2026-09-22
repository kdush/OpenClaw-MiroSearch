"""Acceptance tests for research quality improvements (Phases 1-5).

Tests map to acceptance cases defined in the requirements:
A. Simple fact, light+compact
B. Contested numeric/stat, verified+deep+detailed
C. Chained-clue research, research+deep (Phase 4 infrastructure)
D. Same query, searxng-only vs parallel-trusted
E. Same deep query, compact vs detailed
"""

from models import ResearchRequest
from services.profile_resolver import resolve_effective_research_params


class TestAcceptanceA:
    """A. Simple fact, light+compact: ≤2 search rounds; clear answer + source; no long essay."""

    def test_light_compact_effective_config(self):
        """Verify light+compact produces expected effective_config."""
        req = ResearchRequest(
            query="Capital of France?",
            mode="balanced",
            research_intensity="light",
            output_detail_level="compact",
        )
        effective = resolve_effective_research_params(
            mode=req.mode,
            research_intensity=req.research_intensity,
            output_detail_level=req.output_detail_level,
        )

        assert effective.research_intensity == "light"
        assert effective.output_detail_level == "compact"
        assert effective.mode == "balanced"

    def test_light_intensity_reduces_budget(self):
        """Verify light intensity reduces max_turns budget."""
        from services.profile_resolver import build_full_overrides

        _, overrides_standard = build_full_overrides(
            mode="balanced",
            search_profile=None,
            search_result_num=None,
            verification_min_search_rounds=None,
            output_detail_level=None,
            research_intensity="standard",
        )

        _, overrides_light = build_full_overrides(
            mode="balanced",
            search_profile=None,
            search_result_num=None,
            verification_min_search_rounds=None,
            output_detail_level=None,
            research_intensity="light",
        )

        # Extract max_turns from overrides
        def get_max_turns(overrides):
            for override in overrides:
                if "main_agent.max_turns=" in override:
                    return int(override.split("=")[1])
            return None

        turns_standard = get_max_turns(overrides_standard)
        turns_light = get_max_turns(overrides_light)

        assert turns_light is not None
        assert turns_standard is not None
        assert (
            turns_light < turns_standard
        ), "Light should have fewer turns than standard"


class TestAcceptanceB:
    """B. Contested numeric/stat, verified+deep+detailed: meets min verification rounds;
    multi-source comparison; cites disagreement; citations resolvable."""

    def test_verified_deep_detailed_config(self):
        """Verify verified+deep+detailed produces expected effective_config."""
        req = ResearchRequest(
            query="Global CO2 emissions in 2023",
            mode="verified",
            research_intensity="deep",
            verification_min_search_rounds=4,
            output_detail_level="detailed",
        )
        effective = resolve_effective_research_params(
            mode=req.mode,
            research_intensity=req.research_intensity,
            verification_min_search_rounds=req.verification_min_search_rounds,
            output_detail_level=req.output_detail_level,
        )

        assert effective.research_intensity == "deep"
        assert effective.output_detail_level == "detailed"
        assert effective.mode == "verified"
        assert effective.verification_min_search_rounds >= 4

    def test_deep_intensity_increases_budget(self):
        """Verify deep intensity increases budget compared to standard."""
        from services.profile_resolver import build_full_overrides

        _, overrides_standard = build_full_overrides(
            mode="verified",
            search_profile=None,
            search_result_num=None,
            verification_min_search_rounds=4,
            output_detail_level=None,
            research_intensity="standard",
        )

        _, overrides_deep = build_full_overrides(
            mode="verified",
            search_profile=None,
            search_result_num=None,
            verification_min_search_rounds=4,
            output_detail_level=None,
            research_intensity="deep",
        )

        def get_max_turns(overrides):
            for override in overrides:
                if "main_agent.max_turns=" in override:
                    return int(override.split("=")[1])
            return None

        turns_standard = get_max_turns(overrides_standard)
        turns_deep = get_max_turns(overrides_deep)

        assert turns_deep is not None
        assert turns_standard is not None
        assert turns_deep > turns_standard, "Deep should have more turns than standard"

    def test_deep_enables_lead_tracking(self):
        """Verify deep intensity enables lead tracking."""
        from services.profile_resolver import build_full_overrides

        _, overrides_deep = build_full_overrides(
            mode="research",
            search_profile=None,
            search_result_num=None,
            verification_min_search_rounds=None,
            output_detail_level=None,
            research_intensity="deep",
        )

        # Check that lead tracking is enabled
        assert any("enable_lead_tracking=true" in o for o in overrides_deep)


class TestAcceptanceC:
    """C. Chained-clue research, research+deep: lead trail present; ≥1 follow-up search from a prior lead."""

    def test_lead_tracker_basic_functionality(self):
        """Verify LeadTracker can extract and track leads."""
        from src.core.lead_tracker import LeadTrackingManager

        manager = LeadTrackingManager(enabled=True, max_follow_ups=3)
        manager.initialize("Research quantum computing")

        # Simulate assistant response with leads
        response_text = """
        Based on initial research, we found quantum computing developments in 2024.
        However, we need to investigate: what are the specific breakthroughs by IBM?
        There is also uncertainty regarding Google's claimed quantum supremacy metrics.
        """

        leads = manager.process_turn_response(response_text, turn=1)

        # Should extract leads
        assert len(leads) > 0, "Should extract at least one lead from response"

        # Record follow-up
        if leads:
            manager.record_follow_up(
                leads[0], turn=2, findings="Found IBM's quantum processor details"
            )

        # Get trail section
        trail = manager.get_trail_section()
        assert "Lead Trail" in trail or "线索追踪" in trail
        assert manager.trail.follow_up_count > 0


class TestAcceptanceD:
    """D. Same query, searxng-only vs parallel-trusted: effective_config differs; metrics show route difference."""

    def test_search_profile_affects_effective_config(self):
        """Verify different search profiles produce different effective configs."""
        effective_searxng = resolve_effective_research_params(
            mode="balanced",
            search_profile="searxng-only",
        )

        effective_parallel = resolve_effective_research_params(
            mode="balanced",
            search_profile="parallel-trusted",
        )

        assert effective_searxng.search_profile == "searxng-only"
        assert effective_parallel.search_profile == "parallel-trusted"

    def test_search_profile_env_mapping(self):
        """Verify search profiles produce different environment configurations."""
        from services.profile_resolver import build_full_overrides

        env_searxng, _ = build_full_overrides(
            mode="balanced",
            search_profile="searxng-only",
            search_result_num=None,
            verification_min_search_rounds=None,
            output_detail_level=None,
        )

        env_parallel, _ = build_full_overrides(
            mode="balanced",
            search_profile="parallel-trusted",
            search_result_num=None,
            verification_min_search_rounds=None,
            output_detail_level=None,
        )

        # Verify different search provider configurations
        assert (
            env_searxng["SEARCH_PROVIDER_ORDER"]
            != env_parallel["SEARCH_PROVIDER_ORDER"]
        )
        assert (
            env_searxng["SEARCH_PROVIDER_MODE"] != env_parallel["SEARCH_PROVIDER_MODE"]
        )
        assert env_searxng.get("SEARCH_PROVIDER_ORDER_STRICT") == "1"
        assert env_parallel.get("SEARCH_PROVIDER_ORDER_STRICT") != "1"


class TestAcceptanceE:
    """E. Same deep query, compact vs detailed: compact ~30s scannable; detailed full sections without duplicate fluff."""

    def test_detail_level_affects_output_constraints(self):
        """Verify different detail levels produce different output constraints."""
        from services.profile_resolver import get_mode_overrides_for_output_detail

        overrides_compact = get_mode_overrides_for_output_detail("compact")
        overrides_detailed = get_mode_overrides_for_output_detail("detailed")

        def get_max_tokens(overrides):
            for override in overrides:
                if "llm.max_tokens=" in override:
                    return int(override.split("=")[1])
            return None

        def get_max_turns(overrides):
            for override in overrides:
                if "main_agent.max_turns=" in override:
                    return int(override.split("=")[1])
            return None

        tokens_compact = get_max_tokens(overrides_compact)
        tokens_detailed = get_max_tokens(overrides_detailed)
        turns_compact = get_max_turns(overrides_compact)
        turns_detailed = get_max_turns(overrides_detailed)

        assert tokens_compact is not None
        assert tokens_detailed is not None
        assert tokens_compact < tokens_detailed, "Compact should have lower token limit"

        assert turns_compact is not None
        assert turns_detailed is not None
        assert turns_compact < turns_detailed, "Compact should have fewer turns"

    def test_structure_validation_per_detail_level(self):
        """Verify structure validation has different requirements per detail level."""
        from src.io.report_structure import ReportStructureValidator

        # Get required sections for each level
        sections_compact = ReportStructureValidator.REQUIRED_SECTIONS["compact"]
        sections_detailed = ReportStructureValidator.REQUIRED_SECTIONS["detailed"]

        # Detailed should have more required sections
        assert len(sections_detailed) > len(sections_compact)

        # Both should require TL;DR and conclusion
        compact_names = [s.name for s in sections_compact]
        detailed_names = [s.name for s in sections_detailed]

        assert "tldr" in compact_names
        assert "conclusion" in compact_names
        assert "tldr" in detailed_names
        assert "conclusion" in detailed_names
        assert "evidence" in detailed_names


class TestRunMetricsExtensions:
    """Test that RunMetrics captures all new fields."""

    def test_run_metrics_has_new_fields(self):
        """Verify RunMetrics includes new tracking fields."""
        from src.logging.task_logger import RunMetrics

        metrics = RunMetrics()

        # Phase 1 fields
        assert hasattr(metrics, "search_rounds")
        assert hasattr(metrics, "scrape_count")
        assert hasattr(metrics, "follow_up_searches")
        assert hasattr(metrics, "effective_config")

        # Test helper methods
        metrics.record_search_round()
        assert metrics.search_rounds == 1

        metrics.record_scrape(3)
        assert metrics.scrape_count == 3

        metrics.record_follow_up_search()
        assert metrics.follow_up_searches == 1

        metrics.set_effective_config(
            mode="verified",
            search_profile="parallel-trusted",
            search_result_num=30,
            verification_min_search_rounds=4,
            output_detail_level="detailed",
            research_intensity="deep",
        )
        assert metrics.effective_config["research_intensity"] == "deep"
        assert metrics.effective_config["mode"] == "verified"


class TestReportStructureValidation:
    """Test report structure validation (Phase 2)."""

    def test_valid_balanced_report(self):
        """Test that a well-structured report passes validation."""
        from src.io.report_structure import ReportStructureValidator

        report = """
## TL;DR

Paris is the capital of France, located in the north-central part of the country.

## Conclusion

Based on multiple authoritative sources, Paris has been the capital of France
since the late medieval period. It serves as the political, economic, and cultural
center of the nation.

## Evidence

Historical records and government sources confirm this [1][2][3].

## References

1. French Government Official Site
2. Encyclopedia Britannica
3. CIA World Factbook
"""

        is_valid, issues, metadata = ReportStructureValidator.validate_structure(
            report, "balanced"
        )

        assert is_valid, f"Report should be valid, but has issues: {issues}"
        assert len(metadata["found_sections"]) >= 2
        assert metadata["has_citations"]

    def test_invalid_report_missing_sections(self):
        """Test that reports missing sections are detected."""
        from src.io.report_structure import ReportStructureValidator

        incomplete_report = "Paris is the capital. That's all."

        is_valid, issues, metadata = ReportStructureValidator.validate_structure(
            incomplete_report, "balanced"
        )

        assert not is_valid
        assert len(issues) > 0
        assert len(metadata["missing_required"]) > 0


class TestEffectiveConfigTracking:
    """Test effective_config tracking through the pipeline."""

    def test_effective_config_in_task_meta(self):
        """Verify TaskMeta can store effective_config."""
        from services.task_store import TaskMeta, TaskStatus

        meta = TaskMeta(
            task_id="test-123",
            status=TaskStatus.QUEUED,
            query="test query",
            research_intensity="deep",
            effective_config={
                "mode": "verified",
                "search_profile": "parallel-trusted",
                "research_intensity": "deep",
            },
        )

        # Convert to dict and back
        meta_dict = meta.to_dict()
        assert "effective_config" in meta_dict

        restored = TaskMeta.from_dict(meta_dict)
        assert restored.effective_config is not None
        assert restored.effective_config["research_intensity"] == "deep"
