"""profile_resolver 单元测试。

覆盖 normalize_*、build_search_env、build_mode_overrides、build_full_overrides
五个核心函数，确保 worker 的策略解析与 gradio-demo 行为对齐。
"""

import pytest

from services import profile_resolver as pr


# ---- normalize_research_mode ---------------------------------------------
class TestNormalizeResearchMode:
    def test_default_when_none(self, monkeypatch):
        monkeypatch.delenv("DEFAULT_RESEARCH_MODE", raising=False)
        assert pr.normalize_research_mode(None) == "balanced"

    def test_default_from_env(self, monkeypatch):
        monkeypatch.setenv("DEFAULT_RESEARCH_MODE", "verified")
        assert pr.normalize_research_mode(None) == "verified"

    def test_unknown_falls_back_to_balanced(self):
        assert pr.normalize_research_mode("unknown-mode") == "balanced"

    def test_case_insensitive_and_strip(self):
        assert pr.normalize_research_mode("  Verified  ") == "verified"

    @pytest.mark.parametrize(
        "mode",
        ["production-web", "verified", "research", "balanced", "quota", "thinking"],
    )
    def test_known_modes_pass_through(self, mode):
        assert pr.normalize_research_mode(mode) == mode


# ---- normalize_search_profile --------------------------------------------
class TestNormalizeSearchProfile:
    def test_default_when_none(self, monkeypatch):
        monkeypatch.delenv("DEFAULT_SEARCH_PROFILE", raising=False)
        assert pr.normalize_search_profile(None) == "searxng-first"

    def test_unknown_falls_back(self):
        assert pr.normalize_search_profile("foo-bar") == "searxng-first"

    @pytest.mark.parametrize(
        "profile", list(pr.SEARCH_PROFILE_ENV_MAP.keys()),
    )
    def test_known_profiles_pass_through(self, profile):
        assert pr.normalize_search_profile(profile) == profile


# ---- normalize_search_result_num -----------------------------------------
class TestNormalizeSearchResultNum:
    def test_default_when_none(self, monkeypatch):
        monkeypatch.delenv("DEFAULT_SEARCH_RESULT_NUM", raising=False)
        assert pr.normalize_search_result_num(None) == 20

    def test_invalid_falls_back_to_min(self):
        # 99 不在 [10, 20, 30] 内 → 取 choices[0]=10
        assert pr.normalize_search_result_num(99) == 10

    def test_string_int_accepted(self):
        assert pr.normalize_search_result_num("30") == 30

    def test_non_numeric_string_falls_back_to_default(self, monkeypatch):
        # 解析失败 → 回落到 _default_search_result_num()（默认 20）
        monkeypatch.delenv("DEFAULT_SEARCH_RESULT_NUM", raising=False)
        assert pr.normalize_search_result_num("abc") == 20


# ---- normalize_verification_min_search_rounds ----------------------------
class TestNormalizeVerificationMinSearchRounds:
    def test_clamp_lower_bound(self):
        assert pr.normalize_verification_min_search_rounds(0) == 1
        assert pr.normalize_verification_min_search_rounds(-5) == 1

    def test_clamp_upper_bound(self):
        assert (
            pr.normalize_verification_min_search_rounds(99)
            == pr.MAX_VERIFICATION_MIN_SEARCH_ROUNDS
        )

    def test_default_when_none(self, monkeypatch):
        monkeypatch.delenv("DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS", raising=False)
        assert pr.normalize_verification_min_search_rounds(None) == 3


# ---- resolve_effective_min_search_rounds ---------------------------------
class TestResolveEffectiveMinSearchRounds:
    def test_only_verified_uses_user_value(self, monkeypatch):
        monkeypatch.delenv("DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS", raising=False)
        assert pr.resolve_effective_min_search_rounds("verified", 6) == 6

    def test_other_modes_force_default(self, monkeypatch):
        monkeypatch.setenv("DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS", "4")
        # balanced 模式忽略用户传入，固定为 default
        assert pr.resolve_effective_min_search_rounds("balanced", 8) == 4


# ---- normalize_output_detail_level ---------------------------------------
class TestNormalizeOutputDetailLevel:
    def test_default_when_none(self, monkeypatch):
        monkeypatch.delenv("DEFAULT_OUTPUT_DETAIL_LEVEL", raising=False)
        assert pr.normalize_output_detail_level(None) == "detailed"

    @pytest.mark.parametrize("level", ["compact", "balanced", "detailed"])
    def test_known_levels(self, level):
        assert pr.normalize_output_detail_level(level) == level

    def test_unknown_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("DEFAULT_OUTPUT_DETAIL_LEVEL", "compact")
        assert pr.normalize_output_detail_level("xxx") == "compact"


