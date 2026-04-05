"""搜索源 Provider 协议与注册。"""

from .base import SearchParams, SearchProvider, SearchResult
from .registry import ProviderRegistry

__all__ = ["SearchProvider", "SearchResult", "SearchParams", "ProviderRegistry"]
