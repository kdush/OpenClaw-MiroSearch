"""检索/预算策略与用户显式配置之间的优先级规则。

gradio-demo 与 api-server 两个入口共用本模块，避免同一问题因入口不同而走出
不同的路由或预算参数。
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

# 置信度阈值属于调优参数：用户在 .env 里显式设置时应优先于策略预设。
# 其余键（provider order / mode）定义策略本身，由策略覆盖才有意义。
USER_TUNABLE_SEARCH_ENV_PREFIXES: Tuple[str, ...] = ("SEARCH_CONFIDENCE_",)

# 环境变量 → Hydra 键、类型与取值范围。
BudgetSpec = Tuple[str, str, Optional[float], Optional[float]]
EXPLICIT_BUDGET_ENV_MAP: Tuple[BudgetSpec, ...] = (
    ("LLM_MAX_TOKENS", "llm.max_tokens", 256, None),
    ("KEEP_TOOL_RESULT", "agent.keep_tool_result", -1, None),
    ("MAIN_AGENT_MAX_TURNS", "agent.main_agent.max_turns", 1, None),
    ("CONTEXT_COMPRESS_LIMIT", "agent.context_compress_limit", 0, None),
    ("LLM_TEMPERATURE", "llm.temperature", 0.0, 2.0),
)

_BOOL_ENV_KEYS: Tuple[Tuple[str, str], ...] = (
    ("RETRY_WITH_SUMMARY", "agent.retry_with_summary"),
)

_TRUE_TOKENS = {"1", "true", "yes", "on"}


def _hydra_override_key(override: str) -> str:
    normalized = override.lstrip("+")
    key, separator, _ = normalized.partition("=")
    return key if separator else normalized


def apply_user_search_env_precedence(profile_env: Dict[str, str]) -> Dict[str, str]:
    """剔除策略预设中用户已显式设定的调优键，让 .env 的值真正生效。"""
    return {
        key: value
        for key, value in profile_env.items()
        if not (
            key.startswith(USER_TUNABLE_SEARCH_ENV_PREFIXES)
            and os.environ.get(key, "").strip()
        )
    }


def apply_explicit_budget_overrides(overrides: List[str]) -> List[str]:
    """把用户显式设定的预算旋钮提到最高优先级。

    模式预设排在 overrides 末尾（detail < mode 是刻意设计），但它会连带覆盖
    ``LLM_MAX_TOKENS`` 这类 .env 设置，使这些变量变成静默失效的死旋钮。
    """
    replacements: Dict[str, str] = {}

    for env_name, override_key, minimum, maximum in EXPLICIT_BUDGET_ENV_MAP:
        raw = os.environ.get(env_name, "").strip()
        if not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        if minimum is not None:
            value = max(minimum, value)
        if maximum is not None:
            value = min(maximum, value)
        rendered = str(int(value)) if override_key != "llm.temperature" else str(value)
        replacements[override_key] = rendered

    for env_name, override_key in _BOOL_ENV_KEYS:
        raw = os.environ.get(env_name, "").strip()
        if raw:
            replacements[override_key] = str(raw.lower() in _TRUE_TOKENS)

    if not replacements:
        return overrides

    keys = set(replacements)
    pruned = [o for o in overrides if _hydra_override_key(o) not in keys]
    pruned.extend(f"{key}={value}" for key, value in replacements.items())
    return pruned
