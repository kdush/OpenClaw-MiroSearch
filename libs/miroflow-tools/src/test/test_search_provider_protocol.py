"""SearchProvider 协议接口、数据结构和 Registry 测试。"""

import pytest

from miroflow_tools.dev_mcp_servers.providers.base import (
    SearchParams,
    SearchProvider,
    SearchResult,
)
from miroflow_tools.dev_mcp_servers.providers.registry import ProviderRegistry


# ---------------------------------------------------------------------------
# SearchResult 测试
# ---------------------------------------------------------------------------


class TestSearchResult:
    def test_create(self):
        r = SearchResult(
            position=1, title="Test", link="https://example.com", snippet="desc"
        )
        assert r.position == 1
        assert r.title == "Test"
        assert r.link == "https://example.com"
        assert r.snippet == "desc"

    def test_to_dict_basic(self):
        r = SearchResult(
            position=1, title="Test", link="https://example.com", snippet="desc"
        )
        d = r.to_dict()
        assert d == {
            "position": 1,
            "title": "Test",
            "link": "https://example.com",
            "snippet": "desc",
        }

    def test_to_dict_with_source(self):
        r = SearchResult(
            position=1,
            title="Test",
            link="https://example.com",
            snippet="",
            source="Reuters",
        )
        d = r.to_dict()
        assert d["source"] == "Reuters"

    def test_to_dict_with_extra(self):
        r = SearchResult(
            position=1,
            title="Test",
            link="https://example.com",
            snippet="",
            extra={"date": "2026-01-01"},
        )
        d = r.to_dict()
        assert d["date"] == "2026-01-01"

    def test_default_fields(self):
        r = SearchResult(position=1, title="T", link="L")
        assert r.snippet == ""
        assert r.source == ""
        assert r.extra == {}


# ---------------------------------------------------------------------------
# SearchParams 测试
# ---------------------------------------------------------------------------


class TestSearchParams:
    def test_create_minimal(self):
        p = SearchParams(query="test")
        assert p.query == "test"
        assert p.num == 10
        assert p.page == 1
        assert p.hl == "en"
        assert p.gl == "us"
        assert p.location is None
        assert p.tbs is None
        assert p.autocorrect is None

    def test_create_full(self):
        p = SearchParams(
            query="deep learning",
            num=20,
            page=2,
            hl="zh",
            gl="cn",
            location="Beijing",
            tbs="qdr:w",
            autocorrect=True,
        )
        assert p.query == "deep learning"
        assert p.num == 20
        assert p.location == "Beijing"
        assert p.tbs == "qdr:w"


# ---------------------------------------------------------------------------
# SearchProvider Protocol 测试
# ---------------------------------------------------------------------------


class _FakeProvider:
    """测试用 fake provider。"""

    def __init__(self, name: str, available: bool = True):
        self._name = name
        self._available = available

    @property
    def name(self) -> str:
        return self._name

    def is_available(self) -> bool:
        return self._available

    async def search(self, params):
        return [
            SearchResult(
                position=1, title="fake", link="https://fake.com", snippet=""
            )
        ], {"provider": self._name}


class TestSearchProviderProtocol:
    def test_fake_satisfies_protocol(self):
        """验证 _FakeProvider 满足 SearchProvider 协议。"""
        p = _FakeProvider("test")
        assert isinstance(p, SearchProvider)

    def test_non_provider_fails_check(self):
        """没有实现协议方法的类不满足 SearchProvider。"""

        class NotAProvider:
            pass

        assert not isinstance(NotAProvider(), SearchProvider)


# ---------------------------------------------------------------------------
# ProviderRegistry 测试
# ---------------------------------------------------------------------------


class TestProviderRegistry:
    def test_register_and_get(self):
        reg = ProviderRegistry()
        p = _FakeProvider("test")
        reg.register(p)
        assert reg.get("test") is p

    def test_get_missing_returns_none(self):
        reg = ProviderRegistry()
        assert reg.get("nope") is None

    def test_available_names(self):
        reg = ProviderRegistry()
        reg.register(_FakeProvider("a", available=True))
        reg.register(_FakeProvider("b", available=False))
        assert reg.available_names() == ["a"]

    def test_resolve_order_filters_unavailable(self):
        reg = ProviderRegistry()
        reg.register(_FakeProvider("serper", available=True))
        reg.register(_FakeProvider("serpapi", available=True))
        reg.register(_FakeProvider("searxng", available=False))
        resolved = reg.resolve_order("searxng,serpapi,serper")
        # searxng 不可用，被过滤
        assert resolved == ["serpapi", "serper"]

    def test_resolve_order_appends_unconfigured(self):
        reg = ProviderRegistry()
        reg.register(_FakeProvider("serper", available=True))
        reg.register(_FakeProvider("serpapi", available=True))
        # 只配置 serper，serpapi 应被追加
        resolved = reg.resolve_order("serper")
        assert resolved == ["serper", "serpapi"]

    def test_duplicate_register_overwrites(self):
        reg = ProviderRegistry()
        p1 = _FakeProvider("x")
        p2 = _FakeProvider("x")
        reg.register(p1)
        reg.register(p2)
        assert reg.get("x") is p2

    def test_contains(self):
        reg = ProviderRegistry()
        reg.register(_FakeProvider("a"))
        assert "a" in reg
        assert "b" not in reg

    def test_len(self):
        reg = ProviderRegistry()
        assert len(reg) == 0
        reg.register(_FakeProvider("a"))
        assert len(reg) == 1
