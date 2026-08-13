# Copyright (c) 2025 MiroMind
# This source code is licensed under the Apache 2.0 License.

"""
Base client module for LLM providers.

This module defines the abstract base class and common utilities for LLM clients,
supporting both OpenAI and Anthropic API formats.
"""

import asyncio
import dataclasses
import os
from abc import ABC
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Tuple,
    TypedDict,
)

from omegaconf import DictConfig

from ..logging.task_logger import TaskLog
from .util import with_timeout

# Default timeout for LLM API calls (10 minutes)
DEFAULT_LLM_TIMEOUT_SECONDS = 600
DEFAULT_SUMMARY_MAX_TOKENS = 3072
DEFAULT_VERIFICATION_MAX_TOKENS = 2048
SUMMARY_AGENT_TYPES = frozenset({"final_summary", "failure_summary"})
VERIFICATION_AGENT_TYPES = frozenset({"verification"})
FAST_AGENT_TYPES = frozenset({"failure_summary"})
INTERNAL_MESSAGE_TYPE_KEY = "_miroflow_message_type"
TOOL_RESULT_MESSAGE_TYPE = "tool_result"
OMITTED_TOOL_RESULT_TEXT = "Tool result is omitted to save tokens."


class TokenUsage(TypedDict, total=True):
    """
    Unified token usage tracking across different LLM providers.

    We unify OpenAI and Anthropic formats. There are four usage types:
    - input/output tokens: Standard input and output token counts
    - cache write/read tokens: Tokens involved in caching operations

    Provider-specific notes:
    - OpenAI: Cache write is free, cache read is cheaper
    - Anthropic: Cache write has a small cost, cache read is cheaper
    """

    total_input_tokens: int
    total_output_tokens: int
    total_cache_read_input_tokens: int
    total_cache_write_input_tokens: int


