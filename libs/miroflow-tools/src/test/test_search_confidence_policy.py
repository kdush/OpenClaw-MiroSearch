"""检索质量门控：单 provider 部署下 confidence 硬约束必须可达。

回归目标：
- 只配 1 个检索源时，``MIN_PROVIDER_COVERAGE=2`` 的门控永远达不到，会白白耗尽
  补检轮次；门槛钳制到实际 provider 数后 verdict 才可用。
- confidence 只看 provider 实际返回的结果，响应级 flag 不能替代结果。
"""

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

_SEARCH_MODULE = "miroflow_tools.dev_mcp_servers.search_and_scrape_webpage"


def _reset_search_env(monkeypatch) -> None:
    """把 confidence 相关 env 清零到默认值，使测试对 .env/系统环境不可见。"""
    for env_key in (
        "SEARCH_PROVIDER_ORDER",
        "SEARCH_PROVIDER_TRUSTED_ORDER",
        "SEARCH_PROFILE",
        "SEARCH_CONFIDENCE_ENABLED",
        "SEARCH_CONFIDENCE_SCORE_THRESHOLD",
        "SEARCH_CONFIDENCE_MIN_RESULTS",
        "SEARCH_CONFIDENCE_MIN_UNIQUE_DOMAINS",
        "SEARCH_CONFIDENCE_MIN_PROVIDER_COVERAGE",
        "SEARCH_CONFIDENCE_MIN_HIGH_CONF_HITS",
        "SEARCH_CONFIDENCE_HIGH_CONF_DOMAINS",
        "SEARXNG_BASE_URL",
        "SERPER_API_KEY",
        "SERPAPI_API_KEY",
        "TAVILY_API_KEY",
    ):
        monkeypatch.delenv(env_key, raising=False)


def _reload_search_module():
    """重建模块级常量（env 只在 import 时读取）。"""
    sys.modules.pop(_SEARCH_MODULE, None)
    return importlib.import_module(_SEARCH_MODULE)


def _set_single_provider_env(monkeypatch) -> None:
    _reset_search_env(monkeypatch)
    monkeypatch.setenv("SEARCH_PROVIDER_ORDER", "serper")
    monkeypatch.setenv("SERPER_API_KEY", "test-key")
    # 门槛压到单条结果可达，聚焦"钳制后的 provider 覆盖数不再挡住门控"
    monkeypatch.setenv("SEARCH_CONFIDENCE_MIN_RESULTS", "1")
    monkeypatch.setenv("SEARCH_CONFIDENCE_MIN_UNIQUE_DOMAINS", "1")
    monkeypatch.setenv("SEARCH_CONFIDENCE_MIN_HIGH_CONF_HITS", "1")


@pytest.mark.unit
def test_single_provider_confidence_gate_reachable(monkeypatch):
    """单 provider 环境：MIN_PROVIDER_COVERAGE 钳制到实际 provider 数，
    confidence 校验可用且 verdict 正确。"""
    _set_single_provider_env(monkeypatch)
    module = _reload_search_module()

    organic_results = [
        {
            "title": "Reuters 报道",
            "link": "https://www.reuters.com/world/example",
            "snippet": "示例摘要。",
            "source": "serper",
        }
    ]
    search_params: dict = {"provider": "serper"}

    module._ensure_confidence_evaluated(organic_results, search_params)
    verdict = module._confidence_verdict_line(search_params)

    assert search_params["confidence"]["constraints"]["min_provider_coverage"] == 1
    assert search_params["confidence"]["passed"] is True
    assert "passed=yes" in verdict
    assert "providers=1/1" in verdict


@pytest.mark.unit
def test_single_provider_confidence_fail_reports_no(monkeypatch):
    """结果为空时报 passed=no，而不是因 provider_coverage 硬约束不可达
    而永远 passed=yes。"""
    _set_single_provider_env(monkeypatch)
    module = _reload_search_module()

    search_params: dict = {"provider": "serper"}

    module._ensure_confidence_evaluated([], search_params)
    verdict = module._confidence_verdict_line(search_params)

    assert search_params["confidence"]["passed"] is False
    assert "passed=no" in verdict


