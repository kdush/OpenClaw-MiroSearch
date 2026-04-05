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
        """注册一个搜索源，同名覆盖。"""
        self._providers[provider.name] = provider
        logger.debug(
            "注册搜索源: %s (available=%s)", provider.name, provider.is_available()
        )

    def get(self, name: str) -> Optional[SearchProvider]:
        """按名称获取搜索源，不存在返回 None。"""
        return self._providers.get(name)

    def available_names(self) -> list[str]:
        """返回所有当前可用的搜索源名称列表。"""
        return [name for name, p in self._providers.items() if p.is_available()]

    def resolve_order(self, order_config: str) -> list[str]:
        """
        按配置字符串解析可用 provider 顺序。

        不可用的自动过滤；未显式配置但可用的追加到末尾。
        """
        seen: set[str] = set()
        result: list[str] = []
        configured = [
            p.strip().lower() for p in order_config.split(",") if p.strip()
        ]
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
