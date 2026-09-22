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
