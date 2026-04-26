# Copyright (c) 2025 MiroMind
# This source code is licensed under the Apache 2.0 License.

"""LLM Key 池轮转与 429 感知退避 单元测试。

覆盖场景：
- _extract_retry_after 从 RateLimitError 提取 Retry-After
- 单 Key 429 时自动切换到下一 Key 并成功重试
- Key 全部耗尽时优雅报错，不无限循环
"""

import importlib
from pathlib import Path
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from omegaconf import OmegaConf
from openai import RateLimitError

# ---------------------------------------------------------------------------
# 确保测试可直接导入项目源码
# ---------------------------------------------------------------------------
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

OpenAIClient = importlib.import_module("src.llm.providers.openai_client").OpenAIClient


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------

def _make_minimal_cfg(**overrides) -> Any:
    """构造最小化的 Hydra DictConfig，用于实例化 OpenAIClient。"""
    base = {
        "llm": {
            "provider": "openai",
            "model_name": "test-model",
            "temperature": 0.7,
            "top_p": 0.9,
            "min_p": 0.0,
            "top_k": 50,
            "max_context_length": 4096,
            "max_tokens": 1024,
            "async_client": True,
            "api_key": "single-fallback-key",
            "base_url": "http://localhost:9999/v1",
            "max_retries": 3,
            "retry_wait_seconds": 0.01,
        },
        "agent": {
            "keep_tool_result": 5,
        },
    }
    cfg = OmegaConf.create(base)
    if overrides:
        cfg = OmegaConf.merge(cfg, overrides)
    return cfg


def _make_task_log() -> MagicMock:
    """构造 mock TaskLog。"""
    task_log = MagicMock()
    task_log.log_step = MagicMock()
    task_log.record_stage_timing = MagicMock()
    return task_log


def _make_rate_limit_error(retry_after: Optional[str] = None) -> RateLimitError:
    """构造 mock RateLimitError，可选设置 Retry-After header。"""
    headers = {}
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    mock_response = httpx.Response(
        status_code=429,
        headers=headers,
        request=httpx.Request("POST", "http://localhost/v1/chat/completions"),
    )
    return RateLimitError(
        message="Rate limit exceeded",
        response=mock_response,
        body={"error": {"message": "Rate limit exceeded"}},
    )


def _make_success_response(content: str = "Hello") -> MagicMock:
    """构造成功的 OpenAI chat completion response mock。"""
    choice = MagicMock()
    choice.finish_reason = "stop"
    choice.message.content = content
    choice.message.tool_calls = None
    usage = MagicMock()
    usage.prompt_tokens = 10
    usage.completion_tokens = 5
    usage.prompt_tokens_details = None
    usage.completion_tokens_details = None
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = usage
    resp.model = "test-model"
    return resp


# ===========================================================================
# _extract_retry_after 测试
# ===========================================================================


class TestExtractRetryAfter:
    def test_with_retry_after_header(self):
        err = _make_rate_limit_error(retry_after="30")
        result = OpenAIClient._extract_retry_after(err)
        assert result == 30.0

    def test_without_retry_after_header(self):
        err = _make_rate_limit_error(retry_after=None)
        result = OpenAIClient._extract_retry_after(err, default=15.0)
        assert result == 15.0

    def test_retry_after_below_minimum(self):
        err = _make_rate_limit_error(retry_after="0.1")
        result = OpenAIClient._extract_retry_after(err)
        assert result == 1.0  # 最小 1 秒

    def test_non_numeric_retry_after_uses_default(self):
        err = _make_rate_limit_error(retry_after="not-a-number")
        result = OpenAIClient._extract_retry_after(err, default=20.0)
        assert result == 20.0


# ===========================================================================
# Key 池初始化测试
# ===========================================================================


class TestKeyPoolInit:
    def test_multi_key_from_env(self, monkeypatch):
        """OPENAI_API_KEYS 设置多 Key 时，pool.size > 1。"""
        monkeypatch.setenv("OPENAI_API_KEYS", "key-a,key-b,key-c")
        task_log = _make_task_log()
        cfg = _make_minimal_cfg()
        with patch.object(OpenAIClient, "_create_client", return_value=MagicMock()):
            client = OpenAIClient(task_id="test-1", cfg=cfg, task_log=task_log)
        assert client._key_pool.size == 3

    def test_fallback_to_single_key(self, monkeypatch):
        """OPENAI_API_KEYS 未设置时，回退到 cfg 中的 api_key。"""
        monkeypatch.delenv("OPENAI_API_KEYS", raising=False)
        task_log = _make_task_log()
        cfg = _make_minimal_cfg()
        with patch.object(OpenAIClient, "_create_client", return_value=MagicMock()):
            client = OpenAIClient(task_id="test-2", cfg=cfg, task_log=task_log)
        assert client._key_pool.size == 1
        assert client._key_pool.current_key() == "single-fallback-key"


