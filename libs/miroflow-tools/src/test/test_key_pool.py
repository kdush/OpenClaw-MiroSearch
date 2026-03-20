# Copyright (c) 2025 MiroMind
# This source code is licensed under the Apache 2.0 License.

"""KeyPool 单元测试：轮转、429 标记、冷却、全部耗尽场景。"""

import os
import time

import pytest

from miroflow_tools.mcp_servers.utils.key_pool import KeyPool


# ------------------------------------------------------------------
# 构造
# ------------------------------------------------------------------


class TestKeyPoolConstruction:
    def test_single_key(self):
        pool = KeyPool(["key-a"])
        assert pool.size == 1
        assert pool.current_key() == "key-a"

    def test_multiple_keys(self):
        pool = KeyPool(["k1", "k2", "k3"])
        assert pool.size == 3

    def test_dedup_preserves_order(self):
        pool = KeyPool(["k1", "k2", "k1", "k3", "k2"])
        assert pool.size == 3
        assert pool.current_key() == "k1"

    def test_empty_keys_raises(self):
        with pytest.raises(ValueError, match="至少需要一个"):
            KeyPool([])

    def test_whitespace_only_keys_raises(self):
        with pytest.raises(ValueError, match="没有有效"):
            KeyPool(["  ", "", "\t"])

    def test_strips_whitespace(self):
        pool = KeyPool(["  key-a  ", " key-b "])
        assert pool.current_key() == "key-a"
        assert pool.size == 2


# ------------------------------------------------------------------
# from_env 工厂
# ------------------------------------------------------------------


class TestKeyPoolFromEnv:
    def test_from_env_comma_separated(self, monkeypatch):
        monkeypatch.setenv("TEST_API_KEYS", "k1,k2,k3")
        pool = KeyPool.from_env("TEST_API_KEYS")
        assert pool.size == 3

    def test_from_env_fallback_key(self, monkeypatch):
        monkeypatch.delenv("TEST_API_KEYS", raising=False)
        pool = KeyPool.from_env("TEST_API_KEYS", fallback_key="fallback-k")
        assert pool.size == 1
        assert pool.current_key() == "fallback-k"

    def test_from_env_no_key_raises(self, monkeypatch):
        monkeypatch.delenv("TEST_API_KEYS", raising=False)
        with pytest.raises(ValueError, match="无法创建"):
            KeyPool.from_env("TEST_API_KEYS")

    def test_from_env_ignores_empty_segments(self, monkeypatch):
        monkeypatch.setenv("TEST_API_KEYS", "k1,,k2,,,k3,")
        pool = KeyPool.from_env("TEST_API_KEYS")
        assert pool.size == 3


# ------------------------------------------------------------------
# 轮转
# ------------------------------------------------------------------


class TestKeyPoolRotation:
    def test_rotate_cycles(self):
        pool = KeyPool(["a", "b", "c"])
        assert pool.current_key() == "a"
        assert pool.rotate() == "b"
        assert pool.current_key() == "b"
        assert pool.rotate() == "c"
        assert pool.rotate() == "a"  # 回到起点

    def test_single_key_rotate_stays(self):
        pool = KeyPool(["only"])
        assert pool.rotate() == "only"
        assert pool.rotate() == "only"


# ------------------------------------------------------------------
# 429 标记与冷却
# ------------------------------------------------------------------


class TestKeyPoolRateLimit:
    def test_mark_rate_limited_and_next_available(self):
        pool = KeyPool(["a", "b", "c"])
        pool.mark_rate_limited("a", retry_after=60.0)
        # a 被限速，next_available 应跳过 a
        nk = pool.next_available_key()
        assert nk == "b"

    def test_all_exhausted(self):
        pool = KeyPool(["a", "b"])
        pool.mark_rate_limited("a", retry_after=60.0)
        pool.mark_rate_limited("b", retry_after=60.0)
        assert pool.all_exhausted() is True
        assert pool.next_available_key() is None

    def test_cooldown_expires(self):
        pool = KeyPool(["a", "b"])
        pool.mark_rate_limited("a", retry_after=0.05)  # 50ms
        pool.mark_rate_limited("b", retry_after=60.0)
        # a 马上就过期
        time.sleep(0.06)
        assert pool.all_exhausted() is False
        nk = pool.next_available_key()
        assert nk == "a"

    def test_min_cooldown_remaining(self):
        pool = KeyPool(["a", "b"])
        pool.mark_rate_limited("a", retry_after=10.0)
        pool.mark_rate_limited("b", retry_after=20.0)
        remaining = pool.min_cooldown_remaining()
        # 应接近 10 秒（允许一定误差）
        assert 9.0 < remaining < 11.0

    def test_min_cooldown_remaining_zero_when_available(self):
        pool = KeyPool(["a", "b"])
        pool.mark_rate_limited("a", retry_after=10.0)
        # b 未被限速
        assert pool.min_cooldown_remaining() == 0.0


# ------------------------------------------------------------------
# get_status
# ------------------------------------------------------------------


class TestKeyPoolStatus:
    def test_status_shows_all_keys(self):
        pool = KeyPool(["abcdef123456", "xyz789"])
        pool.mark_rate_limited("abcdef123456", retry_after=30.0)
        status = pool.get_status()
        assert len(status) == 2
        # 第一个被限速
        masked_0, avail_0, remaining_0 = status[0]
        assert avail_0 is False
        assert remaining_0 > 0
        # 第二个可用
        masked_1, avail_1, remaining_1 = status[1]
        assert avail_1 is True
        assert remaining_1 == 0.0
