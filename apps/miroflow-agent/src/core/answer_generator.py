# Copyright (c) 2025 MiroMind
# This source code is licensed under the Apache 2.0 License.

"""
Answer generator module for final answer generation and context management.

This module provides the AnswerGenerator class that handles:
- LLM call processing
- Failure summary generation for context compression
- Final answer generation with retries
- Context management fallback strategies
"""

import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from omegaconf import DictConfig

from ..io.output_formatter import OutputFormatter
from ..llm.base_client import BaseClient
from ..logging.task_logger import TaskLog
from ..utils.parsing_utils import extract_failure_experience_summary
from ..utils.prompt_utils import (
    FAILURE_SUMMARY_ASSISTANT_PREFIX,
    FAILURE_SUMMARY_PROMPT,
    FORMAT_ERROR_MESSAGE,
    generate_cross_verification_prompt,
    generate_agent_summarize_prompt,
)
from ..utils.wrapper_utils import ErrorBox, ResponseBox
from .stream_handler import StreamHandler

logger = logging.getLogger(__name__)

# Safety limits for retry loops
DEFAULT_MAX_FINAL_ANSWER_RETRIES = 3
RESEARCH_COMPACT_TARGET_MAX_CHARS = max(
    300, int(os.getenv("RESEARCH_COMPACT_TARGET_MAX_CHARS", "1200"))
)
RESEARCH_BALANCED_TARGET_MIN_CHARS = max(
    600, int(os.getenv("RESEARCH_BALANCED_TARGET_MIN_CHARS", "1800"))
)
RESEARCH_BALANCED_TARGET_MAX_CHARS = max(
    RESEARCH_BALANCED_TARGET_MIN_CHARS,
    int(os.getenv("RESEARCH_BALANCED_TARGET_MAX_CHARS", "3200")),
)
RESEARCH_DETAILED_TARGET_MIN_CHARS = max(
    1000, int(os.getenv("RESEARCH_DETAILED_TARGET_MIN_CHARS", "12000"))
)
RESEARCH_BALANCED_MIN_SECTIONS = max(
    4, int(os.getenv("RESEARCH_BALANCED_MIN_SECTIONS", "7"))
)
RESEARCH_DETAILED_MIN_SECTIONS = max(
    6, int(os.getenv("RESEARCH_DETAILED_MIN_SECTIONS", "12"))
)
RESEARCH_BALANCED_RETRY_MIN_CHARS = max(
    300, int(os.getenv("RESEARCH_BALANCED_RETRY_MIN_CHARS", "1500"))
)
RESEARCH_DETAILED_RETRY_MIN_CHARS = max(
    RESEARCH_BALANCED_RETRY_MIN_CHARS,
    int(os.getenv("RESEARCH_DETAILED_RETRY_MIN_CHARS", "5000")),
)


