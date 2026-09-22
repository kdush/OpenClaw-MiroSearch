#!/usr/bin/env python3
# Copyright (c) 2025 MiroMind
"""Live acceptance harness for research-quality cases A–E (Round 3+).

Loads Zhipu/OpenAI credentials from an external JSON file (never printed).
Runs the agent pipeline via Hydra compose + execute_task_pipeline.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from hydra import compose, initialize_config_dir
from hydra.core.global_hydra import GlobalHydra
from omegaconf import OmegaConf

# Ensure package imports resolve when run from apps/miroflow-agent
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.lead_tracker import resolve_lead_tracking_config  # noqa: E402
from src.core.pipeline import (  # noqa: E402
    create_pipeline_components,
    execute_task_pipeline,
)
from src.io.report_structure import ReportStructureValidator  # noqa: E402


def _evaluate_hotspot_gates(
    summary: str, detail_level: str, gates: str
) -> Dict[str, Any]:
    """Round-5 hard gates for hotspot cross-verification reports."""
    result: Dict[str, Any] = {
        "gates": gates,
        "detail_level": detail_level,
        "pass": False,
        "failures": [],
        "structure_valid": False,
        "structure_issues": [],
        "structure_metadata": {},
        "summary_chars": len(summary or ""),
        "has_confidence_marker": False,
        "has_conflicts_heading": False,
    }
    if not summary:
        result["failures"].append("empty_summary")
        return result

    level = (
        detail_level
        if detail_level in {"compact", "balanced", "detailed"}
        else "balanced"
    )
    if gates == "hotspot_compact":
        level = "compact"
    elif gates == "hotspot_detailed":
        level = "detailed"

    valid, issues, meta = ReportStructureValidator.validate_structure(summary, level)
    result["structure_valid"] = valid
    result["structure_issues"] = issues
    result["structure_metadata"] = {
        "found_sections": meta.get("found_sections"),
        "missing_required": meta.get("missing_required"),
        "section_lengths": meta.get("section_lengths"),
        "citation_count": meta.get("citation_count"),
        "has_conflicts_section": meta.get("has_conflicts_section"),
        "unsourced_number_warnings": meta.get("unsourced_number_warnings"),
    }
    result["has_conflicts_heading"] = bool(meta.get("has_conflicts_section"))

    conf = re.search(
        r"(置信度|confidence\s*[:=：]?\s*(high|medium|low|高|中|低))",
        summary,
        re.IGNORECASE,
    )
    result["has_confidence_marker"] = bool(conf)

    if gates == "hotspot_detailed":
        for miss in meta.get("missing_required") or []:
            result["failures"].append(f"missing_{miss}")
        if not result["has_conflicts_heading"]:
            result["failures"].append("missing_conflicts_section")
        if not result["has_confidence_marker"]:
            result["failures"].append("missing_confidence_marker")
        # Presentation UX (layout round)
        if re.search(
            r"(?i)Token Usage|Pricing is disabled|Total Input Tokens", summary
        ):
            result["failures"].append("diagnostic_noise_leaked")
        if re.search(r"(?m)https?://www\.?\s*$", summary):
            result["failures"].append("truncated_url_stub")
        if re.search(r"(?im)^\*\*状态\*\*:\s*pending\s*$", summary):
            result["failures"].append("raw_pending_lead_status")
        result["has_content_analysis"] = bool(
            re.search(r"(?i)内容分析|Content Analysis", summary)
        )
        result["has_mermaid_topology"] = ("```mermaid" in summary) or (
            "flowchart" in summary
        )
        if not result["has_content_analysis"]:
            result["failures"].append("missing_content_analysis")
        if not result["has_mermaid_topology"]:
            result["failures"].append("missing_mermaid_topology")
        if (meta.get("citation_count") or 0) < 1 and "http" not in summary.lower():
            # Accept Chinese named-source cues (据…报道 / 来源 / 官方声明)
            named = re.search(
                r"(来源|据.{0,24}(报道|称|声明|通报)|官方[声声]?明?|"
                r"路透|美联社|新华|半岛|法新)",
                summary,
            )
            if not named:
                result["failures"].append("no_source_attribution")
        # Soft: unsourced numbers — record but do not auto-fail alone
        result["unsourced_number_warnings"] = (
            meta.get("unsourced_number_warnings") or []
        )
    elif gates == "hotspot_compact":
        # Compact must stay shorter and still avoid invented certainty
        if result["summary_chars"] > 3500:
            result["failures"].append("compact_too_long")
        hedge = re.search(
            r"(不确定|未证实|无法确认|冲突|分歧|争议|allegedly|unconfirm|uncertain|conflict)",
            summary,
            re.IGNORECASE,
        )
        if not hedge:
            result["failures"].append("compact_missing_uncertainty_hedge")
    elif gates == "profile_compare":
        # Profile divergence judged across H5A/H5B pair; per-case just record structure
        result["note"] = "pair_compared_in_results_doc"

    result["pass"] = len(result["failures"]) == 0
    return result


CASES: Dict[str, Dict[str, Any]] = {
    "A": {
        "query": "What is the capital of France?",
        "effective_config": {
            "mode": "balanced",
            "search_profile": "parallel-trusted",
            "search_result_num": 10,
            "verification_min_search_rounds": 1,
            "output_detail_level": "compact",
            "research_intensity": "light",
        },
        "overrides": [
            "agent=mirothinker_v1.5_keep5_max200",
            "llm=glm-flash",
            "agent.main_agent.max_turns=5",
            "++agent.research_intensity=light",
            "++agent.enable_lead_tracking=false",
            "+output_formatter.detail_level=compact",
            "++agent.output_detail_level=compact",
        ],
    },
    "B": {
        "query": "What were the global CO2 emissions in 2023?",
        "effective_config": {
            "mode": "verified",
            "search_profile": "parallel-trusted",
            "search_result_num": 20,
            "verification_min_search_rounds": 3,
            "output_detail_level": "detailed",
            "research_intensity": "deep",
        },
        "overrides": [
            "agent=mirothinker_v1.5_keep5_max200",
            "llm=glm-flash",
            "agent.main_agent.max_turns=10",
            "++agent.research_intensity=deep",
            "+agent.main_agent.research_mode=verified",
            "+output_formatter.detail_level=detailed",
            "++agent.output_detail_level=detailed",
            "+agent.main_agent.verification_min_search_rounds=3",
            # Intentionally also set under main_agent to prove dual-path enable
            "+agent.main_agent.enable_lead_tracking=true",
        ],
    },
    "C": {
        "query": "How did the discovery of DNA structure lead to CRISPR technology?",
        "effective_config": {
            "mode": "research",
            "search_profile": "parallel-trusted",
            "search_result_num": 20,
            "verification_min_search_rounds": 2,
            "output_detail_level": "balanced",
            "research_intensity": "deep",
        },
        "overrides": [
            "agent=mirothinker_v1.5_keep5_max200",
            "llm=glm-flash",
            "agent.main_agent.max_turns=10",
            # Round-2-style main_agent path (must enable after fix)
            "+agent.main_agent.research_intensity=deep",
            "+agent.main_agent.enable_lead_tracking=true",
            "+agent.main_agent.max_lead_follow_ups=2",
            "+output_formatter.detail_level=balanced",
        ],
    },
    "D1": {
        "query": "What is the current population of Tokyo?",
        "effective_config": {
            "mode": "balanced",
            "search_profile": "searxng-only",
            "search_result_num": 15,
            "verification_min_search_rounds": 2,
            "output_detail_level": "balanced",
            "research_intensity": "standard",
        },
        "overrides": [
            "agent=mirothinker_v1.5_keep5_max200",
            "llm=glm-flash",
            "agent.main_agent.max_turns=6",
            "++agent.research_intensity=standard",
            "+output_formatter.detail_level=balanced",
        ],
        # Mirrors profile_resolver.build_search_env("searxng-only", 15)
        "env": {
            "SEARCH_PROVIDER_ORDER": "searxng",
            "SEARCH_PROVIDER_MODE": "fallback",
            "SEARCH_PROVIDER_ORDER_STRICT": "1",
            "SEARCH_RESULT_NUM": "15",
            # Point at local compose default; unreachable without docker (documented).
            "SEARXNG_BASE_URL": "http://127.0.0.1:27080",
        },
    },
    "D2": {
        "query": "What is the current population of Tokyo?",
        "effective_config": {
            "mode": "balanced",
            "search_profile": "parallel-trusted",
            "search_result_num": 15,
            "verification_min_search_rounds": 2,
            "output_detail_level": "balanced",
            "research_intensity": "standard",
        },
        "overrides": [
            "agent=mirothinker_v1.5_keep5_max200",
            "llm=glm-flash",
            "agent.main_agent.max_turns=6",
            "++agent.research_intensity=standard",
            "+output_formatter.detail_level=balanced",
        ],
        # Mirrors profile_resolver.build_search_env("parallel-trusted", 15)
        # Serper is last in trusted order; with only SERPER_API_KEY it is the
        # sole available provider and provides live search.
        "env": {
            "SEARCH_PROVIDER_ORDER": "serpapi,tavily,searxng,serper",
            "SEARCH_PROVIDER_MODE": "parallel_conf_fallback",
            "SEARCH_PROVIDER_TRUSTED_ORDER": "serpapi,tavily,searxng,serper",
            "SEARCH_PROVIDER_ORDER_STRICT": "0",
            "SEARCH_PROVIDER_PARALLEL_MAX_WAIT_MS": "4500",
            "SEARCH_PROVIDER_PARALLEL_MIN_SUCCESS": "1",
            "SEARCH_PROVIDER_FALLBACK_MAX_STEPS": "3",
            "SEARCH_CONFIDENCE_ENABLED": "1",
            "SEARCH_CONFIDENCE_SCORE_THRESHOLD": "0.62",
            "SEARCH_CONFIDENCE_MIN_RESULTS": "8",
            "SEARCH_CONFIDENCE_MIN_UNIQUE_DOMAINS": "5",
            "SEARCH_CONFIDENCE_MIN_PROVIDER_COVERAGE": "2",
            "SEARCH_CONFIDENCE_MIN_HIGH_CONF_HITS": "2",
            "SEARCH_RESULT_NUM": "15",
            # Leave SEARXNG unset so only Serper is available for this profile
            # (honest: searxng not reachable in this environment).
            "SEARXNG_BASE_URL": "",
        },
    },
    "E1": {
        "query": "Summarize the main causes of the 2008 financial crisis.",
        "effective_config": {
            "mode": "research",
            "search_profile": "parallel-trusted",
            "search_result_num": 15,
            "verification_min_search_rounds": 2,
            "output_detail_level": "compact",
            "research_intensity": "deep",
        },
        "overrides": [
            "agent=mirothinker_v1.5_keep5_max200",
            "llm=glm-flash",
            "agent.main_agent.max_turns=8",
            "++agent.research_intensity=deep",
            "+output_formatter.detail_level=compact",
            "++agent.output_detail_level=compact",
        ],
    },
    "E2": {
        "query": "Summarize the main causes of the 2008 financial crisis.",
        "effective_config": {
            "mode": "research",
            "search_profile": "parallel-trusted",
            "search_result_num": 15,
            "verification_min_search_rounds": 2,
            "output_detail_level": "detailed",
            "research_intensity": "deep",
        },
        "overrides": [
            "agent=mirothinker_v1.5_keep5_max200",
            "llm=glm-flash",
            "agent.main_agent.max_turns=10",
            "++agent.research_intensity=deep",
            "+output_formatter.detail_level=detailed",
            "++agent.output_detail_level=detailed",
        ],
    },
    # --- Round 5: hotspot-event cross-verification (anti-pollution) ---
    "H1": {
        "query": (
            "智谱 ZCode 被指静默上传代码仓库：上传了哪些内容？是否跨境？"
            "官方如何回应？各方说法有何冲突？"
        ),
        "effective_config": {
            "mode": "verified",
            "search_profile": "parallel-trusted",
            "search_result_num": 20,
            "verification_min_search_rounds": 3,
            "output_detail_level": "detailed",
            "research_intensity": "deep",
        },
        "overrides": [
            "agent=mirothinker_v1.5_keep5_max200",
            "llm=glm-flash",
            "agent.main_agent.max_turns=12",
            "++agent.research_intensity=deep",
            "++agent.research_report_mode=true",
            "+agent.main_agent.research_mode=verified",
            "+output_formatter.detail_level=detailed",
            "++agent.output_detail_level=detailed",
            "+agent.main_agent.verification_min_search_rounds=3",
            "+agent.main_agent.enable_lead_tracking=true",
            # Round 6 deep efficiency knobs
            "++agent.max_lead_follow_ups=2",
            "++agent.max_scrape_per_task=8",
            "++agent.deep_early_stop_on_agreement=true",
            "++agent.parallel_tool_calls=true",
            # Round 7 LLM-path wall-clock
            "++agent.deep_exit_on_early_stop=true",
            "++agent.deep_post_early_stop_turns=1",
            "++agent.max_final_answer_retries=1",
            # Round 8 oneshot final report / summary context
            "++agent.oneshot_final_report=true",
            "++agent.summary_keep_tool_result=2",
            "++agent.summary_max_tokens_cap=4096",
            "++llm.summary_max_tokens=4096",
        ],
        "env": {
            "SEARCH_PROVIDER_ORDER": "serpapi,tavily,searxng,serper",
            "SEARCH_PROVIDER_MODE": "parallel_conf_fallback",
            "SEARCH_PROVIDER_TRUSTED_ORDER": "serpapi,tavily,searxng,serper",
            "SEARCH_PROVIDER_ORDER_STRICT": "0",
            "SEARCH_RESULT_NUM": "20",
            "SEARXNG_BASE_URL": "",
        },
        "gates": "hotspot_detailed",
    },
    "H2": {
        "query": (
            "韩国 Re2O 抗衰针原料是否含逝者皮肤？求美者知情同意争议与中国合规现状如何？"
            "列出冲突点。"
        ),
        "effective_config": {
            "mode": "research",
            "search_profile": "parallel-trusted",
            "search_result_num": 20,
            "verification_min_search_rounds": 2,
            "output_detail_level": "detailed",
            "research_intensity": "deep",
        },
        "overrides": [
            "agent=mirothinker_v1.5_keep5_max200",
            "llm=glm-flash",
            "agent.main_agent.max_turns=12",
            "++agent.research_intensity=deep",
            "++agent.research_report_mode=true",
            "+output_formatter.detail_level=detailed",
            "++agent.output_detail_level=detailed",
            "+agent.main_agent.enable_lead_tracking=true",
        ],
        "env": {
            "SEARCH_PROVIDER_ORDER": "serpapi,tavily,searxng,serper",
            "SEARCH_PROVIDER_MODE": "parallel_conf_fallback",
            "SEARCH_PROVIDER_TRUSTED_ORDER": "serpapi,tavily,searxng,serper",
            "SEARCH_PROVIDER_ORDER_STRICT": "0",
            "SEARCH_RESULT_NUM": "20",
            "SEARXNG_BASE_URL": "",
        },
        "gates": "hotspot_detailed",
    },
    "H3": {
        "query": (
            "2026年9月胡塞武装称袭击沙特首都及延布能源设施："
            "胡塞宣称与沙特官方说法有何分歧？目前能确认什么、不能确认什么？"
        ),
        "effective_config": {
            "mode": "verified",
            "search_profile": "parallel-trusted",
            "search_result_num": 20,
            "verification_min_search_rounds": 3,
            "output_detail_level": "detailed",
            "research_intensity": "deep",
        },
        "overrides": [
            "agent=mirothinker_v1.5_keep5_max200",
            "llm=glm-flash",
            "agent.main_agent.max_turns=12",
            "++agent.research_intensity=deep",
            "++agent.research_report_mode=true",
            "+agent.main_agent.research_mode=verified",
            "+output_formatter.detail_level=detailed",
            "++agent.output_detail_level=detailed",
            "+agent.main_agent.verification_min_search_rounds=3",
            "+agent.main_agent.enable_lead_tracking=true",
            # Round 6 deep efficiency knobs
            "++agent.max_lead_follow_ups=2",
            "++agent.max_scrape_per_task=8",
            "++agent.deep_early_stop_on_agreement=true",
            "++agent.parallel_tool_calls=true",
            # Round 7 LLM-path wall-clock
            "++agent.deep_exit_on_early_stop=true",
            "++agent.deep_post_early_stop_turns=1",
            "++agent.max_final_answer_retries=1",
            # Round 8 oneshot final report / summary context
            "++agent.oneshot_final_report=true",
            "++agent.summary_keep_tool_result=2",
            "++agent.summary_max_tokens_cap=4096",
            "++llm.summary_max_tokens=4096",
        ],
        "env": {
            "SEARCH_PROVIDER_ORDER": "serpapi,tavily,searxng,serper",
            "SEARCH_PROVIDER_MODE": "parallel_conf_fallback",
            "SEARCH_PROVIDER_TRUSTED_ORDER": "serpapi,tavily,searxng,serper",
            "SEARCH_PROVIDER_ORDER_STRICT": "0",
            "SEARCH_RESULT_NUM": "20",
            "SEARXNG_BASE_URL": "",
        },
        "gates": "hotspot_detailed",
    },
    "H4": {
        "query": (
            "智谱 ZCode 被指静默上传代码仓库：上传了哪些内容？是否跨境？"
            "官方如何回应？各方说法有何冲突？"
        ),
        "effective_config": {
            "mode": "balanced",
            "search_profile": "parallel-trusted",
            "search_result_num": 8,
            "verification_min_search_rounds": 1,
            "output_detail_level": "compact",
            "research_intensity": "light",
        },
        "overrides": [
            "agent=mirothinker_v1.5_keep5_max200",
            "llm=glm-flash",
            "agent.main_agent.max_turns=5",
            "++agent.research_intensity=light",
            "++agent.enable_lead_tracking=false",
            "+output_formatter.detail_level=compact",
            "++agent.output_detail_level=compact",
        ],
        "env": {
            "SEARCH_PROVIDER_ORDER": "serpapi,tavily,searxng,serper",
            "SEARCH_PROVIDER_MODE": "parallel_conf_fallback",
            "SEARCH_PROVIDER_TRUSTED_ORDER": "serpapi,tavily,searxng,serper",
            "SEARCH_PROVIDER_ORDER_STRICT": "0",
            "SEARCH_RESULT_NUM": "8",
            "SEARXNG_BASE_URL": "",
        },
        "gates": "hotspot_compact",
    },
    "H5A": {
        "query": (
            "2026年9月胡塞武装称袭击沙特首都及延布能源设施："
            "胡塞宣称与沙特官方说法有何分歧？目前能确认什么、不能确认什么？"
        ),
        "effective_config": {
            "mode": "verified",
            "search_profile": "searxng-only",
            "search_result_num": 15,
            "verification_min_search_rounds": 2,
            "output_detail_level": "detailed",
            "research_intensity": "deep",
        },
        "overrides": [
            "agent=mirothinker_v1.5_keep5_max200",
            "llm=glm-flash",
            "agent.main_agent.max_turns=8",
            "++agent.research_intensity=deep",
            "++agent.research_report_mode=true",
            "+agent.main_agent.research_mode=verified",
            "+output_formatter.detail_level=detailed",
            "++agent.output_detail_level=detailed",
        ],
        "env": {
            "SEARCH_PROVIDER_ORDER": "searxng",
            "SEARCH_PROVIDER_MODE": "fallback",
            "SEARCH_PROVIDER_ORDER_STRICT": "1",
            "SEARCH_RESULT_NUM": "15",
            "SEARXNG_BASE_URL": "http://127.0.0.1:27080",
        },
        "gates": "profile_compare",
    },
    "H5B": {
        "query": (
            "2026年9月胡塞武装称袭击沙特首都及延布能源设施："
            "胡塞宣称与沙特官方说法有何分歧？目前能确认什么、不能确认什么？"
        ),
        "effective_config": {
            "mode": "verified",
            "search_profile": "parallel-trusted",
            "search_result_num": 15,
            "verification_min_search_rounds": 2,
            "output_detail_level": "detailed",
            "research_intensity": "deep",
        },
        "overrides": [
            "agent=mirothinker_v1.5_keep5_max200",
            "llm=glm-flash",
            "agent.main_agent.max_turns=8",
            "++agent.research_intensity=deep",
            "++agent.research_report_mode=true",
            "+agent.main_agent.research_mode=verified",
            "+output_formatter.detail_level=detailed",
            "++agent.output_detail_level=detailed",
        ],
        "env": {
            "SEARCH_PROVIDER_ORDER": "serpapi,tavily,searxng,serper",
            "SEARCH_PROVIDER_MODE": "parallel_conf_fallback",
            "SEARCH_PROVIDER_TRUSTED_ORDER": "serpapi,tavily,searxng,serper",
            "SEARCH_PROVIDER_ORDER_STRICT": "0",
            "SEARCH_RESULT_NUM": "15",
            "SEARXNG_BASE_URL": "",
        },
        "gates": "profile_compare",
    },
    # --- Layout live topics: celebrity / opinion / tech rumor ---
    "L1": {
        "query": (
            "请锁定一条近两周娱乐圈多源冲突明显的明星恋情/分手/复合八卦热搜："
            "营销号与主流娱乐媒体说法有何冲突？当事人或工作室如何回应？"
            "目前能确认什么、不能确认什么？请标明置信度，并做内容分析与关系拓扑。"
        ),
        "effective_config": {
            "mode": "verified",
            "search_profile": "parallel-trusted",
            "search_result_num": 20,
            "verification_min_search_rounds": 3,
            "output_detail_level": "detailed",
            "research_intensity": "deep",
        },
        "overrides": [
            "agent=mirothinker_v1.5_keep5_max200",
            "llm=glm-flash",
            "agent.main_agent.max_turns=12",
            "++agent.research_intensity=deep",
            "++agent.research_report_mode=true",
            "+agent.main_agent.research_mode=verified",
            "+output_formatter.detail_level=detailed",
            "++agent.output_detail_level=detailed",
            "+agent.main_agent.verification_min_search_rounds=3",
            "+agent.main_agent.enable_lead_tracking=true",
            "++agent.max_lead_follow_ups=2",
            "++agent.max_scrape_per_task=8",
            "++agent.deep_early_stop_on_agreement=true",
            "++agent.parallel_tool_calls=true",
            "++agent.deep_exit_on_early_stop=true",
            "++agent.deep_post_early_stop_turns=1",
            "++agent.max_final_answer_retries=1",
            "++agent.oneshot_final_report=true",
            "++agent.summary_keep_tool_result=2",
            "++agent.summary_max_tokens_cap=4096",
            "++llm.summary_max_tokens=4096",
        ],
        "env": {
            "SEARCH_PROVIDER_ORDER": "serpapi,tavily,searxng,serper",
            "SEARCH_PROVIDER_MODE": "parallel_conf_fallback",
            "SEARCH_PROVIDER_TRUSTED_ORDER": "serpapi,tavily,searxng,serper",
            "SEARCH_PROVIDER_ORDER_STRICT": "0",
            "SEARCH_RESULT_NUM": "20",
            "SEARXNG_BASE_URL": "",
        },
        "gates": "hotspot_detailed",
    },
    "L2": {
        "query": (
            "请锁定一则近两周有明显对立叙事的中文网络舆情事件（非娱乐八卦）："
            "官方通报或主流媒体与自媒体/网民转载说法的冲突点是什么？"
            "哪些数字或因果尚未核实？请标明置信度，并输出内容分析与 Mermaid 关系拓扑。"
        ),
        "effective_config": {
            "mode": "verified",
            "search_profile": "parallel-trusted",
            "search_result_num": 20,
            "verification_min_search_rounds": 3,
            "output_detail_level": "detailed",
            "research_intensity": "deep",
        },
        "overrides": [
            "agent=mirothinker_v1.5_keep5_max200",
            "llm=glm-flash",
            "agent.main_agent.max_turns=12",
            "++agent.research_intensity=deep",
            "++agent.research_report_mode=true",
            "+agent.main_agent.research_mode=verified",
            "+output_formatter.detail_level=detailed",
            "++agent.output_detail_level=detailed",
            "+agent.main_agent.verification_min_search_rounds=3",
            "+agent.main_agent.enable_lead_tracking=true",
            "++agent.max_lead_follow_ups=2",
            "++agent.max_scrape_per_task=8",
            "++agent.deep_early_stop_on_agreement=true",
            "++agent.parallel_tool_calls=true",
            "++agent.deep_exit_on_early_stop=true",
            "++agent.deep_post_early_stop_turns=1",
            "++agent.max_final_answer_retries=1",
            "++agent.oneshot_final_report=true",
            "++agent.summary_keep_tool_result=2",
            "++agent.summary_max_tokens_cap=4096",
            "++llm.summary_max_tokens=4096",
        ],
        "env": {
            "SEARCH_PROVIDER_ORDER": "serpapi,tavily,searxng,serper",
            "SEARCH_PROVIDER_MODE": "parallel_conf_fallback",
            "SEARCH_PROVIDER_TRUSTED_ORDER": "serpapi,tavily,searxng,serper",
            "SEARCH_PROVIDER_ORDER_STRICT": "0",
            "SEARCH_RESULT_NUM": "20",
            "SEARXNG_BASE_URL": "",
        },
        "gates": "hotspot_detailed",
    },
    "L3": {
        "query": (
            "请锁定一条近期科技/AI 领域流传较广的谣言或夸大宣称（产品造假、数据注水或政策误读均可）："
            "谣言版本、官方/权威媒体澄清、第三方测评之间有何冲突？"
            "证据链到哪一步仍断？请给置信度，并附内容分析与关系拓扑。"
        ),
        "effective_config": {
            "mode": "verified",
            "search_profile": "parallel-trusted",
            "search_result_num": 20,
            "verification_min_search_rounds": 3,
            "output_detail_level": "detailed",
            "research_intensity": "deep",
        },
        "overrides": [
            "agent=mirothinker_v1.5_keep5_max200",
            "llm=glm-flash",
            "agent.main_agent.max_turns=12",
            "++agent.research_intensity=deep",
            "++agent.research_report_mode=true",
            "+agent.main_agent.research_mode=verified",
            "+output_formatter.detail_level=detailed",
            "++agent.output_detail_level=detailed",
            "+agent.main_agent.verification_min_search_rounds=3",
            "+agent.main_agent.enable_lead_tracking=true",
            "++agent.max_lead_follow_ups=2",
            "++agent.max_scrape_per_task=8",
            "++agent.deep_early_stop_on_agreement=true",
            "++agent.parallel_tool_calls=true",
            "++agent.deep_exit_on_early_stop=true",
            "++agent.deep_post_early_stop_turns=1",
            "++agent.max_final_answer_retries=1",
            "++agent.oneshot_final_report=true",
            "++agent.summary_keep_tool_result=2",
            "++agent.summary_max_tokens_cap=4096",
            "++llm.summary_max_tokens=4096",
        ],
        "env": {
            "SEARCH_PROVIDER_ORDER": "serpapi,tavily,searxng,serper",
            "SEARCH_PROVIDER_MODE": "parallel_conf_fallback",
            "SEARCH_PROVIDER_TRUSTED_ORDER": "serpapi,tavily,searxng,serper",
            "SEARCH_PROVIDER_ORDER_STRICT": "0",
            "SEARCH_RESULT_NUM": "20",
            "SEARXNG_BASE_URL": "",
        },
        "gates": "hotspot_detailed",
    },
}


def _load_credentials(path: Path) -> None:
    data = json.loads(path.read_text())
    # Map into env expected by OpenAI-compatible client / Hydra overrides / search MCP
    mapping = {
        "OPENAI_API_KEY": data.get("OPENAI_API_KEY") or data.get("API_KEY"),
        "OPENAI_BASE_URL": data.get("OPENAI_BASE_URL") or data.get("BASE_URL"),
        "API_KEY": data.get("API_KEY") or data.get("OPENAI_API_KEY"),
        "BASE_URL": data.get("BASE_URL") or data.get("OPENAI_BASE_URL"),
        "DEFAULT_MODEL_NAME": data.get("DEFAULT_MODEL_NAME", "glm-5.3-flash"),
        "DEFAULT_LLM_PROVIDER": data.get("DEFAULT_LLM_PROVIDER", "openai"),
        "MODEL_TOOL_NAME": data.get("MODEL_TOOL_NAME"),
        "MODEL_FAST_NAME": data.get("MODEL_FAST_NAME"),
        "MODEL_THINKING_NAME": data.get("MODEL_THINKING_NAME"),
        "MODEL_SUMMARY_NAME": data.get("MODEL_SUMMARY_NAME"),
        # Search keys (Round 4 Case D) — never print
        "SERPER_API_KEY": data.get("SERPER_API_KEY"),
        "SERPER_API_KEYS": data.get("SERPER_API_KEYS"),
        "SERPAPI_API_KEY": data.get("SERPAPI_API_KEY"),
        "TAVILY_API_KEY": data.get("TAVILY_API_KEY"),
        "SEARXNG_BASE_URL": data.get("SEARXNG_BASE_URL"),
    }
    for k, v in mapping.items():
        if v is not None and str(v) != "":
            os.environ[k] = str(v)
    # Never print secrets
    has_serper = bool(
        os.environ.get("SERPER_API_KEY") or os.environ.get("SERPER_API_KEYS")
    )
    print(
        f"[creds] serper_loaded={has_serper} model={os.environ.get('DEFAULT_MODEL_NAME')}"
    )


def _compose_cfg(overrides: List[str]):
    conf_dir = str((ROOT / "conf").resolve())
    GlobalHydra.instance().clear()
    with initialize_config_dir(config_dir=conf_dir, version_base=None):
        cfg = compose(config_name="config", overrides=overrides)
    # Inject credentials from env into llm config (no secrets in yaml)
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("API_KEY") or ""
    base_url = (
        os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("BASE_URL")
        or cfg.llm.get("base_url")
    )
    if api_key:
        OmegaConf.update(cfg, "llm.api_key", api_key, merge=True)
    if base_url:
        OmegaConf.update(cfg, "llm.base_url", base_url, merge=True)
    OmegaConf.update(cfg, "llm.model_name", "glm-5.3-flash", merge=True)
    OmegaConf.update(cfg, "llm.provider", "openai", merge=True)
    return cfg


def _extract_route_traces_from_log(log_path: Optional[str]) -> List[Dict[str, Any]]:
    """Pull search route_trace / provider fields from tool call logs."""
    if not log_path:
        return []
    p = Path(log_path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError:
        return []
    traces: List[Dict[str, Any]] = []
    steps = data.get("step_logs") or data.get("steps") or data.get("log_steps") or []
    for step in steps:
        if not isinstance(step, dict):
            continue
        tool_calls = step.get("tool_calls") or step.get("tools") or []
        if isinstance(step.get("tool_call"), dict):
            tool_calls = [step["tool_call"]]
        if not isinstance(tool_calls, list):
            continue
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            name = str(tc.get("tool_name") or tc.get("name") or "")
            if "search" not in name.lower() and "google" not in name.lower():
                continue
            result = tc.get("result")
            parsed: Any = result
            if isinstance(result, str):
                try:
                    parsed = json.loads(result)
                except json.JSONDecodeError:
                    continue
            if not isinstance(parsed, dict):
                continue
            params = parsed.get("searchParameters") or {}
            traces.append(
                {
                    "tool_name": name,
                    "provider": parsed.get("provider") or params.get("provider"),
                    "provider_mode": params.get("provider_mode"),
                    "provider_order": params.get("provider_order"),
                    "route_trace": parsed.get("route_trace")
                    or params.get("route_trace"),
                    "success": parsed.get("success"),
                    "organic_count": len(parsed.get("organic") or []),
                    "error": (parsed.get("error") or "")[:300],
                }
            )
    return traces


def _read_raw_summary_from_log(log_path: Optional[str]) -> str:
    """Pre-presentation final report text logged by answer_generator.

    Structure gates must judge what the agent actually wrote: the returned
    ``final_summary`` has already been reshaped by prepare_user_facing_report,
    which renames headings and moves diagnostics/leads into folds.
    """
    if not log_path:
        return ""
    p = Path(log_path)
    if not p.exists():
        return ""
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError:
        return ""
    steps = data.get("step_logs") or data.get("steps") or data.get("log_steps") or []
    for step in reversed(steps):
        if not isinstance(step, dict):
            continue
        name = str(step.get("step_name") or step.get("name") or "")
        if "Final Answer" not in name:
            continue
        text = str(step.get("message") or step.get("content") or "")
        if "Final answer content:" not in text:
            continue
        raw = text.split("Final answer content:", 1)[1].strip()
        if raw:
            return raw
    return ""


def _read_metrics_from_log(log_path: Optional[str]) -> Dict[str, Any]:
    if not log_path:
        return {}
    p = Path(log_path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}
    metrics = data.get("run_metrics") or data.get("metrics") or {}
    # Also scan steps for lead tracking evidence
    steps = data.get("step_logs") or data.get("steps") or data.get("log_steps") or []
    lead_steps = [
        s
        for s in steps
        if isinstance(s, dict)
        and "lead" in str(s.get("step_name", s.get("name", ""))).lower()
    ]
    return {
        "raw": metrics if isinstance(metrics, dict) else {},
        "lead_steps_count": len(lead_steps),
        "lead_steps_sample": lead_steps[:5],
        "status": data.get("status"),
        "final_boxed_answer": data.get("final_boxed_answer", ""),
        "route_traces": _extract_route_traces_from_log(log_path),
    }


async def run_case(case_id: str, case: Dict[str, Any], out_dir: Path) -> Dict[str, Any]:
    env_patch = case.get("env") or {}
    old_env = {k: os.environ.get(k) for k in env_patch}
    try:
        for k, v in env_patch.items():
            os.environ[k] = str(v)

        cfg = _compose_cfg(list(case["overrides"]))
        enabled, max_fu = resolve_lead_tracking_config(cfg)
        print(
            f"[{case_id}] tracker.enabled={enabled} max_follow_ups={max_fu} "
            f"intensity={case['effective_config'].get('research_intensity')}"
        )

        main_tm, sub_tms, formatter = create_pipeline_components(cfg)
        task_id = f"acceptance_{case_id.lower()}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        started = datetime.now(timezone.utc)
        result = await execute_task_pipeline(
            cfg=cfg,
            task_id=task_id,
            task_file_name="",
            task_description=case["query"],
            main_agent_tool_manager=main_tm,
            sub_agent_tool_managers=sub_tms,
            output_formatter=formatter,
            log_dir=str(out_dir / "logs"),
            effective_config=case["effective_config"],
        )
        ended = datetime.now(timezone.utc)
        log_path = result.get("log_file_path")
        metrics_info = _read_metrics_from_log(log_path)
        summary = result.get("final_summary") or ""
        raw_summary = _read_raw_summary_from_log(log_path) or summary
        boxed = result.get("final_boxed_answer") or ""
        has_trail = ("Lead Trail" in raw_summary) or ("线索追踪" in raw_summary)
        gates_kind = case.get("gates") or ""
        gate_eval = (
            _evaluate_hotspot_gates(
                raw_summary,
                case["effective_config"].get("output_detail_level", "balanced"),
                gates_kind,
            )
            if gates_kind
            else None
        )

        # Persist full summary for Round-5 excerpting (separate from JSON record size)
        summary_path = out_dir / f"case_{case_id}_summary.md"
        if summary:
            summary_path.write_text(summary, encoding="utf-8")

        record = {
            "case_id": case_id,
            "query": case["query"],
            "overrides": case["overrides"],
            "env_patch": env_patch,
            "effective_config": case["effective_config"],
            "tracker_enabled_at_compose": enabled,
            "tracker_max_follow_ups": max_fu,
            "status": result.get("status"),
            "duration_seconds": (ended - started).total_seconds(),
            "final_boxed_answer": boxed,
            "final_summary_excerpt": summary[:16000],
            "final_summary_chars": len(summary),
            "summary_file": str(summary_path) if summary else None,
            "has_lead_trail": has_trail,
            "log_file": log_path,
            "metrics": metrics_info.get("raw", {}),
            "route_traces": metrics_info.get("route_traces", []),
            "lead_steps_count": metrics_info.get("lead_steps_count", 0),
            "gate_evaluation": gate_eval,
            "error": result.get("error"),
        }
        (out_dir / f"case_{case_id}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=str)
        )
        gate_note = ""
        if gate_eval is not None:
            gate_note = (
                f" gates_pass={gate_eval.get('pass')} "
                f"failures={gate_eval.get('failures')}"
            )
        print(
            f"[{case_id}] status={record['status']} "
            f"trail={has_trail} "
            f"metrics={record['metrics']} "
            f"duration={record['duration_seconds']:.1f}s"
            f"{gate_note}"
        )
        return record
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


async def main_async(args: argparse.Namespace) -> int:
    creds = Path(args.credentials)
    if not creds.exists():
        print(f"Credentials file not found: {creds}", file=sys.stderr)
        return 2
    _load_credentials(creds)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "logs").mkdir(parents=True, exist_ok=True)

    case_ids = [c.strip().upper() for c in args.cases.split(",") if c.strip()]
    # Normalize aliases
    expanded: List[str] = []
    for c in case_ids:
        if c == "D":
            expanded.extend(["D1", "D2"])
        elif c == "E":
            expanded.extend(["E1", "E2"])
        elif c == "H":
            expanded.extend(["H1", "H2", "H3", "H4", "H5A", "H5B"])
        elif c == "H5":
            expanded.extend(["H5A", "H5B"])
        elif c == "L":
            expanded.extend(["L1", "L2", "L3"])
        else:
            expanded.append(c)

    results: Dict[str, Any] = {}
    for case_id in expanded:
        if case_id not in CASES:
            print(f"Unknown case {case_id}", file=sys.stderr)
            return 2
        results[case_id] = await run_case(case_id, CASES[case_id], out_dir)

    # H5 pair divergence note
    if "H5A" in results and "H5B" in results:
        a_hits = (results["H5A"].get("metrics") or {}).get("search_provider_hits") or {}
        b_hits = (results["H5B"].get("metrics") or {}).get("search_provider_hits") or {}
        a_prof = (results["H5A"].get("effective_config") or {}).get("search_profile")
        b_prof = (results["H5B"].get("effective_config") or {}).get("search_profile")
        results["_h5_compare"] = {
            "profiles_differ": a_prof != b_prof,
            "h5a_profile": a_prof,
            "h5b_profile": b_prof,
            "h5a_provider_hits": a_hits,
            "h5b_provider_hits": b_hits,
            "provider_sets_differ": set(a_hits) != set(b_hits),
            "searxng_note": (
                "SearXNG expected unreachable in this VM without docker; "
                "document honestly if H5A hits only failed searxng attempts."
            ),
        }

    combined = out_dir / "acceptance_results.json"
    combined.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    print(f"Wrote {combined}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Live acceptance A–E / H harness")
    parser.add_argument(
        "--credentials",
        default=str(
            Path(
                "/home/ubuntu/.cursor/projects/workspace/uploads/"
                "round5_credentials_81d8.json"
            )
        ),
    )
    parser.add_argument(
        "--out-dir",
        default=str(ROOT.parents[1] / "docs" / "acceptance" / "artifacts" / "round5"),
    )
    parser.add_argument(
        "--cases",
        default="H",
        help="Comma-separated case ids (A–E, H, H1–H4, H5/H5A/H5B, L/L1–L3)",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
