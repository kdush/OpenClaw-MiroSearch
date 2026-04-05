"""SearchProvider 协议定义和共享数据结构。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable


@dataclass
class SearchResult:
    """单条搜索结果的标准化表示。"""

    position: int
    title: str
    link: str
    snippet: str = ""
    source: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转为字典，仅包含非空字段。"""
        d: dict[str, Any] = {
            "position": self.position,
            "title": self.title,
            "link": self.link,
            "snippet": self.snippet,
        }
        if self.source:
            d["source"] = self.source
        if self.extra:
            d.update(self.extra)
        return d


@dataclass
class SearchParams:
    """搜索请求参数，由路由层统一构建后传递给各 Provider。"""

    query: str
    num: int = 10
    page: int = 1
    hl: str = "en"
    gl: str = "us"
    location: Optional[str] = None
    tbs: Optional[str] = None
    autocorrect: Optional[bool] = None


@runtime_checkable
class SearchProvider(Protocol):
    """搜索源协议接口。所有搜索源必须实现此协议。"""

    @property
    def name(self) -> str:
        """搜索源唯一标识符，如 'serper'、'serpapi'、'searxng'。"""
        ...

    def is_available(self) -> bool:
        """当前搜索源是否可用（API Key 已配置等）。"""
        ...

    async def search(
        self, params: SearchParams
    ) -> tuple[list[SearchResult], dict[str, Any]]:
        """
        执行搜索，返回 (结果列表, 元信息字典)。

        元信息字典至少包含:
        - provider: str  搜索源名称
        - q: str         实际使用的查询词
        """
        ...
