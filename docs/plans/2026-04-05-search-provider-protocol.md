# SearchProvider 协议化 实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将搜索源实现从 1264 行巨石文件中解耦为独立的 Provider 类，通过协议接口和注册机制管理，新增搜索源无需修改核心代码。

**Architecture:** 定义 `SearchProvider` Protocol，每个搜索源（SearXNG / SerpAPI / Serper）实现为独立 Provider 类，通过 `ProviderRegistry` 按环境变量自动注册。路由层（fallback / merge / parallel / parallel_conf_fallback）仅依赖 Protocol 接口，不感知具体实现。

**Tech Stack:** Python 3.12+, httpx, tenacity, Protocol (typing), dataclass

---

## 现状分析

### 核心文件
- `libs/miroflow-tools/src/miroflow_tools/dev_mcp_servers/search_and_scrape_webpage.py`（1264 行）
  - 三个 provider 实现内联在 `execute_provider_search()` 嵌套函数（L642-L785）
  - 四种路由模式在 `perform_search()` 方法中（L788-L1095）
  - 辅助函数：`_merge_provider_results`、`_evaluate_confidence`、`_parse_provider_order` 等
  - HTTP 请求函数：`make_serper_request`、`make_serpapi_request`、`make_searxng_request`
  - SearXNG 预检逻辑：`_ensure_searxng_json_ready`
  - 全局 httpx 连接池：`_get_shared_client`

### 另有独立 MCP Server（不在本次重构范围内，但需保持兼容）
- `mcp_servers/serper_mcp_server.py` — 独立 Serper 工具
- `mcp_servers/searching_google_mcp_server.py` — 通过 stdio 调用 serper_mcp_server

---

## 目标文件结构

```
libs/miroflow-tools/src/miroflow_tools/
├── dev_mcp_servers/
│   ├── search_and_scrape_webpage.py   # 精简为路由层 + MCP tool
│   └── providers/
│       ├── __init__.py                # 导出 registry
│       ├── base.py                    # SearchProvider Protocol + SearchResult dataclass
│       ├── registry.py                # ProviderRegistry 注册和发现
│       ├── serper.py                  # SerperProvider
│       ├── serpapi.py                 # SerpAPIProvider
│       └── searxng.py                # SearXNGProvider
```

---

## Task 1: 定义 SearchProvider 协议和数据结构

**Files:**
- Create: `libs/miroflow-tools/src/miroflow_tools/dev_mcp_servers/providers/__init__.py`
- Create: `libs/miroflow-tools/src/miroflow_tools/dev_mcp_servers/providers/base.py`
- Test: `libs/miroflow-tools/src/test/test_search_provider_protocol.py`

### Step 1: 编写协议测试

```python
"""SearchProvider 协议接口测试。"""
import pytest
from miroflow_tools.dev_mcp_servers.providers.base import (
    SearchProvider,
    SearchResult,
    SearchParams,
)


class TestSearchResult:
    def test_create(self):
        r = SearchResult(position=1, title="Test", link="https://example.com", snippet="desc")
        assert r.position == 1
        assert r.link == "https://example.com"

    def test_to_dict(self):
        r = SearchResult(position=1, title="Test", link="https://example.com", snippet="desc")
        d = r.to_dict()
        assert d["position"] == 1
        assert d["link"] == "https://example.com"


class TestSearchParams:
    def test_create(self):
        p = SearchParams(query="test", num=10, page=1, hl="en", gl="us")
        assert p.query == "test"


class TestSearchProviderProtocol:
    def test_protocol_compliance(self):
        """验证 Protocol 定义的方法签名存在。"""
        assert hasattr(SearchProvider, "name")
        assert hasattr(SearchProvider, "search")
        assert hasattr(SearchProvider, "is_available")
```

### Step 2: 实现 base.py

```python
"""SearchProvider 协议定义和共享数据结构。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
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
        d = {"position": self.position, "title": self.title, "link": self.link, "snippet": self.snippet}
        if self.source:
            d["source"] = self.source
        if self.extra:
            d.update(self.extra)
        return d


@dataclass
class SearchParams:
    """搜索请求参数。"""
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

    async def search(self, params: SearchParams) -> tuple[list[SearchResult], dict[str, Any]]:
        """
        执行搜索，返回 (结果列表, 元信息字典)。
        元信息字典包含 provider、query 等调试信息。
        """
        ...
```

### Step 3: 创建 __init__.py

