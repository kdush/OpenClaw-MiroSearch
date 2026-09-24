# Copyright (c) 2025 MiroMind
# This source code is licensed under the Apache 2.0 License.

"""Deep-research efficiency knobs (Round 6–8).

Pragmatic caps that preserve cross-verification quality while cutting
wasteful clue-chasing, full-page scrapes, and LLM timeout/summary burn.
See docs/DEEP_EFFICIENCY.md.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple


# Defaults (overridable via agent.* / env / Hydra)
DEFAULT_DEEP_MAX_LEAD_FOLLOW_UPS = 2
DEFAULT_DEEP_MAX_SCRAPE_PER_TASK = 8
DEFAULT_STANDARD_MAX_SCRAPE_PER_TASK = 12
DEFAULT_LIGHT_MAX_SCRAPE_PER_TASK = 4
DEFAULT_DEEP_EARLY_STOP_MIN_AGREEING_SOURCES = 2
DEFAULT_PARALLEL_TOOL_CALLS = True
# Round 8: nudge once then force summary (was 2 in Round 7)
DEFAULT_DEEP_POST_EARLY_STOP_TURNS = 1
DEFAULT_DEEP_EXIT_ON_EARLY_STOP = True
# Round 7: single structured summary (+ local Evidence repair); one repair LLM pass max
DEFAULT_DEEP_MAX_FINAL_ANSWER_RETRIES = 1
DEFAULT_STANDARD_MAX_FINAL_ANSWER_RETRIES = 3
# Round 8: one-shot skeleton final report + capped summary context
# deep 与 standard 都走骨架；light 档保留自由发挥（无详细报告需求）
DEFAULT_ONESHOT_FINAL_REPORT_INTENSITIES = ("deep", "standard")
DEFAULT_DEEP_SUMMARY_KEEP_TOOL_RESULT = 2
DEFAULT_DEEP_SUMMARY_MAX_TOKENS = 4096
DEFAULT_STANDARD_SUMMARY_KEEP_TOOL_RESULT = -1  # inherit agent.keep_tool_result


def _get(container: Any, key: str, default: Any = None) -> Any:
    if container is None:
        return default
    if hasattr(container, "get"):
        return container.get(key, default)
    return getattr(container, key, default)


def resolve_agent_value(cfg: Any, key: str, default: Any = None) -> Any:
    """Look up ``agent.<key>``, falling back to ``agent.main_agent.<key>``.

    Accepts flags on either ``agent.*`` (API profile_resolver path) or
    ``agent.main_agent.*`` (CLI harness path).
    """
    agent = getattr(cfg, "agent", cfg)
    value = _get(agent, key, None)
    if value is None:
        main = _get(agent, "main_agent", None)
        value = _get(main, key, None)
    return default if value is None else value


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def resolve_research_intensity(cfg: Any) -> str:
    intensity = resolve_agent_value(cfg, "research_intensity")
    return str(intensity or "standard").strip().lower()


def resolve_max_scrape_per_task(cfg: Any) -> int:
    """Hard cap on full-page scrapes per task (0 = unlimited)."""
    explicit = resolve_agent_value(cfg, "max_scrape_per_task")
    if explicit is not None:
        return max(0, _as_int(explicit, 0))

    intensity = resolve_research_intensity(cfg)
    if intensity == "deep":
        return DEFAULT_DEEP_MAX_SCRAPE_PER_TASK
    if intensity == "light":
        return DEFAULT_LIGHT_MAX_SCRAPE_PER_TASK
    return DEFAULT_STANDARD_MAX_SCRAPE_PER_TASK


def resolve_parallel_tool_calls(cfg: Any) -> bool:
    explicit = resolve_agent_value(cfg, "parallel_tool_calls")
    return _as_bool(explicit, DEFAULT_PARALLEL_TOOL_CALLS)


def resolve_early_stop_config(cfg: Any) -> Tuple[bool, int]:
    """Return (enabled, min_agreeing_independent_sources)."""
    intensity = resolve_research_intensity(cfg)

    explicit = resolve_agent_value(cfg, "deep_early_stop_on_agreement")
    # Default ON for deep; OFF otherwise unless explicitly enabled
    if explicit is None:
        enabled = intensity == "deep"
    else:
        enabled = _as_bool(explicit, intensity == "deep")

    min_agree = resolve_agent_value(cfg, "deep_early_stop_min_sources")
    min_agree_int = max(
        2,
        _as_int(min_agree, DEFAULT_DEEP_EARLY_STOP_MIN_AGREEING_SOURCES),
    )
    return enabled, min_agree_int


def default_max_lead_follow_ups_for_intensity(intensity: str) -> int:
    """Clue Top-K default: deep follows only top 1–2 leads."""
    if str(intensity or "").strip().lower() == "deep":
        return DEFAULT_DEEP_MAX_LEAD_FOLLOW_UPS
    return 3


def scrape_skip_message(max_scrape: int, scrape_count: int) -> str:
    return (
        f"[scrape_budget] Full-page scrape skipped "
        f"({scrape_count}/{max_scrape} used). "
        "Prefer search snippets / titles already retrieved and continue with "
        "snippet-level evidence; the budget cannot be reset mid-task, so only "
        "conflict-critical URLs justify a scrape while budget remains."
    )


def resolve_exit_on_early_stop(cfg: Any) -> Tuple[bool, int]:
    """Return (exit_enabled, post_early_stop_turns).

    Round 7: after multi-source agreement early-stop fires, cap remaining
    LLM turns tightly so deep runs do not burn the full max_turns budget.
    """
    intensity = resolve_research_intensity(cfg)

    explicit = resolve_agent_value(cfg, "deep_exit_on_early_stop")
    if explicit is None:
        enabled = intensity == "deep" and DEFAULT_DEEP_EXIT_ON_EARLY_STOP
    else:
        enabled = _as_bool(explicit, intensity == "deep")

    post = resolve_agent_value(cfg, "deep_post_early_stop_turns")
    post_turns = max(
        0,
        _as_int(post, DEFAULT_DEEP_POST_EARLY_STOP_TURNS),
    )
    return enabled, post_turns


def resolve_max_final_answer_retries(cfg: Any) -> int:
    """Prefer a single summary pass for deep intensity (Round 7)."""
    explicit = resolve_agent_value(cfg, "max_final_answer_retries")
    if explicit is not None:
        return max(1, _as_int(explicit, DEFAULT_STANDARD_MAX_FINAL_ANSWER_RETRIES))

    intensity = resolve_research_intensity(cfg)
    if intensity == "deep":
        return DEFAULT_DEEP_MAX_FINAL_ANSWER_RETRIES
    return DEFAULT_STANDARD_MAX_FINAL_ANSWER_RETRIES


def resolve_oneshot_final_report(cfg: Any) -> bool:
    """Round 8: fill a structured skeleton once; repair locally, no rewrite loops."""
    intensity = resolve_research_intensity(cfg)
    default = intensity in DEFAULT_ONESHOT_FINAL_REPORT_INTENSITIES

    explicit = resolve_agent_value(cfg, "oneshot_final_report")
    if explicit is None:
        return default
    return _as_bool(explicit, default)


def resolve_summary_keep_tool_result(
    cfg: Any, *, research_keep: Optional[int] = None
) -> int:
    """Tool dumps retained for final_summary only (Round 8).

    Research turns still use ``agent.keep_tool_result``. Final summary keeps
    only the last N full dumps so the model does not re-digest omitted stubs
    or the entire scrape history.
    """
    intensity = resolve_research_intensity(cfg)

    explicit = resolve_agent_value(cfg, "summary_keep_tool_result")
    if explicit is not None:
        return max(-1, _as_int(explicit, DEFAULT_DEEP_SUMMARY_KEEP_TOOL_RESULT))

    if intensity == "deep" and resolve_oneshot_final_report(cfg):
        return DEFAULT_DEEP_SUMMARY_KEEP_TOOL_RESULT

    if research_keep is not None:
        return max(
            -1, _as_int(research_keep, DEFAULT_STANDARD_SUMMARY_KEEP_TOOL_RESULT)
        )
    keep = resolve_agent_value(cfg, "keep_tool_result")
    if keep is None:
        return DEFAULT_STANDARD_SUMMARY_KEEP_TOOL_RESULT
    return max(-1, _as_int(keep, DEFAULT_STANDARD_SUMMARY_KEEP_TOOL_RESULT))


def resolve_summary_max_tokens_cap(cfg: Any) -> Optional[int]:
    """Optional hard cap on summary_max_tokens for deep oneshot (Round 8)."""
    intensity = resolve_research_intensity(cfg)

    explicit = resolve_agent_value(cfg, "summary_max_tokens_cap")
    if explicit is not None:
        return max(1024, _as_int(explicit, DEFAULT_DEEP_SUMMARY_MAX_TOKENS))

    if intensity == "deep" and resolve_oneshot_final_report(cfg):
        return DEFAULT_DEEP_SUMMARY_MAX_TOKENS
    return None