def _parse_bool_flag(value: Any, default: bool = False) -> bool:
    """将多种输入类型安全解析为布尔值。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


class AnswerGenerator:
    """
    Generator for final answers with context management support.

    Handles the generation of final answers, failure summaries for retry,
    and various fallback strategies based on context management settings.
    """

    def __init__(
        self,
        llm_client: BaseClient,
        output_formatter: OutputFormatter,
        task_log: TaskLog,
        stream_handler: StreamHandler,
        cfg: DictConfig,
        intermediate_boxed_answers: List[str],
    ):
        """
        Initialize the answer generator.

        Args:
            llm_client: The LLM client for API calls
            output_formatter: Formatter for output processing
            task_log: Logger for task execution
            stream_handler: Handler for streaming events
            cfg: Configuration object
            intermediate_boxed_answers: List to track intermediate answers
        """
        self.llm_client = llm_client
        self.output_formatter = output_formatter
        self.task_log = task_log
        self.stream = stream_handler
        self.cfg = cfg
        self.intermediate_boxed_answers = intermediate_boxed_answers

        # Context management settings
        self.context_compress_limit = cfg.agent.get("context_compress_limit", 0)
        self.max_final_answer_retries = (
            DEFAULT_MAX_FINAL_ANSWER_RETRIES if cfg.agent.keep_tool_result == -1 else 1
        )
        self.retry_with_summary = cfg.agent.get("retry_with_summary", True)
        self.output_detail_level = str(
            cfg.agent.get("output_detail_level", "balanced")
        ).strip().lower()
        if self.output_detail_level not in {"compact", "balanced", "detailed"}:
            self.output_detail_level = "balanced"
        self.demo_mode = os.getenv("DEMO_MODE", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.research_report_mode = _parse_bool_flag(
            cfg.agent.get("research_report_mode", self.demo_mode),
            default=self.demo_mode,
        )
        self.summary_retry_min_chars = self._get_summary_retry_min_chars()
        verification_cfg = cfg.agent.get("verification", {})
        self.verification_enabled = bool(verification_cfg.get("enabled", False))
        self.verification_use_high_model = bool(
            verification_cfg.get("use_high_model_for_verification", True)
        )
        self.verification_min_search_rounds = max(
            1, int(verification_cfg.get("min_search_rounds", 3))
        )
        self.verification_min_high_conf_sources = max(
            1, int(verification_cfg.get("min_high_conf_sources", 2))
        )
        raw_domains = verification_cfg.get("high_conf_domains", [])
        self.verification_high_conf_domains = [
            str(domain).strip().lower() for domain in raw_domains if str(domain).strip()
        ]

    async def _emit_stage_heartbeat(
        self,
        phase: str,
        *,
        turn: int = 0,
        detail: str = "",
        agent_name: str = "Final Summary",
    ) -> None:
        """发送阶段心跳，便于前端展示总结/校验阶段进度。"""
        try:
            await self.stream.update(
                "stage_heartbeat",
                {
                    "phase": phase,
                    "turn": max(0, int(turn)),
                    "detail": detail,
                    "agent_name": agent_name,
                    "timestamp": time.time(),
                },
            )
        except Exception:
            # 心跳为辅助信息，不能影响主流程
            pass

    def _build_main_summary_prompt(self, task_description: str) -> str:
        """
        根据输出档位和运行模式构建最终总结提示词。

        research_report_mode=true 时，覆盖短答案模板约束，强制按研究报告输出。
        """
        summary_prompt = generate_agent_summarize_prompt(
            task_description,
            agent_type="main",
        )
        if self.research_report_mode:
            if self.output_detail_level == "detailed":
                summary_prompt += (
                    "\n\n研究报告模式（详细档，最高优先级覆盖）：\n"
                    "1) 覆盖并忽略上文\u201c尽量短/少词/仅数字\u201d的竞赛型约束，本次必须输出超长完整研究报告。\n"
                    "2) **核心原则：全量保留，去重整合，禁止压缩。**\n"
                    "   - 每一轮检索获取的事实、数据、引述、发现都必须在最终报告中体现。\n"
                    "   - 多轮检索中重复出现的信息进行去重合并，保留描述最完整的版本。\n"
                    "   - 不同轮次从不同角度获取的互补信息全部保留，按主题组织到同一章节。\n"
                    "   - 绝对禁止为了控制篇幅而省略、压缩或概括检索到的具体信息。\n"
                    f"3) 正文字数目标：在信息充分时不少于 {RESEARCH_DETAILED_TARGET_MIN_CHARS} 个中文字符。最终报告必须比任何单轮检索输出都更长、更完整。\n"
                    f"4) 至少包含 {RESEARCH_DETAILED_MIN_SECTIONS} 个一级小节，且每节提供可核验事实、时间锚点与来源线索。\n"
                    "5) 必须包含：关键结论速览、详细时间线、关键数字表（含来源）、已知/不确定/冲突信息对照、深度背景分析、风险与后续观察。\n"
                    "6) 每个要点必须展开论述，包含具体描述、原因背景、数据证据、来源引用，而非一句话带过。\n"
                    "7) 禁止空泛总结；若数据不足，明确缺口、已尝试口径和下一步补证方案。\n"
                    "8) 结尾额外给出一条 \\boxed{一句话核心结论}，用于结构化提取；但正文必须完整保留。"
                )
            elif self.output_detail_level == "balanced":
                summary_prompt += (
                    "\n\n研究报告模式（适中档，最高优先级覆盖）：\n"
                    "1) 覆盖并忽略上文短答案约束，输出中篇结构化报告。\n"
                    f"2) 正文字数目标：约 {RESEARCH_BALANCED_TARGET_MIN_CHARS}-{RESEARCH_BALANCED_TARGET_MAX_CHARS} 个中文字符。\n"
                    f"3) 至少包含 {RESEARCH_BALANCED_MIN_SECTIONS} 个一级小节，核心结论在前，非核心信息放在“补充信息”或“附录”。\n"
                    "4) 每个核心结论至少给出一条证据或数字；若有冲突，明确冲突来源与口径差异。\n"
                    "5) 结尾额外给出一条 \\boxed{一句话核心结论}。"
                )
            else:
                summary_prompt += (
                    "\n\n研究报告模式（精简档，最高优先级覆盖）：\n"
                    f"1) 保持短篇幅，正文控制在约 {RESEARCH_COMPACT_TARGET_MAX_CHARS} 个中文字符以内。\n"
                    "2) 只保留最关键结论与必要证据，非核心背景可省略。\n"
                    "3) 结尾额外给出一条 \\boxed{一句话核心结论}。"
                )
        else:
            if self.output_detail_level == "detailed":
                summary_prompt += (
                    "\n\nDetailed Output Requirements:\n"
                    "1) Provide a complete, structured report with clear section headings.\n"
                    "2) Include a timeline/time anchor section using absolute dates.\n"
                    "3) Include a 'Key Figures' section with as many verifiable numbers as available.\n"
                    "4) Include 'What is known / uncertain / conflicting' sections.\n"
                    "5) Include actionable next steps or monitoring points when applicable.\n"
                    "6) Do not be overly concise; prioritize completeness and traceable detail."
                )
            elif self.output_detail_level == "compact":
                summary_prompt += (
                    "\n\nCompact Output Requirements:\n"
                    "Keep the response concise and only include the most critical findings."
                )

        if self.verification_enabled:
            summary_prompt += (
                "\n\n在最终答案中必须体现以下核验约束：\n"
                "1) 使用绝对时间锚点（例如：截至 YYYY-MM-DD）。\n"
                "2) 明确统计口径与统计对象定义。\n"
                "3) 若关键数字存在冲突，输出区间与冲突说明，不要伪造单值。\n"
                "4) 优先引用高置信来源，并在正文中说明来源等级。"
            )
        return summary_prompt

    def _get_summary_retry_min_chars(self) -> int:
        """
        返回当前档位触发“总结过短重试”的最小字符阈值。
        """
        if not self.research_report_mode:
            return 0
        if self.output_detail_level == "detailed":
            return RESEARCH_DETAILED_RETRY_MIN_CHARS
        if self.output_detail_level == "balanced":
            return RESEARCH_BALANCED_RETRY_MIN_CHARS
        return 0

    def _is_summary_too_short(self, final_answer_text: str) -> bool:
        """
        判断最终总结是否短于当前档位阈值。
        """
        if self.summary_retry_min_chars <= 0:
            return False
        normalized_text = "".join(str(final_answer_text or "").split())
        return len(normalized_text) < self.summary_retry_min_chars

    def _build_expand_summary_prompt(self) -> str:
        """
        构建“总结过短”时的补充提示，强制模型汇总多轮检索信息。
        """
        if self.output_detail_level == "detailed":
            return (
                "你的上一版总结严重过短，丢失了大量检索信息。必须完全重写：\n"
                "1) 逐一检查每轮检索结果，确保每轮中的每个独立事实、数据、引述都在报告中体现。\n"
                "2) 全量保留所有信息，通过去重整合（而非压缩精简）来组织内容。\n"
                "3) 多轮重复信息合并为最完整版本，不同角度的互补信息全部保留。\n"
                "4) 明确区分：核心结论、关键数字（含来源）、来源分歧、不确定项、深度背景。\n"
                "5) 每个要点展开论述，包含具体描述、原因背景、数据证据，而非一句话概括。\n"
                "6) 最终报告必须比任何单轮检索输出都更长、更完整。\n"
                "7) 结尾保留 \\boxed{一句话核心结论}。"
            )
        return (
            "你的上一版总结偏短，请在保持结构清晰的前提下补充关键信息：\n"
            "1) 覆盖多轮检索得到的主要事实与数字，不得遗漏；\n"
            "2) 补齐必要的来源分歧与不确定项说明；\n"
            "3) 结尾保留 \\boxed{一句话核心结论}。"
        )

    async def generate_cross_verification_note(
        self,
        system_prompt: str,
        message_history: List[Dict[str, Any]],
        turn_count: int,
        task_description: str,
    ) -> List[Dict[str, Any]]:
        """
        使用高级模型执行一次“无工具交叉校验反思”，减少口径混用导致的数值错误。
        """
        if not self.verification_enabled:
            return message_history

        verification_prompt = generate_cross_verification_prompt(
            task_description=task_description,
            min_search_rounds=self.verification_min_search_rounds,
            min_high_conf_sources=self.verification_min_high_conf_sources,
            high_conf_domains=self.verification_high_conf_domains,
        )
        verification_history = message_history.copy()
        verification_history.append({"role": "user", "content": verification_prompt})

        agent_type = "verification" if self.verification_use_high_model else "main"
        await self._emit_stage_heartbeat(
            "校验",
            turn=turn_count,
            detail="交叉校验中（无工具）",
            agent_name="Final Summary",
        )
        (
            verification_text,
            _,
            _,
            updated_history,
        ) = await self.handle_llm_call(
            system_prompt=system_prompt,
            message_history=verification_history,
            tool_definitions=[],
            step_id=turn_count + 20,
            purpose="Main Agent | Cross Verification",
            agent_type=agent_type,
        )

        if not verification_text and agent_type == "verification":
            self.task_log.log_step(
                "warning",
                "Main Agent | Cross Verification",
                "高级模型交叉校验未返回有效内容，降级到 summary 模型重试一次。",
            )
            await self._emit_stage_heartbeat(
                "校验",
                turn=turn_count,
                detail="交叉校验降级重试",
                agent_name="Final Summary",
            )
            (
                verification_text,
                _,
                _,
                updated_history,
            ) = await self.handle_llm_call(
                system_prompt=system_prompt,
                message_history=verification_history,
                tool_definitions=[],
                step_id=turn_count + 21,
                purpose="Main Agent | Cross Verification (Fallback)",
                agent_type="final_summary",
            )

        if verification_text:
            self.task_log.log_step(
                "info",
                "Main Agent | Cross Verification",
                f"交叉校验完成，摘要预览: {verification_text[:300]}",
            )
            return updated_history

        self.task_log.log_step(
            "warning",
            "Main Agent | Cross Verification",
            "交叉校验未生成有效内容，回退到原始历史继续总结。",
        )
        return message_history

    async def handle_llm_call(
        self,
        system_prompt: str,
        message_history: List[Dict[str, Any]],
        tool_definitions: List[Dict],
        step_id: int,
        purpose: str = "",
        agent_type: str = "main",
    ) -> Tuple[Optional[str], bool, Optional[Any], List[Dict[str, Any]]]:
        """
        Unified LLM call and logging processing.

        Args:
            system_prompt: System prompt for the LLM
            message_history: Conversation history
            tool_definitions: Available tool definitions
            step_id: Current step ID for logging
            purpose: Description of the call purpose
            agent_type: Type of agent making the call

        Returns:
            Tuple of (response_text, should_break, tool_calls_info, message_history)
        """
        original_message_history = message_history
        llm_call_start_time = time.perf_counter()
        try:
            response, message_history = await self.llm_client.create_message(
                system_prompt=system_prompt,
                message_history=message_history,
                tool_definitions=tool_definitions,
                keep_tool_result=self.cfg.agent.keep_tool_result,
                step_id=step_id,
                task_log=self.task_log,
                agent_type=agent_type,
            )

            if ErrorBox.is_error_box(response):
                await self.stream.show_error(str(response))
                response = None

            if ResponseBox.is_response_box(response):
                if response.has_extra_info():
                    extra_info = response.get_extra_info()
                    if extra_info.get("warning_msg"):
                        await self.stream.show_error(
                            extra_info.get("warning_msg", "Empty warning message")
                        )
                response = response.get_response()

            # Check if response is None (indicating an error occurred)
            if response is None:
                self.task_log.log_step(
                    "error",
                    f"{purpose} | LLM Call Failed",
                    f"{purpose} failed - no response received",
                )
                return "", False, None, original_message_history

            # Use client's response processing method
            assistant_response_text, should_break, message_history = (
                self.llm_client.process_llm_response(
                    response, message_history, agent_type
                )
            )

            # Use client's tool call information extraction method
            tool_calls_info = self.llm_client.extract_tool_calls_info(
                response, assistant_response_text
            )

            self.task_log.log_step(
                "info",
                f"{purpose} | LLM Call",
                "completed successfully",
            )
            elapsed_ms = int((time.perf_counter() - llm_call_start_time) * 1000)
            self.task_log.record_stage_timing(
                f"answer_generator.llm_call.{agent_type}",
                elapsed_ms,
                message=f"{purpose} completed in {elapsed_ms}ms",
                metadata={
                    "purpose": purpose,
                    "agent_type": agent_type,
                    "tool_definition_count": len(tool_definitions or []),
                    "tool_call_count": len(tool_calls_info or []),
                    "should_break": should_break,
                },
            )
            return (
                assistant_response_text,
                should_break,
                tool_calls_info,
                message_history,
            )

        except Exception as e:
            elapsed_ms = int((time.perf_counter() - llm_call_start_time) * 1000)
            self.task_log.record_stage_timing(
                f"answer_generator.llm_call.{agent_type}",
                elapsed_ms,
                message=f"{purpose} failed in {elapsed_ms}ms",
                metadata={
                    "purpose": purpose,
                    "agent_type": agent_type,
                    "tool_definition_count": len(tool_definitions or []),
                    "status": "error",
                    "error": str(e),
                },
                info_level="error",
            )
            self.task_log.log_step(
                "error",
                f"{purpose} | LLM Call ERROR",
                f"{purpose} error: {str(e)}",
            )
            # Return empty response with should_break=False, need to retry
            return "", False, None, original_message_history

    async def generate_failure_summary(
        self,
        system_prompt: str,
        message_history: List[Dict[str, Any]],
        tool_definitions: List[Dict],
        turn_count: int,
    ) -> Optional[str]:
        """
        Generate a failure experience summary for context compression.

        This is the core of the context management mechanism. When a task attempt fails
        (i.e., the task is not completed within the given turns and context window),
        we compress the entire conversation history into a structured summary containing:
        - Failure type: incomplete / blocked / misdirected / format_missed
        - What happened: the approach taken and why a final answer was not reached
        - Useful findings: facts, intermediate results, or conclusions to be reused

        Args:
            system_prompt: The system prompt used in the conversation
            message_history: The full conversation history to be compressed
            tool_definitions: Available tool definitions
            turn_count: Current turn count for step ID

        Returns:
            The compressed failure experience summary, or None if generation failed
        """
        self.task_log.log_step(
            "info",
            "Main Agent | Failure Summary",
            "Generating failure experience summary for potential retry...",
        )

        # Build failure summary history
        failure_summary_history = message_history.copy()
        if failure_summary_history and failure_summary_history[-1]["role"] == "user":
            failure_summary_history.pop()

        # Add failure summary prompt and assistant prefix for structured output
        failure_summary_history.append(
            {"role": "user", "content": FAILURE_SUMMARY_PROMPT}
        )
        failure_summary_history.append(
            {"role": "assistant", "content": FAILURE_SUMMARY_ASSISTANT_PREFIX}
        )

        # Call LLM to generate failure summary
        (
            failure_summary_text,
            _,
            _,
            _,
        ) = await self.handle_llm_call(
            system_prompt,
            failure_summary_history,
            tool_definitions,
            turn_count + 10,  # Use a different step id
            "Main Agent | Failure Experience Summary",
            agent_type="failure_summary",
        )

        # Prepend the assistant prefix to the response for complete output
        if failure_summary_text:
            failure_summary_text = (
                FAILURE_SUMMARY_ASSISTANT_PREFIX + failure_summary_text
            )
            failure_experience_summary = extract_failure_experience_summary(
                failure_summary_text
            )
            # Truncate for logging, but only add "..." if actually truncated
            log_preview = failure_experience_summary[:500]
            if len(failure_experience_summary) > 500:
                log_preview += "..."
            self.task_log.log_step(
                "info",
                "Main Agent | Failure Summary",
                f"Generated failure experience summary:\n{log_preview}",
            )
            return failure_experience_summary
        else:
            self.task_log.log_step(
                "warning",
                "Main Agent | Failure Summary",
                "Failed to generate failure experience summary",
            )
            return None

    async def generate_final_answer_with_retries(
        self,
        system_prompt: str,
        message_history: List[Dict[str, Any]],
        tool_definitions: List[Dict],
        turn_count: int,
        task_description: str,
    ) -> Tuple[Optional[str], str, Optional[str], str, List[Dict[str, Any]]]:
        """
        Generate final answer with retry mechanism.

        Args:
            system_prompt: System prompt for the LLM
            message_history: Conversation history
            tool_definitions: Available tool definitions
            turn_count: Current turn count
            task_description: Original task description

        Returns:
            Tuple of (final_answer_text, final_summary, final_boxed_answer, usage_log, message_history)
        """
        # Generate summary prompt
        if self.verification_enabled:
            message_history = await self.generate_cross_verification_note(
                system_prompt=system_prompt,
                message_history=message_history,
                turn_count=turn_count,
                task_description=task_description,
            )

        summary_prompt = self._build_main_summary_prompt(task_description)

        if message_history[-1]["role"] == "user":
            message_history.pop(-1)
        message_history.append({"role": "user", "content": summary_prompt})

        final_answer_text = None
        final_boxed_answer = None
        final_summary = ""
        usage_log = ""
        final_summary_agent_types = (
            ["verification", "final_summary"]
            if self.verification_enabled and self.verification_use_high_model
            else ["final_summary"]
        )

        for retry_idx in range(self.max_final_answer_retries):
            current_agent_type = final_summary_agent_types[
                min(retry_idx, len(final_summary_agent_types) - 1)
            ]
            await self._emit_stage_heartbeat(
                "总结" if current_agent_type == "final_summary" else "校验",
                turn=turn_count,
                detail=(
                    f"最终总结生成中（第 {retry_idx + 1}/{self.max_final_answer_retries} 次）"
                ),
                agent_name="Final Summary",
            )
            (
                final_answer_text,
                should_break,
                tool_calls_info,
                message_history,
            ) = await self.handle_llm_call(
                system_prompt,
                message_history,
                tool_definitions,
                turn_count + 1 + retry_idx,
                f"Main agent | Final Summary (attempt {retry_idx + 1}/{self.max_final_answer_retries})",
                agent_type=current_agent_type,
            )

            if final_answer_text:
                final_summary, final_boxed_answer, usage_log = (
                    self.output_formatter.format_final_summary_and_log(
                        final_answer_text, self.llm_client
                    )
                )
                if self._is_summary_too_short(final_answer_text):
                    self.task_log.log_step(
                        "warning",
                        "Main Agent | Final Answer",
                        f"Summary too short on attempt {retry_idx + 1}, length below threshold {self.summary_retry_min_chars}.",
                    )
                    if retry_idx < self.max_final_answer_retries - 1:
                        if message_history and message_history[-1]["role"] == "assistant":
                            message_history.pop()
                        message_history.append(
                            {"role": "user", "content": self._build_expand_summary_prompt()}
                        )
                        continue

                if final_boxed_answer != FORMAT_ERROR_MESSAGE:
                    self.task_log.log_step(
                        "info",
                        "Main Agent | Final Answer",
                        f"Boxed answer found on attempt {retry_idx + 1}",
                    )
                    break
                else:
                    self.task_log.log_step(
                        "warning",
                        "Main Agent | Final Answer",
                        f"No boxed answer on attempt {retry_idx + 1}, retrying...",
                    )
                    if retry_idx < self.max_final_answer_retries - 1:
                        if (
                            message_history
                            and message_history[-1]["role"] == "assistant"
                        ):
                            message_history.pop()
            else:
                self.task_log.log_step(
                    "warning",
                    "Main Agent | Final Answer",
                    f"Failed to generate answer on attempt {retry_idx + 1}",
                )
                if (
                    current_agent_type == "verification"
                    and "final_summary" in final_summary_agent_types
                    and retry_idx < self.max_final_answer_retries - 1
                ):
                    self.task_log.log_step(
                        "warning",
                        "Main Agent | Final Answer",
                        "高级模型总结超时或失败，下一次尝试自动降级到 summary 模型。",
                    )
                if retry_idx < self.max_final_answer_retries - 1:
                    if message_history and message_history[-1]["role"] == "assistant":
                        message_history.pop()

        # Ensure final_boxed_answer is never None
        if final_boxed_answer is None:
            final_boxed_answer = FORMAT_ERROR_MESSAGE

        return (
            final_answer_text,
            final_summary,
            final_boxed_answer,
            usage_log,
            message_history,
        )

    def handle_no_context_management_fallback(
        self,
        final_answer_text: Optional[str],
        final_summary: str,
        final_boxed_answer: Optional[str],
    ) -> Tuple[str, str, str]:
        """
        Handle fallback when context_compress_limit == 0 (no context management).

        In this mode, the model has only one chance to answer.
        We should try to use intermediate answers as fallback to maximize accuracy.

        Args:
            final_answer_text: The generated final answer text
            final_summary: The final summary
            final_boxed_answer: The extracted boxed answer

        Returns:
            Tuple of (final_answer_text, final_summary, final_boxed_answer)
        """
        # Validate final_answer_text
        if not final_answer_text:
            final_answer_text = "No final answer generated."
            final_summary = final_answer_text
            final_boxed_answer = FORMAT_ERROR_MESSAGE
            self.task_log.log_step(
                "error",
                "Main Agent | Final Answer",
                "Unable to generate final answer after all retries",
            )
        else:
            self.task_log.log_step(
                "info",
                "Main Agent | Final Answer",
                f"Final answer content:\n\n{final_answer_text}",
            )

        # Fallback to intermediate answer if no valid boxed answer
        if (
            final_boxed_answer == FORMAT_ERROR_MESSAGE or final_boxed_answer is None
        ) and self.intermediate_boxed_answers:
            final_boxed_answer = self.intermediate_boxed_answers[-1]
            self.task_log.log_step(
                "info",
                "Main Agent | Final Answer (No Context Management)",
                f"Using intermediate boxed answer as fallback: {final_boxed_answer}",
            )

        # Ensure final_boxed_answer is never None
        if final_boxed_answer is None:
            final_boxed_answer = FORMAT_ERROR_MESSAGE

        return final_answer_text, final_summary, final_boxed_answer

    def handle_context_management_no_fallback(
        self,
        final_answer_text: Optional[str],
        final_summary: str,
        final_boxed_answer: Optional[str],
    ) -> Tuple[str, str, str]:
        """
        Handle failure when context_compress_limit > 0 (context management enabled).

        In this mode, the model has multiple chances to retry with context management.
        We should NOT guess or use intermediate answers, because:
        - A wrong guess can reduce accuracy
        - The model will have another chance to answer with failure experience

        Args:
            final_answer_text: The generated final answer text
            final_summary: The final summary
            final_boxed_answer: The extracted boxed answer

        Returns:
            Tuple of (final_answer_text, final_summary, final_boxed_answer)
        """
        # Validate final_answer_text
        if not final_answer_text:
            final_answer_text = "No final answer generated."
            final_summary = final_answer_text
            final_boxed_answer = FORMAT_ERROR_MESSAGE
            self.task_log.log_step(
                "error",
                "Main Agent | Final Answer",
                "Unable to generate final answer after all retries",
            )
        else:
            self.task_log.log_step(
                "info",
                "Main Agent | Final Answer",
                f"Final answer content:\n\n{final_answer_text}",
            )

        # Ensure final_boxed_answer is never None
        if final_boxed_answer is None:
            final_boxed_answer = FORMAT_ERROR_MESSAGE

        # With context management, do NOT fallback to intermediate answers
        if final_boxed_answer == FORMAT_ERROR_MESSAGE:
            self.task_log.log_step(
                "info",
                "Main Agent | Final Answer (Context Management Mode)",
                "No valid boxed answer found. Not using intermediate fallback - will generate failure summary for retry.",
            )

        return final_answer_text, final_summary, final_boxed_answer

    async def generate_and_finalize_answer(
        self,
        system_prompt: str,
        message_history: List[Dict[str, Any]],
        tool_definitions: List[Dict],
        turn_count: int,
        task_description: str,
        reached_max_turns: bool = False,
        is_final_retry: bool = False,
        save_callback=None,
    ) -> Tuple[str, str, Optional[str], str, List[Dict[str, Any]]]:
        """
        Generate final answer and handle fallback based on context management settings.

        Context Management (context_compress_limit > 0) is essentially a context compression
        mechanism that enables multi-attempt problem solving.

        Decision table based on (context_management, reached_max_turns):

        | Context Management | Reached Max Turns | Behavior                                    |
        |--------------------|-------------------|---------------------------------------------|
        | OFF (limit=0)      | No                | Generate answer → fallback to intermediate  |
        | OFF (limit=0)      | Yes               | Generate answer → fallback to intermediate  |
        | ON  (limit>0)      | No                | Generate answer → no fallback, fail summary |
        | ON  (limit>0)      | Yes               | SKIP generation → fail summary directly     |

        Args:
            system_prompt: System prompt for the LLM
            message_history: Conversation history
            tool_definitions: Available tool definitions
            turn_count: Current turn count
            task_description: Original task description
            reached_max_turns: Whether the main loop ended due to reaching max turns
            save_callback: Optional callback to save message history

        Returns:
            Tuple of (final_summary, final_boxed_answer, failure_experience_summary, usage_log, message_history)
        """
        context_management_enabled = self.context_compress_limit > 0
        failure_experience_summary = None
        usage_log = ""

        # CASE: Context management ON + reached max turns + NOT final retry
        # 非 Demo 模式下跳过总结生成，避免盲猜；Demo 模式继续尝试基于现有上下文生成可读总结
        if (
            context_management_enabled
            and reached_max_turns
            and not is_final_retry
            and not self.demo_mode
        ):
            self.task_log.log_step(
                "info",
                "Main Agent | Final Answer (Context Management Mode)",
                "Reached max turns. Skipping answer generation to avoid blind guessing.",
            )

            if save_callback:
                save_callback(system_prompt, message_history)

            if self.retry_with_summary:
                failure_experience_summary = await self.generate_failure_summary(
                    system_prompt, message_history, tool_definitions, turn_count
                )

            return (
                "Task incomplete - reached maximum turns. Will retry with failure experience.",
                FORMAT_ERROR_MESSAGE,
                failure_experience_summary,
                usage_log,
                message_history,
            )

        # ALL OTHER CASES: Generate final answer first
        # (including final retry with reached_max_turns - last chance to get an answer)
        (
            final_answer_text,
            final_summary,
            final_boxed_answer,
            usage_log,
            message_history,
        ) = await self.generate_final_answer_with_retries(
            system_prompt=system_prompt,
            message_history=message_history,
            tool_definitions=tool_definitions,
            turn_count=turn_count,
            task_description=task_description,
        )

        if save_callback:
            save_callback(system_prompt, message_history)

        # CASE: Context management OFF or final retry
        # Try to use intermediate answers as fallback to maximize accuracy
        # For final retry, there's no more retry opportunity, so we use fallback
        if not context_management_enabled or is_final_retry:
            final_answer_text, final_summary, final_boxed_answer = (
                self.handle_no_context_management_fallback(
                    final_answer_text, final_summary, final_boxed_answer
                )
            )
            if is_final_retry:
                self.task_log.log_step(
                    "info",
                    "Main Agent | Final Answer (Final Retry)",
                    "This is the final retry. Using intermediate fallback if available.",
                )
            return (
                final_summary,
                final_boxed_answer,
                None,
                usage_log,
                message_history,
            )

        # CASE: Context management ON + normal completion (not reached max turns, not final retry)
        # Don't use fallback - wrong guess would reduce accuracy
        final_answer_text, final_summary, final_boxed_answer = (
            self.handle_context_management_no_fallback(
                final_answer_text, final_summary, final_boxed_answer
            )
        )

        if final_boxed_answer == FORMAT_ERROR_MESSAGE and self.retry_with_summary:
            failure_experience_summary = await self.generate_failure_summary(
                system_prompt, message_history, tool_definitions, turn_count
            )

        return (
            final_summary,
            final_boxed_answer,
            failure_experience_summary,
            usage_log,
            message_history,
        )