```python
"""搜索源 Provider 协议与注册。"""
from .base import SearchProvider, SearchResult, SearchParams
from .registry import ProviderRegistry

__all__ = ["SearchProvider", "SearchResult", "SearchParams", "ProviderRegistry"]
```

### Step 4: 运行测试验证

```bash
cd libs/miroflow-tools && uv run pytest src/test/test_search_provider_protocol.py -v
```

### Step 5: 提交

```bash
git add libs/miroflow-tools/src/miroflow_tools/dev_mcp_servers/providers/
git add libs/miroflow-tools/src/test/test_search_provider_protocol.py
git commit -m "feat(search): 定义 SearchProvider 协议接口和 SearchResult 数据结构"
```

---

## Task 2: 实现 ProviderRegistry

**Files:**
- Create: `libs/miroflow-tools/src/miroflow_tools/dev_mcp_servers/providers/registry.py`
- Test: `libs/miroflow-tools/src/test/test_search_provider_protocol.py`（追加）

### Step 1: 追加 Registry 测试

```python
from miroflow_tools.dev_mcp_servers.providers.registry import ProviderRegistry
from miroflow_tools.dev_mcp_servers.providers.base import SearchProvider, SearchResult, SearchParams

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
        return [SearchResult(position=1, title="fake", link="https://fake.com", snippet="")], {"provider": self._name}


class TestProviderRegistry:
    def test_register_and_get(self):
        reg = ProviderRegistry()
        p = _FakeProvider("test")
        reg.register(p)
        assert reg.get("test") is p

    def test_get_missing_returns_none(self):
        reg = ProviderRegistry()
        assert reg.get("nope") is None

    def test_available_providers(self):
        reg = ProviderRegistry()
        reg.register(_FakeProvider("a", available=True))
        reg.register(_FakeProvider("b", available=False))
        assert reg.available_names() == ["a"]

    def test_resolve_order(self):
        reg = ProviderRegistry()
        reg.register(_FakeProvider("serper", available=True))
        reg.register(_FakeProvider("serpapi", available=True))
        reg.register(_FakeProvider("searxng", available=False))
        resolved = reg.resolve_order("searxng,serpapi,serper")
        assert resolved == ["serpapi", "serper"]  # searxng 不可用，被过滤

    def test_duplicate_register_overwrites(self):
        reg = ProviderRegistry()
        p1 = _FakeProvider("x")
        p2 = _FakeProvider("x")
        reg.register(p1)
        reg.register(p2)
        assert reg.get("x") is p2
```

### Step 2: 实现 registry.py

```python
"""搜索源注册中心。"""
from __future__ import annotations

import logging
from typing import Optional

from .base import SearchProvider

logger = logging.getLogger("miroflow")


class ProviderRegistry:
    """管理搜索源的注册、发现和排序。"""

    def __init__(self) -> None:
        self._providers: dict[str, SearchProvider] = {}

    def register(self, provider: SearchProvider) -> None:
        self._providers[provider.name] = provider
        logger.debug("注册搜索源: %s (available=%s)", provider.name, provider.is_available())

    def get(self, name: str) -> Optional[SearchProvider]:
        return self._providers.get(name)

    def available_names(self) -> list[str]:
        return [name for name, p in self._providers.items() if p.is_available()]

    def resolve_order(self, order_config: str) -> list[str]:
        """按配置字符串解析可用 provider 顺序，不可用的自动过滤。"""
        seen: set[str] = set()
        result: list[str] = []
        configured = [p.strip().lower() for p in order_config.split(",") if p.strip()]
        for name in configured:
            if name not in seen and self.get(name) and self.get(name).is_available():
                result.append(name)
                seen.add(name)
        # 追加未显式配置但可用的 provider
        for name in self._providers:
            if name not in seen and self._providers[name].is_available():
                result.append(name)
                seen.add(name)
        return result

    def __contains__(self, name: str) -> bool:
        return name in self._providers

    def __len__(self) -> int:
        return len(self._providers)
```

### Step 3: 运行测试

```bash
cd libs/miroflow-tools && uv run pytest src/test/test_search_provider_protocol.py -v
```

### Step 4: 提交

```bash
git commit -m "feat(search): 实现 ProviderRegistry 搜索源注册中心"
```

---

## Task 3: 提取 SerperProvider

**Files:**
- Create: `libs/miroflow-tools/src/miroflow_tools/dev_mcp_servers/providers/serper.py`
- Test: `libs/miroflow-tools/src/test/test_serper_provider.py`