class TestOpenAISdkRetries:
    def test_sdk_internal_retries_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("OPENAI_SDK_MAX_RETRIES", raising=False)
        task_log = _make_task_log()
        cfg = _make_minimal_cfg()

        client = OpenAIClient(task_id="test-sdk-retries", cfg=cfg, task_log=task_log)

        assert client.client.max_retries == 0

    def test_sdk_internal_retries_can_be_overridden(self, monkeypatch):
        monkeypatch.setenv("OPENAI_SDK_MAX_RETRIES", "1")
        task_log = _make_task_log()
        cfg = _make_minimal_cfg()

        client = OpenAIClient(task_id="test-sdk-retries-override", cfg=cfg, task_log=task_log)

        assert client.client.max_retries == 1


# ===========================================================================
# 429 Key 轮转集成测试
# ===========================================================================


class TestRateLimitKeyRotation:
    @pytest.mark.asyncio
    async def test_429_switches_key_and_retries(self, monkeypatch):
        """单 Key 返回 429 时自动切换到下一 Key 并成功重试。"""
        monkeypatch.setenv("OPENAI_API_KEYS", "key-a,key-b")
        task_log = _make_task_log()
        cfg = _make_minimal_cfg()

        mock_client = AsyncMock()
        # 第一次调用 429，第二次成功
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[
                _make_rate_limit_error(retry_after="5"),
                _make_success_response("OK after rotation"),
            ]
        )

        with patch.object(OpenAIClient, "_create_client", return_value=mock_client):
            client = OpenAIClient(task_id="test-rotate", cfg=cfg, task_log=task_log)
            client.client = mock_client

            response, _ = await client._create_message(
                system_prompt="You are a test assistant.",
                messages_history=[{"role": "user", "content": "test"}],
                tools_definitions=[],
            )

        # 验证：成功返回且 key 已切换
        assert response.choices[0].message.content == "OK after rotation"
        assert mock_client.api_key == "key-b"

    @pytest.mark.asyncio
    async def test_all_keys_exhausted_raises(self, monkeypatch):
        """Key 全部耗尽时优雅报错，不无限循环。"""
        monkeypatch.setenv("OPENAI_API_KEYS", "key-a,key-b")
        task_log = _make_task_log()
        cfg = _make_minimal_cfg(llm={"max_retries": 2, "retry_wait_seconds": 0.01})

        mock_client = AsyncMock()
        # 所有调用都返回 429
        mock_client.chat.completions.create = AsyncMock(
            side_effect=_make_rate_limit_error(retry_after="0.01"),
        )

        with patch.object(OpenAIClient, "_create_client", return_value=mock_client):
            client = OpenAIClient(task_id="test-exhausted", cfg=cfg, task_log=task_log)
            client.client = mock_client

            with pytest.raises(RateLimitError):
                await client._create_message(
                    system_prompt="You are a test assistant.",
                    messages_history=[{"role": "user", "content": "test"}],
                    tools_definitions=[],
                )

    @pytest.mark.asyncio
    async def test_single_key_429_still_retries(self, monkeypatch):
        """单 Key 池遇到 429 也能走冷却等待后重试。"""
        monkeypatch.delenv("OPENAI_API_KEYS", raising=False)
        task_log = _make_task_log()
        cfg = _make_minimal_cfg(llm={"max_retries": 3, "retry_wait_seconds": 0.01})

        mock_client = AsyncMock()
        # 第一次 429（冷却 0.01s），第二次成功
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[
                _make_rate_limit_error(retry_after="0.01"),
                _make_success_response("Recovered"),
            ]
        )

        with patch.object(OpenAIClient, "_create_client", return_value=mock_client):
            client = OpenAIClient(task_id="test-single-429", cfg=cfg, task_log=task_log)
            client.client = mock_client

            response, _ = await client._create_message(
                system_prompt="You are a test assistant.",
                messages_history=[{"role": "user", "content": "test"}],
                tools_definitions=[],
            )

        assert response.choices[0].message.content == "Recovered"
