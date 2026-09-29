"""M5 运行中降档：真实 ``google_search`` 路径必须按检索结果更新档位与门槛。

评审阻塞项回归：此前只在发起检索前调用一次 ``resolve_provider_tier``，一路
provider 超时后 ``provider_tier`` 仍记录为 ``multi-provider``，置信度仍按两路
覆盖门槛判定——档位、门槛与降级原因都与实际路由不符。
"""

import asyncio
import importlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

_MODULE = "miroflow_tools.dev_mcp_servers.search_and_scrape_webpage"

_ENV = {
    "SEARCH_PROVIDER_ORDER": "fast,slow",
    "SEARCH_PROVIDER_MODE": "parallel",
    "SEARCH_PROFILE": "",
    "SEARCH_PROVIDER_ORDER_STRICT": "",
    "SEARCH_CONFIDENCE_ENABLED": "true",
    "SEARCH_CONFIDENCE_SCORE_THRESHOLD": "0",
    "SEARCH_CONFIDENCE_MIN_RESULTS": "1",
    "SEARCH_CONFIDENCE_MIN_UNIQUE_DOMAINS": "1",
    "SEARCH_CONFIDENCE_MIN_HIGH_CONF_HITS": "1",
    "SEARCH_CONFIDENCE_MIN_PROVIDER_COVERAGE": "2",
}


