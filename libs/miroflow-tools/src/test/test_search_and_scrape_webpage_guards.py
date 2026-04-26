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


# ---------------------------------------------------------------------------
# scrape_url 工具：单元测试
# ---------------------------------------------------------------------------


def _scrape_url_callable():
    """获取 scrape_url 的真实异步函数（FastMCP 装饰后藏在工具注册表里）。"""
    import json as _json

    search_mod = _load_search_module()
    fn = getattr(search_mod, "scrape_url", None)
    if callable(fn):
        return fn, search_mod, _json
    raise RuntimeError("scrape_url is not exposed as a module-level callable")


@pytest.mark.asyncio
async def test_scrape_url_rejects_non_http_scheme():
    fn, _, json_lib = _scrape_url_callable()
    raw = await fn("ftp://example.com/x")
    payload = json_lib.loads(raw)
    assert payload["success"] is False
    assert "http(s)" in payload["error"]


@pytest.mark.asyncio
async def test_scrape_url_rejects_empty_url():
    fn, _, json_lib = _scrape_url_callable()
    raw = await fn("")
    payload = json_lib.loads(raw)
    assert payload["success"] is False
    assert "url is required" in payload["error"]


@pytest.mark.asyncio
async def test_scrape_url_blocks_private_host(monkeypatch):
    fn, search_mod, json_lib = _scrape_url_callable()
    # 强制 SSRF 守卫返回 True，模拟内网解析
    monkeypatch.setattr(
        search_mod, "_is_private_or_loopback_host", lambda _host: True
    )
    raw = await fn("http://intranet.example/")
    payload = json_lib.loads(raw)
    assert payload["success"] is False
    assert "private" in payload["error"]


@pytest.mark.asyncio
async def test_scrape_url_extracts_main_content(monkeypatch):
    fn, search_mod, json_lib = _scrape_url_callable()
    monkeypatch.setattr(
        search_mod, "_is_private_or_loopback_host", lambda _host: False
    )

    # 确保正文长度明显超过 max_chars=500，便于断言截断生效
    paragraph = "<p>第一条 任何人不得在公共场所吸烟，违者处以五十元罚款。</p>" * 30
    html = (
        "<html><head><title>Demo Page</title></head><body>"
        "<header>菜单</header><nav>导航</nav>"
        "<main><article><h1>条例标题</h1>"
        f"{paragraph}"
        "</article></main>"
        "<footer>版权信息</footer></body></html>"
    )

    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=html,
            headers={"content-type": "text/html; charset=utf-8"},
            request=request,
        )

    transport = httpx.MockTransport(_handler)

    class _PatchedClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs.pop("transport", None)
            super().__init__(*args, transport=transport, **kwargs)

    monkeypatch.setattr(search_mod.httpx, "AsyncClient", _PatchedClient)

    raw = await fn("https://example.com/regulation", max_chars=500)
    payload = json_lib.loads(raw)
    assert payload["success"] is True
    assert payload["http_status"] == 200
    assert payload["title"] == "Demo Page"
    assert "条例标题" in payload["content"]
    assert payload["truncated"] is True
    assert payload["content_length"] == 500
    # 不应包含被剥离的导航/菜单/页脚
    assert "菜单" not in payload["content"]
    assert "导航" not in payload["content"]
    assert "版权信息" not in payload["content"]


@pytest.mark.asyncio
async def test_scrape_url_rejects_non_html_content_type(monkeypatch):
    fn, search_mod, json_lib = _scrape_url_callable()
    monkeypatch.setattr(
        search_mod, "_is_private_or_loopback_host", lambda _host: False
    )

    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"%PDF-1.4 binary",
            headers={"content-type": "application/pdf"},
            request=request,
        )

    transport = httpx.MockTransport(_handler)

    class _PatchedClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs.pop("transport", None)
            super().__init__(*args, transport=transport, **kwargs)

    monkeypatch.setattr(search_mod.httpx, "AsyncClient", _PatchedClient)

    raw = await fn("https://example.com/file.pdf")
    payload = json_lib.loads(raw)
    assert payload["success"] is False
    assert "unsupported content_type" in payload["error"]
    assert payload["content_type"] == "application/pdf"
