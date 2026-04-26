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


# ---------------------------------------------------------------------------
# scrape_url v0.2.3 增强（T1 共享 client + T2 重定向 SSRF + T4 编码兜底）
# ---------------------------------------------------------------------------


def _make_patched_client(transport: httpx.MockTransport):
    """构造一个把 transport 强制注入 httpx.AsyncClient 的 patch 类。

    由于改造后 _get_scrape_client() 仍然走 httpx.AsyncClient(...) 实例化，
    monkeypatch search_mod.httpx.AsyncClient 即可拦截真实网络。
    """

    class _PatchedClient(httpx.AsyncClient):
        instantiation_count = 0

        def __init__(self, *args, **kwargs):
            type(self).instantiation_count += 1
            kwargs.pop("transport", None)
            super().__init__(*args, transport=transport, **kwargs)

    return _PatchedClient


@pytest.mark.asyncio
async def test_scrape_url_returns_metrics_and_encoding_fields(monkeypatch):
    fn, search_mod, json_lib = _scrape_url_callable()
    monkeypatch.setattr(
        search_mod, "_is_private_or_loopback_host", lambda _h: False
    )

    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<html><body><main>hello world 中文</main></body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
            request=request,
        )

    transport = httpx.MockTransport(_handler)
    monkeypatch.setattr(
        search_mod.httpx, "AsyncClient", _make_patched_client(transport)
    )

    raw = await fn("https://example.com/page")
    payload = json_lib.loads(raw)

    assert payload["success"] is True
    assert "metrics" in payload
    metrics = payload["metrics"]
    assert {"t_request_ms", "t_parse_ms", "t_extract_ms", "redirect_hops"} <= set(
        metrics.keys()
    )
    assert metrics["redirect_hops"] == 0
    assert all(isinstance(v, int) and v >= 0 for v in metrics.values())
    # encoding 字段标注实际使用的解码方案
    assert payload["encoding"] in {"utf-8", "utf_8"}
    # 不应漏出 redirect_chain（无重定向时省略）
    assert "redirect_chain" not in payload


@pytest.mark.asyncio
async def test_scrape_url_follows_redirect_chain_within_limit(monkeypatch):
    fn, search_mod, json_lib = _scrape_url_callable()
    monkeypatch.setattr(
        search_mod, "_is_private_or_loopback_host", lambda _h: False
    )

    call_log = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        call_log.append(url_str)
        if url_str == "https://example.com/start":
            return httpx.Response(
                302,
                headers={"location": "https://example.com/middle"},
                request=request,
            )
        if url_str == "https://example.com/middle":
            return httpx.Response(
                301,
                headers={"location": "https://example.com/final"},
                request=request,
            )
        return httpx.Response(
            200,
            text="<html><body><main>arrived</main></body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
            request=request,
        )

    transport = httpx.MockTransport(_handler)
    monkeypatch.setattr(
        search_mod.httpx, "AsyncClient", _make_patched_client(transport)
    )

    raw = await fn("https://example.com/start")
    payload = json_lib.loads(raw)

    assert payload["success"] is True
    assert payload["redirect_chain"] == [
        "https://example.com/middle",
        "https://example.com/final",
    ]
    assert payload["metrics"]["redirect_hops"] == 2
    assert call_log == [
        "https://example.com/start",
        "https://example.com/middle",
        "https://example.com/final",
    ]
    assert "arrived" in payload["content"]


@pytest.mark.asyncio
async def test_scrape_url_blocks_redirect_to_private_host(monkeypatch):
    fn, search_mod, json_lib = _scrape_url_callable()

    # 仅当目标是 intranet.example 时判私网，确保起始 host 通过初始 SSRF 校验
    monkeypatch.setattr(
        search_mod,
        "_is_private_or_loopback_host",
        lambda host: host == "intranet.example",
    )

    async def _handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://example.com/start":
            return httpx.Response(
                302,
                headers={"location": "http://intranet.example/admin"},
                request=request,
            )
        # 第二跳不应该被实际请求到
        raise AssertionError(f"unexpected request to {request.url}")

    transport = httpx.MockTransport(_handler)
    monkeypatch.setattr(
        search_mod.httpx, "AsyncClient", _make_patched_client(transport)
    )

    raw = await fn("https://example.com/start")
    payload = json_lib.loads(raw)

    assert payload["success"] is False
    assert "redirect_blocked" in payload["error"]
    assert "private/loopback" in payload["error"]
    assert payload["redirect_chain"] == ["http://intranet.example/admin"]
    assert payload["metrics"]["redirect_hops"] == 1


