import importlib.util
import os
import sys
from pathlib import Path


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
    module_name = "gradio_demo_main_layout_tests"
    spec = importlib.util.spec_from_file_location(module_name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_build_demo_uses_two_column_layout_with_right_options():
    demo_main = _load_demo_main()
    demo = demo_main.build_demo()
    components = demo.config["components"]
    dependencies = demo.config["dependencies"]

    component_by_elem_id = {}
    for component in components:
        elem_id = component.get("props", {}).get("elem_id")
        if elem_id:
            component_by_elem_id[elem_id] = component

    assert component_by_elem_id["layout-shell"]["type"] == "row"
    assert "left-history-column" not in component_by_elem_id
    assert component_by_elem_id["main-content-column"]["type"] == "column"
    assert component_by_elem_id["right-options-column"]["type"] == "column"
    assert component_by_elem_id["mode-selector"]["type"] == "dropdown"
    assert component_by_elem_id["search-profile-selector"]["type"] == "dropdown"
    assert component_by_elem_id["search-result-num-selector"]["type"] == "dropdown"
    assert component_by_elem_id["verification-rounds-selector"]["type"] == "slider"
    assert component_by_elem_id["output-detail-level-selector"]["type"] == "dropdown"

    run_research_api = [
        dependency
        for dependency in dependencies
        if dependency.get("api_name") == "run_research_once"
    ]
    assert len(run_research_api) == 1
    assert len(run_research_api[0]["inputs"]) == 6
