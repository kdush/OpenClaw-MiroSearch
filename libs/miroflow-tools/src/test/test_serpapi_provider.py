"""SerpAPIProvider 单元测试。"""

import json

import httpx
import pytest

from miroflow_tools.dev_mcp_servers.providers.base import SearchParams, SearchResult
from miroflow_tools.dev_mcp_servers.providers.serpapi import SerpAPIProvider


# ---------------------------------------------------------------------------
# Mock 数据
# ---------------------------------------------------------------------------

MOCK_SERPAPI_RESPONSE = {
    "organic_results": [
        {
            "position": 1,
            "title": "Result 1",
            "link": "https://example.com/1",
            "snippet": "Snippet 1",
            "source": "Example",
        },
        {
            "position": 2,
            "title": "Result 2",
            "link": "https://example.com/2",
            "snippet": "Snippet 2",
            "source": "Example2",
        },
        {
            "position": 3,
            "title": "Banned HF Dataset",
            "link": "https://huggingface.co/datasets/test",
            "snippet": "Should be filtered",
            "source": "HF",
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


class TestSerpAPIProvider:
    def test_name(self):
        p = SerpAPIProvider(api_key="test-key")
        assert p.name == "serpapi"

    def test_is_available_with_key(self):
        p = SerpAPIProvider(api_key="test-key")
        assert p.is_available() is True

    def test_is_not_available_without_key(self):
        p = SerpAPIProvider(api_key="", key_pool=None)
        p._api_key = ""
        p._key_pool = None
        assert p.is_available() is False

    @pytest.mark.asyncio
    async def test_search_basic(self, monkeypatch):
        """验证搜索结果正确解析，banned URL 被过滤。"""
        provider = SerpAPIProvider(api_key="fake-key")
        mock_client = httpx.AsyncClient(
            transport=_mock_transport(MOCK_SERPAPI_RESPONSE)
        )
        monkeypatch.setattr(
            "miroflow_tools.dev_mcp_servers.providers.serpapi.get_shared_client",
            lambda: _async_return(mock_client),
        )

        params = SearchParams(query="test query", num=10)
        results, meta = await provider.search(params)

        assert len(results) == 2
        assert results[0].title == "Result 1"
        assert results[0].source == "Example"
        assert results[1].title == "Result 2"
        assert meta["provider"] == "serpapi"

    @pytest.mark.asyncio
    async def test_chinese_hl_mapping(self, monkeypatch):
        """验证中文 hl 参数正确映射。"""
        captured_params = []

        async def capturing_handler(request: httpx.Request) -> httpx.Response:
            captured_params.append(dict(request.url.params))
            return httpx.Response(
                status_code=200,
                json=MOCK_SERPAPI_RESPONSE,
                request=request,
            )

        mock_client = httpx.AsyncClient(
            transport=httpx.MockTransport(capturing_handler)
        )
        monkeypatch.setattr(
            "miroflow_tools.dev_mcp_servers.providers.serpapi.get_shared_client",
            lambda: _async_return(mock_client),
        )

        provider = SerpAPIProvider(api_key="fake-key")
        params = SearchParams(query="深度学习", hl="zh", gl="cn")
        await provider.search(params)

        assert len(captured_params) == 1
        assert captured_params[0]["hl"] == "zh-cn"

    @pytest.mark.asyncio
    async def test_start_offset_calculation(self, monkeypatch):
        """验证 page > 1 时 start 参数计算正确。"""
        captured_params = []

        async def capturing_handler(request: httpx.Request) -> httpx.Response:
            captured_params.append(dict(request.url.params))
            return httpx.Response(
                status_code=200,
                json=MOCK_SERPAPI_RESPONSE,
                request=request,
            )

        mock_client = httpx.AsyncClient(
            transport=httpx.MockTransport(capturing_handler)
        )
        monkeypatch.setattr(
            "miroflow_tools.dev_mcp_servers.providers.serpapi.get_shared_client",
            lambda: _async_return(mock_client),
        )

        provider = SerpAPIProvider(api_key="fake-key")
        params = SearchParams(query="test", num=10, page=3)
        await provider.search(params)

        # page=3, num=10 -> start = (3-1)*10 = 20
        assert captured_params[0]["start"] == "20"

    @pytest.mark.asyncio
    async def test_search_returns_search_result_type(self, monkeypatch):
        provider = SerpAPIProvider(api_key="fake-key")
        mock_client = httpx.AsyncClient(
            transport=_mock_transport(MOCK_SERPAPI_RESPONSE)
        )
        monkeypatch.setattr(
            "miroflow_tools.dev_mcp_servers.providers.serpapi.get_shared_client",
            lambda: _async_return(mock_client),
        )

        params = SearchParams(query="test")
        results, _ = await provider.search(params)
        for r in results:
            assert isinstance(r, SearchResult)
