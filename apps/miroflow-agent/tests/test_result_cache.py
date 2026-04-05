"""ResultCache 回归测试。

验证：
1. 基本 get/put 读写
2. LRU 淘汰策略
3. TTL 过期机制
4. 相同参数生成相同 key
5. invalidate / clear 操作
"""

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.cache.result_cache import ResultCache


def test_basic_get_put():
    """写入后应能读取相同结果。"""
    cache = ResultCache(max_size=10, ttl_seconds=60)
    key = ResultCache.make_key("test query", "balanced")
    cache.put(key, "result-123")
    assert cache.get(key) == "result-123"


def test_cache_miss():
    """未写入的 key 应返回 None。"""
    cache = ResultCache()
    assert cache.get("nonexistent") is None


def test_lru_eviction():
    """超过 max_size 时应淘汰最久未访问的条目。"""
    cache = ResultCache(max_size=3, ttl_seconds=0)
    cache.put("a", "1")
    cache.put("b", "2")
    cache.put("c", "3")
    # 写入第 4 条应淘汰 "a"
    cache.put("d", "4")
    assert cache.get("a") is None
    assert cache.get("b") == "2"
    assert cache.size == 3


def test_lru_access_refreshes_order():
    """访问条目应刷新其 LRU 顺序。"""
    cache = ResultCache(max_size=3, ttl_seconds=0)
    cache.put("a", "1")
    cache.put("b", "2")
    cache.put("c", "3")
    # 访问 "a" 使其变为最新
    cache.get("a")
    # 写入第 4 条应淘汰 "b"（最久未访问）
    cache.put("d", "4")
    assert cache.get("a") == "1"
    assert cache.get("b") is None


def test_ttl_expiration():
    """过期条目应返回 None。"""
    cache = ResultCache(max_size=10, ttl_seconds=1)
    key = "ttl-test"
    cache.put(key, "value")
    assert cache.get(key) == "value"
    time.sleep(1.1)
    assert cache.get(key) is None


def test_make_key_deterministic():
    """相同参数应生成相同 key。"""
    k1 = ResultCache.make_key("hello world", "balanced", "parallel-trusted", "detailed")
    k2 = ResultCache.make_key("hello world", "balanced", "parallel-trusted", "detailed")
    assert k1 == k2


def test_make_key_case_insensitive_query():
    """query 部分应忽略大小写。"""
    k1 = ResultCache.make_key("Hello World")
    k2 = ResultCache.make_key("hello world")
    assert k1 == k2


def test_make_key_different_params():
    """不同参数应生成不同 key。"""
    k1 = ResultCache.make_key("query", "balanced")
    k2 = ResultCache.make_key("query", "verified")
    assert k1 != k2


def test_invalidate():
    """invalidate 应删除指定条目。"""
    cache = ResultCache()
    cache.put("x", "val")
    assert cache.invalidate("x") is True
    assert cache.get("x") is None
    assert cache.invalidate("x") is False


def test_clear():
    """clear 应清空所有条目并返回清除数量。"""
    cache = ResultCache()
    cache.put("a", "1")
    cache.put("b", "2")
    count = cache.clear()
    assert count == 2
    assert cache.size == 0


def test_stats():
    """stats 应返回正确的统计信息。"""
    cache = ResultCache(max_size=50, ttl_seconds=120)
    cache.put("k", "v")
    s = cache.stats()
    assert s["size"] == 1
    assert s["max_size"] == 50
    assert s["ttl_seconds"] == 120