### 关键逻辑
从 `search_and_scrape_webpage.py` L646-L676 提取，核心是：
1. 构建 payload 调用 Serper REST API
2. 解析 `data["organic"]` 为 SearchResult 列表
3. Key 池轮转（`_serper_key_pool`）
4. 过滤 banned URL

### Step 1: 编写测试（mock HTTP）

使用 `pytest` + `httpx` mock 测试 SerperProvider 的搜索逻辑和结果解析。

### Step 2: 实现 SerperProvider

```python
class SerperProvider:
    """Serper API 搜索源。"""

    def __init__(self, api_key: str = "", key_pool: Optional[KeyPool] = None,
                 base_url: str = "https://google.serper.dev"):
        self._api_key = api_key
        self._key_pool = key_pool
        self._base_url = base_url

    @property
    def name(self) -> str:
        return "serper"

    def is_available(self) -> bool:
        return bool(self._key_pool or self._api_key)

    async def search(self, params: SearchParams) -> tuple[list[SearchResult], dict[str, Any]]:
        # 构建 payload，调用 API，解析结果
        ...
```

### Step 3-5: 测试、运行、提交

---

## Task 4: 提取 SerpAPIProvider

**Files:**
- Create: `libs/miroflow-tools/src/miroflow_tools/dev_mcp_servers/providers/serpapi.py`
- Test: `libs/miroflow-tools/src/test/test_serpapi_provider.py`

### 关键逻辑
从 `search_and_scrape_webpage.py` L678-L726 提取。特殊处理：
1. 中文 hl 参数映射（zh -> zh-cn）
2. start 参数计算（`(page-1) * num`）
3. 结果字段映射（`organic_results` -> 标准格式）

---

## Task 5: 提取 SearXNGProvider

**Files:**
- Create: `libs/miroflow-tools/src/miroflow_tools/dev_mcp_servers/providers/searxng.py`
- Test: `libs/miroflow-tools/src/test/test_searxng_provider.py`

### 关键逻辑
从 `search_and_scrape_webpage.py` L728-L785 提取。特殊处理：
1. tbs 到 time_range 映射
2. SearXNG 预检逻辑（`_ensure_searxng_json_ready`）
3. 结果字段映射（`url` -> `link`，`content` -> `snippet`）

---

## Task 6: 重构 search_and_scrape_webpage.py 使用 Provider 协议

**Files:**
- Modify: `libs/miroflow-tools/src/miroflow_tools/dev_mcp_servers/search_and_scrape_webpage.py`

### 关键变更
1. 顶层初始化 `ProviderRegistry` 并注册三个 Provider
2. 将 `execute_provider_search()` 嵌套函数替换为 `registry.get(name).search(params)` 调用
3. 将 `perform_search()` 中的 `available_providers` dict 替换为 `registry.available_names()`
4. 将 `_parse_provider_order()` 替换为 `registry.resolve_order()`
5. 移除已迁移到 Provider 类的 `make_serper_request` / `make_serpapi_request` / `make_searxng_request`
6. 保留路由逻辑（fallback/merge/parallel/parallel_conf_fallback）和置信度评估

### 预期效果
- `search_and_scrape_webpage.py` 从 ~1264 行降至 ~600 行
- 每个 Provider 独立测试、独立配置
- 新增搜索源只需：(1) 创建 Provider 类 (2) 注册到 registry

---

## Task 7: 全量回归测试

**Files:**
- 运行现有测试确保无破坏性变更

```bash
cd libs/miroflow-tools && uv run pytest -v
cd apps/miroflow-agent && uv run pytest -v
cd apps/gradio-demo && uv run pytest -v
cd apps/api-server && uv run pytest tests/ -v
```

---

## Task 8: 更新文档和 CHANGELOG

**Files:**
- Modify: `docs/CHANGELOG.md` — 添加 v0.1.15 条目
- Modify: `docs/ROADMAP.md` — 标记 SearchProvider 协议化已完成
- Modify: `libs/miroflow-tools/README.md` — 补充 Provider 扩展说明

---

## 风险与注意事项

1. **向后兼容**：`mcp_servers/serper_mcp_server.py` 和 `searching_google_mcp_server.py` 不在本次重构范围，保持原样
2. **全局连接池**：`_get_shared_client()` 需要在 Provider 之间共享，放在 `base.py` 或保留在主文件
3. **环境变量**：Provider 初始化在模块加载时读取环境变量，与当前行为一致
4. **Key 池**：各 Provider 独立管理自己的 KeyPool 实例
5. **SearXNG 预检**：预检逻辑随 SearXNGProvider 迁移，状态由 Provider 实例持有
