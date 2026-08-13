# Copyright (c) 2025 MiroMind
# This source code is licensed under the Apache 2.0 License.

"""LLM 客户端工具服务映射隔离回归测试。"""

from __future__ import annotations

import asyncio
import sys
import warnings
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from omegaconf import OmegaConf

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.answer_generator import AnswerGenerator  # noqa: E402
from src.llm.providers.anthropic_client import AnthropicClient  # noqa: E402
from src.llm.providers.openai_client import OpenAIClient  # noqa: E402
from src.utils.parsing_utils import (  # noqa: E402
    fix_server_name_in_text,
    parse_llm_response_for_tool_calls,
    parse_tool_server_mapping,
)

TOOL_NAME = "google_search"
WRONG_SERVER_NAME = "shared-wrong-server"


def test_parsing_utils_compiles_without_syntax_warnings() -> None:
    """解析模块源码不得因文档字符串转义产生 SyntaxWarning。"""
    source_path = PROJECT_ROOT / "src" / "utils" / "parsing_utils.py"
    with warnings.catch_warnings():
        warnings.simplefilter("error", SyntaxWarning)
        compile(
            source_path.read_text(encoding="utf-8"),
            str(source_path),
            "exec",
        )


def _make_cfg(provider: str, *, async_client: bool) -> Any:
    """构造不连接真实服务的最小客户端配置。"""
    return OmegaConf.create(
        {
            "llm": {
                "provider": provider,
                "model_name": "test-model",
                "temperature": 0.3,
                "top_p": 1.0,
                "min_p": 0.0,
                "top_k": -1,
                "max_context_length": 8_192,
                "max_tokens": 1_024,
                "async_client": async_client,
                "api_key": "test-key",
                "base_url": "https://example.invalid",
                "max_retries": 1,
                "retry_wait_seconds": 0.01,
            },
            "agent": {"keep_tool_result": -1},
        }
    )


def _make_task_log() -> MagicMock:
    """构造客户端初始化所需的最小日志对象。"""
    task_log = MagicMock()
    task_log.log_step = MagicMock()
    task_log.record_stage_timing = MagicMock()
    task_log.run_metrics = MagicMock()
    task_log.run_metrics.record_model_route = MagicMock()
    return task_log


def _tool_definitions(server_name: str) -> list[dict[str, Any]]:
    """构造拥有同名工具、但服务名不同的工具定义。"""
    return [
        {
            "name": server_name,
            "tools": [
                {
                    "name": TOOL_NAME,
                    "description": "测试搜索工具",
                    "schema": {"type": "object", "properties": {}},
                }
            ],
        }
    ]


def _xml_tool_call() -> str:
    """构造需要按客户端映射修正服务名的 XML 工具调用。"""
    return (
        "<use_mcp_tool>\n"
        f"<server_name>{WRONG_SERVER_NAME}</server_name>\n"
        f"<tool_name>{TOOL_NAME}</tool_name>\n"
        '<arguments>{"query": "isolation"}</arguments>\n'
        "</use_mcp_tool>"
    )


def _longcat_tool_call() -> str:
    """构造未携带服务名、必须通过映射补全的 LongCat 工具调用。"""
    return (
        "<longcat_tool_call>"
        f'{{"name": "{TOOL_NAME}", "arguments": {{"query": "isolation"}}}}'
        "</longcat_tool_call>"
    )


def _make_provider_response(client: Any, text: str) -> Any:
    """按 Provider 构造文本响应。"""
    if isinstance(client, OpenAIClient):
        message = SimpleNamespace(content=text, tool_calls=None)
        choice = SimpleNamespace(finish_reason="stop", message=message)
        return SimpleNamespace(choices=[choice])
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
    )


