# Copyright (c) 2025 MiroMind
"""Unit tests for Round-6/7 deep efficiency knobs."""

from src.core.deep_efficiency import (
    DEFAULT_DEEP_MAX_FINAL_ANSWER_RETRIES,
    DEFAULT_DEEP_MAX_LEAD_FOLLOW_UPS,
    DEFAULT_DEEP_MAX_SCRAPE_PER_TASK,
    DEFAULT_DEEP_POST_EARLY_STOP_TURNS,
    default_max_lead_follow_ups_for_intensity,
    resolve_early_stop_config,
    resolve_exit_on_early_stop,
    resolve_max_final_answer_retries,
    resolve_max_scrape_per_task,
    resolve_oneshot_final_report,
    resolve_parallel_tool_calls,
    resolve_summary_keep_tool_result,
)
from src.core.lead_tracker import resolve_lead_tracking_config


class _CfgDict(dict):
    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError as exc:
            raise AttributeError(item) from exc


def test_deep_default_clue_top_k_is_two():
    assert default_max_lead_follow_ups_for_intensity("deep") == 2
    assert DEFAULT_DEEP_MAX_LEAD_FOLLOW_UPS == 2


def test_resolve_lead_tracking_deep_defaults_to_top_k_two():
    agent = _CfgDict(research_intensity="deep")
    cfg = _CfgDict(agent=agent)
    enabled, max_fu = resolve_lead_tracking_config(cfg)
    assert enabled is True
    assert max_fu == 2


def test_resolve_max_scrape_per_task_deep_default():
    agent = _CfgDict(research_intensity="deep")
    cfg = _CfgDict(agent=agent)
    assert resolve_max_scrape_per_task(cfg) == DEFAULT_DEEP_MAX_SCRAPE_PER_TASK


def test_early_stop_defaults_on_for_deep():
    agent = _CfgDict(research_intensity="deep")
    cfg = _CfgDict(agent=agent)
    enabled, min_src = resolve_early_stop_config(cfg)
    assert enabled is True
    assert min_src >= 2


def test_parallel_tool_calls_default_true():
    agent = _CfgDict(research_intensity="deep")
    cfg = _CfgDict(agent=agent)
    assert resolve_parallel_tool_calls(cfg) is True


def test_exit_on_early_stop_defaults_for_deep():
    agent = _CfgDict(research_intensity="deep")
    cfg = _CfgDict(agent=agent)
    enabled, post = resolve_exit_on_early_stop(cfg)
    assert enabled is True
    assert post == DEFAULT_DEEP_POST_EARLY_STOP_TURNS


def test_exit_on_early_stop_off_for_standard():
    agent = _CfgDict(research_intensity="standard")
    cfg = _CfgDict(agent=agent)
    enabled, _post = resolve_exit_on_early_stop(cfg)
    assert enabled is False


def test_max_final_answer_retries_deep_defaults_to_one():
    agent = _CfgDict(research_intensity="deep")
    cfg = _CfgDict(agent=agent)
    assert (
        resolve_max_final_answer_retries(cfg) == DEFAULT_DEEP_MAX_FINAL_ANSWER_RETRIES
    )
    assert DEFAULT_DEEP_MAX_FINAL_ANSWER_RETRIES == 1


def test_max_final_answer_retries_explicit_wins():
    agent = _CfgDict(research_intensity="deep", max_final_answer_retries=3)
    cfg = _CfgDict(agent=agent)
    assert resolve_max_final_answer_retries(cfg) == 3


def test_oneshot_skeleton_defaults_on_for_standard_not_light():
    standard = _CfgDict(agent=_CfgDict(research_intensity="standard"))
    light = _CfgDict(agent=_CfgDict(research_intensity="light"))
    assert resolve_oneshot_final_report(standard) is True
    assert resolve_oneshot_final_report(light) is False


def test_round8_oneshot_and_summary_keep_defaults():
    agent = _CfgDict(research_intensity="deep")
    cfg = _CfgDict(agent=agent)
    assert resolve_oneshot_final_report(cfg) is True
    assert resolve_summary_keep_tool_result(cfg) == 2
    assert DEFAULT_DEEP_POST_EARLY_STOP_TURNS == 1
