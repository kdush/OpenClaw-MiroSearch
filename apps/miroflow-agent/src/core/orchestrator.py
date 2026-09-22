# Copyright (c) 2025 MiroMind
# This source code is licensed under the Apache 2.0 License.

"""
Orchestrator module for coordinating agent task execution.

This module contains the main Orchestrator class that manages the execution of tasks
by coordinating between the main agent, sub-agents, and various tools.
"""

import asyncio
import gc
import json
import logging
import time
import uuid
from collections import defaultdict
from datetime import date
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from miroflow_tools.manager import ToolManager
from omegaconf import DictConfig

from ..config.settings import expose_sub_agents_as_tools
from ..io.input_handler import process_input
from ..io.output_formatter import OutputFormatter
from ..io.report_presentation import prepare_user_facing_report
from ..llm.base_client import BaseClient
from ..logging.task_logger import TaskLog, get_utc_plus_8_time
from ..utils.parsing_utils import extract_llm_response_text
from ..utils.prompt_utils import (
    FORMAT_ERROR_MESSAGE,
    generate_agent_specific_system_prompt,
    generate_agent_summarize_prompt,
    mcp_tags,
    refusal_keywords,
)
from .answer_generator import AnswerGenerator
from .deep_efficiency import (
    resolve_early_stop_config,
    resolve_exit_on_early_stop,
    resolve_max_scrape_per_task,
    resolve_parallel_tool_calls,
    scrape_budget_exceeded,
    scrape_skip_message,
)
from .lead_tracker import LeadTrackingManager, resolve_lead_tracking_config
from .stream_handler import StreamHandler
from .tool_executor import ToolExecutor

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

# Default timeout for LLM calls in seconds
DEFAULT_LLM_TIMEOUT = 600

# Safety limits for retry loops
DEFAULT_MAX_CONSECUTIVE_ROLLBACKS = 5

# Additional attempts beyond max_turns for total loop protection
EXTRA_ATTEMPTS_BUFFER = 200
DEFAULT_MAX_CONSECUTIVE_LLM_FAILURES = 6
DEFAULT_LLM_FAILURE_SLEEP_SECONDS = 2

DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS = 3
DEFAULT_VERIFICATION_MIN_HIGH_CONF_SOURCES = 2
DEFAULT_VERIFICATION_MAX_GUIDANCE_ATTEMPTS = 3
DEFAULT_VERIFICATION_MAX_STAGNANT_GUIDANCE_ATTEMPTS = 1
DEFAULT_HIGH_CONF_DOMAINS = [
    "reuters.com",
    "apnews.com",
    "bbc.com",
    "aljazeera.com",
    "un.org",
    "iaea.org",
    "defense.gov",
    "mod.gov.il",
    "state.gov",
    "who.int",
    "imf.org",
    "worldbank.org",
]
SEARCH_TOOL_NAMES = {"google_search", "sogou_search"}
# Scraping tools that fetch and parse web page content
SCRAPE_TOOL_NAMES = {
    "jina_reader",
    "firecrawl",
    "fetch_page",
    "scrape_webpage",
    "search_and_scrape_webpage",
    "jina_scrape_llm_summary",
    "browser_navigate",
    "browser_screenshot",
    "scrape_url",
    "scrape_and_extract_info",
}


def _list_tools(sub_agent_tool_managers: Dict[str, ToolManager]):
    """
    Create a cached async function for fetching sub-agent tool definitions.

    This factory function returns an async closure that lazily fetches and caches
    tool definitions from all sub-agent tool managers. The cache ensures that
    tool definitions are only fetched once per orchestrator instance.

    Args:
        sub_agent_tool_managers: Dictionary mapping sub-agent names to their ToolManager instances.

    Returns:
        An async function that returns a dictionary of tool definitions for each sub-agent.
    """
    cache = None

    async def wrapped():
        nonlocal cache
        if cache is None:
            # Only fetch tool definitions if not already cached
            result = {
                name: await tool_manager.get_all_tool_definitions()
                for name, tool_manager in sub_agent_tool_managers.items()
            }
            cache = result
        return cache

    return wrapped


