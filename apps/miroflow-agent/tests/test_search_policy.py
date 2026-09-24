"""入口共享的检索/预算优先级规则。

api-server 与 gradio-demo 都靠这两个 helper 让 .env 里的用户设置在
profile/mode 预设面前真正生效，因此规则本身需要独立回归测试。
"""

from src.config.search_policy import (
    apply_explicit_budget_overrides,
    apply_user_search_env_precedence,
)


def test_user_threshold_envs_survive_profile_preset(monkeypatch):
    """用户显式设置的 SEARCH_CONFIDENCE_* 阈值不再被 profile 预设覆盖。"""
    profile_env = {
        "SEARCH_CONFIDENCE_MIN_SCORE": "0.5",
        "SEARCH_CONFIDENCE_MIN_PROVIDER_COVERAGE": "2",
        "SEARCH_PROVIDER_ORDER": "serpapi,tavily,serper",
    }
    for env_key in profile_env:
        monkeypatch.delenv(env_key, raising=False)
    monkeypatch.setenv("SEARCH_CONFIDENCE_MIN_SCORE", "0.95")

    resolved = apply_user_search_env_precedence(profile_env)

    # 剔除预设值后，.env 里的 0.95 才会生效；其余键不受影响
    assert "SEARCH_CONFIDENCE_MIN_SCORE" not in resolved
    assert resolved["SEARCH_CONFIDENCE_MIN_PROVIDER_COVERAGE"] == "2"
    assert resolved["SEARCH_PROVIDER_ORDER"] == "serpapi,tavily,serper"


def test_profile_values_still_apply_when_user_silent(monkeypatch):
    """用户未显式设置（或只写了空白）时，profile 预设继续生效。"""
    profile_env = {"SEARCH_CONFIDENCE_MIN_SCORE": "0.5"}
    monkeypatch.delenv("SEARCH_CONFIDENCE_MIN_SCORE", raising=False)

    assert apply_user_search_env_precedence(dict(profile_env)) == profile_env

    monkeypatch.setenv("SEARCH_CONFIDENCE_MIN_SCORE", "   ")
    assert apply_user_search_env_precedence(dict(profile_env)) == profile_env


def test_explicit_budget_env_outranks_mode_preset(monkeypatch):
    """用户显式 LLM_MAX_TOKENS 必须排在模式预设之后（Hydra 后者胜出）。"""
    monkeypatch.setenv("LLM_MAX_TOKENS", "16384")
    overrides = [
        "agent.output_detail_level=detailed",
        "llm.max_tokens=3072",  # 模式预设位置
    ]

    resolved = apply_explicit_budget_overrides(overrides)

    assert "llm.max_tokens=3072" not in resolved
    assert "agent.output_detail_level=detailed" in resolved
    assert resolved.index("llm.max_tokens=16384") > resolved.index(
        "agent.output_detail_level=detailed"
    )
