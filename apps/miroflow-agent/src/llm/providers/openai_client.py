# Copyright (c) 2025 MiroMind
# This source code is licensed under the Apache 2.0 License.

"""
OpenAI-compatible LLM client implementation.

This module provides the OpenAIClient class for interacting with OpenAI's API
and OpenAI-compatible endpoints (such as vLLM, Qwen, DeepSeek, etc.).

Features:
- Async and sync API support
- Automatic retry with exponential backoff
- Token usage tracking and context length management
- MCP tool call parsing and response processing
"""

import asyncio
import dataclasses
import logging
import os
import re
import time
from typing import Any, Dict, List, Tuple, Union

import tiktoken
from openai import AsyncOpenAI, DefaultAsyncHttpxClient, DefaultHttpxClient, OpenAI

from ...utils.prompt_utils import generate_mcp_system_prompt, generate_no_mcp_system_prompt
from ..base_client import BaseClient

logger = logging.getLogger("miroflow_agent")

DEFAULT_OPENAI_MAX_RETRIES = 4
DEFAULT_OPENAI_RETRY_WAIT_SECONDS = 6.0
DEFAULT_OPENAI_HTTP_TIMEOUT_SECONDS = 90.0
DEFAULT_TOOL_RESULT_MAX_CHARS = 4000
DEFAULT_SUMMARY_MAX_TOKENS = 3072
DEFAULT_VERIFICATION_MAX_TOKENS = 2048
SUMMARY_AGENT_TYPES = {"final_summary", "failure_summary"}
VERIFICATION_AGENT_TYPES = {"verification"}
FAST_AGENT_TYPES = {"failure_summary"}


