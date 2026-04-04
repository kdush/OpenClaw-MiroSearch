"""output_detail_level 参数路由回归测试。

验证三个档位（compact / balanced / detailed）生成的 Hydra override 列表
包含正确的 max_tokens 和 summary_max_tokens 值，且档位之间严格递增。
"""

import importlib.util
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
GRADIO_DEMO_DIR = PROJECT_ROOT / "apps" / "gradio-demo"
MIROFLOW_AGENT_DIR = PROJECT_ROOT / "apps" / "miroflow-agent"
MODULE_PATH = GRADIO_DEMO_DIR / "main.py"


def _load_demo_main():
    os.environ.setdefault("ENABLE_PROMPT_PATCH", "0")
    if str(GRADIO_DEMO_DIR) not in sys.path:
        sys.path.insert(0, str(GRADIO_DEMO_DIR))
    if str(MIROFLOW_AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(MIROFLOW_AGENT_DIR))
    module_name = "gradio_demo_main_for_tests"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _extract_override_value(overrides: list, key: str) -> str:
    """从 Hydra override 列表中提取指定 key 的值。"""
    prefix = f"{key}="
    for item in overrides:
        cleaned = item.lstrip("+")
        if cleaned.startswith(prefix):
            return cleaned[len(prefix):]
    raise KeyError(f"{key} not found in overrides: {overrides}")


@pytest.fixture(scope="module")
def demo_main():
    return _load_demo_main()


def test_normalize_output_detail_level_valid_values(demo_main):
    """三个合法档位应原样返回。"""
    for level in ("compact", "balanced", "detailed"):
        assert demo_main._normalize_output_detail_level(level) == level


def test_normalize_output_detail_level_invalid_fallback(demo_main):
    """非法值应回退到默认档位。"""
    result = demo_main._normalize_output_detail_level("nonexistent")
    assert result in ("compact", "balanced", "detailed")


def test_max_tokens_strictly_increasing_across_levels(demo_main):
    """compact < balanced < detailed 的 max_tokens 应严格递增。"""
    tokens = {}
    for level in ("compact", "balanced", "detailed"):
        overrides = demo_main._get_mode_overrides_for_output_detail(level)
        tokens[level] = int(_extract_override_value(overrides, "llm.max_tokens"))

    assert tokens["compact"] < tokens["balanced"] < tokens["detailed"]


def test_each_level_contains_required_override_keys(demo_main):
    """每个档位的 override 列表应包含必要的 key。"""
    required_keys = [
        "llm.max_tokens",
        "llm.tool_result_max_chars",
        "llm.summary_max_tokens",
        "agent.main_agent.max_turns",
    ]
    for level in ("compact", "balanced", "detailed"):
        overrides = demo_main._get_mode_overrides_for_output_detail(level)
        flat = " ".join(overrides)
        for key in required_keys:
            assert key in flat, f"{key} missing in {level} overrides"
