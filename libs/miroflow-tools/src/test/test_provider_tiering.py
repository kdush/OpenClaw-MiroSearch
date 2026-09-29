"""M5：检索 provider / profile 自动分档测试矩阵。

覆盖路线图要求的：单 Serper、单 SearXNG、多 provider、无凭据、运行中一路超时，
并验证各档置信门槛（provider 覆盖要求）在当前配置下可达。
"""

from miroflow_tools.dev_mcp_servers.providers.tiering import (
    TIER_MULTI,
    TIER_NONE,
    TIER_SINGLE,
    resolve_provider_tier,
)


def test_single_serper_tier():
    decision = resolve_provider_tier("serp-first", ["serper"])

    assert decision.tier == TIER_SINGLE
    assert decision.profile == "serp-first"
    assert decision.effective_order == ["serper"]
    assert decision.min_provider_coverage == 1
    assert decision.degraded_from == ""


def test_single_searxng_tier():
    decision = resolve_provider_tier("searxng-first", ["searxng"])

    assert decision.tier == TIER_SINGLE
    assert decision.profile == "searxng-first"
    assert decision.effective_order == ["searxng"]


def test_multi_provider_auto_upgrades_and_records_degradation():
    decision = resolve_provider_tier("serp-first", ["serper", "searxng"])

    assert decision.tier == TIER_MULTI
    assert decision.profile == "multi-route"
    assert decision.min_provider_coverage == 2
    # 请求的是单档 profile，实际升档 → 记录降级/变更路径
    assert decision.degraded_from == "serp-first"
    assert "自动升档" in decision.reason


def test_no_credentials_yields_no_provider_tier():
    decision = resolve_provider_tier("serp-first", [])

    assert decision.tier == TIER_NONE
    assert decision.effective_order == []
    assert decision.min_provider_coverage == 0
    assert "无可用 provider" in decision.reason


def test_mid_run_timeout_downgrades_to_single_tier():
    """运行中一路超时：把它从健康集合剔除后按单档重算。"""
    decision = resolve_provider_tier(
        "multi-route", ["serper", "searxng"], healthy={"serper"}
    )

    assert decision.tier == TIER_SINGLE
    assert decision.effective_order == ["serper"]
    assert decision.min_provider_coverage == 1
    assert decision.degraded_from == "multi-route"


def test_strict_route_is_not_silently_expanded():
    decision = resolve_provider_tier(
        "searxng-only",
        ["serper", "searxng"],
        requested_order="searxng",
        strict=True,
    )

    assert decision.strict is True
    assert decision.effective_order == ["searxng"]
    assert decision.profile == "searxng-only"


def test_strict_route_without_its_provider_is_unavailable():
    decision = resolve_provider_tier(
        "searxng-only", ["serper"], requested_order="searxng", strict=True
    )

    assert decision.tier == TIER_NONE
    assert decision.effective_order == []
    assert "严格路由" in decision.reason


def test_confidence_threshold_is_reachable_in_every_tier():
    """禁止出现"所选 profile 的要求在当前配置下结构性不可达"。"""
    cases = [
        ("serp-first", ["serper"], None),
        ("serp-first", ["serper", "searxng"], None),
        ("serp-first", ["serper", "searxng", "tavily"], {"serper", "searxng"}),
        ("searxng-only", ["searxng"], None),
    ]
    for profile, available, healthy in cases:
        decision = resolve_provider_tier(
            profile, available, requested_order=",".join(available), healthy=healthy
        )
        assert decision.min_provider_coverage <= len(decision.effective_order)


def test_decision_is_serializable():
    import json

    decision = resolve_provider_tier("serp-first", ["serper", "searxng"])
    payload = decision.to_dict()

    assert payload["tier"] == TIER_MULTI
    assert json.loads(json.dumps(payload)) == payload