# ---- get_mode_overrides_for_output_detail --------------------------------
class TestGetModeOverridesForOutputDetail:
    def test_compact_marker(self):
        overrides = pr.get_mode_overrides_for_output_detail("compact")
        assert "++agent.output_detail_level=compact" in overrides
        assert any(o.startswith("agent.main_agent.max_turns=") for o in overrides)

    def test_balanced_marker(self):
        overrides = pr.get_mode_overrides_for_output_detail("balanced")
        assert "++agent.output_detail_level=balanced" in overrides

    def test_detailed_marker(self):
        overrides = pr.get_mode_overrides_for_output_detail("detailed")
        assert "++agent.output_detail_level=detailed" in overrides
        assert "++agent.research_report_mode=true" in overrides


# ---- build_search_env ----------------------------------------------------
class TestBuildSearchEnv:
    def test_includes_search_result_num(self):
        env = pr.build_search_env("searxng-first", 30)
        assert env["SEARCH_RESULT_NUM"] == "30"
        assert env["SEARCH_PROVIDER_ORDER"] == "searxng,serpapi,tavily,serper"

    def test_parallel_trusted_has_confidence_keys(self):
        env = pr.build_search_env("parallel-trusted", 20)
        assert env["SEARCH_PROVIDER_MODE"] == "parallel_conf_fallback"
        assert "SEARCH_CONFIDENCE_ENABLED" in env
        assert env["SEARCH_RESULT_NUM"] == "20"

    def test_unknown_profile_falls_back_to_searxng_first(self):
        # build_search_env 不做 normalize，但参数缺失会兜底（用于内部调用）
        env = pr.build_search_env("definitely-not-exist", 10)
        assert env["SEARCH_PROVIDER_ORDER"] == "searxng,serpapi,tavily,serper"


# ---- build_mode_overrides -------------------------------------------------
class TestBuildModeOverrides:
    def test_verified_picks_demo_verified_search(self):
        overrides = pr.build_mode_overrides("verified")
        assert "agent=demo_verified_search" in overrides
        assert "agent.main_agent.max_turns=14" in overrides

    def test_balanced_picks_demo_search_only(self):
        overrides = pr.build_mode_overrides("balanced")
        assert "agent=demo_search_only" in overrides
        assert "agent.main_agent.max_turns=11" in overrides

    def test_thinking_picks_demo_no_tools(self):
        overrides = pr.build_mode_overrides("thinking")
        assert "agent=demo_no_tools" in overrides

    def test_quota_uses_fast_model(self, monkeypatch):
        monkeypatch.setenv("DEFAULT_MODEL_NAME", "default-model")
        monkeypatch.setenv("MODEL_FAST_NAME", "fast-model")
        overrides = pr.build_mode_overrides("quota")
        assert "llm.model_name=fast-model" in overrides

    def test_production_web_picks_prod_search_only(self):
        overrides = pr.build_mode_overrides("production-web")
        assert "agent=prod_search_only" in overrides


# ---- build_full_overrides -------------------------------------------------
class TestBuildFullOverrides:
    def test_verified_includes_min_search_rounds(self):
        env, overrides = pr.build_full_overrides(
            mode="verified",
            search_profile="parallel-trusted",
            search_result_num=20,
            verification_min_search_rounds=5,
            output_detail_level="detailed",
        )
        assert env["SEARCH_PROVIDER_MODE"] == "parallel_conf_fallback"
        assert "agent=demo_verified_search" in overrides
        assert "agent.verification.min_search_rounds=5" in overrides
        assert "++agent.output_detail_level=detailed" in overrides

    def test_balanced_skips_min_search_rounds(self):
        _, overrides = pr.build_full_overrides(
            mode="balanced",
            search_profile="searxng-first",
            search_result_num=10,
            verification_min_search_rounds=8,  # 应被忽略
            output_detail_level="balanced",
        )
        assert not any("verification.min_search_rounds" in o for o in overrides)

    def test_normalizes_invalid_inputs(self, monkeypatch):
        monkeypatch.delenv("DEFAULT_RESEARCH_MODE", raising=False)
        monkeypatch.delenv("DEFAULT_SEARCH_PROFILE", raising=False)
        monkeypatch.delenv("DEFAULT_OUTPUT_DETAIL_LEVEL", raising=False)
        env, overrides = pr.build_full_overrides(
            mode="bogus",
            search_profile="bogus",
            search_result_num=999,
            verification_min_search_rounds=None,
            output_detail_level="bogus",
        )
        # 全部回落到默认值
        assert "agent=demo_search_only" in overrides  # balanced 默认
        assert env["SEARCH_PROVIDER_ORDER"] == "searxng,serpapi,tavily,serper"
        assert env["SEARCH_RESULT_NUM"] == "10"  # 999 不在 choices 中 → choices[0]
        assert "++agent.output_detail_level=detailed" in overrides

    def test_string_search_result_num_accepted(self):
        env, _ = pr.build_full_overrides(
            mode="balanced",
            search_profile="searxng-first",
            search_result_num="30",  # type: ignore[arg-type]
            verification_min_search_rounds=3,
            output_detail_level="detailed",
        )
        assert env["SEARCH_RESULT_NUM"] == "30"
