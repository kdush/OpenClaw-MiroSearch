# Copyright (c) 2025 MiroMind
# This source code is licensed under the Apache 2.0 License.

"""
真实凭据集成测试：Key 池轮转 + 降级。

运行方式（需要设置环境变量）：
    SERPAPI_API_KEYS="key1,key2" \
    OPENROUTER_API_KEYS="sk-or-...,sk-or-..." \
    LONGCAT_API_KEY="ak_..." \
    uv run pytest tests/test_integration_key_rotation.py -v --no-cov -m integration

所有测试标记为 integration，默认跳过（缺少环境变量时）。
"""

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 确保可以导入项目代码
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LIBS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "miroflow-tools"
if str(LIBS_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(LIBS_ROOT / "src"))

from miroflow_tools.mcp_servers.utils.key_pool import KeyPool


# ---------------------------------------------------------------------------
# SerpAPI Key 轮转测试
# ---------------------------------------------------------------------------

SERPAPI_KEYS_RAW = os.getenv("SERPAPI_API_KEYS", "")

_need_serpapi = pytest.mark.skipif(
    not SERPAPI_KEYS_RAW or len(SERPAPI_KEYS_RAW.split(",")) < 2,
    reason="需要 SERPAPI_API_KEYS 环境变量（至少 2 个 Key）",
)


@pytest.mark.integration
@_need_serpapi
def test_serpapi_key_pool_rotation():
    """SerpAPI 多 Key 池轮转：验证 round-robin。"""
    pool = KeyPool.from_env("SERPAPI_API_KEYS")
    assert pool.size >= 2

    first = pool.current_key()
    second = pool.rotate()
    assert first != second
    third = pool.rotate()
    assert third == first  # round-robin 回到第一个

    print(f"✓ SerpAPI pool size={pool.size}, rotation OK")


@pytest.mark.integration
@_need_serpapi
@pytest.mark.asyncio
async def test_serpapi_real_search():
    """SerpAPI 真实搜索请求，验证 Key 可用。"""
    import httpx

    pool = KeyPool.from_env("SERPAPI_API_KEYS")
    key = pool.current_key()
    params = {
        "engine": "google",
        "q": "MiroThinker AI",
        "api_key": key,
        "hl": "en",
        "gl": "us",
        "num": 3,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get("https://serpapi.com/search.json", params=params)
        assert resp.status_code == 200
        data = resp.json()
        assert "organic_results" in data or "search_metadata" in data
        print(f"✓ SerpAPI search OK, key=...{key[-6:]}")


# ---------------------------------------------------------------------------
# OpenRouter Key 轮转 + 降级测试
# ---------------------------------------------------------------------------

OPENROUTER_KEYS_RAW = os.getenv("OPENROUTER_API_KEYS", "")

_need_openrouter = pytest.mark.skipif(
    not OPENROUTER_KEYS_RAW or len(OPENROUTER_KEYS_RAW.split(",")) < 2,
    reason="需要 OPENROUTER_API_KEYS 环境变量（至少 2 个 Key）",
)


@pytest.mark.integration
@_need_openrouter
def test_openrouter_key_pool_rotation():
    """OpenRouter 多 Key 池轮转：验证 round-robin。"""
    pool = KeyPool.from_env("OPENROUTER_API_KEYS")
    assert pool.size >= 2

    first = pool.current_key()
    second = pool.rotate()
    assert first != second
    print(f"✓ OpenRouter pool size={pool.size}, rotation OK")


@pytest.mark.integration
@_need_openrouter
@pytest.mark.asyncio
async def test_openrouter_real_chat():
    """OpenRouter 真实 chat completion，验证 Key 可用。"""
    from openai import AsyncOpenAI

    pool = KeyPool.from_env("OPENROUTER_API_KEYS")
    key = pool.current_key()
    client = AsyncOpenAI(
        api_key=key,
        base_url="https://openrouter.ai/api/v1",
    )
    response = await client.chat.completions.create(
        model="openai/gpt-4.1-nano",
        messages=[{"role": "user", "content": "Say 'rotation test OK' in 5 words or less."}],
        max_tokens=20,
    )
    content = response.choices[0].message.content or ""
    print(f"✓ OpenRouter chat OK, key=...{key[-6:]}, response={content[:50]}")
    assert len(content) > 0


@pytest.mark.integration
@_need_openrouter
@pytest.mark.asyncio
async def test_openrouter_key_switch_on_invalid():
    """OpenRouter 一个无效 Key + 一个有效 Key，验证切换逻辑。"""
    keys_raw = OPENROUTER_KEYS_RAW
    valid_keys = [k.strip() for k in keys_raw.split(",") if k.strip()]
    # 构造一个 "无效key,有效key" 池
    fake_key = "sk-or-v1-invalid_key_for_test_000000000000000000"
    pool = KeyPool([fake_key, valid_keys[0]])

    # 先用无效 Key
    assert pool.current_key() == fake_key
    # 标记无效 Key 为限速（模拟 429）
    pool.mark_rate_limited(fake_key, retry_after=60.0)
    # 获取下一个可用 Key
    next_key = pool.next_available_key()
    assert next_key == valid_keys[0]
    print(f"✓ OpenRouter key switch OK: invalid → valid (...{next_key[-6:]})")


# ---------------------------------------------------------------------------
# 龙猫 (LongCat) Key 测试
# ---------------------------------------------------------------------------

LONGCAT_API_KEY = os.getenv("LONGCAT_API_KEY", "")

_need_longcat = pytest.mark.skipif(
    not LONGCAT_API_KEY,
    reason="需要 LONGCAT_API_KEY 环境变量",
)


@pytest.mark.integration
@_need_longcat
@pytest.mark.asyncio
async def test_longcat_real_chat():
    """龙猫 LongCat-Flash-Chat 真实请求。"""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=LONGCAT_API_KEY,
        base_url="https://api.longcat.chat/openai",
    )
    response = await client.chat.completions.create(
        model="LongCat-Flash-Chat",
        messages=[{"role": "user", "content": "Say hello in Chinese."}],
        max_tokens=30,
    )
    content = response.choices[0].message.content or ""
    print(f"✓ LongCat chat OK, response={content[:50]}")
    assert len(content) > 0


@pytest.mark.integration
@_need_longcat
def test_longcat_single_key_pool():
    """龙猫单 Key 池构建正确。"""
    pool = KeyPool([LONGCAT_API_KEY])
    assert pool.size == 1
    assert pool.current_key() == LONGCAT_API_KEY
    # 单 Key 轮转后仍然是自己
    assert pool.rotate() == LONGCAT_API_KEY
    print("✓ LongCat single-key pool OK")
