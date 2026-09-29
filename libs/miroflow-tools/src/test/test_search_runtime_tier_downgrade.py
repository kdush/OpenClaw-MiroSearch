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