def _reload(monkeypatch):
    for key, value in _ENV.items():
        monkeypatch.setenv(key, value)
    for key in (
        "SEARXNG_BASE_URL",
        "SERPER_API_KEY",
        "SERPAPI_API_KEY",
        "TAVILY_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    sys.modules.pop(_MODULE, None)
    return importlib.import_module(_MODULE)


def _fake_registry(search_mod, *, slow_sleep: float):
    from miroflow_tools.dev_mcp_servers.providers.base import SearchResult
    from miroflow_tools.dev_mcp_servers.providers.registry import ProviderRegistry

    class _FastProvider:
        @property
        def name(self):
            return "fast"

        def is_available(self):
            return True

        async def search(self, _params):
            return (
                [
                    SearchResult(
                        position=1,
                        title="Reuters 报道",
                        link="https://www.reuters.com/world/example",
                        snippet="示例摘要。",
                        source="fast",
                    )
                ],
                {"provider": "fast"},
            )

    class _SlowProvider:
        @property
        def name(self):
            return "slow"

        def is_available(self):
            return True

        async def search(self, _params):
            await asyncio.sleep(slow_sleep)
            return [], {"provider": "slow"}

    registry = ProviderRegistry()
    registry.register(_FastProvider())
    registry.register(_SlowProvider())
    return registry


@pytest.mark.asyncio
async def test_one_provider_timeout_downgrades_tier_and_floor(monkeypatch):
    search_mod = _reload(monkeypatch)
    monkeypatch.setattr(
        search_mod, "_registry", _fake_registry(search_mod, slow_sleep=5)
    )
    # 缩短并发等待，让慢 provider 必然超时（模块常量在调用时读取）
    monkeypatch.setattr(search_mod, "SEARCH_PROVIDER_PARALLEL_MAX_WAIT_MS", 60)

    payload = json.loads(await search_mod.google_search("test query", num=3))

    params = payload["searchParameters"]
    tier = params["provider_tier"]
    # 一路超时 → 档位必须按剩余健康 provider 重算
    assert tier["tier"] == "single-provider"
    assert tier["min_provider_coverage"] == 1
    assert tier["effective_order"] == ["fast"]
    assert tier["degraded_from"] == "multi-provider"
    assert "slow" in tier["reason"] and "不健康" in tier["reason"]

    # 门槛跟着降，否则单路覆盖永远不可达
    assert params["confidence"]["constraints"]["min_provider_coverage"] == 1
    assert params["confidence"]["passed"] is True

    # 降级可归因：route_trace 里留下超时记录
    trace = params["route_trace"]
    assert any(
        entry["provider"] == "slow" and entry["status"] == "timeout" for entry in trace
    )


@pytest.mark.asyncio
async def test_healthy_providers_keep_multi_tier(monkeypatch):
    """两路都健康时不降档：档位仍是 multi-provider、门槛仍是 2。"""
    search_mod = _reload(monkeypatch)
    monkeypatch.setattr(
        search_mod, "_registry", _fake_registry(search_mod, slow_sleep=0)
    )
    monkeypatch.setattr(search_mod, "SEARCH_PROVIDER_PARALLEL_MAX_WAIT_MS", 60)
    # 让"慢"provider 也返回结果，构造两路健康
    registry = search_mod._registry
    from miroflow_tools.dev_mcp_servers.providers.base import SearchResult

    class _SecondProvider:
        @property
        def name(self):
            return "slow"

        def is_available(self):
            return True

        async def search(self, _params):
            return (
                [
                    SearchResult(
                        position=1,
                        title="AP 报道",
                        link="https://apnews.com/article/example",
                        snippet="另一路摘要。",
                        source="slow",
                    )
                ],
                {"provider": "slow"},
            )

    registry.register(_SecondProvider())

    payload = json.loads(await search_mod.google_search("test query", num=3))

    tier = payload["searchParameters"]["provider_tier"]
    assert tier["tier"] == "multi-provider"
    assert tier["min_provider_coverage"] == 2
    assert tier["degraded_from"] == ""


@pytest.mark.asyncio
async def test_serial_fallback_downgrades_after_provider_failure(monkeypatch):
    """串行回退：前一路报错时档位同样跟着降，门槛不落后于实际路由。"""
    search_mod = _reload(monkeypatch)
    monkeypatch.setenv("SEARCH_PROVIDER_MODE", "fallback")
    sys.modules.pop(_MODULE, None)
    search_mod = importlib.import_module(_MODULE)

    from miroflow_tools.dev_mcp_servers.providers.base import SearchResult
    from miroflow_tools.dev_mcp_servers.providers.registry import ProviderRegistry

    class _BrokenProvider:
        @property
        def name(self):
            return "broken"

        def is_available(self):
            return True

        async def search(self, _params):
            raise RuntimeError("provider boom")

    class _GoodProvider:
        @property
        def name(self):
            return "good"

        def is_available(self):
            return True

        async def search(self, _params):
            return (
                [
                    SearchResult(
                        position=1,
                        title="Reuters",
                        link="https://www.reuters.com/world/example",
                        snippet="s",
                    )
                ],
                {"provider": "good"},
            )

    registry = ProviderRegistry()
    registry.register(_BrokenProvider())
    registry.register(_GoodProvider())
    monkeypatch.setattr(search_mod, "_registry", registry)
    monkeypatch.setattr(search_mod, "SEARCH_PROVIDER_ORDER", "broken,good")
    monkeypatch.setattr(search_mod, "SEARCH_PROVIDER_MODE", "fallback")

    payload = json.loads(await search_mod.google_search("test query", num=3))

    tier = payload["searchParameters"]["provider_tier"]
    assert tier["tier"] == "single-provider"
    assert tier["min_provider_coverage"] == 1
    assert tier["effective_order"] == ["good"]
    assert tier["degraded_from"] == "multi-provider"
    assert (
        payload["searchParameters"]["confidence"]["constraints"][
            "min_provider_coverage"
        ]
        == 1
    )


@pytest.mark.asyncio
async def test_serial_fallback_success_converges_to_single_route(monkeypatch):
    """串行回退第一路正常成功、第二路未调用：档位与门槛必须收敛为单路。

    回归：回退模式命中即返回，second 从未被调用（没有任何"失败"，故
    ``_recompute_tier_from_health`` 不会降档），但预先算出的 ``multi-provider``
    仍要求两路覆盖——门槛对本次路由结构性不可达，正常成功的检索被判
    ``passed=false``。
    """
    search_mod = _reload(monkeypatch)
    monkeypatch.setenv("SEARCH_PROVIDER_ORDER", "first,second")
    monkeypatch.setenv("SEARCH_PROVIDER_MODE", "fallback")
    sys.modules.pop(_MODULE, None)
    search_mod = importlib.import_module(_MODULE)

    from miroflow_tools.dev_mcp_servers.providers.base import SearchResult
    from miroflow_tools.dev_mcp_servers.providers.registry import ProviderRegistry

    called: list[str] = []

    class _AlwaysOkProvider:
        def __init__(self, name, link):
            self._name = name
            self._link = link

        @property
        def name(self):
            return self._name

        def is_available(self):
            return True

        async def search(self, _params):
            called.append(self._name)
            return (
                [
                    SearchResult(
                        position=1,
                        title=self._name,
                        link=self._link,
                        snippet="s",
                    )
                ],
                {"provider": self._name},
            )

    registry = ProviderRegistry()
    registry.register(_AlwaysOkProvider("first", "https://www.reuters.com/world/x"))
    registry.register(_AlwaysOkProvider("second", "https://apnews.com/article/x"))
    monkeypatch.setattr(search_mod, "_registry", registry)
    monkeypatch.setattr(search_mod, "SEARCH_PROVIDER_ORDER", "first,second")
    monkeypatch.setattr(search_mod, "SEARCH_PROVIDER_MODE", "fallback")

    payload = json.loads(await search_mod.google_search("test query", num=3))

    params = payload["searchParameters"]
    # 回退语义不变：命中即返回，第二路不应被调用
    assert called == ["first"]

    tier = params["provider_tier"]
    assert tier["tier"] == "single-provider"
    assert tier["min_provider_coverage"] == 1
    assert tier["effective_order"] == ["first"]
    assert tier["degraded_from"] == "multi-provider"
    # profile 也随生效档位收敛，不能留着 multi-route（记录须与实际路由自洽）
    assert tier["profile"] == "serp-first"
    assert "first" in tier["reason"]

    # 门槛随有效档位收敛，正常成功的检索不再被判失败
    assert params["confidence"]["constraints"]["min_provider_coverage"] == 1
    assert params["confidence"]["metrics"]["provider_coverage"] == 1
    assert params["confidence"]["passed"] is True


def test_strict_serial_fallback_converges_without_rewriting_profile(monkeypatch):
    """严格路由下收敛只降覆盖门槛，不得把配置的 profile 换成单路 profile。"""
    from dataclasses import replace

    from miroflow_tools.dev_mcp_servers.providers.tiering import resolve_provider_tier

    search_mod = _reload(monkeypatch)
    monkeypatch.setattr(search_mod, "SEARCH_PROFILE", "searxng-only")
    monkeypatch.setattr(search_mod, "SEARCH_PROVIDER_ORDER", "first,second")

    decision = replace(
        resolve_provider_tier(
            "searxng-only",
            ["first", "second"],
            requested_order="first,second",
            strict=True,
        ),
        effective_order=["first", "second"],
    )
    assert decision.min_provider_coverage == 2

    converged = search_mod._converge_tier_to_single_route(
        decision, "first", phase="串行回退", detail="命中即返回"
    )
    assert converged.tier == "single-provider"
    assert converged.min_provider_coverage == 1
    assert converged.effective_order == ["first"]
    # strict 与 profile 必须自洽：严格路由保持配置的 profile
    assert converged.strict is True
    assert converged.profile == "searxng-only"


def _providers_in_output(organic: list[dict]) -> set[str]:
    """测试侧独立统计"真正进入最终输出的 provider"（不复用被测实现，避免循环论证）。"""
    return {
        str(discovery.get("provider"))
        for item in organic
        for discovery in item.get("discoveries") or []
        if discovery.get("provider")
    }


def _merge_registry(*, first_count: int = 5, second_behavior: str = "ok"):
    """构造聚合场景的注册表：first 返回 ``first_count`` 条，second 行为可控。

    ``first_count > num`` 用来验证"第一路结果已够 result_num，也必须补齐覆盖门槛
    再停"；``first_count < num`` 则让 first 不足以触发提前停止，从而把"另一路
    失败/空结果"单独隔离成自变量。
    """
    from miroflow_tools.dev_mcp_servers.providers.base import SearchResult
    from miroflow_tools.dev_mcp_servers.providers.registry import ProviderRegistry

    called: list[str] = []

    def _hits(name, domain, count):
        return [
            SearchResult(
                position=i,
                title=f"{name}-{i}",
                link=f"https://{domain}/article/{i}",
                snippet="s",
            )
            for i in range(1, count + 1)
        ]

    class _First:
        @property
        def name(self):
            return "first"

        def is_available(self):
            return True

        async def search(self, _params):
            called.append("first")
            return (
                _hits("first", "www.reuters.com", first_count),
                {"provider": "first"},
            )

    class _Second:
        @property
        def name(self):
            return "second"

        def is_available(self):
            return True

        async def search(self, _params):
            called.append("second")
            if second_behavior == "raise":
                raise RuntimeError("second unavailable")
            if second_behavior == "empty":
                return ([], {"provider": "second"})
            return (_hits("second", "apnews.com", 5), {"provider": "second"})

    registry = ProviderRegistry()
    registry.register(_First())
    registry.register(_Second())
    return registry, called


def _reload_for_merge(
    monkeypatch, *, first_count: int = 5, second_behavior: str = "ok"
):
    search_mod = _reload(monkeypatch)
    monkeypatch.setenv("SEARCH_PROVIDER_ORDER", "first,second")
    monkeypatch.setenv("SEARCH_PROVIDER_MODE", "merge")
    monkeypatch.setenv("SEARCH_PROFILE", "multi-route")
    sys.modules.pop(_MODULE, None)
    search_mod = importlib.import_module(_MODULE)

    registry, called = _merge_registry(
        first_count=first_count, second_behavior=second_behavior
    )
    monkeypatch.setattr(search_mod, "_registry", registry)
    monkeypatch.setattr(search_mod, "SEARCH_PROVIDER_ORDER", "first,second")
    monkeypatch.setattr(search_mod, "SEARCH_PROVIDER_MODE", "merge")
    monkeypatch.setattr(search_mod, "SEARCH_PROFILE", "multi-route")
    return search_mod, called


@pytest.mark.asyncio
async def test_merge_mode_fills_coverage_floor_before_stopping(monkeypatch):
    """聚合模式的卖点是交叉验真：第一路结果够了也必须补齐覆盖门槛再停。

    回归：``merge`` 分支原先「结果够 result_num 就 break」，第一路返回够数即停、
    second 从未被调用；且返回的 ``search_params`` 只硬编码 ``provider="multi-route"``、
    不写 ``providers_with_results`` → 置信覆盖率恒为 1，而档位仍是 ``multi-provider``
    门槛 2 → 正常成功的聚合检索被判 ``passed=false``（实测必然触发，与只调一路无关）。
    """
    search_mod, called = _reload_for_merge(monkeypatch)

    payload = json.loads(await search_mod.google_search("test query", num=3))
    params = payload["searchParameters"]

    # 第一路已够 result_num，但覆盖门槛未满足 → 必须继续调第二路
    assert called == ["first", "second"]

    # 覆盖率的依据必须是"真正进入最终输出的 provider"，而不是"调用时返回过结果的"：
    # 逐路填满会让第二路被 limit 整段截掉，此时报两路覆盖就是虚报。
    organic = payload["organic"]
    assert len(organic) == 3
    in_output = _providers_in_output(organic)
    assert in_output == {"first", "second"}
    assert params["providers_with_results"] == sorted(in_output)

    tier = params["provider_tier"]
    assert tier["tier"] == "multi-provider"
    assert tier["min_provider_coverage"] == 2

    confidence = params["confidence"]
    assert confidence["metrics"]["provider_coverage"] == 2
    assert confidence["constraints"]["min_provider_coverage"] == 2
    assert confidence["passed"] is True


@pytest.mark.asyncio
async def test_merge_mode_does_not_overreport_coverage(monkeypatch):
    """第二路进不了最终输出时，覆盖率不得仍报 2（num 小于 provider 数）。

    回归：``providers_with_results`` 原先取自未截断的 ``provider_results_map``，
    ``num`` 只容得下一路证据时仍会报覆盖率 2、``passed=true``，与交付的 organic
    只有一路来源相矛盾。
    """
    search_mod, called = _reload_for_merge(monkeypatch)

    payload = json.loads(await search_mod.google_search("test query", num=1))
    params = payload["searchParameters"]

    assert called == ["first", "second"]

    organic = payload["organic"]
    assert len(organic) == 1
    in_output = _providers_in_output(organic)
    assert in_output == {"first"}

    # 覆盖率必须与交付证据一致，不虚报第二路
    assert params["providers_with_results"] == ["first"]
    confidence = params["confidence"]
    assert confidence["metrics"]["provider_coverage"] == 1
    assert confidence["passed"] is True

    # 档位随之收敛，避免"门槛 2 / 覆盖 1"自相矛盾
    tier = params["provider_tier"]
    assert tier["tier"] == "single-provider"
    assert tier["min_provider_coverage"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("second_behavior", ["raise", "empty"])
async def test_merge_mode_single_contributor_converges_to_single_route(
    monkeypatch, second_behavior
):
    """聚合时只有一路真正产出结果：覆盖门槛不可达 → 收敛为单路。

    回归：first 只返回 1 条（不足以提前停止），第二路失败（``raise``，进
    ``merge_failed``）或返回空结果（``empty``，不进 ``merge_failed``）时实际只有
    一路贡献。``empty`` 分支修复前档位不会降档（无失败），门槛仍为 2 而覆盖率只有
    1 → 正常成功的聚合检索被判 ``passed=false``；``raise`` 分支虽能靠
    ``_recompute_tier_from_health`` 降档，但返回里没有真实的 ``providers_with_results``，
    覆盖率只能拿 ``provider="multi-route"`` 兜底，记录与实际路由并不自洽。
    """
    search_mod, called = _reload_for_merge(
        monkeypatch, first_count=1, second_behavior=second_behavior
    )

    payload = json.loads(await search_mod.google_search("test query", num=3))
    params = payload["searchParameters"]

    assert called == ["first", "second"]

    tier = params["provider_tier"]
    assert tier["tier"] == "single-provider"
    assert tier["min_provider_coverage"] == 1
    assert tier["degraded_from"] == "multi-provider"

    confidence = params["confidence"]
    assert confidence["metrics"]["provider_coverage"] == 1
    assert confidence["constraints"]["min_provider_coverage"] == 1
    assert confidence["passed"] is True

    # 记录必须写明真实产出结果的 provider，而不是让覆盖率退回 provider="multi-route"
    assert params["providers_with_results"] == ["first"]


def _reload_for_parallel(monkeypatch, *, mode: str = "parallel"):
    search_mod = _reload(monkeypatch)
    monkeypatch.setenv("SEARCH_PROVIDER_ORDER", "first,second")
    monkeypatch.setenv("SEARCH_PROVIDER_MODE", mode)
    sys.modules.pop(_MODULE, None)
    search_mod = importlib.import_module(_MODULE)

    registry, called = _merge_registry()
    monkeypatch.setattr(search_mod, "_registry", registry)
    monkeypatch.setattr(search_mod, "SEARCH_PROVIDER_ORDER", "first,second")
    monkeypatch.setattr(search_mod, "SEARCH_PROVIDER_MODE", mode)
    return search_mod, called


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["parallel", "parallel_conf_fallback"])
async def test_parallel_coverage_reflects_output_not_call_results(monkeypatch, mode):
    """并发分支的覆盖率同样只能反映真正进入最终输出的 provider。

    回归：并发分支把 ``providers_with_results``（并发时返回过结果的 provider）直接
    当作覆盖率传给 ``_evaluate_confidence``，``num`` 小于 provider 数时会虚报——
    交付的 organic 只有一路证据，覆盖率却报 2、``passed=true``。
    """
    search_mod, called = _reload_for_parallel(monkeypatch, mode=mode)

    # num 足够：两路的结果都进了输出 → 覆盖率 2 属实
    payload = json.loads(await search_mod.google_search("test query", num=3))
    params = payload["searchParameters"]
    assert sorted(called) == ["first", "second"]
    in_output = _providers_in_output(payload["organic"])
    assert in_output == {"first", "second"}
    assert params["providers_with_results"] == ["first", "second"]
    assert params["confidence"]["metrics"]["provider_coverage"] == 2
    assert params["confidence"]["passed"] is True

    # num 只容得下一路：覆盖率必须跟着降到 1，不得虚报第二路
    called.clear()
    payload = json.loads(await search_mod.google_search("test query", num=1))
    params = payload["searchParameters"]
    in_output = _providers_in_output(payload["organic"])
    assert in_output == {"first"}
    assert params["providers_with_results"] == ["first"]
    assert params["confidence"]["metrics"]["provider_coverage"] == 1
    assert params["confidence"]["passed"] is True
    # 档位随之收敛，避免"门槛 2 / 覆盖 1"自相矛盾
    tier = params["provider_tier"]
    assert tier["tier"] == "single-provider"
    assert tier["min_provider_coverage"] == 1
