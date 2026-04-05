import importlib
import sys
import types

import httpx
import pytest


def _install_tencentcloud_stubs() -> None:
    """为缺失的 tencentcloud 依赖注入最小桩，避免单测导入失败。"""
    if "tencentcloud" in sys.modules:
        return

    tencentcloud = types.ModuleType("tencentcloud")
    common = types.ModuleType("tencentcloud.common")
    credential = types.ModuleType("tencentcloud.common.credential")
    common_client = types.ModuleType("tencentcloud.common.common_client")
    exception_pkg = types.ModuleType("tencentcloud.common.exception")
    exception_mod = types.ModuleType(
        "tencentcloud.common.exception.tencent_cloud_sdk_exception"
    )
    profile_pkg = types.ModuleType("tencentcloud.common.profile")
    client_profile = types.ModuleType("tencentcloud.common.profile.client_profile")
    http_profile = types.ModuleType("tencentcloud.common.profile.http_profile")

    class _DummyCredential:
        def __init__(self, *_args, **_kwargs):
            pass

    class _DummyCommonClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def call_json(self, *_args, **_kwargs):
            return {"Response": {}}

    class _DummyTencentCloudSDKException(Exception):
        pass

    class _DummyClientProfile:
        def __init__(self):
            self.httpProfile = None

    class _DummyHttpProfile:
        def __init__(self):
            self.endpoint = ""

    credential.Credential = _DummyCredential
    common_client.CommonClient = _DummyCommonClient
    exception_mod.TencentCloudSDKException = _DummyTencentCloudSDKException
    client_profile.ClientProfile = _DummyClientProfile
    http_profile.HttpProfile = _DummyHttpProfile

    common.credential = credential
    common.common_client = common_client
    common.exception = exception_pkg
    common.profile = profile_pkg
    exception_pkg.tencent_cloud_sdk_exception = exception_mod
    profile_pkg.client_profile = client_profile
    profile_pkg.http_profile = http_profile

    tencentcloud.common = common

    sys.modules["tencentcloud"] = tencentcloud
    sys.modules["tencentcloud.common"] = common
    sys.modules["tencentcloud.common.credential"] = credential
    sys.modules["tencentcloud.common.common_client"] = common_client
    sys.modules["tencentcloud.common.exception"] = exception_pkg
    sys.modules[
        "tencentcloud.common.exception.tencent_cloud_sdk_exception"
    ] = exception_mod
    sys.modules["tencentcloud.common.profile"] = profile_pkg
    sys.modules["tencentcloud.common.profile.client_profile"] = client_profile
    sys.modules["tencentcloud.common.profile.http_profile"] = http_profile


def _load_search_module():
    _install_tencentcloud_stubs()
    module_name = "miroflow_tools.dev_mcp_servers.search_and_scrape_webpage"
    if module_name in sys.modules:
        del sys.modules[module_name]
    return importlib.import_module(module_name)


def test_searxng_only_downgrade_disabled(monkeypatch):
    search_mod = _load_search_module()
    monkeypatch.setattr(search_mod, "SEARCH_SEARXNG_ONLY_ALLOW_DOWNGRADE", False)
    providers, downgraded, added = search_mod._build_searxng_only_downgrade_providers(
        ["searxng"],
    )
    assert providers == ["searxng"]
    assert downgraded is False
    assert added == []


def test_searxng_only_downgrade_enabled(monkeypatch):
    search_mod = _load_search_module()
    monkeypatch.setattr(search_mod, "SEARCH_SEARXNG_ONLY_ALLOW_DOWNGRADE", True)
    monkeypatch.setattr(search_mod, "SEARCH_SEARXNG_ONLY_DOWNGRADE_ORDER", "serpapi,serper")

    # 构造 registry：serpapi 可用，serper 不可用
    from miroflow_tools.dev_mcp_servers.providers.registry import ProviderRegistry

    class _FakeAvailable:
        @property
        def name(self):
            return "serpapi"

        def is_available(self):
            return True

    class _FakeUnavailable:
        @property
        def name(self):
            return "serper"

        def is_available(self):
            return False

    fake_registry = ProviderRegistry()
    fake_registry.register(_FakeAvailable())
    fake_registry.register(_FakeUnavailable())
    monkeypatch.setattr(search_mod, "_registry", fake_registry)

    providers, downgraded, added = search_mod._build_searxng_only_downgrade_providers(
        ["searxng"],
    )
    assert providers == ["searxng", "serpapi"]
    assert downgraded is True
    assert added == ["serpapi"]


def test_format_provider_error_forbidden_json():
    search_mod = _load_search_module()
    request = httpx.Request("GET", "http://example.com/search")
    response = httpx.Response(403, request=request)
    exc = httpx.HTTPStatusError("forbidden", request=request, response=response)
    message = search_mod._format_provider_error("searxng", exc)
    assert "http_403_json_forbidden" in message


@pytest.mark.asyncio
async def test_searxng_precheck_raises_on_403(monkeypatch):
    """预检 403 现在由 SearXNGProvider 内部处理，此处直接测试 Provider。"""
    from miroflow_tools.dev_mcp_servers.providers.searxng import (
        SearXNGProvider,
        SearxngPrecheckError,
    )

    async def _forbidden_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"}, request=request)

    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_forbidden_handler)
    )

    async def _async_return(value):
        return value

    monkeypatch.setattr(
        "miroflow_tools.dev_mcp_servers.providers.searxng.get_shared_client",
        lambda: _async_return(mock_client),
    )

    provider = SearXNGProvider(base_url="http://searxng.local")
    provider._precheck_enabled = True
    provider._precheck_state = {
        "checked_at": 0.0,
        "ok": False,
        "reason": "not_checked",
    }

    from miroflow_tools.dev_mcp_servers.providers.base import SearchParams

    with pytest.raises(SearxngPrecheckError):
        await provider.search(SearchParams(query="test"))
