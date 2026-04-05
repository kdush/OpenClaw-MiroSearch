"""搜索源 Provider 协议与注册。"""

from .base import SearchParams, SearchProvider, SearchResult
from .registry import ProviderRegistry
from .searxng import SearXNGProvider
from .serpapi import SerpAPIProvider
from .serper import SerperProvider

__all__ = [
    "SearchProvider",
    "SearchResult",
    "SearchParams",
    "ProviderRegistry",
    "SerperProvider",
    "SerpAPIProvider",
    "SearXNGProvider",
]