@pytest.mark.unit
def test_confidence_follows_actual_results(monkeypatch):
    """响应级 flag 乐观也不能让空结果通过门控。"""
    _set_single_provider_env(monkeypatch)
    module = _reload_search_module()

    search_params: dict = {
        "provider": "serper",
        "low_confidence": False,
        "provider_ok": True,
    }

    module._ensure_confidence_evaluated([], search_params)

    assert search_params["confidence"]["passed"] is False


@pytest.mark.unit
def test_searxng_only_confidence_ignores_other_available_providers(monkeypatch):
    """Route allows only searxng; commercial keys in env must not raise coverage."""
    _reset_search_env(monkeypatch)
    monkeypatch.setenv("SEARCH_PROVIDER_ORDER", "searxng")
    monkeypatch.setenv("SEARCH_PROVIDER_ORDER_STRICT", "1")
    monkeypatch.setenv("SEARXNG_BASE_URL", "http://127.0.0.1:27080")
    monkeypatch.setenv("SERPER_API_KEY", "commercial-key")
    monkeypatch.setenv("SERPAPI_API_KEY", "commercial-key")
    monkeypatch.setenv("SEARCH_CONFIDENCE_MIN_RESULTS", "1")
    monkeypatch.setenv("SEARCH_CONFIDENCE_MIN_UNIQUE_DOMAINS", "1")
    monkeypatch.setenv("SEARCH_CONFIDENCE_MIN_HIGH_CONF_HITS", "1")
    monkeypatch.setenv("SEARCH_CONFIDENCE_MIN_PROVIDER_COVERAGE", "2")
    module = _reload_search_module()

    organic = [
        {
            "title": "Reuters",
            "link": "https://www.reuters.com/world/example",
            "snippet": "ok",
            "source": "searxng",
        }
    ]
    confidence = module._evaluate_confidence(
        organic,
        {"searxng"},
        allowed_providers=["searxng"],
    )
    assert confidence["constraints"]["min_provider_coverage"] == 1
    assert confidence["passed"] is True

    # Without allowed_providers the ceiling falls back to every credentialed
    # provider (searxng + serper + serpapi), so coverage 1 no longer satisfies it.
    wide = module._evaluate_confidence(organic, {"searxng"})
    assert wide["constraints"]["min_provider_coverage"] == 2
    assert wide["passed"] is False


@pytest.mark.unit
def test_confidence_floor_follows_resolved_provider_tier(monkeypatch):
    """M5：生效档位决定覆盖门槛——单 provider 档不得被套上多 provider 默认值。"""
    _reset_search_env(monkeypatch)
    module = _reload_search_module()

    results = [{"title": "t", "link": "https://a.com/x", "snippet": "s"}]

    single = module._evaluate_confidence(
        results,
        {"serper"},
        allowed_providers=["serper", "searxng"],
        min_provider_coverage=1,
    )
    multi = module._evaluate_confidence(
        results,
        {"serper"},
        allowed_providers=["serper", "searxng"],
        min_provider_coverage=2,
    )
    default = module._evaluate_confidence(
        results,
        {"serper"},
        allowed_providers=["serper", "searxng"],
    )

    assert single["constraints"]["min_provider_coverage"] == 1
    assert multi["constraints"]["min_provider_coverage"] == 2
    # 不传档位时保持历史默认
    assert default["constraints"]["min_provider_coverage"] == 2


@pytest.mark.unit
def test_tier_floor_still_capped_by_reachable_route(monkeypatch):
    """档位门槛高于本路由实际可达数时仍被钳制，避免结构性不可达。"""
    _reset_search_env(monkeypatch)
    module = _reload_search_module()

    confidence = module._evaluate_confidence(
        [{"title": "t", "link": "https://a.com/x", "snippet": "s"}],
        {"serper"},
        allowed_providers=["serper"],
        min_provider_coverage=2,
    )

    assert confidence["constraints"]["min_provider_coverage"] == 1


@pytest.mark.unit
def test_no_provider_tier_relaxes_coverage_floor(monkeypatch):
    """no-provider 档（min_provider_coverage=0）不得被强制抬回 1。"""
    _reset_search_env(monkeypatch)
    module = _reload_search_module()

    confidence = module._evaluate_confidence(
        [],
        set(),
        allowed_providers=["serper", "searxng"],
        min_provider_coverage=0,
    )

    assert confidence["constraints"]["min_provider_coverage"] == 0