@dataclasses.dataclass
class OpenAIClient(BaseClient):
    def __post_init__(self):
        super().__post_init__()
        self.model_tool_name = self.cfg.llm.get(
            "model_tool_name", os.getenv("MODEL_TOOL_NAME", self.model_name)
        )
        self.model_fast_name = self.cfg.llm.get(
            "model_fast_name", os.getenv("MODEL_FAST_NAME", self.model_name)
        )
        self.model_thinking_name = self.cfg.llm.get(
            "model_thinking_name",
            os.getenv("MODEL_THINKING_NAME", self.model_fast_name),
        )
        self.model_summary_name = self.cfg.llm.get(
            "model_summary_name",
            os.getenv("MODEL_SUMMARY_NAME", self.model_fast_name),
        )
        cfg_max_retries = self.cfg.llm.get("max_retries")
        self.max_retries = (
            int(cfg_max_retries)
            if cfg_max_retries is not None
            else self._read_env_int("LLM_MAX_RETRIES", DEFAULT_OPENAI_MAX_RETRIES)
        )
        cfg_retry_wait_seconds = self.cfg.llm.get("retry_wait_seconds")
        self.retry_wait_seconds = (
            float(cfg_retry_wait_seconds)
            if cfg_retry_wait_seconds is not None
            else self._read_env_float(
                "LLM_RETRY_WAIT_SECONDS", DEFAULT_OPENAI_RETRY_WAIT_SECONDS
            )
        )
        cfg_tool_result_max_chars = self.cfg.llm.get("tool_result_max_chars")
        self.tool_result_max_chars = (
            int(cfg_tool_result_max_chars)
            if cfg_tool_result_max_chars is not None
            else self._read_env_int(
                "LLM_TOOL_RESULT_MAX_CHARS", DEFAULT_TOOL_RESULT_MAX_CHARS
            )
        )
        cfg_summary_max_tokens = self.cfg.llm.get("summary_max_tokens")
        self.summary_max_tokens = (
            int(cfg_summary_max_tokens)
            if cfg_summary_max_tokens is not None
            else self._read_env_int(
                "LLM_SUMMARY_MAX_TOKENS", DEFAULT_SUMMARY_MAX_TOKENS
            )
        )
        cfg_verification_max_tokens = self.cfg.llm.get("verification_max_tokens")
        self.verification_max_tokens = (
            int(cfg_verification_max_tokens)
            if cfg_verification_max_tokens is not None
            else self._read_env_int(
                "LLM_VERIFICATION_MAX_TOKENS", DEFAULT_VERIFICATION_MAX_TOKENS
            )
        )

    @staticmethod
    def _read_env_int(name: str, default: int) -> int:
        raw_value = os.getenv(name)
        if raw_value is None:
            return default
        try:
            return int(raw_value)
        except ValueError:
            return default

    @staticmethod
    def _read_env_float(name: str, default: float) -> float:
        raw_value = os.getenv(name)
        if raw_value is None:
            return default
        try:
            return float(raw_value)
        except ValueError:
            return default

    def _resolve_model_name(self, tools_definitions, agent_type: str) -> str:
        if agent_type in VERIFICATION_AGENT_TYPES:
            return self.model_thinking_name
        if agent_type in FAST_AGENT_TYPES:
            return self.model_fast_name
        if agent_type in SUMMARY_AGENT_TYPES:
            return self.model_summary_name
        if tools_definitions:
            return self.model_tool_name
        return self.model_thinking_name

    def _sanitize_tool_result_text(self, text: str) -> str:
        normalized_text = re.sub(r"<[^>]+>", " ", text)
        normalized_text = re.sub(r"\s+", " ", normalized_text).strip()
        if len(normalized_text) <= self.tool_result_max_chars:
            return normalized_text
        return normalized_text[: self.tool_result_max_chars] + "...(truncated)"

    def _create_client(self) -> Union[AsyncOpenAI, OpenAI]:
        """Create LLM client"""
        timeout_seconds = self._read_env_float(
            "LLM_HTTP_TIMEOUT_SECONDS", DEFAULT_OPENAI_HTTP_TIMEOUT_SECONDS
        )
        http_client_args = {
            "headers": {"x-upstream-session-id": self.task_id},
            "timeout": timeout_seconds,
        }
        if self.async_client:
            return AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                http_client=DefaultAsyncHttpxClient(**http_client_args),
            )
        else:
            return OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                http_client=DefaultHttpxClient(**http_client_args),
            )

    def _update_token_usage(self, usage_data: Any) -> None:
        """Update cumulative token usage"""
        if usage_data:
            input_tokens = getattr(usage_data, "prompt_tokens", 0)
            output_tokens = getattr(usage_data, "completion_tokens", 0)
            prompt_tokens_details = getattr(usage_data, "prompt_tokens_details", None)
            if prompt_tokens_details:
                cached_tokens = (
                    getattr(prompt_tokens_details, "cached_tokens", None) or 0
                )
            else:
                cached_tokens = 0

            # Record token usage for the most recent call
            self.last_call_tokens = {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
            }

            # OpenAI does not provide cache_creation_input_tokens
            self.token_usage["total_input_tokens"] += input_tokens
            self.token_usage["total_output_tokens"] += output_tokens
            self.token_usage["total_cache_read_input_tokens"] += cached_tokens

            self.task_log.log_step(
                "info",
                "LLM | Token Usage",
                f"Input: {self.token_usage['total_input_tokens']}, "
                f"Output: {self.token_usage['total_output_tokens']}",
            )

    async def _create_message(
        self,
        system_prompt: str,
        messages_history: List[Dict[str, Any]],
        tools_definitions,
        keep_tool_result: int = -1,
        agent_type: str = "main",
    ):
        """
        Send message to OpenAI API.
        :param system_prompt: System prompt string.
        :param messages_history: Message history list.
        :return: OpenAI API response object or None (if error occurs).
        """

        # Create a copy for sending to LLM (to avoid modifying the original)
        messages_for_llm = [m.copy() for m in messages_history]

        # put the system prompt in the first message since OpenAI API does not support system prompt in
        if system_prompt:
            # Check if there's already a system or developer message
            if messages_for_llm and messages_for_llm[0]["role"] in [
                "system",
                "developer",
            ]:
                messages_for_llm[0] = {
                    "role": "system",
                    "content": system_prompt,
                }

            else:
                messages_for_llm.insert(
                    0,
                    {
                        "role": "system",
                        "content": system_prompt,
                    },
                )

        # Filter tool results to save tokens (only affects messages sent to LLM)
        messages_for_llm = self._remove_tool_result_from_messages(
            messages_for_llm, keep_tool_result
        )

        # Retry loop with dynamic max_tokens adjustment
        max_retries = (
            1
            if agent_type in SUMMARY_AGENT_TYPES
            or agent_type in VERIFICATION_AGENT_TYPES
            else self.max_retries
        )
        base_wait_time = self.retry_wait_seconds
        current_max_tokens = self.max_tokens
        if agent_type in SUMMARY_AGENT_TYPES:
            current_max_tokens = min(current_max_tokens, self.summary_max_tokens)
        if agent_type in VERIFICATION_AGENT_TYPES:
            current_max_tokens = min(current_max_tokens, self.verification_max_tokens)
        request_model_name = self._resolve_model_name(tools_definitions, agent_type)
        openai_tools = []
        if tools_definitions:
            try:
                openai_tools = await self.convert_tool_definition_to_tool_call(
                    tools_definitions
                )
            except Exception as e:
                self.task_log.log_step(
                    "warning",
                    "LLM | Tool Definition Conversion",
                    f"Convert tool definitions failed: {str(e)}",
                )

        for attempt in range(max_retries):
            params = {
                "model": request_model_name,
                "temperature": self.temperature,
                "messages": messages_for_llm,
                "stream": False,
                "top_p": self.top_p,
                "extra_body": {},
            }
            if openai_tools:
                params["tools"] = openai_tools
                params["tool_choice"] = "auto"
            # Check if the model is GPT-5, and adjust the parameter accordingly
            if "gpt-5" in request_model_name:
                # Use 'max_completion_tokens' for GPT-5
                params["max_completion_tokens"] = current_max_tokens
            else:
                # Use 'max_tokens' for GPT-4 and other models
                params["max_tokens"] = current_max_tokens

            # Add repetition_penalty if it's not the default value
            if self.repetition_penalty != 1.0:
                params["extra_body"]["repetition_penalty"] = self.repetition_penalty

            if "deepseek-v3-1" in self.model_name:
                params["extra_body"]["thinking"] = {"type": "enabled"}

            # auto-detect if we need to continue from the last assistant message
            if messages_for_llm and messages_for_llm[-1].get("role") == "assistant":
                params["extra_body"]["continue_final_message"] = True
                params["extra_body"]["add_generation_prompt"] = False

            try:
                request_start_time = time.perf_counter()
                if self.async_client:
                    response = await self.client.chat.completions.create(**params)
                else:
                    response = self.client.chat.completions.create(**params)
                request_duration_ms = int(
                    (time.perf_counter() - request_start_time) * 1000
                )
                # Update token count
                self._update_token_usage(getattr(response, "usage", None))
                response_model_name = getattr(response, "model", "N/A")
                self.task_log.log_step(
                    "info",
                    "LLM | Model Route",
                    f"agent_type={agent_type}, requested={request_model_name}, responded={response_model_name}",
                )
                self.task_log.log_step(
                    "info",
                    "LLM | Response Status",
                    f"{getattr(response.choices[0], 'finish_reason', 'N/A')}",
                )
                self.task_log.record_stage_timing(
                    f"llm.request.{agent_type}",
                    request_duration_ms,
                    message=f"LLM request completed in {request_duration_ms}ms",
                    metadata={
                        "agent_type": agent_type,
                        "requested_model": request_model_name,
                        "responded_model": response_model_name,
                        "tool_count": len(openai_tools),
                        "message_count": len(messages_for_llm),
                    },
                )

                # Check if response was truncated due to length limit
                finish_reason = getattr(response.choices[0], "finish_reason", None)
                if finish_reason == "tool_calls":
                    return response, messages_history

                if finish_reason == "length":
                    if agent_type in SUMMARY_AGENT_TYPES:
                        self.task_log.log_step(
                            "warning",
                            "LLM | Length Limit Reached - Summary Fast Return",
                            "Summary response reached length limit, returning immediately without retry.",
                        )
                        return response, messages_history
                    # If this is not the last retry, increase max_tokens and retry
                    if attempt < max_retries - 1:
                        # Increase max_tokens by 10%
                        current_max_tokens = int(current_max_tokens * 1.1)
                        self.task_log.log_step(
                            "warning",
                            "LLM | Length Limit Reached",
                            f"Response was truncated due to length limit (attempt {attempt + 1}/{max_retries}). Increasing max_tokens to {current_max_tokens} and retrying...",
                        )
                        await asyncio.sleep(base_wait_time)
                        continue
                    else:
                        # Last retry, return the truncated response instead of raising exception
                        self.task_log.log_step(
                            "warning",
                            "LLM | Length Limit Reached - Returning Truncated Response",
                            f"Response was truncated after {max_retries} attempts. Returning truncated response to allow ReAct loop to continue.",
                        )
                        # Return the truncated response and let the orchestrator handle it
                        return response, messages_history

                # Check if the last 50 characters of the response appear more than 5 times in the response content.
                # If so, treat it as a severe repeat and trigger a retry.
                if hasattr(response.choices[0], "message") and hasattr(
                    response.choices[0].message, "content"
                ):
                    resp_content = response.choices[0].message.content or ""
                else:
                    resp_content = getattr(response.choices[0], "text", "")

                if resp_content and len(resp_content) >= 50:
                    tail_50 = resp_content[-50:]
                    repeat_count = resp_content.count(tail_50)
                    if repeat_count > 5:
                        # If this is not the last retry, retry
                        if attempt < max_retries - 1:
                            self.task_log.log_step(
                                "warning",
                                "LLM | Repeat Detected",
                                f"Severe repeat: the last 50 chars appeared over 5 times (attempt {attempt + 1}/{max_retries}), retrying...",
                            )
                            await asyncio.sleep(base_wait_time)
                            continue
                        else:
                            # Last retry, return anyway
                            self.task_log.log_step(
                                "warning",
                                "LLM | Repeat Detected - Returning Anyway",
                                f"Severe repeat detected after {max_retries} attempts. Returning response anyway.",
                            )

                # Success - return the original messages_history (not the filtered copy)
                # This ensures that the complete conversation history is preserved in logs
                return response, messages_history

            except asyncio.TimeoutError as e:
                if attempt < max_retries - 1:
                    self.task_log.log_step(
                        "warning",
                        "LLM | Timeout Error",
                        f"Timeout error (attempt {attempt + 1}/{max_retries}): {str(e)}, retrying...",
                    )
                    await asyncio.sleep(base_wait_time)
                    continue
                else:
                    self.task_log.log_step(
                        "error",
                        "LLM | Timeout Error",
                        f"Timeout error after {max_retries} attempts: {str(e)}",
                    )
                    raise e
            except asyncio.CancelledError as e:
                self.task_log.log_step(
                    "error",
                    "LLM | Request Cancelled",
                    f"Request was cancelled: {str(e)}",
                )
                raise e
            except Exception as e:
                if "Error code: 400" in str(e) and "longer than the model" in str(e):
                    self.task_log.log_step(
                        "error",
                        "LLM | Context Length Error",
                        f"Error: {str(e)}",
                    )
                    raise e
                else:
                    if attempt < max_retries - 1:
                        self.task_log.log_step(
                            "warning",
                            "LLM | API Error",
                            f"Error (attempt {attempt + 1}/{max_retries}): {str(e)}, retrying...",
                        )
                        await asyncio.sleep(base_wait_time)
                        continue
                    else:
                        self.task_log.log_step(
                            "error",
                            "LLM | API Error",
                            f"Error after {max_retries} attempts: {str(e)}",
                        )
                        raise e

        # Should never reach here, but just in case
        raise Exception("Unexpected error: retry loop completed without returning")

    def process_llm_response(
        self, llm_response: Any, message_history: List[Dict], agent_type: str = "main"
    ) -> tuple[str, bool, List[Dict]]:
        """Process LLM response"""
        if not llm_response or not llm_response.choices:
            error_msg = "LLM did not return a valid response."
            self.task_log.log_step(
                "error", "LLM | Response Error", f"Error: {error_msg}"
            )
            return "", True, message_history  # Exit loop, return message_history

        # Extract LLM response text
        from ...utils.parsing_utils import fix_server_name_in_text

        if llm_response.choices[0].finish_reason == "stop":
            assistant_response_text = llm_response.choices[0].message.content or ""
            assistant_response_text = fix_server_name_in_text(assistant_response_text)

            message_history.append(
                {"role": "assistant", "content": assistant_response_text}
            )
        elif llm_response.choices[0].finish_reason == "tool_calls":
            assistant_response_text = llm_response.choices[0].message.content or ""
            assistant_response_text = fix_server_name_in_text(assistant_response_text)
            message_history.append(
                {"role": "assistant", "content": assistant_response_text}
            )

        elif llm_response.choices[0].finish_reason == "length":
            assistant_response_text = llm_response.choices[0].message.content or ""
            assistant_response_text = fix_server_name_in_text(assistant_response_text)
            if assistant_response_text == "":
                assistant_response_text = "LLM response is empty."
            elif "Context length exceeded" in assistant_response_text:
                # This is the case where context length is exceeded, needs special handling
                self.task_log.log_step(
                    "warning",
                    "LLM | Context Length",
                    "Detected context length exceeded, returning error status",
                )
                message_history.append(
                    {"role": "assistant", "content": assistant_response_text}
                )
                return (
                    assistant_response_text,
                    True,
                    message_history,
                )  # Return True to indicate need to exit loop

            # Add assistant response to history
            message_history.append(
                {"role": "assistant", "content": assistant_response_text}
            )

        else:
            raise ValueError(
                f"Unsupported finish reason: {llm_response.choices[0].finish_reason}"
            )

        return assistant_response_text, False, message_history

    def extract_tool_calls_info(
        self, llm_response: Any, assistant_response_text: str
    ) -> List[Dict]:
        """Extract tool call information from LLM response"""
        from ...utils.parsing_utils import parse_llm_response_for_tool_calls

        tool_calls = getattr(llm_response.choices[0].message, "tool_calls", None)
        if tool_calls:
            return parse_llm_response_for_tool_calls(tool_calls)
        return parse_llm_response_for_tool_calls(assistant_response_text)

    def update_message_history(
        self, message_history: List[Dict], all_tool_results_content_with_id: List[Tuple]
    ) -> List[Dict]:
        """Update message history with tool calls data (llm client specific)"""

        merged_text = "\n".join(
            [
                self._sanitize_tool_result_text(item[1]["text"])
                for item in all_tool_results_content_with_id
                if item[1]["type"] == "text"
            ]
        )

        message_history.append(
            {
                "role": "user",
                "content": merged_text,
            }
        )

        return message_history

    def generate_agent_system_prompt(self, date: Any, mcp_servers: List[Dict]) -> str:
        from ...utils.parsing_utils import set_tool_server_mapping

        if mcp_servers:
            prompt = generate_mcp_system_prompt(date, mcp_servers)
            prompt += (
                "\n\nImportant: When tools are available, use OpenAI native function calling only. "
                "Do not output any <use_mcp_tool> XML tags in plain text."
            )
        else:
            prompt = generate_no_mcp_system_prompt(date)
            prompt += (
                "\n\nNo tools are available in this run. "
                "Do not attempt any tool invocation and answer directly."
            )
        set_tool_server_mapping(prompt)
        return prompt

    def _estimate_tokens(self, text: str) -> int:
        """Use tiktoken to estimate the number of tokens in text"""
        if not hasattr(self, "encoding"):
            # Initialize tiktoken encoder
            try:
                self.encoding = tiktoken.get_encoding("o200k_base")
            except Exception:
                # If o200k_base is not available, use cl100k_base as fallback
                self.encoding = tiktoken.get_encoding("cl100k_base")

        try:
            return len(self.encoding.encode(text))
        except Exception as e:
            # If encoding fails, use simple estimation: approximately 1 token per 4 characters
            self.task_log.log_step(
                "error",
                "LLM | Token Estimation Error",
                f"Error: {str(e)}",
            )
            return len(text) // 4

    def ensure_summary_context(
        self, message_history: list, summary_prompt: str
    ) -> tuple[bool, list]:
        """
        Check if current message_history + summary_prompt will exceed context
        If it will exceed, remove the last assistant-user pair and return False
        Return True to continue, False if messages have been rolled back
        """
        # Get token usage from the last LLM call
        last_prompt_tokens = self.last_call_tokens.get("prompt_tokens", 0)
        last_completion_tokens = self.last_call_tokens.get("completion_tokens", 0)
        buffer_factor = 1.5

        # Calculate token count for summary prompt
        summary_tokens = int(self._estimate_tokens(summary_prompt) * buffer_factor)

        # Calculate token count for the last user message in message_history
        last_user_tokens = 0
        if message_history[-1]["role"] == "user":
            content = message_history[-1]["content"]
            last_user_tokens = int(self._estimate_tokens(str(content)) * buffer_factor)

        # Calculate total token count: last prompt + completion + last user message + summary + reserved response space
        estimated_total = (
            last_prompt_tokens
            + last_completion_tokens
            + last_user_tokens
            + summary_tokens
            + self.max_tokens
            + 1000  # Add 1000 tokens as buffer
        )

        if estimated_total >= self.max_context_length:
            self.task_log.log_step(
                "info",
                "LLM | Context Limit Reached",
                "Context limit reached, proceeding to step back and summarize the conversation",
            )

            # Remove the last user message (tool call results)
            if message_history[-1]["role"] == "user":
                message_history.pop()

            # Remove the second-to-last assistant message (tool call request)
            if message_history[-1]["role"] == "assistant":
                message_history.pop()

            self.task_log.log_step(
                "info",
                "LLM | Context Limit Reached",
                f"Removed the last assistant-user pair, current message_history length: {len(message_history)}",
            )

            return False, message_history

        self.task_log.log_step(
            "info",
            "LLM | Context Limit Not Reached",
            f"{estimated_total}/{self.max_context_length}",
        )
        return True, message_history

    def format_token_usage_summary(self) -> tuple[List[str], str]:
        """Format token usage statistics, return summary_lines for format_final_summary and log string"""
        token_usage = self.get_token_usage()

        total_input = token_usage.get("total_input_tokens", 0)
        total_output = token_usage.get("total_output_tokens", 0)
        cache_input = token_usage.get("total_cache_input_tokens", 0)

        summary_lines = []
        summary_lines.append("\n" + "-" * 20 + " Token Usage " + "-" * 20)
        summary_lines.append(f"Total Input Tokens: {total_input}")
        summary_lines.append(f"Total Cache Input Tokens: {cache_input}")
        summary_lines.append(f"Total Output Tokens: {total_output}")
        summary_lines.append("-" * (40 + len(" Token Usage ")))
        summary_lines.append("Pricing is disabled - no cost information available")
        summary_lines.append("-" * (40 + len(" Token Usage ")))

        # Generate log string
        log_string = (
            f"[{self.model_name}] Total Input: {total_input}, "
            f"Cache Input: {cache_input}, "
            f"Output: {total_output}"
        )

        return summary_lines, log_string

    def get_token_usage(self):
        return self.token_usage.copy()
