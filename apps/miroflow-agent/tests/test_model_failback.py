"""模型 failback 切换回归测试。

验证 OpenAIClient.activate_fallback() 的行为：
1. 配置了 fallback 模型时能成功激活
2. 激活后所有模型名均切换为 fallback
3. 重复调用不会二次激活
4. 未配置 fallback 模型时调用返回 False
5. 激活后 run_metrics 正确记录
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.logging.task_logger import RunMetrics, TaskLog


def _make_mock_client(fallback_name: str = ""):
    """构造一个最小化的 mock OpenAIClient 用于测试 failback 逻辑。

    直接在裸实例上设置属性，绕过 __post_init__ 中的外部依赖。
    """
    from src.llm.providers.openai_client import OpenAIClient

    task_log = TaskLog(task_id="failback-test", start_time="2026-04-05 00:00:00")

    client = object.__new__(OpenAIClient)
    client.model_name = "main-model"
    client.model_tool_name = "main-model"
    client.model_fast_name = "main-model"
    client.model_thinking_name = "main-model"
    client.model_summary_name = "main-model"
    client.model_fallback_name = fallback_name
    client._fallback_activated = False
    client.task_log = task_log

    return client


def test_activate_fallback_switches_all_model_names():
    """配置了 fallback 模型时，activate_fallback 应将所有模型名切换。"""
    client = _make_mock_client(fallback_name="backup-model")

    result = client.activate_fallback()

    assert result is True
    assert client.model_name == "backup-model"
    assert client.model_tool_name == "backup-model"
    assert client.model_fast_name == "backup-model"
    assert client.model_thinking_name == "backup-model"
    assert client.model_summary_name == "backup-model"
    assert client._fallback_activated is True


def test_activate_fallback_idempotent():
    """重复调用 activate_fallback 不应二次激活。"""
    client = _make_mock_client(fallback_name="backup-model")

    first = client.activate_fallback()
    second = client.activate_fallback()

    assert first is True
    assert second is False


def test_activate_fallback_no_fallback_configured():
    """未配置 fallback 模型时，activate_fallback 应返回 False 且不修改模型名。"""
    client = _make_mock_client(fallback_name="")

    result = client.activate_fallback()

    assert result is False
    assert client.model_name == "main-model"
    assert client._fallback_activated is False


def test_activate_fallback_updates_run_metrics():
    """activate_fallback 应正确更新 task_log.run_metrics。"""
    client = _make_mock_client(fallback_name="backup-model")

    client.activate_fallback()

    metrics = client.task_log.run_metrics
    assert metrics.failback_activated is True
    assert metrics.failback_model == "backup-model"