class Orchestrator:
    """
    Main orchestrator for coordinating agent task execution.

    Manages the execution loop for main and sub-agents, coordinating
    LLM calls, tool execution, streaming events, and context management.
    """

    def __init__(
        self,
        main_agent_tool_manager: ToolManager,
        sub_agent_tool_managers: Dict[str, ToolManager],
        llm_client: BaseClient,
        output_formatter: OutputFormatter,
        cfg: DictConfig,
        task_log: Optional["TaskLog"] = None,
        stream_queue: Optional[Any] = None,
        tool_definitions: Optional[List[Dict[str, Any]]] = None,
        sub_agent_tool_definitions: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ):
        """
        Initialize the orchestrator.

        Args:
            main_agent_tool_manager: Tool manager for main agent
            sub_agent_tool_managers: Dictionary of tool managers for sub-agents
            llm_client: The LLM client for API calls
            output_formatter: Formatter for output processing
            cfg: Configuration object
            task_log: Logger for task execution
            stream_queue: Optional async queue for streaming events
            tool_definitions: Pre-fetched tool definitions (optional)
            sub_agent_tool_definitions: Pre-fetched sub-agent tool definitions (optional)
        """
        self.main_agent_tool_manager = main_agent_tool_manager
        self.sub_agent_tool_managers = sub_agent_tool_managers
        self.llm_client = llm_client
        self.output_formatter = output_formatter
        self.cfg = cfg
        self.task_log = task_log
        self.stream_queue = stream_queue
        self.tool_definitions = tool_definitions
        self.sub_agent_tool_definitions = sub_agent_tool_definitions

        # Initialize sub-agent tool list function
        self._list_sub_agent_tools = None
        if sub_agent_tool_managers:
            self._list_sub_agent_tools = _list_tools(sub_agent_tool_managers)

        # Pass task_log to llm_client
        if self.llm_client and task_log:
            self.llm_client.task_log = task_log

        # Track boxed answers extracted during main loop turns
        self.intermediate_boxed_answers: List[str] = []

        # 阶段心跳同名去重，避免高频心跳刷 stderr，仅在阶段/回合/明细变化时打印一次
        self._last_stage_log_key: Optional[tuple] = None

        # Record used subtask / q / Query to detect duplicates
        self.used_queries: Dict[str, Dict[str, int]] = {}

        # Retry loop protection limits
        self.MAX_CONSECUTIVE_ROLLBACKS = DEFAULT_MAX_CONSECUTIVE_ROLLBACKS
        self.max_consecutive_llm_failures = max(
            1,
            int(
                cfg.agent.get(
                    "max_consecutive_llm_failures",
                    DEFAULT_MAX_CONSECUTIVE_LLM_FAILURES,
                )
            ),
        )
        self.llm_failure_sleep_seconds = max(
            0,
            int(
                cfg.agent.get(
                    "llm_failure_sleep_seconds",
                    DEFAULT_LLM_FAILURE_SLEEP_SECONDS,
                )
            ),
        )

        # Context management settings
        self.context_compress_limit = cfg.agent.get("context_compress_limit", 0)

        # Initialize helper components
        self.stream = StreamHandler(stream_queue)
        self.tool_executor = ToolExecutor(
            main_agent_tool_manager=main_agent_tool_manager,
            sub_agent_tool_managers=sub_agent_tool_managers,
            output_formatter=output_formatter,
            task_log=task_log,
            stream_handler=self.stream,
            max_consecutive_rollbacks=DEFAULT_MAX_CONSECUTIVE_ROLLBACKS,
        )
        self.answer_generator = AnswerGenerator(
            llm_client=llm_client,
            output_formatter=output_formatter,
            task_log=task_log,
            stream_handler=self.stream,
            cfg=cfg,
            intermediate_boxed_answers=self.intermediate_boxed_answers,
        )

        # 交叉校验配置（用于数字事实任务的多轮检索与高置信来源约束）
        verification_cfg = cfg.agent.get("verification", {})
        self.verification_enabled = bool(verification_cfg.get("enabled", False))
        self.verification_min_search_rounds = max(
            1,
            int(
                verification_cfg.get(
                    "min_search_rounds", DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS
                )
            ),
        )
        self.verification_min_high_conf_sources = max(
            1,
            int(
                verification_cfg.get(
                    "min_high_conf_sources", DEFAULT_VERIFICATION_MIN_HIGH_CONF_SOURCES
                )
            ),
        )
        self.verification_max_guidance_attempts = max(
            1,
            int(
                verification_cfg.get(
                    "max_guidance_attempts", DEFAULT_VERIFICATION_MAX_GUIDANCE_ATTEMPTS
                )
            ),
        )
        raw_domains = verification_cfg.get(
            "high_conf_domains", DEFAULT_HIGH_CONF_DOMAINS
        )
        self.verification_high_conf_domains = {
            str(domain).strip().lower() for domain in raw_domains if str(domain).strip()
        }
        self.verification_search_rounds = 0
        self.verification_guidance_attempts = 0
        self.verification_high_conf_source_domains: set[str] = set()
        # 工具侧 confidence 门控通过后，不再为补齐高置信来源追加检索指令
        self.retrieval_confidence_passed = False
        self.verification_guidance_anchor_search_rounds = 0
        self.verification_guidance_anchor_high_conf_sources = 0
        self.verification_stagnant_guidance_attempts = 0
        self.verification_max_stagnant_guidance_attempts = max(
            1,
            int(
                verification_cfg.get(
                    "max_stagnant_guidance_attempts",
                    DEFAULT_VERIFICATION_MAX_STAGNANT_GUIDANCE_ATTEMPTS,
                )
            ),
        )

        # Lead tracking for deep research (Phase 4)
        # Resolve from agent root OR main_agent (CLI harness), and auto-enable
        # when research_intensity == deep (see resolve_lead_tracking_config).
        enable_lead_tracking, max_follow_ups = resolve_lead_tracking_config(cfg)
        self.lead_tracker = LeadTrackingManager(
            enabled=enable_lead_tracking,
            max_follow_ups=max_follow_ups,
        )

        # Round 6–7 deep efficiency knobs
        self.max_scrape_per_task = resolve_max_scrape_per_task(cfg)
        self.parallel_tool_calls = resolve_parallel_tool_calls(cfg)
        (
            self.deep_early_stop_enabled,
            self.deep_early_stop_min_sources,
        ) = resolve_early_stop_config(cfg)
        (
            self.deep_exit_on_early_stop,
            self.deep_post_early_stop_turns,
        ) = resolve_exit_on_early_stop(cfg)
        # Unique domains seen in search results (for early-stop agreement)
        self.independent_source_domains: set[str] = set()
        self.deep_early_stop_triggered = False
        self.deep_early_stop_turn = 0
        self._deep_convergence_nudge_sent = False

    async def _emit_stage_heartbeat(
        self,
        phase: str,
        *,
        turn: int = 0,
        detail: str = "",
        agent_name: str = "main",
        tool_name: str = "",
    ) -> None:
        """发送阶段心跳，便于前端显示当前回合与阶段。"""
        payload: Dict[str, Any] = {
            "phase": phase,
            "turn": max(0, int(turn)),
            "detail": detail,
            "agent_name": agent_name,
            "search_round": int(self.verification_search_rounds),
            "verification_min_search_rounds": int(self.verification_min_search_rounds),
            "verification_high_conf_sources": int(
                len(self.verification_high_conf_source_domains)
            ),
            "verification_min_high_conf_sources": int(
                self.verification_min_high_conf_sources
            ),
            "timestamp": time.time(),
        }
        if tool_name:
            payload["tool_name"] = tool_name

        # 同名去重后镜像到 stderr：仅在阶段/回合/明细/工具变化时打印一行 INFO，
        # 让 docker logs 也能持续看到任务进度，便于运维侧观察长任务。
        log_key = (phase, payload["turn"], detail, agent_name, tool_name)
        if log_key != self._last_stage_log_key:
            self._last_stage_log_key = log_key
            tool_suffix = f" tool={tool_name}" if tool_name else ""
            logger.info(
                "🫀 stage_heartbeat | agent=%s | phase=%s | turn=%s | detail=%s%s",
                agent_name,
                phase,
                payload["turn"],
                detail,
                tool_suffix,
            )

        try:
            await self.stream.update("stage_heartbeat", payload)
        except Exception:
            # 心跳是辅助信息，不能影响主流程
            pass

    async def _emit_final_output(
        self,
        markdown: str,
        result_quality: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """仅在存在可用答案时发送 final_output 终态事件。"""
        answer_available = (
            bool(result_quality.get("answer_available", False))
            if result_quality is not None
            else bool(str(markdown or "").strip())
        )
        if not answer_available or not str(markdown or "").strip():
            return False
        await self.stream.update("final_output", {"markdown": markdown})
        return True

    def _log_final_outcome(
        self,
        task_id: str,
        result_quality: Dict[str, Any],
    ) -> None:
        """根据答案可用性记录真实终态，避免失败路径伪报完成。"""
        if result_quality.get("answer_available", False):
            self.task_log.log_step(
                "info",
                "Main Agent | Task Completed",
                f"Main agent task {task_id} completed successfully",
            )
            return

        issues = result_quality.get("issues", [])
        self.task_log.log_step(
            "error",
            "Main Agent | Final Answer Unavailable",
            (
                f"Main agent task {task_id} ended without a usable final answer; "
                f"issues={issues}"
            ),
        )

    @staticmethod
    def _normalize_domain(url: str) -> str:
        if not url:
            return ""
        try:
            netloc = urlparse(url).netloc.lower().strip()
        except Exception:
            return ""
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc

    def _is_high_conf_domain(self, domain: str) -> bool:
        if not domain:
            return False
        for trusted_domain in self.verification_high_conf_domains:
            if domain == trusted_domain or domain.endswith(f".{trusted_domain}"):
                return True
        return False

    def _extract_search_links(self, tool_name: str, tool_result: dict) -> List[str]:
        if tool_name not in SEARCH_TOOL_NAMES:
            return []
        if "error" in tool_result:
            return []
        raw_result = tool_result.get("result")
        if not raw_result:
            return []
        if isinstance(raw_result, str):
            try:
                parsed = json.loads(raw_result)
            except json.JSONDecodeError:
                return []
        elif isinstance(raw_result, dict):
            parsed = raw_result
        else:
            return []

        links: List[str] = []
        for item in parsed.get("organic", []):
            link = item.get("link")
            if isinstance(link, str) and link.strip():
                links.append(link.strip())
        for item in parsed.get("Pages", []):
            link = item.get("url") or item.get("link")
            if isinstance(link, str) and link.strip():
                links.append(link.strip())
        return links

    def _parse_search_tool_payload(self, tool_result: dict) -> dict:
        """Parse google_search / sogou_search tool payload into a dict."""
        raw_result = (
            tool_result.get("result") if isinstance(tool_result, dict) else None
        )
        if not raw_result:
            return {}
        if isinstance(raw_result, str):
            try:
                parsed = json.loads(raw_result)
            except json.JSONDecodeError:
                return {}
        elif isinstance(raw_result, dict):
            parsed = raw_result
        else:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _record_search_provider_metrics(self, parsed: dict) -> None:
        """Record which providers were attempted / returned results."""
        metrics = self.task_log.run_metrics
        params = parsed.get("searchParameters") or {}
        if not isinstance(params, dict):
            params = {}

        route_trace = parsed.get("route_trace") or params.get("route_trace") or []
        recorded = False
        if isinstance(route_trace, list):
            for entry in route_trace:
                if not isinstance(entry, dict):
                    continue
                provider = entry.get("provider")
                if not provider:
                    continue
                metrics.record_search_provider_hit(str(provider))
                recorded = True

        if recorded:
            return

        provider = parsed.get("provider") or params.get("provider")
        if provider and provider != "multi-route":
            metrics.record_search_provider_hit(str(provider))
            return

        order = params.get("provider_order") or []
        if isinstance(order, list):
            for name in order:
                if name:
                    metrics.record_search_provider_hit(str(name))

    def _record_search_evidence(self, tool_name: str, tool_result: dict):
        # Always count the tool invocation for route comparison (Case D).
        self.task_log.run_metrics.record_search_attempt()
        parsed = self._parse_search_tool_payload(tool_result)
        if parsed:
            self._record_search_provider_metrics(parsed)
            self._record_retrieval_confidence(parsed)

        links = self._extract_search_links(tool_name, tool_result)
        if not links:
            return
        # 无论是否启用验证门控，都递增全局检索轮次
        self.task_log.run_metrics.search_rounds += 1
        for link in links:
            domain = self._normalize_domain(link)
            if domain:
                self.independent_source_domains.add(domain)
                if self.verification_enabled and self._is_high_conf_domain(domain):
                    self.verification_high_conf_source_domains.add(domain)

        if not self.verification_enabled:
            return

        self.verification_search_rounds += 1

    def _record_retrieval_confidence(self, parsed: dict) -> None:
        """记录工具侧 confidence 门控结果（通过了就不再凑高置信来源）。"""
        if self.retrieval_confidence_passed:
            return
        confidence = parsed.get("confidence")
        if not isinstance(confidence, dict):
            params = parsed.get("searchParameters")
            confidence = (
                params.get("confidence") if isinstance(params, dict) else None
            )
        # enabled=false 表示用户不要置信度门控，此时不拿它当检索质量背书
        if not isinstance(confidence, dict):
            return
        if not (confidence.get("enabled") and confidence.get("passed")):
            return

        self.retrieval_confidence_passed = True
        self.task_log.run_metrics.retrieval_confidence_passed = True
        self.task_log.log_step(
            "info",
            "Main Agent | Search Confidence",
            "检索质量门控通过（工具侧 confidence passed=yes），"
            "不再为补齐来源追加检索指令。",
            metadata={
                "confidence_score": confidence.get("score"),
                "confidence_metrics": confidence.get("metrics"),
            },
        )

    def _record_scrape_metric(
        self, tool_name: str, tool_result: dict, turn_count: int
    ) -> None:
        """Count only real full-page scrapes (budget-skip results excluded)."""
        if (
            tool_name not in SCRAPE_TOOL_NAMES
            or "error" in tool_result
            or "[scrape_budget]" in str(tool_result.get("result") or "")
        ):
            return
        self.task_log.run_metrics.record_scrape(count=1)
        self.task_log.log_step(
            "debug",
            f"Main Agent | Turn: {turn_count} | Metrics",
            f"Recorded scrape for tool: {tool_name}",
        )

    def _should_early_stop_clue_chase(self) -> bool:
        """Round 6: stop extra lead follow-ups once multi-source agreement exists.

        Triggers when ≥N independent source domains are present and minimum
        search rounds are satisfied — enough to fill Conflicts without endless
        clue chasing.
        """
        if not self.deep_early_stop_enabled:
            return False
        agreeing = len(self.independent_source_domains)
        if agreeing < self.deep_early_stop_min_sources:
            return False
        search_rounds = int(self.task_log.run_metrics.search_rounds or 0)
        min_rounds = max(2, int(self.verification_min_search_rounds or 2))
        if search_rounds < min_rounds:
            return False
        return True

    def _note_deep_early_stop(self, turn_count: int, reason: str = "") -> None:
        """Record first early-stop trigger for metrics + logs."""
        if self.deep_early_stop_triggered:
            return
        self.deep_early_stop_triggered = True
        self.deep_early_stop_turn = max(0, int(turn_count))
        self.task_log.run_metrics.record_early_stop(self.deep_early_stop_turn)
        self.task_log.log_step(
            "info",
            f"Main Agent | Turn: {turn_count} | Deep Early-Stop",
            (
                "≥"
                f"{self.deep_early_stop_min_sources} independent sources "
                f"({len(self.independent_source_domains)} domains) and "
                f"search_rounds={self.task_log.run_metrics.search_rounds}; "
                f"stopping extra lead follow-ups"
                + (
                    f"; will exit after ≤{self.deep_post_early_stop_turns} more turns"
                    if self.deep_exit_on_early_stop
                    else ""
                )
                + (f" ({reason})" if reason else "")
                + "."
            ),
            metadata={
                "independent_domains": sorted(self.independent_source_domains)[:12],
                "search_rounds": self.task_log.run_metrics.search_rounds,
                "reason": reason,
                "exit_on_early_stop": self.deep_exit_on_early_stop,
                "post_early_stop_turns": self.deep_post_early_stop_turns,
            },
        )

    def _should_force_summary_after_early_stop(self, turn_count: int) -> bool:
        """Round 7: cap remaining turns once early-stop evidence is enough."""
        if not self.deep_exit_on_early_stop:
            return False
        if not self._should_early_stop_clue_chase():
            return False
        self._note_deep_early_stop(turn_count, reason="force-summary-check")
        return turn_count >= (
            self.deep_early_stop_turn + self.deep_post_early_stop_turns
        )

    async def _maybe_nudge_and_force_summary(
        self,
        message_history: List[Dict[str, Any]],
        turn_count: int,
    ) -> bool:
        """If early-stop turn budget is exhausted, nudge once then force exit.

        Returns True when the main loop should break into final summary.
        """
        if not self._should_force_summary_after_early_stop(turn_count):
            return False
        if not self._deep_convergence_nudge_sent:
            self._deep_convergence_nudge_sent = True
            message_history.append(
                {
                    "role": "user",
                    "content": (
                        "已有足够独立来源与检索轮次，请停止继续检索/抓取，"
                        "立即基于现有证据撰写完整研究报告。"
                        "必须包含 Conflicts、Timeline、Evidence、Confirmed vs Unconfirmed；"
                        "不确定处标明不可确认，勿编造确定性。"
                    ),
                }
            )
            self.task_log.log_step(
                "info",
                f"Main Agent | Turn: {turn_count} | Deep Convergence",
                (
                    f"Early-stop turn budget exhausted "
                    f"(triggered@turn={self.deep_early_stop_turn}, "
                    f"post_turns={self.deep_post_early_stop_turns}); "
                    "nudging model to write the report on the next turn."
                ),
            )
            # Allow one more LLM turn to consume the nudge.
            return False
        self.task_log.log_step(
            "info",
            f"Main Agent | Turn: {turn_count} | Deep Convergence Exit",
            "Exiting main loop after early-stop turn cap to avoid LLM timeout burn.",
        )
        return True

    async def _execute_regular_tool_call(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict,
        turn_count: int,
    ) -> dict:
        """Execute one non-subagent tool, honoring scrape budget."""
        if tool_name in SCRAPE_TOOL_NAMES and scrape_budget_exceeded(
            self.task_log.run_metrics.scrape_count, self.max_scrape_per_task
        ):
            skip_msg = scrape_skip_message(
                self.max_scrape_per_task, self.task_log.run_metrics.scrape_count
            )
            self.task_log.log_step(
                "info",
                f"Main Agent | Turn: {turn_count} | Scrape Budget",
                skip_msg,
            )
            return {
                "server_name": server_name,
                "tool_name": tool_name,
                "result": skip_msg,
            }
        return await self.main_agent_tool_manager.execute_tool_call(
            server_name=server_name,
            tool_name=tool_name,
            arguments=arguments,
        )

    async def _parallel_execute_regular_main_tools(
        self,
        tool_calls: List[Dict[str, Any]],
        turn_count: int,
    ) -> List[Dict[str, Any]]:
        """Run multiple regular (non-subagent) tool calls concurrently.

        Returns a list of dicts with keys: call, tool_result, duration_ms,
        tool_call_id, error (optional).
        """
        await self._emit_stage_heartbeat(
            "并行工具",
            turn=turn_count,
            detail=f"并行执行 {len(tool_calls)} 个工具调用",
            agent_name="main",
        )
        self.task_log.log_step(
            "info",
            f"Main Agent | Turn: {turn_count} | Parallel Tools",
            f"Executing {len(tool_calls)} tool calls in parallel",
        )

        prepared: List[Dict[str, Any]] = []
        for call in tool_calls:
            arguments = self.tool_executor.fix_tool_call_arguments(
                call["tool_name"], call["arguments"]
            )
            tool_call_id = await self.stream.tool_call(call["tool_name"], arguments)
            prepared.append(
                {
                    "call": call,
                    "arguments": arguments,
                    "tool_call_id": tool_call_id,
                }
            )

        async def _one(item: Dict[str, Any]) -> Dict[str, Any]:
            call = item["call"]
            start = time.time()
            try:
                tool_result = await self._execute_regular_tool_call(
                    call["server_name"],
                    call["tool_name"],
                    item["arguments"],
                    turn_count,
                )
                duration_ms = int((time.time() - start) * 1000)
                return {
                    "call": call,
                    "arguments": item["arguments"],
                    "tool_call_id": item["tool_call_id"],
                    "tool_result": tool_result,
                    "duration_ms": duration_ms,
                    "error": None,
                }
            except Exception as exc:  # noqa: BLE001 — surface per-tool failure
                duration_ms = int((time.time() - start) * 1000)
                return {
                    "call": call,
                    "arguments": item["arguments"],
                    "tool_call_id": item["tool_call_id"],
                    "tool_result": {
                        "server_name": call["server_name"],
                        "tool_name": call["tool_name"],
                        "error": str(exc),
                    },
                    "duration_ms": duration_ms,
                    "error": str(exc),
                }

        return list(await asyncio.gather(*[_one(item) for item in prepared]))

    def _verification_requirements_met(self) -> bool:
        if not self.verification_enabled:
            return True
        if self.verification_search_rounds < self.verification_min_search_rounds:
            return False
        # 工具侧 confidence 已达标：检索质量由 search 门控背书，不再追加检索
        if self.retrieval_confidence_passed:
            return True
        return (
            len(self.verification_high_conf_source_domains)
            >= self.verification_min_high_conf_sources
        )

    def _has_verification_progress_since_last_guidance(self) -> bool:
        if self.verification_guidance_attempts == 0:
            return True
        return (
            self.verification_search_rounds
            > self.verification_guidance_anchor_search_rounds
            or len(self.verification_high_conf_source_domains)
            > self.verification_guidance_anchor_high_conf_sources
        )

    def _mark_verification_guidance_issued(self) -> None:
        self.verification_guidance_anchor_search_rounds = (
            self.verification_search_rounds
        )
        self.verification_guidance_anchor_high_conf_sources = len(
            self.verification_high_conf_source_domains
        )

    async def _inject_lead_followup(
        self,
        message_history: List[Dict[str, Any]],
        turn_count: int,
        reason: str = "",
    ) -> bool:
        """Inject a lead follow-up user message if tracking is active.

        Returns True when a follow-up was injected (caller should ``continue``).
        """
        if not self.lead_tracker.enabled or not self.lead_tracker.trail:
            return False
        if not self.lead_tracker.trail.should_continue_following():
            return False

        # Round 6 early-stop: enough independent sources → skip extra clue chases
        if self._should_early_stop_clue_chase():
            self._note_deep_early_stop(turn_count, reason=reason or "lead-followup")
            return False

        top_leads = self.lead_tracker.trail.get_top_unfollowed_leads(k=1)
        if not top_leads:
            return False

        lead = top_leads[0]
        lead_followup_prompt = (
            f"继续深入研究以下线索：\n\n{lead.question}\n\n"
            f"请执行检索工具查找相关信息，并在发现后记录结果。"
            f"优先使用搜索摘要；仅对冲突关键 URL 做全文抓取。"
            f"若同一观点已有≥2个独立来源支撑且冲突点已可成文，可停止追线索并开始写报告。"
        )
        message_history.append({"role": "user", "content": lead_followup_prompt})
        self.lead_tracker.trail.mark_followed_up(
            lead, turn=turn_count, findings="正在追踪中..."
        )
        self.task_log.run_metrics.record_follow_up_search()
        self.task_log.log_step(
            "info",
            f"Main Agent | Turn: {turn_count} | Lead Follow-up",
            f"追踪线索（{reason}）：{lead.question[:100]}",
        )
        await self._emit_stage_heartbeat(
            "线索追踪",
            turn=turn_count,
            detail="追踪研究线索",
            agent_name="main",
        )
        return True

    def _should_issue_verification_guidance(
        self, turn_count: int, max_turns: int
    ) -> bool:
        if not self.verification_enabled:
            return False
        if self._verification_requirements_met():
            return False
        if (
            self.verification_guidance_attempts
            >= self.verification_max_guidance_attempts
        ):
            return False
        if turn_count >= max_turns:
            return False

        if self._has_verification_progress_since_last_guidance():
            self.verification_stagnant_guidance_attempts = 0
            return True

        self.verification_stagnant_guidance_attempts += 1
        self.task_log.log_step(
            "warning",
            "Main Agent | Verification Gate",
            "上一轮校验加压后没有新增检索证据，停止重复追加检索指令，直接进入收敛阶段。",
            metadata={
                "verification_search_rounds": self.verification_search_rounds,
                "verification_min_search_rounds": self.verification_min_search_rounds,
                "verification_high_conf_sources": len(
                    self.verification_high_conf_source_domains
                ),
                "verification_min_high_conf_sources": self.verification_min_high_conf_sources,
                "verification_guidance_attempts": self.verification_guidance_attempts,
                "verification_stagnant_guidance_attempts": self.verification_stagnant_guidance_attempts,
                "verification_max_stagnant_guidance_attempts": self.verification_max_stagnant_guidance_attempts,
            },
        )
        return (
            self.verification_stagnant_guidance_attempts
            < self.verification_max_stagnant_guidance_attempts
        )

    def _build_verification_followup_prompt(self, task_description: str) -> str:
        if not self.verification_enabled:
            return ""
        missing_rounds = max(
            0, self.verification_min_search_rounds - self.verification_search_rounds
        )
        missing_sources = max(
            0,
            self.verification_min_high_conf_sources
            - len(self.verification_high_conf_source_domains),
        )
        if missing_rounds == 0 and missing_sources == 0:
            return ""
        recommended_domains = ", ".join(sorted(self.verification_high_conf_domains)[:8])
        return (
            "继续执行联网核验，不要现在下结论。\n"
            f'当前任务："{task_description}"\n'
            f"尚缺检索轮次：{missing_rounds}，尚缺高置信来源：{missing_sources}。\n"
            "请执行下一轮检索并做口径统一，至少覆盖：\n"
            "1) 时间范围（明确起止范围与截至绝对日期）；\n"
            "2) 核心对象边界（明确统计或对比对象的纳入与排除规则）；\n"
            "3) 关键指标口径（明确单位、维度、是否合并统计）。\n"
            "4) 数字证据（至少补齐 3 条“数字+时间+来源”，如伤亡、损失、规模、时间点）。\n"
            "若当前检索仍缺数字，下一轮请使用数字定向关键词（示例：伤亡/死亡/受伤/损失/规模/截至日期）。\n"
            f"优先高置信来源域名：{recommended_domains}。\n"
            "如果仍有冲突，给出“数字区间+冲突原因+来源对应表”，不要给伪精确单值。\n"
            "禁止用“结果被省略/未展示”替代数字证据。"
        )

    def _build_verification_status_note(self) -> str:
        if not self.verification_enabled:
            return ""
        domains = (
            ", ".join(sorted(self.verification_high_conf_source_domains))
            if self.verification_high_conf_source_domains
            else "无"
        )
        confidence_note = (
            "- 检索质量门控：已通过（工具侧 confidence passed=yes，不再追加检索）\n"
            if self.retrieval_confidence_passed
            else ""
        )
        return (
            "交叉校验状态汇总：\n"
            f"- 检索轮次：{self.verification_search_rounds}/{self.verification_min_search_rounds}\n"
            f"- 高置信来源数：{len(self.verification_high_conf_source_domains)}/{self.verification_min_high_conf_sources}\n"
            f"- 已命中高置信域名：{domains}\n"
            f"{confidence_note}"
            "请在最终答案中明确写出时间锚点、关键指标口径、对象边界与分组规则，并处理数字冲突。\n"
            "最终答案必须包含“关键数字速览”：至少 3 条“数字+时间+来源”；若不足，明确缺失项与已尝试检索口径。"
        )

    def _save_message_history(
        self, system_prompt: str, message_history: List[Dict[str, Any]]
    ):
        """Save message history to task log."""
        self.task_log.main_agent_message_history = {
            "system_prompt": system_prompt,
            "message_history": message_history,
        }
        self.task_log.save()

    async def _handle_response_format_issues(
        self,
        assistant_response_text: str,
        message_history: List[Dict[str, Any]],
        turn_count: int,
        consecutive_rollbacks: int,
        total_attempts: int,
        max_attempts: int,
        agent_name: str,
    ) -> tuple:
        """
        Handle MCP tag format errors and refusal keywords.

        Args:
            assistant_response_text: The LLM response text
            message_history: Current message history
            turn_count: Current turn count
            consecutive_rollbacks: Current consecutive rollback count
            total_attempts: Total attempts made
            max_attempts: Maximum allowed attempts
            agent_name: Name of the agent for logging

        Returns:
            Tuple of (should_continue, should_break, turn_count, consecutive_rollbacks, message_history)
        """
        # Check for MCP tags in response (format error)
        if any(mcp_tag in assistant_response_text for mcp_tag in mcp_tags):
            if consecutive_rollbacks < self.MAX_CONSECUTIVE_ROLLBACKS - 1:
                turn_count -= 1
                consecutive_rollbacks += 1
                if message_history[-1]["role"] == "assistant":
                    message_history.pop()
                self.task_log.log_step(
                    "warning",
                    f"{agent_name} | Turn: {turn_count} | Rollback",
                    f"Tool call format incorrect - found MCP tags in response. "
                    f"Consecutive rollbacks: {consecutive_rollbacks}/{self.MAX_CONSECUTIVE_ROLLBACKS}, "
                    f"Total attempts: {total_attempts}/{max_attempts}",
                )
                return True, False, turn_count, consecutive_rollbacks, message_history
            else:
                self.task_log.log_step(
                    "warning",
                    f"{agent_name} | Turn: {turn_count} | End After Max Rollbacks",
                    f"Ending agent loop after {consecutive_rollbacks} consecutive MCP format errors",
                )
                return False, True, turn_count, consecutive_rollbacks, message_history

        # Check for refusal keywords
        if any(keyword in assistant_response_text for keyword in refusal_keywords):
            matched_keywords = [
                kw for kw in refusal_keywords if kw in assistant_response_text
            ]
            if consecutive_rollbacks < self.MAX_CONSECUTIVE_ROLLBACKS - 1:
                turn_count -= 1
                consecutive_rollbacks += 1
                if message_history[-1]["role"] == "assistant":
                    message_history.pop()
                self.task_log.log_step(
                    "warning",
                    f"{agent_name} | Turn: {turn_count} | Rollback",
                    f"LLM refused to answer - found refusal keywords: {matched_keywords}. "
                    f"Consecutive rollbacks: {consecutive_rollbacks}/{self.MAX_CONSECUTIVE_ROLLBACKS}, "
                    f"Total attempts: {total_attempts}/{max_attempts}",
                )
                return True, False, turn_count, consecutive_rollbacks, message_history
            else:
                self.task_log.log_step(
                    "warning",
                    f"{agent_name} | Turn: {turn_count} | End After Max Rollbacks",
                    f"Ending agent loop after {consecutive_rollbacks} consecutive refusals with keywords: {matched_keywords}",
                )
                return False, True, turn_count, consecutive_rollbacks, message_history

        # No format issues - normal end without tool calls
        return False, True, turn_count, consecutive_rollbacks, message_history

    async def _check_duplicate_query(
        self,
        tool_name: str,
        arguments: dict,
        cache_name: str,
        consecutive_rollbacks: int,
        turn_count: int,
        total_attempts: int,
        max_attempts: int,
        message_history: List[Dict[str, Any]],
        agent_name: str,
    ) -> tuple:
        """
        Check for duplicate queries and handle rollback if needed.

        Args:
            tool_name: Name of the tool being called
            arguments: Tool arguments
            cache_name: Name of the query cache to use
            consecutive_rollbacks: Current consecutive rollback count
            turn_count: Current turn count
            total_attempts: Total attempts made
            max_attempts: Maximum allowed attempts
            message_history: Current message history
            agent_name: Name of the agent for logging

        Returns:
            Tuple of (is_duplicate, should_rollback, turn_count, consecutive_rollbacks, message_history)
        """
        query_str = self.tool_executor.get_query_str_from_tool_call(
            tool_name, arguments
        )
        if not query_str:
            return False, False, turn_count, consecutive_rollbacks, message_history

        self.used_queries.setdefault(cache_name, defaultdict(int))
        count = self.used_queries[cache_name][query_str]

        if count > 0:
            if consecutive_rollbacks < self.MAX_CONSECUTIVE_ROLLBACKS - 1:
                message_history.pop()
                turn_count -= 1
                consecutive_rollbacks += 1
                self.task_log.log_step(
                    "warning",
                    f"{agent_name} | Turn: {turn_count} | Rollback",
                    f"Duplicate query detected - tool: {tool_name}, query: '{query_str}', "
                    f"previous count: {count}. Consecutive rollbacks: {consecutive_rollbacks}/"
                    f"{self.MAX_CONSECUTIVE_ROLLBACKS}, Total attempts: {total_attempts}/{max_attempts}",
                )
                return True, True, turn_count, consecutive_rollbacks, message_history
            else:
                if self.verification_enabled and agent_name == "Main Agent":
                    message_history.pop()
                    turn_count -= 1
                    consecutive_rollbacks += 1
                    self.task_log.log_step(
                        "warning",
                        f"{agent_name} | Turn: {turn_count} | End After Duplicate Rollbacks",
                        f"Verified 模式下重复查询次数过多，终止当前回合并进入总结阶段 - "
                        f"tool: {tool_name}, query: '{query_str}', previous count: {count}",
                    )
                    return (
                        True,
                        True,
                        turn_count,
                        consecutive_rollbacks,
                        message_history,
                    )
                self.task_log.log_step(
                    "warning",
                    f"{agent_name} | Turn: {turn_count} | Allow Duplicate",
                    f"Allowing duplicate query after {consecutive_rollbacks} rollbacks - "
                    f"tool: {tool_name}, query: '{query_str}', previous count: {count}",
                )

        return False, False, turn_count, consecutive_rollbacks, message_history

    async def _record_query(self, cache_name: str, tool_name: str, arguments: dict):
        """Record a successful query execution."""
        query_str = self.tool_executor.get_query_str_from_tool_call(
            tool_name, arguments
        )
        if query_str:
            self.used_queries.setdefault(cache_name, defaultdict(int))
            self.used_queries[cache_name][query_str] += 1

    async def run_sub_agent(
        self,
        sub_agent_name: str,
        task_description: str,
    ):
        """
        Run a sub-agent to handle a subtask.

        Args:
            sub_agent_name: Name of the sub-agent to run
            task_description: Description of the subtask

        Returns:
            The final answer text from the sub-agent
        """
        task_description += "\n\nPlease provide the answer and detailed supporting information of the subtask given to you."
        self.task_log.log_step(
            "info",
            f"{sub_agent_name} | Task Description",
            f"Subtask: {task_description}",
        )

        # Stream sub-agent start
        display_name = sub_agent_name.replace("agent-", "")
        sub_agent_id = await self.stream.start_agent(display_name)
        await self.stream.start_llm(display_name)

        # Start new sub-agent session
        self.task_log.start_sub_agent_session(sub_agent_name, task_description)

        # Initialize message history
        message_history = [{"role": "user", "content": task_description}]

        # Get sub-agent tool definitions
        if not self.sub_agent_tool_definitions:
            tool_definitions = await self._list_sub_agent_tools()
            tool_definitions = tool_definitions.get(sub_agent_name, {})
        else:
            tool_definitions = self.sub_agent_tool_definitions[sub_agent_name]

        if not tool_definitions:
            self.task_log.log_step(
                "warning",
                f"{sub_agent_name} | No Tools",
                "No tool definitions available.",
            )

        # Generate sub-agent system prompt
        system_prompt = self.llm_client.generate_agent_system_prompt(
            date=date.today(),
            mcp_servers=tool_definitions,
        ) + generate_agent_specific_system_prompt(agent_type=sub_agent_name)

        # Limit sub-agent turns
        if self.cfg.agent.sub_agents:
            max_turns = self.cfg.agent.sub_agents[sub_agent_name].max_turns
        else:
            max_turns = 0
        turn_count = 0
        total_attempts = 0
        max_attempts = max_turns + EXTRA_ATTEMPTS_BUFFER
        consecutive_rollbacks = 0
        consecutive_llm_failures = 0

        while turn_count < max_turns and total_attempts < max_attempts:
            turn_count += 1
            total_attempts += 1

            if consecutive_rollbacks >= self.MAX_CONSECUTIVE_ROLLBACKS:
                self.task_log.log_step(
                    "error",
                    f"{sub_agent_name} | Too Many Rollbacks",
                    f"Reached {consecutive_rollbacks} consecutive rollbacks, breaking loop.",
                )
                break

            self.task_log.save()

            # Reset 'last_call_tokens' using provider-correct key names
            # (Anthropic uses input/output, OpenAI uses prompt/completion).
            self.llm_client.reset_last_call_tokens()

            # LLM call using answer generator
            (
                assistant_response_text,
                should_break,
                tool_calls,
                message_history,
            ) = await self.answer_generator.handle_llm_call(
                system_prompt,
                message_history,
                tool_definitions,
                turn_count,
                f"{sub_agent_name} | Turn: {turn_count}",
                agent_type=sub_agent_name,
            )

            if should_break:
                self.task_log.log_step(
                    "info",
                    f"{sub_agent_name} | Turn: {turn_count} | LLM Call",
                    "should break is True, breaking the loop",
                )
                break

            llm_has_progress = bool(assistant_response_text) or bool(tool_calls)

            if llm_has_progress:
                if consecutive_llm_failures > 0:
                    self.task_log.log_step(
                        "info",
                        f"{sub_agent_name} | Turn: {turn_count} | LLM Recovery",
                        f"Recovered after {consecutive_llm_failures} consecutive empty/failed LLM responses.",
                    )
                consecutive_llm_failures = 0
                if assistant_response_text:
                    text_response = extract_llm_response_text(assistant_response_text)
                    if text_response:
                        await self.stream.tool_call(
                            "show_text", {"text": text_response}
                        )
            else:
                consecutive_llm_failures += 1
                self.task_log.log_step(
                    "warning",
                    f"{sub_agent_name} | Turn: {turn_count} | LLM Call",
                    "LLM call failed or empty response, retrying.",
                    metadata={
                        "consecutive_llm_failures": consecutive_llm_failures,
                        "max_consecutive_llm_failures": self.max_consecutive_llm_failures,
                    },
                )
                if consecutive_llm_failures >= self.max_consecutive_llm_failures:
                    # 尝试激活 failback 模型；成功则重置计数器继续，否则终止
                    if self.llm_client.activate_fallback():
                        consecutive_llm_failures = 0
                        await asyncio.sleep(self.llm_failure_sleep_seconds)
                        continue
                    self.task_log.log_step(
                        "error",
                        f"{sub_agent_name} | LLM Failure Guard",
                        f"Exceeded max consecutive LLM failures ({self.max_consecutive_llm_failures}), stopping sub-agent loop.",
                    )
                    break
                await asyncio.sleep(self.llm_failure_sleep_seconds)
                continue

            # Handle no tool calls case
            if not tool_calls:
                (
                    should_continue,
                    should_break_loop,
                    turn_count,
                    consecutive_rollbacks,
                    message_history,
                ) = await self._handle_response_format_issues(
                    assistant_response_text,
                    message_history,
                    turn_count,
                    consecutive_rollbacks,
                    total_attempts,
                    max_attempts,
                    sub_agent_name,
                )
                if should_continue:
                    continue
                if should_break_loop:
                    if not any(
                        mcp_tag in assistant_response_text for mcp_tag in mcp_tags
                    ) and not any(
                        keyword in assistant_response_text
                        for keyword in refusal_keywords
                    ):
                        self.task_log.log_step(
                            "info",
                            f"{sub_agent_name} | Turn: {turn_count} | LLM Call",
                            f"No tool calls found in {sub_agent_name}, ending on turn {turn_count}",
                        )
                    break

            # Execute tool calls
            tool_calls_data = []
            all_tool_results_content_with_id = []
            should_rollback_turn = False

            for call in tool_calls:
                server_name = call["server_name"]
                tool_name = call["tool_name"]
                arguments = call["arguments"]
                call_id = call["id"]

                # Fix common parameter name mistakes
                arguments = self.tool_executor.fix_tool_call_arguments(
                    tool_name, arguments
                )

                self.task_log.log_step(
                    "info",
                    f"{sub_agent_name} | Turn: {turn_count} | Tool Call",
                    f"Executing {tool_name} on {server_name}",
                )

                call_start_time = time.time()
                try:
                    # Check for duplicate query
                    cache_name = sub_agent_id + "_" + tool_name
                    (
                        is_duplicate,
                        should_rollback,
                        turn_count,
                        consecutive_rollbacks,
                        message_history,
                    ) = await self._check_duplicate_query(
                        tool_name,
                        arguments,
                        cache_name,
                        consecutive_rollbacks,
                        turn_count,
                        total_attempts,
                        max_attempts,
                        message_history,
                        sub_agent_name,
                    )
                    if should_rollback:
                        should_rollback_turn = True
                        break

                    # Send stream event
                    tool_call_id = await self.stream.tool_call(tool_name, arguments)

                    # Execute tool call
                    tool_result = await self.sub_agent_tool_managers[
                        sub_agent_name
                    ].execute_tool_call(server_name, tool_name, arguments)

                    # Update query count if successful
                    if "error" not in tool_result:
                        await self._record_query(cache_name, tool_name, arguments)

                    # Post-process result
                    tool_result = self.tool_executor.post_process_tool_call_result(
                        tool_name, tool_result
                    )
                    result = (
                        tool_result.get("result")
                        if tool_result.get("result")
                        else tool_result.get("error")
                    )

                    # Check for errors that should trigger rollback
                    if self.tool_executor.should_rollback_result(
                        tool_name, result, tool_result
                    ):
                        if consecutive_rollbacks < self.MAX_CONSECUTIVE_ROLLBACKS - 1:
                            message_history.pop()
                            turn_count -= 1
                            consecutive_rollbacks += 1
                            should_rollback_turn = True
                            self.task_log.log_step(
                                "warning",
                                f"{sub_agent_name} | Turn: {turn_count} | Rollback",
                                f"Tool result error - tool: {tool_name}, result: '{str(result)[:200]}'",
                            )
                            break

                    await self.stream.tool_call(
                        tool_name, {"result": result}, tool_call_id=tool_call_id
                    )
                    call_end_time = time.time()
                    call_duration_ms = int((call_end_time - call_start_time) * 1000)

                    self.task_log.log_step(
                        "info",
                        f"{sub_agent_name} | Turn: {turn_count} | Tool Call",
                        f"Tool {tool_name} completed in {call_duration_ms}ms",
                    )

                    tool_calls_data.append(
                        {
                            "server_name": server_name,
                            "tool_name": tool_name,
                            "arguments": arguments,
                            "result": tool_result,
                            "duration_ms": call_duration_ms,
                            "call_time": get_utc_plus_8_time(),
                        }
                    )

                except Exception as e:
                    call_end_time = time.time()
                    call_duration_ms = int((call_end_time - call_start_time) * 1000)

                    tool_calls_data.append(
                        {
                            "server_name": server_name,
                            "tool_name": tool_name,
                            "arguments": arguments,
                            "error": str(e),
                            "duration_ms": call_duration_ms,
                            "call_time": get_utc_plus_8_time(),
                        }
                    )
                    tool_result = {
                        "error": f"Tool call failed: {str(e)}",
                        "server_name": server_name,
                        "tool_name": tool_name,
                    }
                    self.task_log.log_step(
                        "error",
                        f"{sub_agent_name} | Turn: {turn_count} | Tool Call",
                        f"Tool {tool_name} failed to execute: {str(e)}",
                    )

                tool_result_for_llm = self.output_formatter.format_tool_result_for_user(
                    tool_result
                )
                all_tool_results_content_with_id.append((call_id, tool_result_for_llm))

            if should_rollback_turn:
                continue

            # Reset consecutive rollbacks on successful execution
            if consecutive_rollbacks > 0:
                self.task_log.log_step(
                    "info",
                    f"{sub_agent_name} | Turn: {turn_count} | Recovery",
                    f"Successfully recovered after {consecutive_rollbacks} consecutive rollbacks",
                )
            consecutive_rollbacks = 0

            # Update message history
            message_history = self.llm_client.update_message_history(
                message_history, all_tool_results_content_with_id
            )

            # Check context length
            temp_summary_prompt = generate_agent_summarize_prompt(
                task_description,
                agent_type=sub_agent_name,
            )

            pass_length_check, message_history = self.llm_client.ensure_summary_context(
                message_history, temp_summary_prompt
            )

            if not pass_length_check:
                turn_count = max_turns
                self.task_log.log_step(
                    "info",
                    f"{sub_agent_name} | Turn: {turn_count} | Context Limit Reached",
                    "Context limit reached, triggering summary",
                )
                break

        # Log loop end
        if turn_count >= max_turns:
            self.task_log.log_step(
                "info",
                f"{sub_agent_name} | Max Turns Reached / Context Limit Reached",
                f"Reached maximum turns ({max_turns}) or context limit reached",
            )
        else:
            self.task_log.log_step(
                "info",
                f"{sub_agent_name} | Main Loop Completed",
                f"Main loop completed after {turn_count} turns",
            )

        # Generate final summary
        self.task_log.log_step(
            "info",
            f"{sub_agent_name} | Final Summary",
            f"Generating {sub_agent_name} final summary",
        )

        summary_prompt = generate_agent_summarize_prompt(
            task_description,
            agent_type=sub_agent_name,
        )

        message_history.append({"role": "user", "content": summary_prompt})

        await self.stream.tool_call(
            "Partial Summary", {}, tool_call_id=str(uuid.uuid4())
        )

        # Generate final answer
        (
            final_answer_text,
            should_break,
            tool_calls_info,
            message_history,
        ) = await self.answer_generator.handle_llm_call(
            system_prompt,
            message_history,
            [],
            turn_count + 1,
            f"{sub_agent_name} | Final summary",
            agent_type="final_summary",
        )

        if final_answer_text:
            self.task_log.log_step(
                "info",
                f"{sub_agent_name} | Final Answer",
                "Final answer generated successfully",
            )
        else:
            final_answer_text = (
                f"No final answer generated by sub agent {sub_agent_name}."
            )
            self.task_log.log_step(
                "error",
                f"{sub_agent_name} | Final Answer",
                "Unable to generate final answer",
            )

        # Save session history
        self.task_log.sub_agent_message_history_sessions[
            self.task_log.current_sub_agent_session_id
        ] = {"system_prompt": system_prompt, "message_history": message_history}

        self.task_log.save()
        self.task_log.end_sub_agent_session(sub_agent_name)

        # Remove thinking content
        final_answer_text = final_answer_text.split("<think>")[-1].strip()
        final_answer_text = final_answer_text.split("</think>")[-1].strip()

        # Stream sub-agent end
        await self.stream.end_llm(display_name)
        await self.stream.end_agent(display_name, sub_agent_id)

        return final_answer_text

    async def run_main_agent(
        self,
        task_description,
        task_file_name=None,
        task_id="default_task",
        is_final_retry=False,
    ):
        """
        Execute the main end-to-end task.

        Args:
            task_description: Description of the task to execute
            task_file_name: Optional file associated with the task
            task_id: Unique identifier for the task

        Returns:
            Tuple of (final_summary, final_boxed_answer,
            failure_experience_summary, result_quality)
        """
        workflow_id = await self.stream.start_workflow(task_description)

        self.task_log.log_step("info", "Main Agent", f"Start task with id: {task_id}")
        self.task_log.log_step(
            "info", "Main Agent", f"Task description: {task_description}"
        )
        if task_file_name:
            self.task_log.log_step(
                "info", "Main Agent", f"Associated file: {task_file_name}"
            )

        # Process input
        initial_user_content, processed_task_desc = process_input(
            task_description, task_file_name
        )
        message_history = [{"role": "user", "content": initial_user_content}]

        # Record initial user input
        user_input = processed_task_desc
        if task_file_name:
            user_input += f"\n[Attached file: {task_file_name}]"

        # Get tool definitions
        if not self.tool_definitions:
            tool_definitions = (
                await self.main_agent_tool_manager.get_all_tool_definitions()
            )
            if self.cfg.agent.sub_agents is not None:
                tool_definitions += expose_sub_agents_as_tools(
                    self.cfg.agent.sub_agents
                )
        else:
            tool_definitions = self.tool_definitions

        if not tool_definitions:
            self.task_log.log_step(
                "warning",
                "Main Agent | Tool Definitions",
                "Warning: No tool definitions found. LLM cannot use any tools.",
            )

        # Generate system prompt
        system_prompt = self.llm_client.generate_agent_system_prompt(
            date=date.today(),
            mcp_servers=tool_definitions,
        ) + generate_agent_specific_system_prompt(agent_type="main")
        system_prompt = system_prompt.strip()

        # Main loop configuration
        max_turns = self.cfg.agent.main_agent.max_turns
        turn_count = 0
        total_attempts = 0
        max_attempts = max_turns + EXTRA_ATTEMPTS_BUFFER
        consecutive_rollbacks = 0
        consecutive_llm_failures = 0

        self.current_agent_id = await self.stream.start_agent("main")
        await self.stream.start_llm("main")
        await self._emit_stage_heartbeat(
            "推理",
            turn=0,
            detail="主流程已启动",
            agent_name="main",
        )

        # Initialize lead tracking for this task
        self.task_log.log_step(
            "info",
            "Orchestrator | Lead Tracking Config",
            f"enabled={self.lead_tracker.enabled}, "
            f"max_follow_ups={self.lead_tracker.max_follow_ups}",
        )
        self.lead_tracker.initialize(task_description)

        while turn_count < max_turns and total_attempts < max_attempts:
            turn_count += 1
            total_attempts += 1
            await self._emit_stage_heartbeat(
                "推理",
                turn=turn_count,
                detail="主模型推理中",
                agent_name="main",
            )

            if consecutive_rollbacks >= self.MAX_CONSECUTIVE_ROLLBACKS:
                self.task_log.log_step(
                    "error",
                    "Main Agent | Too Many Rollbacks",
                    f"Reached {consecutive_rollbacks} consecutive rollbacks, breaking loop.",
                )
                break

            self.task_log.save()

            # LLM call
            (
                assistant_response_text,
                should_break,
                tool_calls,
                message_history,
            ) = await self.answer_generator.handle_llm_call(
                system_prompt,
                message_history,
                tool_definitions,
                turn_count,
                f"Main agent | Turn: {turn_count}",
                agent_type="main",
            )

            # Process LLM response
            llm_has_progress = bool(assistant_response_text) or bool(tool_calls)

            if llm_has_progress:
                if consecutive_llm_failures > 0:
                    self.task_log.log_step(
                        "info",
                        f"Main Agent | Turn: {turn_count} | LLM Recovery",
                        f"Recovered after {consecutive_llm_failures} consecutive empty/failed LLM responses.",
                    )
                consecutive_llm_failures = 0
                if assistant_response_text:
                    text_response = extract_llm_response_text(assistant_response_text)
                    if text_response:
                        await self.stream.tool_call(
                            "show_text", {"text": text_response}
                        )

                    # Extract leads from assistant response (Phase 4)
                    if self.lead_tracker.enabled:
                        lead_questions = self.lead_tracker.process_turn_response(
                            assistant_response_text, turn=turn_count
                        )
                        if lead_questions:
                            self.task_log.log_step(
                                "info",
                                f"Main Agent | Turn: {turn_count} | Lead Tracking",
                                f"Extracted {len(lead_questions)} lead(s) for follow-up",
                            )

                # Extract boxed content
                if assistant_response_text:
                    boxed_content = self.output_formatter._extract_boxed_content(
                        assistant_response_text
                    )
                    if boxed_content:
                        self.intermediate_boxed_answers.append(boxed_content)

                if should_break and tool_calls:
                    # Before ending, allow lead follow-up injection when enabled
                    if await self._inject_lead_followup(
                        message_history, turn_count, reason="pre-break-with-tools"
                    ):
                        continue
                    self.task_log.log_step(
                        "info",
                        f"Main Agent | Turn: {turn_count} | LLM Call",
                        "should break is True 且存在工具调用，直接结束循环。",
                    )
                    break
            else:
                consecutive_llm_failures += 1
                self.task_log.log_step(
                    "warning",
                    f"Main Agent | Turn: {turn_count} | LLM Call",
                    "No valid response from LLM, retrying.",
                    metadata={
                        "consecutive_llm_failures": consecutive_llm_failures,
                        "max_consecutive_llm_failures": self.max_consecutive_llm_failures,
                    },
                )
                # Round 7: if early-stop nudge already fired, do not burn more
                # identical timeout turns — exit to final summary immediately.
                if self._deep_convergence_nudge_sent:
                    self.task_log.log_step(
                        "info",
                        f"Main Agent | Turn: {turn_count} | Deep Convergence Exit",
                        "LLM failed after convergence nudge; exiting to summary.",
                    )
                    break
                if consecutive_llm_failures >= self.max_consecutive_llm_failures:
                    # 尝试激活 failback 模型；成功则重置计数器继续，否则终止
                    if self.llm_client.activate_fallback():
                        consecutive_llm_failures = 0
                        await asyncio.sleep(self.llm_failure_sleep_seconds)
                        continue
                    self.task_log.log_step(
                        "error",
                        "Main Agent | LLM Failure Guard",
                        f"Exceeded max consecutive LLM failures ({self.max_consecutive_llm_failures}), ending main loop.",
                    )
                    break
                await asyncio.sleep(self.llm_failure_sleep_seconds)
                continue

            # Handle no tool calls case
            if not tool_calls:
                if self._should_issue_verification_guidance(turn_count, max_turns):
                    self.verification_guidance_attempts += 1
                    self._mark_verification_guidance_issued()
                    followup_prompt = self._build_verification_followup_prompt(
                        task_description
                    )
                    if followup_prompt:
                        await self._emit_stage_heartbeat(
                            "校验",
                            turn=turn_count,
                            detail="命中交叉校验门槛，追加检索指令",
                            agent_name="main",
                        )
                        message_history.append(
                            {"role": "user", "content": followup_prompt}
                        )
                        self.task_log.log_step(
                            "warning",
                            f"Main Agent | Turn: {turn_count} | Verification Gate",
                            "命中交叉校验门槛，追加检索指令并继续多轮检索。",
                            metadata={
                                "verification_search_rounds": self.verification_search_rounds,
                                "verification_min_search_rounds": self.verification_min_search_rounds,
                                "verification_high_conf_sources": len(
                                    self.verification_high_conf_source_domains
                                ),
                                "verification_min_high_conf_sources": self.verification_min_high_conf_sources,
                                "verification_guidance_attempts": self.verification_guidance_attempts,
                            },
                        )
                        continue

                # Check for lead-based follow-ups (Phase 4)
                if await self._inject_lead_followup(
                    message_history, turn_count, reason="no-tool-calls"
                ):
                    continue

                force_exit = await self._maybe_nudge_and_force_summary(
                    message_history, turn_count
                )
                if force_exit:
                    break
                if (
                    self._deep_convergence_nudge_sent
                    and self._should_force_summary_after_early_stop(turn_count)
                ):
                    # Nudge just appended; give the model one more turn.
                    continue

                if should_break:
                    self.task_log.log_step(
                        "info",
                        f"Main Agent | Turn: {turn_count} | LLM Call",
                        "should break is True，且已通过/跳过交叉校验门槛，结束循环。",
                    )
                    break

                (
                    should_continue,
                    should_break_loop,
                    turn_count,
                    consecutive_rollbacks,
                    message_history,
                ) = await self._handle_response_format_issues(
                    assistant_response_text,
                    message_history,
                    turn_count,
                    consecutive_rollbacks,
                    total_attempts,
                    max_attempts,
                    "Main Agent",
                )
                if should_continue:
                    continue
                if should_break_loop:
                    if not any(
                        mcp_tag in assistant_response_text for mcp_tag in mcp_tags
                    ) and not any(
                        keyword in assistant_response_text
                        for keyword in refusal_keywords
                    ):
                        self.task_log.log_step(
                            "info",
                            f"Main Agent | Turn: {turn_count} | LLM Call",
                            "LLM did not request tool usage, ending process.",
                        )
                    break

            # Execute tool calls
            tool_calls_data = []
            all_tool_results_content_with_id = []
            should_rollback_turn = False
            main_agent_last_call_tokens = self.llm_client.last_call_tokens

            # Round 6: parallel multi-search / multi-scrape in one turn when safe
            can_parallel = (
                self.parallel_tool_calls
                and len(tool_calls) > 1
                and all(
                    not str(c.get("server_name", "")).startswith("agent-")
                    for c in tool_calls
                )
            )
            if can_parallel:
                parallel_results = await self._parallel_execute_regular_main_tools(
                    tool_calls, turn_count
                )
                for item in parallel_results:
                    call = item["call"]
                    tool_name = call["tool_name"]
                    arguments = item["arguments"]
                    tool_result = item["tool_result"]
                    call_id = call["id"]
                    cache_name = "main_" + tool_name

                    if "error" not in tool_result:
                        await self._record_query(cache_name, tool_name, arguments)

                    tool_result = self.tool_executor.post_process_tool_call_result(
                        tool_name, tool_result
                    )
                    self._record_search_evidence(tool_name, tool_result)
                    self._record_scrape_metric(tool_name, tool_result, turn_count)

                    result = (
                        tool_result.get("result")
                        if tool_result.get("result")
                        else tool_result.get("error")
                    )
                    await self.stream.tool_call(
                        tool_name,
                        {"result": result},
                        tool_call_id=item["tool_call_id"],
                    )
                    tool_calls_data.append(
                        {
                            "server_name": call["server_name"],
                            "tool_name": tool_name,
                            "arguments": arguments,
                            "result": tool_result,
                            "duration_ms": item["duration_ms"],
                            "call_time": get_utc_plus_8_time(),
                            "parallel": True,
                        }
                    )
                    self.task_log.log_step(
                        "info",
                        f"Main Agent | Turn: {turn_count} | Tool Call",
                        f"Tool {tool_name} completed in {item['duration_ms']}ms (parallel)",
                    )
                    tool_result_for_llm = (
                        self.output_formatter.format_tool_result_for_user(tool_result)
                    )
                    all_tool_results_content_with_id.append(
                        (call_id, tool_result_for_llm)
                    )
            else:
                for call in tool_calls:
                    server_name = call["server_name"]
                    tool_name = call["tool_name"]
                    arguments = call["arguments"]
                    call_id = call["id"]
                    await self._emit_stage_heartbeat(
                        "检索" if tool_name in SEARCH_TOOL_NAMES else "工具调用",
                        turn=turn_count,
                        detail=f"执行工具 {tool_name}",
                        agent_name="main",
                        tool_name=tool_name,
                    )

                    # Fix common parameter name mistakes
                    arguments = self.tool_executor.fix_tool_call_arguments(
                        tool_name, arguments
                    )

                    call_start_time = time.time()
                    try:
                        if (
                            server_name.startswith("agent-")
                            and self.cfg.agent.sub_agents
                        ):
                            # Sub-agent execution
                            cache_name = "main_" + tool_name
                            (
                                is_duplicate,
                                should_rollback,
                                turn_count,
                                consecutive_rollbacks,
                                message_history,
                            ) = await self._check_duplicate_query(
                                tool_name,
                                arguments,
                                cache_name,
                                consecutive_rollbacks,
                                turn_count,
                                total_attempts,
                                max_attempts,
                                message_history,
                                "Main Agent",
                            )
                            if should_rollback:
                                should_rollback_turn = True
                                break

                            # Stream events
                            await self.stream.end_llm("main")
                            await self.stream.end_agent("main", self.current_agent_id)

                            # Execute sub-agent
                            sub_agent_result = await self.run_sub_agent(
                                server_name,
                                arguments["subtask"],
                            )

                            # Update query count
                            await self._record_query(cache_name, tool_name, arguments)

                            tool_result = {
                                "server_name": server_name,
                                "tool_name": tool_name,
                                "result": sub_agent_result,
                            }
                            self.current_agent_id = await self.stream.start_agent(
                                "main", display_name="Summarizing"
                            )
                            await self.stream.start_llm(
                                "main", display_name="Summarizing"
                            )
                        else:
                            # Regular tool execution
                            cache_name = "main_" + tool_name
                            (
                                is_duplicate,
                                should_rollback,
                                turn_count,
                                consecutive_rollbacks,
                                message_history,
                            ) = await self._check_duplicate_query(
                                tool_name,
                                arguments,
                                cache_name,
                                consecutive_rollbacks,
                                turn_count,
                                total_attempts,
                                max_attempts,
                                message_history,
                                "Main Agent",
                            )
                            if should_rollback:
                                should_rollback_turn = True
                                break

                            # Send stream event
                            tool_call_id = await self.stream.tool_call(
                                tool_name, arguments
                            )

                            tool_result = await self._execute_regular_tool_call(
                                server_name, tool_name, arguments, turn_count
                            )

                            # Update query count if successful
                            if "error" not in tool_result:
                                await self._record_query(
                                    cache_name, tool_name, arguments
                                )

                            # Post-process result
                            tool_result = (
                                self.tool_executor.post_process_tool_call_result(
                                    tool_name, tool_result
                                )
                            )
                            self._record_search_evidence(tool_name, tool_result)
                            self._record_scrape_metric(
                                tool_name, tool_result, turn_count
                            )

                            result = (
                                tool_result.get("result")
                                if tool_result.get("result")
                                else tool_result.get("error")
                            )

                            # Check for errors that should trigger rollback
                            if self.tool_executor.should_rollback_result(
                                tool_name, result, tool_result
                            ):
                                if (
                                    consecutive_rollbacks
                                    < self.MAX_CONSECUTIVE_ROLLBACKS - 1
                                ):
                                    message_history.pop()
                                    turn_count -= 1
                                    consecutive_rollbacks += 1
                                    should_rollback_turn = True
                                    self.task_log.log_step(
                                        "warning",
                                        f"Main Agent | Turn: {turn_count} | Rollback",
                                        f"Tool result error - tool: {tool_name}, result: '{str(result)[:200]}'",
                                    )
                                    break

                            await self.stream.tool_call(
                                tool_name, {"result": result}, tool_call_id=tool_call_id
                            )

                        call_end_time = time.time()
                        call_duration_ms = int((call_end_time - call_start_time) * 1000)

                        tool_calls_data.append(
                            {
                                "server_name": server_name,
                                "tool_name": tool_name,
                                "arguments": arguments,
                                "result": tool_result,
                                "duration_ms": call_duration_ms,
                                "call_time": get_utc_plus_8_time(),
                            }
                        )
                        self.task_log.log_step(
                            "info",
                            f"Main Agent | Turn: {turn_count} | Tool Call",
                            f"Tool {tool_name} completed in {call_duration_ms}ms",
                        )

                    except Exception as e:
                        call_end_time = time.time()
                        call_duration_ms = int((call_end_time - call_start_time) * 1000)

                        tool_calls_data.append(
                            {
                                "server_name": server_name,
                                "tool_name": tool_name,
                                "arguments": arguments,
                                "error": str(e),
                                "duration_ms": call_duration_ms,
                                "call_time": get_utc_plus_8_time(),
                            }
                        )
                        tool_result = {
                            "server_name": server_name,
                            "tool_name": tool_name,
                            "error": str(e),
                        }
                        self.task_log.log_step(
                            "error",
                            f"Main Agent | Turn: {turn_count} | Tool Call",
                            f"Tool {tool_name} failed to execute: {str(e)}",
                        )

                    # Format results for LLM
                    tool_result_for_llm = (
                        self.output_formatter.format_tool_result_for_user(tool_result)
                    )
                    all_tool_results_content_with_id.append(
                        (call_id, tool_result_for_llm)
                    )

            if should_rollback_turn:
                continue

            # Reset consecutive rollbacks on successful execution
            if consecutive_rollbacks > 0:
                self.task_log.log_step(
                    "info",
                    f"Main Agent | Turn: {turn_count} | Recovery",
                    f"Successfully recovered after {consecutive_rollbacks} consecutive rollbacks",
                )
            consecutive_rollbacks = 0

            # Update 'last_call_tokens'
            self.llm_client.last_call_tokens = main_agent_last_call_tokens

            # Update message history
            message_history = self.llm_client.update_message_history(
                message_history, all_tool_results_content_with_id
            )

            self.task_log.main_agent_message_history = {
                "system_prompt": system_prompt,
                "message_history": message_history,
            }
            self.task_log.save()

            # Round 7: after tools, exit once early-stop turn budget is spent
            force_exit = await self._maybe_nudge_and_force_summary(
                message_history, turn_count
            )
            if force_exit:
                break
            if (
                self._deep_convergence_nudge_sent
                and self._should_force_summary_after_early_stop(turn_count)
            ):
                # Nudge just appended after tools; next loop iteration runs LLM.
                continue

            # Check context length
            temp_summary_prompt = generate_agent_summarize_prompt(
                task_description,
                agent_type="main",
            )

            pass_length_check, message_history = self.llm_client.ensure_summary_context(
                message_history, temp_summary_prompt
            )

            if not pass_length_check:
                turn_count = max_turns
                self.task_log.log_step(
                    "warning",
                    f"Main Agent | Turn: {turn_count} | Context Limit Reached",
                    "Context limit reached, triggering summary",
                )
                break

        await self.stream.end_llm("main")
        await self.stream.end_agent("main", self.current_agent_id)

        # Determine if max turns was reached
        reached_max_turns = turn_count >= max_turns
        if reached_max_turns:
            self.task_log.log_step(
                "warning",
                "Main Agent | Max Turns Reached / Context Limit Reached",
                f"Reached maximum turns ({max_turns}) or context limit reached",
            )
        else:
            self.task_log.log_step(
                "info",
                "Main Agent | Main Loop Completed",
                f"Main loop completed after {turn_count} turns",
            )

        # Final summary
        await self._emit_stage_heartbeat(
            "总结",
            turn=turn_count,
            detail="进入最终总结阶段",
            agent_name="Final Summary",
        )
        self.task_log.log_step(
            "info", "Main Agent | Final Summary", "Generating final summary"
        )
        if self.verification_enabled:
            await self._emit_stage_heartbeat(
                "校验",
                turn=turn_count,
                detail="输出前交叉校验汇总中",
                agent_name="Final Summary",
            )
            verification_status_note = self._build_verification_status_note()
            if verification_status_note:
                message_history.append(
                    {"role": "user", "content": verification_status_note}
                )
                self.task_log.log_step(
                    "info",
                    "Main Agent | Verification Status",
                    verification_status_note,
                )

        self.current_agent_id = await self.stream.start_agent("Final Summary")
        await self.stream.start_llm("Final Summary")
        await self._emit_stage_heartbeat(
            "总结",
            turn=turn_count,
            detail="最终总结生成中",
            agent_name="Final Summary",
        )

        # Generate final answer using answer generator
        (
            final_summary,
            final_boxed_answer,
            failure_experience_summary,
            usage_log,
            message_history,
            result_quality,
        ) = await self.answer_generator.generate_and_finalize_answer(
            system_prompt=system_prompt,
            message_history=message_history,
            tool_definitions=tool_definitions,
            turn_count=turn_count,
            task_description=task_description,
            reached_max_turns=reached_max_turns,
            is_final_retry=is_final_retry,
            save_callback=self._save_message_history,
        )

        # Append lead trail section to final summary (Phase 4)
        if self.lead_tracker.enabled:
            lead_trail_section = self.lead_tracker.get_trail_section()
            if lead_trail_section:
                final_summary += "\n\n" + lead_trail_section
                self.task_log.log_step(
                    "info",
                    "Main Agent | Lead Trail",
                    f"Appended lead trail section ({len(lead_trail_section)} chars)",
                )
                # Log lead tracking stats
                lead_stats = self.lead_tracker.get_stats()
                if lead_stats.get("enabled"):
                    self.task_log.log_step(
                        "info",
                        "Main Agent | Lead Tracking Stats",
                        f"Total leads: {lead_stats['total_leads']}, "
                        f"Followed up: {lead_stats['followed_up']}, "
                        f"Unfollowed: {lead_stats['unfollowed']}",
                    )

        # User-facing cleanup: strip diagnostics, fix truncated refs, compact
        # pending leads, and add analysis/topology when useful.
        detail = getattr(self.answer_generator, "output_detail_level", "detailed")
        try:
            final_summary = prepare_user_facing_report(
                final_summary, detail_level=str(detail)
            )
        except Exception as exc:  # never block final emit on presentation
            self.task_log.log_step(
                "warning",
                "Main Agent | Report Presentation",
                f"prepare_user_facing_report failed: {exc}",
            )

        final_output_emitted = await self._emit_final_output(
            final_summary,
            result_quality,
        )
        # 只有真实答案才作为正文块展示：占位串（未输出 \boxed{}）会被前端改写成
        # 「未收敛」提示，与已交付的报告相互矛盾。
        has_real_answer = final_boxed_answer not in (None, FORMAT_ERROR_MESSAGE)
        if final_output_emitted and has_real_answer:
            await self.stream.tool_call("show_text", {"text": final_boxed_answer})
        await self.stream.end_llm("Final Summary")
        await self.stream.end_agent("Final Summary", self.current_agent_id)
        await self.stream.end_workflow(workflow_id)

        self.task_log.log_step(
            "info", "Main Agent | Usage Calculation", f"Usage log: {usage_log}"
        )

        self.task_log.log_step(
            "info",
            "Main Agent | Final boxed answer",
            f"Final boxed answer:\n\n{final_boxed_answer}",
        )

        self._log_final_outcome(task_id, result_quality)
        gc.collect()
        return (
            final_summary,
            final_boxed_answer,
            failure_experience_summary,
            result_quality,
        )
