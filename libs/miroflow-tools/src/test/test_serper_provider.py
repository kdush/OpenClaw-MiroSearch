"""SerperProvider 单元测试。"""

import json

import httpx
import pytest
import pytest_asyncio

from miroflow_tools.dev_mcp_servers.providers.base import SearchParams, SearchResult
from miroflow_tools.dev_mcp_servers.providers.serper import SerperProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MOCK_SERPER_RESPONSE = {
    "searchParameters": {"q": "test query", "gl": "us", "hl": "en"},
    "organic": [
        {
            "title": "Result 1",
            "link": "https://example.com/1",
            "snippet": "Snippet 1",
            "position": 1,
        },
        {
            "title": "Result 2",
            "link": "https://example.com/2",
            "snippet": "Snippet 2",
            "position": 2,
        },
        {
            "title": "Banned HF",
            "link": "https://huggingface.co/datasets/test",
            "snippet": "Should be filtered",
            "position": 3,
        },
    ],
}


def _mock_transport(response_data: dict, status_code: int = 200):
    """创建 mock httpx transport。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=status_code,
            json=response_data,
            request=request,
        )

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


class TestSerperProvider:
    def test_name(self):
        p = SerperProvider(api_key="test-key")
        assert p.name == "serper"

    def test_is_available_with_key(self):
        p = SerperProvider(api_key="test-key")
        assert p.is_available() is True

    def test_is_not_available_without_key(self):
        p = SerperProvider(api_key="", key_pool=None)
        # 可能从环境变量读取，用显式空值确保不可用
        p._api_key = ""
        p._key_pool = None
        assert p.is_available() is False

    @pytest.mark.asyncio
    async def test_search_basic(self, monkeypatch):
        """验证搜索结果正确解析，banned URL 被过滤。"""
        provider = SerperProvider(api_key="fake-key")

        # Mock httpx client
        mock_client = httpx.AsyncClient(
            transport=_mock_transport(MOCK_SERPER_RESPONSE)
        )
        monkeypatch.setattr(
            "miroflow_tools.dev_mcp_servers.providers.serper.get_shared_client",
            lambda: _async_return(mock_client),
        )

        params = SearchParams(query="test query", num=10)
        results, meta = await provider.search(params)

        # banned URL 应被过滤
        assert len(results) == 2
        assert results[0].title == "Result 1"
        assert results[0].link == "https://example.com/1"
        assert results[1].title == "Result 2"
        assert meta["provider"] == "serper"

    @pytest.mark.asyncio
    async def test_search_with_params(self, monkeypatch):
        """验证参数正确传递到 payload。"""
        captured_payloads = []

        async def capturing_handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            captured_payloads.append(body)
            return httpx.Response(
                status_code=200,
                json=MOCK_SERPER_RESPONSE,
                request=request,
            )

        mock_client = httpx.AsyncClient(
            transport=httpx.MockTransport(capturing_handler)
        )
        monkeypatch.setattr(
            "miroflow_tools.dev_mcp_servers.providers.serper.get_shared_client",
            lambda: _async_return(mock_client),
        )

        provider = SerperProvider(api_key="fake-key")
        params = SearchParams(
            query="deep learning",
            num=20,
            page=2,
            hl="zh",
            gl="cn",
            location="Beijing",
            tbs="qdr:w",
            autocorrect=True,
        )
        await provider.search(params)

        assert len(captured_payloads) == 1
        payload = captured_payloads[0]
        assert payload["q"] == "deep learning"
        assert payload["num"] == 20
        assert payload["page"] == 2
        assert payload["hl"] == "zh"
        assert payload["gl"] == "cn"
        assert payload["location"] == "Beijing"
        assert payload["tbs"] == "qdr:w"
        assert payload["autocorrect"] is True

    @pytest.mark.asyncio
    async def test_search_returns_search_result_type(self, monkeypatch):
        """验证返回类型是 SearchResult 实例。"""
        provider = SerperProvider(api_key="fake-key")
        mock_client = httpx.AsyncClient(
            transport=_mock_transport(MOCK_SERPER_RESPONSE)
        )
        monkeypatch.setattr(
            "miroflow_tools.dev_mcp_servers.providers.serper.get_shared_client",
            lambda: _async_return(mock_client),
        )

        params = SearchParams(query="test")
        results, _ = await provider.search(params)
        for r in results:
            assert isinstance(r, SearchResult)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


async def _async_return(value):
    """将同步值包装为可 await 的结果。"""
    return value
