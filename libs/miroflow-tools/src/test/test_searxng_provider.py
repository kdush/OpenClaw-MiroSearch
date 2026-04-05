"""SearXNGProvider 单元测试。"""

import json

import httpx
import pytest

from miroflow_tools.dev_mcp_servers.providers.base import SearchParams, SearchResult
from miroflow_tools.dev_mcp_servers.providers.searxng import SearXNGProvider


# ---------------------------------------------------------------------------
# Mock 数据
# ---------------------------------------------------------------------------

MOCK_SEARXNG_RESPONSE = {
    "results": [
        {
            "title": "Result 1",
            "url": "https://example.com/1",
            "content": "Snippet 1",
        },
        {
            "title": "Result 2",
            "url": "https://example.com/2",
            "content": "Snippet 2",
        },
        {
            "title": "Banned HF Space",
            "url": "https://huggingface.co/spaces/test",
            "content": "Should be filtered",
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


class TestSearXNGProvider:
    def test_name(self):
        p = SearXNGProvider(base_url="http://localhost:8888")
        assert p.name == "searxng"

    def test_is_available_with_url(self):
        p = SearXNGProvider(base_url="http://localhost:8888")
        assert p.is_available() is True

    def test_is_not_available_without_url(self):
        p = SearXNGProvider(base_url="")
        p._base_url = ""
        assert p.is_available() is False

    @pytest.mark.asyncio
    async def test_search_basic(self, monkeypatch):
        """验证搜索结果正确解析，字段映射和 banned URL 过滤。"""
        provider = SearXNGProvider(base_url="http://localhost:8888")
        # 跳过预检
        provider._precheck_enabled = False

        mock_client = httpx.AsyncClient(
            transport=_mock_transport(MOCK_SEARXNG_RESPONSE)
        )
        monkeypatch.setattr(
            "miroflow_tools.dev_mcp_servers.providers.searxng.get_shared_client",
            lambda: _async_return(mock_client),
        )

        params = SearchParams(query="test query", num=10)
        results, meta = await provider.search(params)

        # banned URL 应被过滤（huggingface.co/spaces）
        assert len(results) == 2
        assert results[0].title == "Result 1"
        # SearXNG 使用 url -> link 映射
        assert results[0].link == "https://example.com/1"
        # SearXNG 使用 content -> snippet 映射
        assert results[0].snippet == "Snippet 1"
        assert meta["provider"] == "searxng"

    @pytest.mark.asyncio
    async def test_tbs_time_range_mapping(self, monkeypatch):
        """验证 tbs 参数正确映射为 SearXNG 的 time_range。"""
        captured_params = []

        async def capturing_handler(request: httpx.Request) -> httpx.Response:
            captured_params.append(dict(request.url.params))
            return httpx.Response(
                status_code=200,
                json=MOCK_SEARXNG_RESPONSE,
                request=request,
            )

        mock_client = httpx.AsyncClient(
            transport=httpx.MockTransport(capturing_handler)
        )
        monkeypatch.setattr(
            "miroflow_tools.dev_mcp_servers.providers.searxng.get_shared_client",
            lambda: _async_return(mock_client),
        )

        provider = SearXNGProvider(base_url="http://localhost:8888")
        provider._precheck_enabled = False

        params = SearchParams(query="test", tbs="qdr:w")
        await provider.search(params)

        assert len(captured_params) == 1
        assert captured_params[0]["time_range"] == "week"

    @pytest.mark.asyncio
    async def test_num_limit(self, monkeypatch):
        """验证结果数量不超过 params.num。"""
        many_results = {
            "results": [
                {"title": f"R{i}", "url": f"https://example.com/{i}", "content": ""}
                for i in range(20)
            ]
        }
        provider = SearXNGProvider(base_url="http://localhost:8888")
        provider._precheck_enabled = False

        mock_client = httpx.AsyncClient(
            transport=_mock_transport(many_results)
        )
        monkeypatch.setattr(
            "miroflow_tools.dev_mcp_servers.providers.searxng.get_shared_client",
            lambda: _async_return(mock_client),
        )

        params = SearchParams(query="test", num=5)
        results, _ = await provider.search(params)
        assert len(results) == 5

    @pytest.mark.asyncio
    async def test_search_returns_search_result_type(self, monkeypatch):
        provider = SearXNGProvider(base_url="http://localhost:8888")
        provider._precheck_enabled = False

        mock_client = httpx.AsyncClient(
            transport=_mock_transport(MOCK_SEARXNG_RESPONSE)
        )
        monkeypatch.setattr(
            "miroflow_tools.dev_mcp_servers.providers.searxng.get_shared_client",
            lambda: _async_return(mock_client),
        )

        params = SearchParams(query="test")
        results, _ = await provider.search(params)
        for r in results:
            assert isinstance(r, SearchResult)

    @pytest.mark.asyncio
    async def test_precheck_403_raises(self, monkeypatch):
        """验证预检遇到 403 时抛出 SearxngPrecheckError。"""
        from miroflow_tools.dev_mcp_servers.providers.searxng import (
            SearxngPrecheckError,
        )

        async def forbidden_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status_code=403,
                json={"error": "forbidden"},
                request=request,
            )

        mock_client = httpx.AsyncClient(
            transport=httpx.MockTransport(forbidden_handler)
        )
        monkeypatch.setattr(
            "miroflow_tools.dev_mcp_servers.providers.searxng.get_shared_client",
            lambda: _async_return(mock_client),
        )

        provider = SearXNGProvider(base_url="http://localhost:8888")
        provider._precheck_enabled = True
        # 重置预检状态
        provider._precheck_state = {
            "checked_at": 0.0,
            "ok": False,
            "reason": "not_checked",
        }

        with pytest.raises(SearxngPrecheckError, match="403"):
            params = SearchParams(query="test")
            await provider.search(params)
