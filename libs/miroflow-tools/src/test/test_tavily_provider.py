"""TavilyProvider 单元测试。"""

import httpx
import pytest

from miroflow_tools.dev_mcp_servers.providers.base import (
    SearchParams,
    SearchProvider,
    SearchResult,
)
from miroflow_tools.dev_mcp_servers.providers.tavily import TavilyProvider


# ---------------------------------------------------------------------------
# Mock 数据
# ---------------------------------------------------------------------------

MOCK_TAVILY_RESPONSE = {
    "query": "test query",
    "answer": None,
    "results": [
        {
            "url": "https://example.com/1",
            "title": "Tavily Result 1",
            "content": "Tavily content 1, longer than google snippet",
            "score": 0.92,
            "published_date": "2026-01-15",
        },
        {
            "url": "https://example.com/2",
            "title": "Tavily Result 2",
            "content": "Tavily content 2",
            "score": 0.81,
        },
        {
            "url": "https://huggingface.co/datasets/banned",
            "title": "Banned",
            "content": "Should be filtered out",
            "score": 0.5,
        },
    ],
}


def _mock_transport(response_data: dict, status_code: int = 200):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=status_code,
            json=response_data,
            request=request,
        )

    return httpx.MockTransport(handler)


async def _async_return(value):
    return value


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


class TestTavilyProvider:
    def test_name(self):
        p = TavilyProvider(api_key="test-key")
        assert p.name == "tavily"

    def test_satisfies_search_provider_protocol(self):
        """TavilyProvider 必须满足 SearchProvider 协议，否则无法注册到 ProviderRegistry。"""
        p = TavilyProvider(api_key="test-key")
        assert isinstance(p, SearchProvider)

    def test_is_available_with_key(self):
        p = TavilyProvider(api_key="test-key")
        assert p.is_available() is True

    def test_is_not_available_without_key(self, monkeypatch):
        # 清空环境变量避免读取本机 .env 中的 key
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        monkeypatch.delenv("TAVILY_API_KEYS", raising=False)
        p = TavilyProvider(api_key="")
        assert p.is_available() is False

    def test_search_depth_default_basic(self):
        p = TavilyProvider(api_key="test-key")
        assert p._search_depth == "basic"

    def test_search_depth_invalid_falls_back_to_basic(self, monkeypatch):
        monkeypatch.setenv("TAVILY_SEARCH_DEPTH", "ultra")
        p = TavilyProvider(api_key="test-key")
        assert p._search_depth == "basic"

    def test_search_depth_advanced(self):
        p = TavilyProvider(api_key="test-key", search_depth="advanced")
        assert p._search_depth == "advanced"

    @pytest.mark.asyncio
    async def test_search_basic(self, monkeypatch):
        """验证搜索结果正确解析，banned URL 被过滤。"""
        provider = TavilyProvider(api_key="fake-key")
        mock_client = httpx.AsyncClient(transport=_mock_transport(MOCK_TAVILY_RESPONSE))
        monkeypatch.setattr(
            "miroflow_tools.dev_mcp_servers.providers.tavily.get_shared_client",
            lambda: _async_return(mock_client),
        )

        params = SearchParams(query="test query", num=10)
        results, meta = await provider.search(params)

        assert len(results) == 2  # banned huggingface 被过滤
        assert results[0].title == "Tavily Result 1"
        assert results[0].link == "https://example.com/1"
        assert results[0].snippet == "Tavily content 1, longer than google snippet"
        assert results[0].source == "tavily"
        assert results[0].extra.get("score") == 0.92
        assert results[0].extra.get("published_date") == "2026-01-15"
        assert meta["provider"] == "tavily"
        assert meta["q"] == "test query"

    @pytest.mark.asyncio
    async def test_search_returns_search_result_type(self, monkeypatch):
        provider = TavilyProvider(api_key="fake-key")
        mock_client = httpx.AsyncClient(transport=_mock_transport(MOCK_TAVILY_RESPONSE))
        monkeypatch.setattr(
            "miroflow_tools.dev_mcp_servers.providers.tavily.get_shared_client",
            lambda: _async_return(mock_client),
        )

        params = SearchParams(query="test")
        results, _ = await provider.search(params)
        for r in results:
            assert isinstance(r, SearchResult)

    @pytest.mark.asyncio
    async def test_request_body_contains_required_fields(self, monkeypatch):
        """验证 POST body 包含 api_key/query/max_results/search_depth。"""
        captured_bodies = []

        async def capturing_handler(request: httpx.Request) -> httpx.Response:
            import json as _json

            captured_bodies.append(_json.loads(request.content))
            return httpx.Response(
                status_code=200,
                json=MOCK_TAVILY_RESPONSE,
                request=request,
            )

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(capturing_handler))
        monkeypatch.setattr(
            "miroflow_tools.dev_mcp_servers.providers.tavily.get_shared_client",
            lambda: _async_return(mock_client),
        )

        provider = TavilyProvider(api_key="fake-key", search_depth="advanced")
        params = SearchParams(query="climate", num=5)
        await provider.search(params)

        assert len(captured_bodies) == 1
        body = captured_bodies[0]
        assert body["api_key"] == "fake-key"
        assert body["query"] == "climate"
        assert body["max_results"] == 5
        assert body["search_depth"] == "advanced"
        assert body["include_answer"] is False
        assert body["include_raw_content"] is False

    @pytest.mark.asyncio
    async def test_max_results_clamped_to_upper_bound(self, monkeypatch):
        """num 超过 Tavily 上限时应被截到 20。"""
        captured_bodies = []

        async def capturing_handler(request: httpx.Request) -> httpx.Response:
            import json as _json

            captured_bodies.append(_json.loads(request.content))
            return httpx.Response(
                status_code=200,
                json=MOCK_TAVILY_RESPONSE,
                request=request,
            )

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(capturing_handler))
        monkeypatch.setattr(
            "miroflow_tools.dev_mcp_servers.providers.tavily.get_shared_client",
            lambda: _async_return(mock_client),
        )

        provider = TavilyProvider(api_key="fake-key")
        await provider.search(SearchParams(query="test", num=50))

        assert captured_bodies[0]["max_results"] == 20

    @pytest.mark.asyncio
    async def test_answer_field_propagated_to_meta(self, monkeypatch):
        """Tavily 返回 answer 时应进入 meta 供下游 verification 使用。"""
        response = dict(MOCK_TAVILY_RESPONSE)
        response["answer"] = "Climate change is real."

        provider = TavilyProvider(api_key="fake-key")
        mock_client = httpx.AsyncClient(transport=_mock_transport(response))
        monkeypatch.setattr(
            "miroflow_tools.dev_mcp_servers.providers.tavily.get_shared_client",
            lambda: _async_return(mock_client),
        )

        _, meta = await provider.search(SearchParams(query="test"))
        assert meta.get("answer") == "Climate change is real."