def _process_tool_calls(client: Any, system_prompt: str) -> tuple[str, str]:
    """通过客户端的响应处理与工具解析路径读取当前调用映射。"""
    tool_server_mapping = parse_tool_server_mapping(system_prompt)
    xml_response = _make_provider_response(client, _xml_tool_call())
    fixed_text, should_exit, _ = client.process_llm_response(
        xml_response,
        [],
        tool_server_mapping=tool_server_mapping,
    )
    assert should_exit is False

    longcat_response = _make_provider_response(client, _longcat_tool_call())
    parsed_calls = client.extract_tool_calls_info(
        longcat_response,
        _longcat_tool_call(),
        tool_server_mapping=tool_server_mapping,
    )
    return fixed_text, parsed_calls[0]["server_name"]


def _make_answer_generator(client: Any, response_texts: list[str]) -> AnswerGenerator:
    """构造走真实响应处理路径、但不访问网络的 AnswerGenerator。"""
    responses = [
        _make_provider_response(client, response_text)
        for response_text in response_texts
    ]

    async def create_message(**kwargs):
        return responses.pop(0), kwargs["message_history"]

    client.create_message = create_message
    generator = AnswerGenerator.__new__(AnswerGenerator)
    generator.llm_client = client
    generator.cfg = _make_cfg(
        client.provider,
        async_client=client.async_client,
    )
    generator.task_log = client.task_log
    generator.stream = MagicMock()
    generator.output_formatter = MagicMock()
    generator.intermediate_boxed_answers = []
    return generator


