# Copyright (c) 2025 MiroMind
# This source code is licensed under the Apache 2.0 License.

"""
线程安全的 API Key 轮转池。

支持 round-robin 分配，429 限速标记与冷却，全部耗尽时返回最短剩余冷却时间。
LLM 客户端和搜索工具均可复用此模块。
"""

import logging
import os
import threading
import time
from typing import List, Optional, Tuple

logger = logging.getLogger("miroflow")


class KeyPool:
    """线程安全的 API Key 轮转池。

    用法::

        pool = KeyPool.from_env("OPENAI_API_KEYS", fallback_key=cfg_api_key)
        key = pool.current_key()
        # ... 请求失败时：
        pool.mark_rate_limited(key, retry_after=30.0)
        next_key = pool.next_available_key()
    """

    def __init__(self, keys: List[str]):
        if not keys:
            raise ValueError("KeyPool 至少需要一个 Key")
        # 去重并保持顺序
        seen = set()
        unique_keys: List[str] = []
        for k in keys:
            k = k.strip()
            if k and k not in seen:
                seen.add(k)
                unique_keys.append(k)
        if not unique_keys:
            raise ValueError("KeyPool 去重后没有有效 Key")
        self._keys = unique_keys
        self._index = 0
        self._lock = threading.Lock()
        # key -> 冷却截止时间戳（time.monotonic）
        self._cooldowns: dict[str, float] = {}

    # ------------------------------------------------------------------
    # 工厂方法
    # ------------------------------------------------------------------

    @classmethod
    def from_env(
        cls,
        env_name: str,
        fallback_key: Optional[str] = None,
        separator: str = ",",
    ) -> "KeyPool":
        """从环境变量创建 KeyPool。

        优先读取 ``env_name``（逗号分隔多 Key），
        若未设置则回退到 ``fallback_key`` 构建单 Key 池。
        """
        raw = os.getenv(env_name, "")
        keys = [k.strip() for k in raw.split(separator) if k.strip()]
        if not keys and fallback_key:
            keys = [fallback_key.strip()]
        if not keys:
            raise ValueError(
                f"环境变量 {env_name} 未设置且无 fallback_key，无法创建 KeyPool"
            )
        return cls(keys)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    @property
    def size(self) -> int:
        return len(self._keys)

    def current_key(self) -> str:
        with self._lock:
            return self._keys[self._index % len(self._keys)]

    def all_exhausted(self) -> bool:
        """所有 Key 是否均在冷却期内。"""
        now = time.monotonic()
        with self._lock:
            return all(
                self._cooldowns.get(k, 0) > now for k in self._keys
            )

    def min_cooldown_remaining(self) -> float:
        """所有 Key 中最短的剩余冷却秒数（已可用则返回 0）。"""
        now = time.monotonic()
        with self._lock:
            remaining = [
                max(0.0, self._cooldowns.get(k, 0) - now)
                for k in self._keys
            ]
        return min(remaining) if remaining else 0.0

    # ------------------------------------------------------------------
    # 轮转与标记
    # ------------------------------------------------------------------

    def mark_rate_limited(self, key: str, retry_after: float) -> None:
        """标记某个 Key 被限速，设置冷却截止时间。"""
        with self._lock:
            self._cooldowns[key] = time.monotonic() + retry_after
        logger.warning(
            "KeyPool | Key ...%s 被限速，冷却 %.1fs",
            key[-6:] if len(key) > 6 else "***",
            retry_after,
        )

    def next_available_key(self) -> Optional[str]:
        """切换到下一个可用（未在冷却期）的 Key 并返回。

        遍历一轮所有 Key，返回第一个可用的；若全部冷却则返回 None。
        """
        now = time.monotonic()
        with self._lock:
            for _ in range(len(self._keys)):
                self._index = (self._index + 1) % len(self._keys)
                candidate = self._keys[self._index]
                if self._cooldowns.get(candidate, 0) <= now:
                    return candidate
        return None

    def rotate(self) -> str:
        """无条件切换到下一个 Key（不检查冷却），返回新 Key。"""
        with self._lock:
            self._index = (self._index + 1) % len(self._keys)
            return self._keys[self._index]

    def get_status(self) -> List[Tuple[str, bool, float]]:
        """返回所有 Key 的状态列表：(key_masked, is_available, cooldown_remaining)。"""
        now = time.monotonic()
        result = []
        with self._lock:
            for k in self._keys:
                cd = self._cooldowns.get(k, 0)
                remaining = max(0.0, cd - now)
                masked = f"...{k[-6:]}" if len(k) > 6 else "***"
                result.append((masked, remaining <= 0, remaining))
        return result
