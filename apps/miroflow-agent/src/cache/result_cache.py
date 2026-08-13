"""研究结果缓存：内存 LRU + TTL，相同 query+mode+profile 命中缓存避免重复消耗。"""

import hashlib
import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, Optional

logger = logging.getLogger("miroflow_agent")


class ResultCache:
    """线程安全的内存 LRU 缓存，支持 TTL 过期。

    缓存 key 由 query + mode + search_profile + output_detail_level 的哈希值生成。

    参数:
        max_size: 最大缓存条目数（LRU 淘汰）
        ttl_seconds: 缓存有效期（秒），0 表示永不过期
    """

    def __init__(self, max_size: int = 128, ttl_seconds: int = 3600):
        self._max_size = max(1, max_size)
        self._ttl_seconds = max(0, ttl_seconds)
        self._cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def make_key(
        query: str,
        mode: str = "",
        search_profile: str = "",
        output_detail_level: str = "",
        *,
        search_result_num: Optional[int] = None,
        verification_min_search_rounds: Optional[int] = None,
    ) -> str:
        """根据查询参数生成缓存 key。

        - 用完整 sha256 hexdigest，不截断：缓存命中返回的是完整研究结论，64bit
          截断一旦碰撞会返回"不相关问题的答案"，是正确性事故而非性能退化。
        - query 只去除首尾空白，不改变大小写；代码标识符、基因符号和缩写等
          查询可能依赖大小写语义，不能复用另一问题的完整研究结论。
        - mode/profile/detail 属于策略枚举，统一 strip().lower() 归一化。
        - 纳入 search_result_num / verification_min_search_rounds：这两个参数
          会改变检索深度，从而改变最终结论，必须参与 key，否则仅条数不同的请求
          会误命中彼此的缓存。None 表示该参数不影响（保持向后兼容的旧 key）。
        """
        parts = [
            query.strip(),
            mode.strip().lower(),
            search_profile.strip().lower(),
            output_detail_level.strip().lower(),
        ]
        if search_result_num is not None:
            parts.append(f"srn={search_result_num}")
        if verification_min_search_rounds is not None:
            parts.append(f"vmsr={verification_min_search_rounds}")
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Optional[str]:
        """查询缓存，命中则返回结果字符串，未命中或已过期返回 None。"""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if self._ttl_seconds > 0:
                age = time.monotonic() - entry["created_at"]
                if age > self._ttl_seconds:
                    del self._cache[key]
                    return None
            # LRU：移到末尾
            self._cache.move_to_end(key)
            return entry["result"]

    def put(self, key: str, result: str) -> None:
        """写入缓存。"""
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self._cache[key] = {"result": result, "created_at": time.monotonic()}
                return
            if len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)
            self._cache[key] = {"result": result, "created_at": time.monotonic()}

    def invalidate(self, key: str) -> bool:
        """删除指定缓存条目。"""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def clear(self) -> int:
        """清空所有缓存，返回清除条目数。"""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            return count

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._cache)

    def stats(self) -> Dict[str, Any]:
        """返回缓存统计信息。"""
        with self._lock:
            return {
                "size": len(self._cache),
                "max_size": self._max_size,
                "ttl_seconds": self._ttl_seconds,
            }