@pytest.mark.parametrize(
    ("client_type", "provider"),
    [
        (OpenAIClient, "openai"),
        (AnthropicClient, "anthropic"),
    ],
    ids=["openai", "anthropic"],
)
@pytest.mark.parametrize("async_client", [True, False], ids=["async", "sync"])
@pytest.mark.asyncio
async def test_concurrent_clients_keep_tool_server_mapping_isolated(
    client_type: type,
    provider: str,
    async_client: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不同任务及无工具任务不能覆盖彼此的工具服务映射。"""
    monkeypatch.delenv("OPENAI_API_KEYS", raising=False)
    with patch.object(client_type, "_create_client", return_value=MagicMock()):
        first_client = client_type(
            task_id=f"{provider}-first-{async_client}",
            cfg=_make_cfg(provider, async_client=async_client),
            task_log=_make_task_log(),
        )
        second_client = client_type(
            task_id=f"{provider}-second-{async_client}",
            cfg=_make_cfg(provider, async_client=async_client),
            task_log=_make_task_log(),
        )
        no_tools_client = client_type(
            task_id=f"{provider}-no-tools-{async_client}",
            cfg=_make_cfg(provider, async_client=async_client),
            task_log=_make_task_log(),
        )

    first_ready = asyncio.Event()
    second_ready = asyncio.Event()
    release_processing = asyncio.Event()

    async def run_first() -> tuple[str, str]:
        system_prompt = first_client.generate_agent_system_prompt(
            date.today(),
            _tool_definitions("first-search-server"),
        )
        first_ready.set()
        await release_processing.wait()
        return _process_tool_calls(first_client, system_prompt)

    async def run_second() -> tuple[str, str]:
        await first_ready.wait()
        system_prompt = second_client.generate_agent_system_prompt(
            date.today(),
            _tool_definitions("second-search-server"),
        )
        second_ready.set()
        await release_processing.wait()
        return _process_tool_calls(second_client, system_prompt)

    async def run_without_tools() -> tuple[str, str]:
        await second_ready.wait()
        system_prompt = no_tools_client.generate_agent_system_prompt(date.today(), [])
        release_processing.set()
        return _process_tool_calls(no_tools_client, system_prompt)

    first_result, second_result, no_tools_result = await asyncio.gather(
        run_first(),
        run_second(),
        run_without_tools(),
    )

    assert "<server_name>first-search-server</server_name>" in first_result[0]
    assert first_result[1] == "first-search-server"
    assert "<server_name>second-search-server</server_name>" in second_result[0]
    assert second_result[1] == "second-search-server"
    assert f"<server_name>{WRONG_SERVER_NAME}</server_name>" in no_tools_result[0]
    assert no_tools_result[1] == "unknown"


@pytest.mark.parametrize(
    ("client_type", "provider"),
    [
        (OpenAIClient, "openai"),
        (AnthropicClient, "anthropic"),
    ],
    ids=["openai", "anthropic"],
)
@pytest.mark.parametrize("async_client", [True, False], ids=["async", "sync"])
@pytest.mark.asyncio
async def test_shared_client_binds_mapping_to_each_main_sub_main_call(
    client_type: type,
    provider: str,
    async_client: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一 Client 的主→子→主调用必须按各自 prompt 解析 XML/LongCat。"""
    monkeypatch.delenv("OPENAI_API_KEYS", raising=False)
    with patch.object(client_type, "_create_client", return_value=MagicMock()):
        client = client_type(
            task_id=f"{provider}-shared-{async_client}",
            cfg=_make_cfg(provider, async_client=async_client),
            task_log=_make_task_log(),
        )

    generator = _make_answer_generator(
        client,
        [_xml_tool_call(), _xml_tool_call(), _longcat_tool_call()],
    )
    main_prompt = client.generate_agent_system_prompt(
        date.today(),
        _tool_definitions("main-search-server"),
    )
    first_main = await generator.handle_llm_call(
        system_prompt=main_prompt,
        message_history=[],
        tool_definitions=_tool_definitions("main-search-server"),
        step_id=1,
        purpose="Main Agent | First",
    )

    sub_prompt = client.generate_agent_system_prompt(
        date.today(),
        _tool_definitions("sub-search-server"),
    )
    sub_call = await generator.handle_llm_call(
        system_prompt=sub_prompt,
        message_history=[],
        tool_definitions=_tool_definitions("sub-search-server"),
        step_id=2,
        purpose="Sub Agent",
    )
    second_main = await generator.handle_llm_call(
        system_prompt=main_prompt,
        message_history=[],
        tool_definitions=_tool_definitions("main-search-server"),
        step_id=3,
        purpose="Main Agent | Second",
    )

    assert "<server_name>main-search-server</server_name>" in first_main[0]
    assert first_main[2][0]["server_name"] == "main-search-server"
    assert "<server_name>sub-search-server</server_name>" in sub_call[0]
    assert sub_call[2][0]["server_name"] == "sub-search-server"
    assert second_main[2][0]["server_name"] == "main-search-server"


def test_set_tool_server_mapping_compatibility_api_is_stateless() -> None:
    """旧导入应可用，但只返回映射且不得恢复隐式共享状态。"""
    from src import utils

    prompt = (
        f"## Server name: compatibility-search-server\n### Tool name: {TOOL_NAME}\n"
    )
    with pytest.warns(DeprecationWarning, match="parse_tool_server_mapping"):
        mapping = utils.set_tool_server_mapping(prompt)

    assert mapping == {TOOL_NAME: "compatibility-search-server"}
    assert fix_server_name_in_text(_xml_tool_call()) == _xml_tool_call()
    assert (
        parse_llm_response_for_tool_calls(_longcat_tool_call())[0]["server_name"]
        == "unknown"
    )


def test_parsing_helpers_require_explicit_mapping_and_default_to_no_state() -> None:
    """解析工具默认无共享状态，显式映射只影响当前调用。"""
    mapping = {TOOL_NAME: "explicit-search-server"}

    fixed_text = fix_server_name_in_text(
        _xml_tool_call(),
        tool_server_mapping=mapping,
    )
    mapped_calls = parse_llm_response_for_tool_calls(
        _longcat_tool_call(),
        tool_server_mapping=mapping,
    )

    assert "<server_name>explicit-search-server</server_name>" in fixed_text
    assert mapped_calls[0]["server_name"] == "explicit-search-server"
    assert fix_server_name_in_text(_xml_tool_call()) == _xml_tool_call()
    assert (
        parse_llm_response_for_tool_calls(_longcat_tool_call())[0]["server_name"]
        == "unknown"
    )
