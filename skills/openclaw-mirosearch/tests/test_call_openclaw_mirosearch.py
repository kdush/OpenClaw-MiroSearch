import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "call_openclaw_mirosearch.py"


def _load_script_module():
    module_name = "call_openclaw_mirosearch_tests"
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_next_degrade_step_moves_forward_from_balanced_searxng_first():
    script = _load_script_module()

    assert script._next_degrade_step("balanced", "searxng-first") == (
        "quota",
        "searxng-only",
    )


def test_next_degrade_step_returns_none_at_last_step():
    script = _load_script_module()

    assert script._next_degrade_step("quota", "searxng-only") is None