@pytest.mark.asyncio
async def test_scrape_url_rejects_too_many_redirects(monkeypatch):
    fn, search_mod, json_lib = _scrape_url_callable()
    monkeypatch.setattr(
        search_mod, "_is_private_or_loopback_host", lambda _h: False
    )
    # 把跳数上限压低到 2，便于触发
    monkeypatch.setattr(search_mod, "SCRAPE_MAX_REDIRECT_HOPS", 2)

    async def _handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        idx = int(path.rsplit("/", 1)[-1].lstrip("step")) if path.startswith("/step") else 0
        return httpx.Response(
            302,
            headers={"location": f"https://example.com/step{idx + 1}"},
            request=request,
        )

    transport = httpx.MockTransport(_handler)
    monkeypatch.setattr(
        search_mod.httpx, "AsyncClient", _make_patched_client(transport)
    )

    raw = await fn("https://example.com/step0")
    payload = json_lib.loads(raw)

    assert payload["success"] is False
    assert "too many redirects" in payload["error"]
    # max=2 时允许 chain 长度为 2 或 3（第三跳在校验环节抛出）
    assert len(payload["redirect_chain"]) >= 2
    assert payload["metrics"]["redirect_hops"] == len(payload["redirect_chain"])


@pytest.mark.asyncio
async def test_scrape_url_decodes_gbk_via_header(monkeypatch):
    fn, search_mod, json_lib = _scrape_url_callable()
    monkeypatch.setattr(
        search_mod, "_is_private_or_loopback_host", lambda _h: False
    )

    chinese_html = (
        "<html><head><title>政府公告</title></head>"
        "<body><main><article>"
        + "<p>第一条 任何人不得在公共场所吸烟，违者处以五十元罚款。</p>" * 3
        + "</article></main></body></html>"
    )
    gbk_bytes = chinese_html.encode("gbk")

    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=gbk_bytes,
            headers={"content-type": "text/html; charset=GBK"},
            request=request,
        )

    transport = httpx.MockTransport(_handler)
    monkeypatch.setattr(
        search_mod.httpx, "AsyncClient", _make_patched_client(transport)
    )

    raw = await fn("https://example.com/gbk")
    payload = json_lib.loads(raw)

    assert payload["success"] is True
    assert payload["encoding"] == "gbk"
    assert "公共场所吸烟" in payload["content"]


@pytest.mark.asyncio
async def test_scrape_url_decodes_via_meta_charset_when_header_missing(monkeypatch):
    fn, search_mod, json_lib = _scrape_url_callable()
    monkeypatch.setattr(
        search_mod, "_is_private_or_loopback_host", lambda _h: False
    )

    chinese_html = (
        '<html><head><meta charset="gb2312"><title>地方法规</title></head>'
        "<body><main><article>"
        + "<p>第二条 营业场所禁烟标识应当醒目张贴。</p>" * 3
        + "</article></main></body></html>"
    )
    encoded_bytes = chinese_html.encode("gb2312")

    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=encoded_bytes,
            headers={"content-type": "text/html"},  # 故意省略 charset
            request=request,
        )

    transport = httpx.MockTransport(_handler)
    monkeypatch.setattr(
        search_mod.httpx, "AsyncClient", _make_patched_client(transport)
    )

    raw = await fn("https://example.com/meta")
    payload = json_lib.loads(raw)

    assert payload["success"] is True
    # gb2312 在 Python codecs 下与 gbk 互通，charset_normalizer 可能升级到 gb18030
    assert payload["encoding"] in {"gb2312", "gbk", "gb18030"}
    assert "营业场所" in payload["content"]


@pytest.mark.asyncio
async def test_scrape_url_decodes_via_charset_normalizer_fallback(monkeypatch):
    fn, search_mod, json_lib = _scrape_url_callable()
    monkeypatch.setattr(
        search_mod, "_is_private_or_loopback_host", lambda _h: False
    )

    chinese_html = (
        "<html><body><main><article>"
        + "<p>第三条 公共交通工具内禁止吸烟与使用电子烟。</p>" * 5
        + "</article></main></body></html>"
    )
    encoded_bytes = chinese_html.encode("gbk")

    async def _handler(request: httpx.Request) -> httpx.Response:
        # header 与 meta 均无 charset，强制走 charset_normalizer 兜底
        return httpx.Response(
            200,
            content=encoded_bytes,
            headers={"content-type": "text/html"},
            request=request,
        )

    transport = httpx.MockTransport(_handler)
    monkeypatch.setattr(
        search_mod.httpx, "AsyncClient", _make_patched_client(transport)
    )

    raw = await fn("https://example.com/normalizer")
    payload = json_lib.loads(raw)

    assert payload["success"] is True
    assert payload["encoding"] in {"gb18030", "gbk", "gb2312"}
    assert "电子烟" in payload["content"]


@pytest.mark.asyncio
async def test_scrape_url_reuses_shared_client(monkeypatch):
    fn, search_mod, json_lib = _scrape_url_callable()
    monkeypatch.setattr(
        search_mod, "_is_private_or_loopback_host", lambda _h: False
    )

    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<html><body><main>x</main></body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
            request=request,
        )

    transport = httpx.MockTransport(_handler)
    patched = _make_patched_client(transport)
    monkeypatch.setattr(search_mod.httpx, "AsyncClient", patched)

    await fn("https://example.com/a")
    await fn("https://example.com/b")
    await fn("https://example.com/c")

    # 3 次连续调用应当只触发一次 client 实例化（共享 client 命中）
    assert patched.instantiation_count == 1
    assert search_mod._SCRAPE_CLIENT is not None