@dataclasses.dataclass
class BaseClient(ABC):
    """
    Abstract base class for LLM provider clients.

    This class provides the common interface and utilities for interacting with
    different LLM providers (OpenAI, Anthropic, etc.). Concrete implementations
    should override _create_client() and provider-specific methods.

    Attributes:
        task_id: Unique identifier for the current task (used for tracking)
        cfg: Hydra configuration containing LLM settings
        task_log: Optional logger for recording task execution details
    """

    # Required arguments (no default value)
    task_id: str
    cfg: DictConfig

    # Optional arguments (with default value)
    task_log: Optional["TaskLog"] = None

    # Initialized in __post_init__
    client: Any = dataclasses.field(init=False)
    token_usage: TokenUsage = dataclasses.field(init=False)
    last_call_tokens: Dict[str, int] = dataclasses.field(init=False)

    def _zero_last_call_tokens(self) -> Dict[str, int]:
        """Return a zeroed last_call_tokens dict using this provider's key names.

        OpenAI-style providers use ``prompt_tokens``/``completion_tokens``;
        Anthropic uses ``input_tokens``/``output_tokens``. Subclasses override
        this so callers never have to know provider-specific key names.
        """
        return {"prompt_tokens": 0, "completion_tokens": 0}

    def reset_last_call_tokens(self) -> None:
        """Reset the most-recent-call token counters using provider key names.

        Callers (e.g. the orchestrator between turns) must use this instead of
        assigning a literal dict, otherwise the keys can mismatch the provider's
        and break context-length accounting (Anthropic reads ``input_tokens``).
        """
        self.last_call_tokens = self._zero_last_call_tokens()

    def __post_init__(self) -> None:
        # Initialize last_call_tokens before other operations
        self.last_call_tokens: Dict[str, int] = self._zero_last_call_tokens()

        # Explicitly assign from cfg object
        self.provider: str = self.cfg.llm.provider
        self.model_name: str = self._require_non_empty_string(
            self.cfg.llm.model_name,
            "llm.model_name",
        )
        self.temperature: float = self.cfg.llm.temperature
        self.top_p: float = self.cfg.llm.top_p
        self.min_p: float = self.cfg.llm.min_p
        self.top_k: int = self.cfg.llm.top_k
        self.max_context_length: int = self.cfg.llm.max_context_length
        self.max_tokens: int = self._require_positive_int(
            self.cfg.llm.max_tokens,
            "llm.max_tokens",
        )
        self.async_client: bool = self.cfg.llm.async_client
        self.keep_tool_result: int = self.cfg.agent.keep_tool_result
        self.api_key: Optional[str] = self.cfg.llm.get("api_key")
        self.base_url: Optional[str] = self.cfg.llm.get("base_url")
        self.use_tool_calls: Optional[bool] = self.cfg.llm.get("use_tool_calls")
        self.repetition_penalty: float = self.cfg.llm.get("repetition_penalty", 1.0)
        self._initialize_stage_routing()

        self.token_usage = self._reset_token_usage()
        self.client = self._create_client()

        self.task_log.log_step(
            "info",
            "LLM | Initialization",
            f"LLMClient {self.provider} {self.model_name} initialization completed.",
        )

    @staticmethod
    def _require_non_empty_string(value: Any, field_name: str) -> str:
        """校验显式字符串配置，并去除首尾空白。"""
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} 必须是非空字符串，实际值为 {value!r}")
        return value.strip()

    @staticmethod
    def _require_positive_int(value: Any, field_name: str) -> int:
        """校验显式整数配置，拒绝 bool、浮点数和非正数。"""
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{field_name} 必须是正整数，实际值为 {value!r}")
        return value

    @staticmethod
    def _read_non_empty_env(name: str, fallback: str) -> str:
        """读取非空字符串环境变量，空白值安全回退。"""
        raw_value = os.getenv(name)
        if raw_value is None or not raw_value.strip():
            return fallback
        return raw_value.strip()

    @staticmethod
    def _read_positive_env_int(name: str, default: int) -> int:
        """读取正整数环境变量，非法值安全回退到命名默认值。"""
        raw_value = os.getenv(name)
        if raw_value is None:
            return default
        try:
            parsed_value = int(raw_value)
        except ValueError:
            return default
        return parsed_value if parsed_value > 0 else default

    def _read_stage_model_name(
        self,
        config_name: str,
        env_name: str,
        fallback: str,
    ) -> str:
        """读取单个阶段模型配置；显式 cfg 非法时立即失败。"""
        if config_name in self.cfg.llm:
            return self._require_non_empty_string(
                self.cfg.llm[config_name],
                f"llm.{config_name}",
            )
        return self._read_non_empty_env(env_name, fallback)

    def _read_stage_token_limit(
        self,
        config_name: str,
        env_name: str,
        default: int,
    ) -> int:
        """读取阶段 token 上限；显式 cfg 非法时立即失败。"""
        if config_name in self.cfg.llm:
            return self._require_positive_int(
                self.cfg.llm[config_name],
                f"llm.{config_name}",
            )
        return self._read_positive_env_int(env_name, default)

    def _initialize_stage_routing(self) -> None:
        """统一初始化所有 provider 共用的模型与阶段预算。"""
        self.model_tool_name = self._read_stage_model_name(
            "model_tool_name",
            "MODEL_TOOL_NAME",
            self.model_name,
        )
        self.model_fast_name = self._read_stage_model_name(
            "model_fast_name",
            "MODEL_FAST_NAME",
            self.model_name,
        )
        self.model_thinking_name = self._read_stage_model_name(
            "model_thinking_name",
            "MODEL_THINKING_NAME",
            self.model_fast_name,
        )
        self.model_summary_name = self._read_stage_model_name(
            "model_summary_name",
            "MODEL_SUMMARY_NAME",
            self.model_fast_name,
        )
        self.summary_max_tokens = self._read_stage_token_limit(
            "summary_max_tokens",
            "LLM_SUMMARY_MAX_TOKENS",
            DEFAULT_SUMMARY_MAX_TOKENS,
        )
        self.verification_max_tokens = self._read_stage_token_limit(
            "verification_max_tokens",
            "LLM_VERIFICATION_MAX_TOKENS",
            DEFAULT_VERIFICATION_MAX_TOKENS,
        )

    def _resolve_model_name(
        self,
        tools_definitions: Any,
        agent_type: str,
    ) -> str:
        """按执行阶段和工具可用性选择模型。"""
        if agent_type in VERIFICATION_AGENT_TYPES:
            return self.model_thinking_name
        if agent_type in FAST_AGENT_TYPES:
            return self.model_fast_name
        if agent_type in SUMMARY_AGENT_TYPES:
            return self.model_summary_name
        if tools_definitions:
            return self.model_tool_name
        return self.model_thinking_name

    def _resolve_stage_max_tokens(self, agent_type: str) -> int:
        """在全局模式硬上限内应用总结或校验阶段上限。"""
        current_max_tokens = self.max_tokens
        if agent_type in SUMMARY_AGENT_TYPES:
            current_max_tokens = min(current_max_tokens, self.summary_max_tokens)
        if agent_type in VERIFICATION_AGENT_TYPES:
            current_max_tokens = min(
                current_max_tokens,
                self.verification_max_tokens,
            )
        return current_max_tokens

    def _reset_token_usage(self) -> TokenUsage:
        """
        Reset token usage counter to zero.

        Returns:
            A new TokenUsage dict with all counters set to zero.
        """
        return TokenUsage(
            total_input_tokens=0,
            total_output_tokens=0,
            total_cache_write_input_tokens=0,
            total_cache_read_input_tokens=0,
        )

    def _remove_tool_result_from_messages(
        self, messages, keep_tool_result
    ) -> List[Dict]:
        """按内部类型标记省略历史中的真实工具结果。

        Args:
            messages: List of message dictionaries
            keep_tool_result: Number of tool results to keep. -1 means keep all.

        Returns:
            List of messages with tool results filtered according to keep_tool_result
        """
        messages_copy = [m.copy() for m in messages]

        # 只有显式标记的消息才是真实工具结果，普通 user 指令不参与裁剪。
        tool_result_indices = [
            i
            for i, msg in enumerate(messages_copy)
            if msg.get(INTERNAL_MESSAGE_TYPE_KEY) == TOOL_RESULT_MESSAGE_TYPE
        ]

        if keep_tool_result == -1:
            num_tool_results_to_keep = len(tool_result_indices)
        else:
            num_tool_results_to_keep = min(
                max(keep_tool_result, 0),
                len(tool_result_indices),
            )

        tool_result_indices_to_keep = (
            tool_result_indices[-num_tool_results_to_keep:]
            if num_tool_results_to_keep > 0
            else []
        )

        self.task_log.log_step(
            "info",
            "LLM | Message Retention",
            f"Message retention summary: Total tool results: {len(tool_result_indices)}, "
            f"Keeping last {num_tool_results_to_keep} tool results at indices: {tool_result_indices_to_keep}, "
            f"Total ordinary messages: {len(messages_copy) - len(tool_result_indices)}",
        )

        for i, msg in enumerate(messages_copy):
            if i in tool_result_indices and i not in tool_result_indices_to_keep:
                if isinstance(msg.get("content"), list):
                    msg["content"] = [
                        {
                            "type": "text",
                            "text": OMITTED_TOOL_RESULT_TEXT,
                        }
                    ]
                else:
                    msg["content"] = OMITTED_TOOL_RESULT_TEXT

            # 内部审计标记只能保留在原始历史中，不能发送给第三方 SDK。
            msg.pop(INTERNAL_MESSAGE_TYPE_KEY, None)

        return messages_copy

    def _trim_tool_result_pair_for_summary_context(
        self,
        message_history: List[Dict],
    ) -> bool:
        """超限时裁剪最近一组有明确内部标记的工具调用消息。

        仅当 tool_result 消息紧邻在 assistant 调用消息之后时删除这两条，
        从而保护原始任务、普通 user 指令以及末尾 assistant 直接回答。

        Returns:
            是否成功裁剪了一组工具调用消息。
        """
        for result_index in range(len(message_history) - 1, -1, -1):
            result_message = message_history[result_index]
            if (
                result_message.get(INTERNAL_MESSAGE_TYPE_KEY)
                != TOOL_RESULT_MESSAGE_TYPE
            ):
                continue

            assistant_index = result_index - 1
            if (
                assistant_index < 0
                or message_history[assistant_index].get("role") != "assistant"
            ):
                continue

            del message_history[result_index]
            del message_history[assistant_index]
            return True

        return False

    @with_timeout(DEFAULT_LLM_TIMEOUT_SECONDS)
    async def create_message(
        self,
        system_prompt: str,
        message_history: List[Dict],
        tool_definitions: List[Dict],
        keep_tool_result: int = -1,
        step_id: int = 1,
        task_log: Optional["TaskLog"] = None,
        agent_type: str = "main",
    ) -> Tuple[Any, List[Dict]]:
        """
        Call LLM to generate a response with optional tool call support.

        This is the main entry point for LLM interactions. It handles:
        - Message history management
        - Tool result filtering based on keep_tool_result
        - Error handling and logging

        Args:
            system_prompt: System prompt to guide the LLM's behavior
            message_history: List of previous messages in the conversation
            tool_definitions: List of available tool definitions
            keep_tool_result: Number of recent tool results to keep (-1 = keep all)
            step_id: Current step identifier for logging
            task_log: Optional logger for task execution
            agent_type: Type of agent making the call ("main" or sub-agent name)

        Returns:
            Tuple of (response, updated_message_history)
        """
        # Unified LLM call processing
        try:
            response, message_history = await self._create_message(
                system_prompt,
                message_history,
                tool_definitions,
                keep_tool_result=keep_tool_result,
                agent_type=agent_type,
            )

        except Exception as e:
            self.task_log.log_step(
                "error",
                f"FATAL ERROR | {agent_type} | LLM Call ERROR",
                f"{agent_type} failed: {str(e)}",
            )
            response = None

        return response, message_history

    @staticmethod
    async def convert_tool_definition_to_tool_call(tools_definitions):
        """
        Convert MCP tool definitions to OpenAI function call format.

        Transforms the internal tool definition format used by MCP servers into
        the format expected by OpenAI's function calling API.

        Args:
            tools_definitions: List of server definitions, each containing a 'name'
                and 'tools' list with tool specifications.

        Returns:
            List of tool definitions in OpenAI function call format, where each
            tool name is prefixed with its server name (e.g., "server-name-tool-name").
        """
        tool_list = []
        for server in tools_definitions:
            if "tools" in server and len(server["tools"]) > 0:
                for tool in server["tools"]:
                    tool_def = dict(
                        type="function",
                        function=dict(
                            name=f"{server['name']}-{tool['name']}",
                            description=tool["description"],
                            parameters=tool["schema"],
                        ),
                    )
                    tool_list.append(tool_def)
        return tool_list

    async def _rotate_client(self) -> None:
        """Rebuild the SDK client (e.g. after KeyPool advanced to a new key).

        Mutating ``client.api_key`` on an already-constructed AsyncOpenAI is not
        guaranteed to change the Authorization header across SDK versions, so a
        429 key rotation could "succeed" yet keep using the old key. Rebuilding
        via _create_client() (which reads KeyPool.current_key()) is robust, and
        we close the old client to avoid connection leaks.
        """
        old = self.client
        self.client = self._create_client()
        await self._aclose_obj(old)

    async def aclose(self) -> None:
        """Properly close the client and its underlying HTTP connections.

        Prefer this over close() in async contexts: AsyncOpenAI/AsyncAnthropic
        own an httpx.AsyncClient whose close() is a coroutine. Calling close()
        synchronously (the old path) silently failed and leaked connections/fds
        across long-running workers. This awaits the real async close.
        """
        await self._aclose_obj(self.client)

    @staticmethod
    async def _aclose_obj(obj: Any) -> None:
        """Best-effort async close of an SDK client or underlying httpx client."""
        if obj is None:
            return
        close = getattr(obj, "close", None)
        try:
            if close is not None and asyncio.iscoroutinefunction(close):
                await close()
                return
            # Sync close, or fall back to the underlying httpx client.
            inner = getattr(obj, "_client", None)
            inner_close = getattr(inner, "aclose", None)
            if inner_close is not None and asyncio.iscoroutinefunction(inner_close):
                await inner_close()
            elif close is not None:
                close()
            elif inner is not None and hasattr(inner, "close"):
                inner.close()
        except Exception:
            pass  # Ignore errors during cleanup

    def close(self):
        """Synchronous close (legacy). Prefer `await aclose()` in async code.

        Kept for non-async callers; for async clients it can only close the
        underlying httpx client if it exposes a sync close, otherwise cleanup
        is left to GC.
        """
        if hasattr(self.client, "close"):
            if asyncio.iscoroutinefunction(self.client.close):
                if hasattr(self.client, "_client"):
                    try:
                        self.client._client.close()
                    except Exception:
                        pass  # Ignore errors during cleanup
            else:
                self.client.close()
        elif hasattr(self.client, "_client") and hasattr(self.client._client, "close"):
            self.client._client.close()

    def _format_response_for_log(self, response) -> Dict:
        """Format response for logging"""
        if not response:
            return {}

        # Basic response information
        formatted = {
            "response_type": type(response).__name__,
        }

        # Anthropic response
        if hasattr(response, "content"):
            formatted["content"] = []
            for block in response.content:
                if hasattr(block, "type"):
                    if block.type == "text":
                        formatted["content"].append(
                            {
                                "type": "text",
                                "text": block.text[:500] + "..."
                                if len(block.text) > 500
                                else block.text,
                            }
                        )
                    elif block.type == "tool_use":
                        formatted["content"].append(
                            {
                                "type": "tool_use",
                                "id": block.id,
                                "name": block.name,
                                "input": str(block.input)[:200] + "..."
                                if len(str(block.input)) > 200
                                else str(block.input),
                            }
                        )

        # OpenAI response
        if hasattr(response, "choices"):
            formatted["choices"] = []
            for choice in response.choices:
                choice_data = {"finish_reason": choice.finish_reason}
                if hasattr(choice, "message"):
                    message = choice.message
                    choice_data["message"] = {
                        "role": message.role,
                        "content": message.content[:500] + "..."
                        if message.content and len(message.content) > 500
                        else message.content,
                    }
                    if hasattr(message, "tool_calls") and message.tool_calls:
                        choice_data["message"]["tool_calls_count"] = len(
                            message.tool_calls
                        )
                formatted["choices"].append(choice_data)

        return formatted
