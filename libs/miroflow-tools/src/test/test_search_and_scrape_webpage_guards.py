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
        {"searxng": True, "serpapi": True, "serper": True},
    )
    assert providers == ["searxng"]
    assert downgraded is False
    assert added == []


def test_searxng_only_downgrade_enabled(monkeypatch):
    search_mod = _load_search_module()
    monkeypatch.setattr(search_mod, "SEARCH_SEARXNG_ONLY_ALLOW_DOWNGRADE", True)
    monkeypatch.setattr(search_mod, "SEARCH_SEARXNG_ONLY_DOWNGRADE_ORDER", "serpapi,serper")
    providers, downgraded, added = search_mod._build_searxng_only_downgrade_providers(
        ["searxng"],
        {"searxng": True, "serpapi": True, "serper": False},
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
    search_mod = _load_search_module()

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *_args, **_kwargs):
            request = httpx.Request("GET", "http://searxng.local/search")
            return httpx.Response(403, request=request)

    monkeypatch.setattr(search_mod, "SEARXNG_BASE_URL", "http://searxng.local")
    monkeypatch.setattr(search_mod, "SEARXNG_PRECHECK_ENABLED", True)
    monkeypatch.setattr(search_mod, "SEARXNG_PRECHECK_TTL_SECONDS", 10)
    monkeypatch.setattr(
        search_mod,
        "_searxng_precheck_state",
        {"checked_at": 0.0, "ok": False, "reason": "not_checked"},
    )
    monkeypatch.setattr(search_mod.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    with pytest.raises(search_mod.SearxngPrecheckError):
        await search_mod._ensure_searxng_json_ready()