@pytest.mark.unit
def test_serial_fallback_reads_tier_floor_from_search_params(monkeypatch):
    """串行回退路径同样要吃到档位门槛，否则门控只在并发路由下生效。"""
    _reset_search_env(monkeypatch)
    module = _reload_search_module()

    search_params = {
        "provider": "serper",
        "provider_order": ["serper", "searxng"],
        "provider_tier": {"tier": "single-provider", "min_provider_coverage": 1},
    }
    module._ensure_confidence_evaluated(
        [{"title": "t", "link": "https://a.com/x", "snippet": "s"}], search_params
    )

    assert search_params["confidence"]["constraints"]["min_provider_coverage"] == 1


@pytest.mark.unit
def test_serial_fallback_without_tier_keeps_default_floor(monkeypatch):
    _reset_search_env(monkeypatch)
    module = _reload_search_module()

    search_params = {
        "provider": "serper",
        "provider_order": ["serper", "searxng"],
    }
    module._ensure_confidence_evaluated(
        [{"title": "t", "link": "https://a.com/x", "snippet": "s"}], search_params
    )

    assert search_params["confidence"]["constraints"]["min_provider_coverage"] == 2


@pytest.mark.unit
def test_single_provider_deployment_tier_is_reachable(monkeypatch):
    """端到端：只配 serper 时档位判为单 provider，门槛 1，confidence 可用。"""
    _set_single_provider_env(monkeypatch)
    module = _reload_search_module()

    from miroflow_tools.dev_mcp_servers.providers.tiering import resolve_provider_tier

    decision = resolve_provider_tier(
        module.SEARCH_PROVIDER_ORDER,
        module._registry.available_names(),
        requested_order=module.SEARCH_PROVIDER_ORDER,
        strict=module.SEARCH_PROVIDER_ORDER_STRICT,
    )

    assert decision.tier == "single-provider"
    assert decision.min_provider_coverage == 1
    assert decision.effective_order == ["serper"]

    confidence = module._evaluate_confidence(
        [{"title": "t", "link": "https://a.com/x", "snippet": "s"}],
        {"serper"},
        allowed_providers=decision.effective_order,
        min_provider_coverage=decision.min_provider_coverage,
    )
    assert confidence["constraints"]["min_provider_coverage"] == 1
    assert confidence["metrics"]["provider_coverage"] == 1


@pytest.mark.unit
def test_search_profile_env_drives_degradation_attribution(monkeypatch):
    """M5：SEARCH_PROFILE 让档位决策准确记录「从哪个 profile 降级」。

    provider 顺序串无法反推 profile 名（serp-first 与 parallel-trusted 共用同一
    顺序串），所以归因只能靠显式传入的 profile 名。
    """
    _set_single_provider_env(monkeypatch)
    monkeypatch.setenv("SEARCH_PROFILE", "parallel-trusted")
    module = _reload_search_module()

    assert module.SEARCH_PROFILE == "parallel-trusted"

    from miroflow_tools.dev_mcp_servers.providers.tiering import resolve_provider_tier

    decision = resolve_provider_tier(
        module.SEARCH_PROFILE,
        module._registry.available_names(),
        requested_order=module.SEARCH_PROVIDER_ORDER,
        strict=module.SEARCH_PROVIDER_ORDER_STRICT,
    )

    # 只配 serper 却请求 parallel-trusted → 降级到单 provider 档，归因准确
    assert decision.tier == "single-provider"
    assert decision.degraded_from == "parallel-trusted"
    assert decision.min_provider_coverage == 1


@pytest.mark.unit
def test_absent_search_profile_leaves_attribution_empty(monkeypatch):
    """无 profile 概念时归因留空，而不是把 provider 顺序串误当 profile 名。"""
    _set_single_provider_env(monkeypatch)
    monkeypatch.delenv("SEARCH_PROFILE", raising=False)
    module = _reload_search_module()

    assert module.SEARCH_PROFILE == ""

    from miroflow_tools.dev_mcp_servers.providers.tiering import resolve_provider_tier

    decision = resolve_provider_tier(
        module.SEARCH_PROFILE,
        module._registry.available_names(),
        requested_order=module.SEARCH_PROVIDER_ORDER,
        strict=module.SEARCH_PROVIDER_ORDER_STRICT,
    )

    assert decision.tier == "single-provider"
    # 关键回归：不能是 "serper"（那是 provider 名，不是 profile 名）
    assert decision.degraded_from == ""
