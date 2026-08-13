"""output_detail_level 参数路由回归测试。

验证三个档位（compact / balanced / detailed）生成的 Hydra override 列表
包含正确的 max_tokens 和 summary_max_tokens 值，且档位之间严格递增。
"""

import asyncio
import importlib.util
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

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
            return cleaned[len(prefix) :]
    raise KeyError(f"{key} not found in overrides: {overrides}")


MODE_HARD_BUDGET_PATHS = (
    ("agent.main_agent.max_turns", ("agent", "main_agent", "max_turns")),
    ("llm.max_tokens", ("llm", "max_tokens")),
    ("agent.keep_tool_result", ("agent", "keep_tool_result")),
    ("agent.context_compress_limit", ("agent", "context_compress_limit")),
    ("llm.tool_result_max_chars", ("llm", "tool_result_max_chars")),
)


def _config_value(cfg, path: tuple[str, ...]):
    value = cfg
    for part in path:
        value = value[part]
    return value


class _FakeToolManager:
    def __init__(self, definitions=None):
        self.definitions = definitions if definitions is not None else []

    async def get_all_tool_definitions(self):
        return self.definitions


class _ClosableToolManager(_FakeToolManager):
    def __init__(self, definitions=None, *, definition_error=None):
        super().__init__(definitions)
        self.definition_error = definition_error
        self.close_calls = 0

    async def get_all_tool_definitions(self):
        if self.definition_error is not None:
            raise self.definition_error
        return await super().get_all_tool_definitions()

    async def aclose(self):
        self.close_calls += 1


@pytest.fixture(scope="module")
def demo_main():
    return _load_demo_main()


@pytest.fixture
def _clean_preload_cache(demo_main):
    demo_main._preload_cache.clear()
    if hasattr(demo_main, "_preload_inflight"):
        demo_main._preload_inflight.clear()
    yield
    demo_main._preload_cache.clear()
    if hasattr(demo_main, "_preload_inflight"):
        demo_main._preload_inflight.clear()


def _install_stream_profile_cache(demo_main, monkeypatch):
    mode = "balanced"
    search_profile = "searxng-first"
    search_result_num = 20
    verification_min_search_rounds = (
        demo_main._resolve_effective_verification_min_search_rounds(mode, 3)
    )
    output_detail_level = "balanced"
    cache_key = demo_main._compose_profile_cache_key(
        mode,
        search_profile,
        search_result_num,
        verification_min_search_rounds,
        output_detail_level,
    )
    profile_cache = {
        "cfg": object(),
        "tool_definitions": [],
        "sub_agent_tool_definitions": {},
        "search_profile": search_profile,
        "search_result_num": search_result_num,
        "verification_min_search_rounds": verification_min_search_rounds,
        "output_detail_level": output_detail_level,
    }
    demo_main._preload_cache[cache_key] = profile_cache
    monkeypatch.setattr(
        demo_main,
        "_ensure_preloaded",
        lambda *args, **kwargs: {
            "cache_key": cache_key,
            "cache_hit": True,
            "duration_ms": 0,
        },
    )
    monkeypatch.setattr(
        demo_main,
        "_create_task_runtime_components",
        lambda cache: (object(), {}, object()),
    )
    return {
        "mode": mode,
        "search_profile": search_profile,
        "search_result_num": search_result_num,
        "verification_min_search_rounds": verification_min_search_rounds,
        "output_detail_level": output_detail_level,
    }


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


def test_load_config_wraps_compose_failure_as_runtime_error(
    demo_main,
    monkeypatch,
):
    """Hydra compose 失败应成为线程可捕获且保留原因的运行时异常。"""
    compose_error = ValueError("invalid override")
    monkeypatch.setattr(demo_main, "_hydra_initialized", True)

    def fail_compose(**kwargs):
        del kwargs
        raise compose_error

    monkeypatch.setattr(demo_main, "compose", fail_compose)

    with pytest.raises(
        RuntimeError,
        match="Failed to compose Hydra config: invalid override",
    ) as exc_info:
        demo_main.load_miroflow_config()

    assert exc_info.value.__cause__ is compose_error


@pytest.mark.asyncio
async def test_stream_reports_config_compose_failure(
    demo_main,
    _clean_preload_cache,
    monkeypatch,
):
    """后台线程应把配置失败作为 error 事件返回，而不是静默退出。"""
    monkeypatch.setattr(demo_main, "_hydra_initialized", True)

    def fail_compose(**kwargs):
        del kwargs
        raise ValueError("invalid override")

    monkeypatch.setattr(demo_main, "compose", fail_compose)

    events = [
        event
        async for event in demo_main.stream_events_optimized(
            task_id="config-error-task",
            query="验证配置错误",
            mode="balanced",
            search_profile="searxng-first",
            search_result_num=20,
            verification_min_search_rounds=3,
            output_detail_level="balanced",
        )
    ]

    assert [event["event"] for event in events] == ["error", "done"]
    assert events[0]["data"]["workflow_id"] == "config-error-task"
    assert (
        events[0]["data"]["error"] == "Failed to compose Hydra config: invalid override"
    )
    assert events[1]["data"]["status"] == "failed"


@pytest.mark.parametrize("mode", ["quota", "research"])
@pytest.mark.parametrize("detail_level", ["compact", "detailed"])
def test_preloaded_config_keeps_detail_marker_and_mode_hard_budgets(
    demo_main,
    _clean_preload_cache,
    monkeypatch,
    mode,
    detail_level,
):
    """真实 compose 后，篇幅标记保留，但不得覆盖模式硬预算。"""
    monkeypatch.setattr(
        demo_main,
        "create_pipeline_components",
        lambda cfg: (_FakeToolManager(), {}, object()),
    )

    preload_info = demo_main._ensure_preloaded(
        mode=mode,
        search_profile="searxng-first",
        search_result_num=20,
        verification_min_search_rounds=3,
        output_detail_level=detail_level,
    )
    cfg = demo_main._preload_cache[preload_info["cache_key"]]["cfg"]
    expected_mode_overrides = demo_main.MODE_OVERRIDE_MAP[mode]

    assert cfg.agent.output_detail_level == detail_level
    for override_key, config_path in MODE_HARD_BUDGET_PATHS:
        assert _config_value(cfg, config_path) == int(
            _extract_override_value(expected_mode_overrides, override_key)
        )


def test_blank_stage_model_env_falls_back_in_real_gradio_compose(
    monkeypatch,
):
    """Gradio 入口不得把空白阶段模型提升为显式空 Hydra 配置。"""
    monkeypatch.setenv("ENABLE_PROMPT_PATCH", "0")
    monkeypatch.setenv("DEFAULT_MODEL_NAME", "base-model")
    monkeypatch.setenv("MODEL_FAST_NAME", "fast-model")
    monkeypatch.setenv("MODEL_SUMMARY_NAME", "   ")
    module_name = "gradio_demo_main_blank_stage_model_test"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, MODULE_PATH)
    fresh_module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[module_name] = fresh_module

    try:
        spec.loader.exec_module(fresh_module)
        overrides = fresh_module._build_profile_overrides(
            mode="balanced",
            output_detail_level="balanced",
            verification_min_search_rounds=3,
        )
        cfg = fresh_module.load_miroflow_config(overrides)

        assert cfg.llm.model_name == "base-model"
        assert cfg.llm.model_fast_name == "fast-model"
        assert cfg.llm.model_summary_name == "fast-model"
    finally:
        fresh_module.cleanup_executor.shutdown(wait=True)
        sys.modules.pop(module_name, None)


def test_preload_cache_reuses_definitions_but_not_runtime_components(
    demo_main,
    _clean_preload_cache,
    monkeypatch,
):
    """缓存命中时应复用配置/定义，但每个任务都创建独立运行时对象。"""
    created_components = []
    main_definitions = [{"name": "main-tool", "tools": []}]
    sub_definitions = [{"name": "sub-tool", "tools": []}]

    def fake_create_pipeline_components(cfg):
        components = (
            _FakeToolManager(main_definitions),
            {"researcher": _FakeToolManager(sub_definitions)},
            object(),
        )
        created_components.append((cfg, components))
        return components

    monkeypatch.setattr(
        demo_main,
        "create_pipeline_components",
        fake_create_pipeline_components,
    )

    first_preload = demo_main._ensure_preloaded(
        mode="balanced",
        search_profile="searxng-first",
        search_result_num=20,
        verification_min_search_rounds=3,
        output_detail_level="balanced",
    )
    second_preload = demo_main._ensure_preloaded(
        mode="balanced",
        search_profile="searxng-first",
        search_result_num=20,
        verification_min_search_rounds=3,
        output_detail_level="balanced",
    )

    assert first_preload["cache_hit"] is False
    assert second_preload["cache_hit"] is True
    assert first_preload["cache_key"] == second_preload["cache_key"]

    profile_cache = demo_main._preload_cache[first_preload["cache_key"]]
    assert {
        "main_agent_tool_manager",
        "sub_agent_tool_managers",
        "output_formatter",
    }.isdisjoint(profile_cache)

    cached_cfg = profile_cache["cfg"]
    cached_main_definitions = profile_cache["tool_definitions"]
    cached_sub_definitions = profile_cache["sub_agent_tool_definitions"]

    first_runtime = demo_main._create_task_runtime_components(profile_cache)
    second_runtime = demo_main._create_task_runtime_components(profile_cache)

    assert len(created_components) == 3
    assert all(cfg is cached_cfg for cfg, _ in created_components)
    assert first_runtime[0] is not second_runtime[0]
    assert first_runtime[1]["researcher"] is not second_runtime[1]["researcher"]
    assert first_runtime[2] is not second_runtime[2]
    assert profile_cache["tool_definitions"] is cached_main_definitions
    assert profile_cache["sub_agent_tool_definitions"] is cached_sub_definitions


def test_preload_closes_temporary_managers_once_after_success(
    demo_main,
    _clean_preload_cache,
    monkeypatch,
):
    """工具定义发现成功后，临时主/子 manager 应按 identity 去重关闭。"""
    repeated_manager = _ClosableToolManager([])
    other_manager = _ClosableToolManager([])
    monkeypatch.setattr(
        demo_main,
        "create_pipeline_components",
        lambda cfg: (
            repeated_manager,
            {
                "repeated": repeated_manager,
                "other": other_manager,
            },
            object(),
        ),
    )

    demo_main._ensure_preloaded(
        mode="balanced",
        search_profile="searxng-first",
        search_result_num=20,
        verification_min_search_rounds=3,
        output_detail_level="balanced",
    )

    assert repeated_manager.close_calls == 1
    assert other_manager.close_calls == 1


def test_preload_closes_all_temporary_managers_when_discovery_fails(
    demo_main,
    _clean_preload_cache,
    monkeypatch,
):
    """任一工具定义发现失败时，也必须关闭所有已创建的临时 manager。"""
    main_manager = _ClosableToolManager([])
    failing_manager = _ClosableToolManager(
        definition_error=RuntimeError("definition discovery failed")
    )
    untouched_manager = _ClosableToolManager([])
    monkeypatch.setattr(
        demo_main,
        "create_pipeline_components",
        lambda cfg: (
            main_manager,
            {
                "failing": failing_manager,
                "untouched": untouched_manager,
            },
            object(),
        ),
    )

    with pytest.raises(RuntimeError, match="definition discovery failed"):
        demo_main._ensure_preloaded(
            mode="balanced",
            search_profile="searxng-first",
            search_result_num=20,
            verification_min_search_rounds=3,
            output_detail_level="balanced",
        )

    assert main_manager.close_calls == 1
    assert failing_manager.close_calls == 1
    assert untouched_manager.close_calls == 1


def test_runtime_component_creation_serializes_search_environment(
    demo_main,
    monkeypatch,
):
    """用确定性事件编排验证不同 profile 的临时环境严格串行。"""
    start_barrier = threading.Barrier(3)
    state_condition = threading.Condition()
    release_events = {
        "first": threading.Event(),
        "second": threading.Event(),
    }
    entered_order = []
    active_factories = 0
    max_active_factories = 0
    snapshots = {}

    class _Cfg:
        def __init__(self, name):
            self.name = name

    first_cfg = _Cfg("first")
    second_cfg = _Cfg("second")

    def current_search_env():
        return (
            os.environ.get("SEARCH_PROVIDER_ORDER"),
            os.environ.get("SEARCH_PROVIDER_MODE"),
            os.environ.get("SEARCH_RESULT_NUM"),
        )

    def fake_create_pipeline_components(cfg):
        nonlocal active_factories, max_active_factories
        start_snapshot = current_search_env()
        with state_condition:
            active_factories += 1
            max_active_factories = max(max_active_factories, active_factories)
            entered_order.append(cfg.name)
            state_condition.notify_all()
        release_events[cfg.name].wait()
        snapshots[cfg.name] = (start_snapshot, current_search_env())
        with state_condition:
            active_factories -= 1
            state_condition.notify_all()
        return _FakeToolManager(), {}, object()

    monkeypatch.setattr(
        demo_main,
        "create_pipeline_components",
        fake_create_pipeline_components,
    )
    monkeypatch.setenv("SEARCH_PROVIDER_ORDER", "outer-order")
    monkeypatch.setenv("SEARCH_PROVIDER_MODE", "outer-mode")
    monkeypatch.setenv("SEARCH_RESULT_NUM", "99")

    first_profile_cache = {
        "cfg": first_cfg,
        "search_profile": "searxng-only",
        "search_result_num": 10,
    }
    second_profile_cache = {
        "cfg": second_cfg,
        "search_profile": "multi-route",
        "search_result_num": 30,
    }

    def create_runtime(profile_cache):
        start_barrier.wait()
        return demo_main._create_task_runtime_components(profile_cache)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                create_runtime,
                first_profile_cache,
            ),
            executor.submit(
                create_runtime,
                second_profile_cache,
            ),
        ]
        start_barrier.wait()
        with state_condition:
            assert state_condition.wait_for(lambda: len(entered_order) == 1, timeout=5)
            first_entered = entered_order[0]
            assert active_factories == 1
        release_events[first_entered].set()
        with state_condition:
            assert state_condition.wait_for(lambda: len(entered_order) == 2, timeout=5)
            second_entered = entered_order[1]
            assert active_factories == 1
        release_events[second_entered].set()
        for future in futures:
            future.result(timeout=5)

    assert max_active_factories == 1
    assert snapshots["first"] == (
        ("searxng", "fallback", "10"),
        ("searxng", "fallback", "10"),
    )
    assert snapshots["second"] == (
        ("serpapi,tavily,searxng,serper", "merge", "30"),
        ("serpapi,tavily,searxng,serper", "merge", "30"),
    )
    assert current_search_env() == ("outer-order", "outer-mode", "99")


def test_tool_discovery_does_not_block_cached_runtime_component_creation(
    demo_main,
    _clean_preload_cache,
    monkeypatch,
):
    """另一个 profile 的网络发现阻塞时，已缓存任务仍应能创建 runtime。"""
    discovery_started = threading.Event()
    release_discovery = threading.Event()
    cached_runtime_created = threading.Event()
    preload_cfg = SimpleNamespace(agent=SimpleNamespace(sub_agents={}))
    cached_cfg = object()

    class _BlockingDefinitionManager(_ClosableToolManager):
        async def get_all_tool_definitions(self):
            discovery_started.set()
            while not release_discovery.is_set():
                await demo_main.asyncio.sleep(0)
            return []

    def fake_create_pipeline_components(cfg):
        if cfg is cached_cfg:
            cached_runtime_created.set()
            return _FakeToolManager(), {}, object()
        assert cfg is preload_cfg
        return _BlockingDefinitionManager(), {}, object()

    monkeypatch.setattr(
        demo_main, "load_miroflow_config", lambda overrides: preload_cfg
    )
    monkeypatch.setattr(
        demo_main,
        "create_pipeline_components",
        fake_create_pipeline_components,
    )
    cached_profile = {
        "cfg": cached_cfg,
        "search_profile": "searxng-only",
        "search_result_num": 10,
    }

    with ThreadPoolExecutor(max_workers=2) as executor:
        preload_future = executor.submit(
            demo_main._ensure_preloaded,
            "verified",
            "parallel",
            30,
            4,
            "detailed",
        )
        assert discovery_started.wait(timeout=5)
        runtime_future = executor.submit(
            demo_main._create_task_runtime_components,
            cached_profile,
        )
        try:
            assert cached_runtime_created.wait(timeout=1)
            runtime_future.result(timeout=5)
        finally:
            release_discovery.set()
        preload_future.result(timeout=5)


def test_same_profile_preload_uses_singleflight(
    demo_main,
    _clean_preload_cache,
    monkeypatch,
):
    """第二调用确认进入 inflight 等待后，仍只能执行一次工具发现。"""
    discovery_started = threading.Event()
    release_discovery = threading.Event()
    waiter_entered = threading.Event()
    factory_calls = 0
    calls_lock = threading.Lock()
    cfg = SimpleNamespace(agent=SimpleNamespace(sub_agents={}))

    class _BlockingDefinitionManager(_ClosableToolManager):
        async def get_all_tool_definitions(self):
            discovery_started.set()
            while not release_discovery.is_set():
                await demo_main.asyncio.sleep(0)
            return []

    def fake_create_pipeline_components(loaded_cfg):
        nonlocal factory_calls
        assert loaded_cfg is cfg
        with calls_lock:
            factory_calls += 1
        return _BlockingDefinitionManager([]), {}, object()

    monkeypatch.setattr(demo_main, "load_miroflow_config", lambda overrides: cfg)
    monkeypatch.setattr(
        demo_main,
        "create_pipeline_components",
        fake_create_pipeline_components,
    )

    def preload():
        return demo_main._ensure_preloaded(
            mode="balanced",
            search_profile="searxng-first",
            search_result_num=20,
            verification_min_search_rounds=3,
            output_detail_level="balanced",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(preload)
        assert discovery_started.wait(timeout=5)

        cache_key = demo_main._compose_profile_cache_key(
            "balanced",
            "searxng-first",
            20,
            demo_main._resolve_effective_verification_min_search_rounds(
                "balanced",
                3,
            ),
            "balanced",
        )
        with demo_main._preload_cache_lock:
            preload_state = demo_main._preload_inflight[cache_key]
            original_wait = preload_state["event"].wait

            def observed_wait(*args, **kwargs):
                waiter_entered.set()
                return original_wait(*args, **kwargs)

            preload_state["event"].wait = observed_wait

        second_future = executor.submit(preload)
        try:
            assert waiter_entered.wait(timeout=5)
        finally:
            release_discovery.set()
        results = [
            first_future.result(timeout=5),
            second_future.result(timeout=5),
        ]

    assert factory_calls == 1
    assert sorted(result["cache_hit"] for result in results) == [False, True]


@pytest.mark.asyncio
async def test_stream_tasks_pass_fresh_runtime_components_to_pipeline(
    demo_main,
    _clean_preload_cache,
    monkeypatch,
):
    """同一 profile 的连续任务应共享静态缓存，但向 pipeline 传入新组件。"""
    created_components = []
    pipeline_calls = []
    main_definitions = [{"name": "main-tool", "tools": []}]
    sub_definitions = [{"name": "sub-tool", "tools": []}]

    def fake_create_pipeline_components(cfg):
        components = (
            _FakeToolManager(main_definitions),
            {"researcher": _FakeToolManager(sub_definitions)},
            object(),
        )
        created_components.append(components)
        return components

    async def fake_execute_task_pipeline(**kwargs):
        pipeline_calls.append(kwargs)
        return {"status": "completed"}

    monkeypatch.setattr(
        demo_main,
        "create_pipeline_components",
        fake_create_pipeline_components,
    )
    monkeypatch.setattr(
        demo_main,
        "execute_task_pipeline",
        fake_execute_task_pipeline,
    )

    stream_kwargs = {
        "query": "验证运行时隔离",
        "mode": "balanced",
        "search_profile": "searxng-first",
        "search_result_num": 20,
        "verification_min_search_rounds": 3,
        "output_detail_level": "balanced",
    }
    for task_id in ("task-first", "task-second"):
        events = [
            event
            async for event in demo_main.stream_events_optimized(
                task_id=task_id,
                **stream_kwargs,
            )
        ]
        assert [event["event"] for event in events] == ["done"]
        assert events[0]["data"]["status"] == "completed"

    assert len(created_components) == 3
    assert len(pipeline_calls) == 2
    assert (
        pipeline_calls[0]["main_agent_tool_manager"]
        is not pipeline_calls[1]["main_agent_tool_manager"]
    )
    assert (
        pipeline_calls[0]["sub_agent_tool_managers"]["researcher"]
        is not pipeline_calls[1]["sub_agent_tool_managers"]["researcher"]
    )
    assert (
        pipeline_calls[0]["output_formatter"]
        is not pipeline_calls[1]["output_formatter"]
    )
    assert pipeline_calls[0]["cfg"] is pipeline_calls[1]["cfg"]
    assert (
        pipeline_calls[0]["tool_definitions"] is pipeline_calls[1]["tool_definitions"]
    )
    assert (
        pipeline_calls[0]["sub_agent_tool_definitions"]
        is pipeline_calls[1]["sub_agent_tool_definitions"]
    )


@pytest.mark.asyncio
async def test_stream_awaits_cancelled_pipeline_cleanup(
    demo_main,
    _clean_preload_cache,
    monkeypatch,
):
    """取消 watcher 触发后必须 await pipeline，让其 finally 完整执行。"""
    stream_kwargs = _install_stream_profile_cache(demo_main, monkeypatch)
    cleanup_finished = threading.Event()

    async def fake_execute_task_pipeline(**kwargs):
        del kwargs
        try:
            await demo_main.asyncio.Event().wait()
        except demo_main.asyncio.CancelledError:
            await demo_main.asyncio.sleep(0)
            cleanup_finished.set()
            return {"status": "cancelled"}

    monkeypatch.setattr(
        demo_main,
        "execute_task_pipeline",
        fake_execute_task_pipeline,
    )
    monkeypatch.setattr(
        demo_main,
        "PIPELINE_CANCEL_POLL_INTERVAL_SECONDS",
        0,
        raising=False,
    )

    async def disconnected():
        return True

    events = [
        event
        async for event in demo_main.stream_events_optimized(
            task_id="cancel-cleanup-task",
            query="验证取消清理",
            disconnect_check=disconnected,
            **stream_kwargs,
        )
    ]

    assert events == []
    assert cleanup_finished.is_set()


@pytest.mark.asyncio
async def test_stream_thread_cleanup_does_not_block_caller_event_loop(
    demo_main,
    _clean_preload_cache,
    monkeypatch,
):
    """同步 Pipeline 尚未退出时，等待线程清理不得冻结 Gradio 事件循环。"""
    stream_kwargs = _install_stream_profile_cache(demo_main, monkeypatch)
    pipeline_started = threading.Event()
    tick_delay = []
    ticker_task = None
    disconnected_at = 0.0

    async def fake_execute_task_pipeline(**kwargs):
        del kwargs
        pipeline_started.set()
        time.sleep(0.4)
        return {"status": "cancelled"}

    async def ticker():
        await asyncio.sleep(0.02)
        tick_delay.append(time.perf_counter() - disconnected_at)

    async def disconnected():
        nonlocal ticker_task, disconnected_at
        if not pipeline_started.is_set():
            return False
        if ticker_task is None:
            disconnected_at = time.perf_counter()
            ticker_task = asyncio.create_task(ticker())
        return True

    monkeypatch.setattr(
        demo_main,
        "execute_task_pipeline",
        fake_execute_task_pipeline,
    )

    events = [
        event
        async for event in demo_main.stream_events_optimized(
            task_id="nonblocking-thread-cleanup",
            query="验证取消不冻结事件循环",
            disconnect_check=disconnected,
            **stream_kwargs,
        )
    ]
    assert ticker_task is not None
    await ticker_task

    assert events == []
    assert tick_delay and tick_delay[0] < 0.15


@pytest.mark.asyncio
async def test_stream_pipeline_exception_reaches_outer_error_event(
    demo_main,
    _clean_preload_cache,
    monkeypatch,
):
    """pipeline 抛出的异常不得在内层吞掉。"""
    stream_kwargs = _install_stream_profile_cache(demo_main, monkeypatch)

    async def fake_execute_task_pipeline(**kwargs):
        del kwargs
        raise RuntimeError("pipeline exploded")

    monkeypatch.setattr(
        demo_main,
        "execute_task_pipeline",
        fake_execute_task_pipeline,
    )

    events = [
        event
        async for event in demo_main.stream_events_optimized(
            task_id="pipeline-error-task",
            query="验证异常事件",
            **stream_kwargs,
        )
    ]

    assert [event["event"] for event in events] == ["error", "done"]
    assert events[0]["data"]["error"] == "pipeline exploded"
    assert events[1]["data"]["status"] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pipeline_result", "expected_event_types", "expected_status"),
    [
        (
            {"status": "failed", "error": "structured failure"},
            ["error", "done"],
            "failed",
        ),
        (
            {"status": "cancelled", "error": "user cancelled"},
            ["done"],
            "cancelled",
        ),
    ],
)
async def test_stream_emits_explicit_structured_terminal_events(
    demo_main,
    _clean_preload_cache,
    monkeypatch,
    pipeline_result,
    expected_event_types,
    expected_status,
):
    """结构化 failed/cancelled 结果应遵循 error + done/status 协议。"""
    stream_kwargs = _install_stream_profile_cache(demo_main, monkeypatch)

    async def fake_execute_task_pipeline(**kwargs):
        del kwargs
        return pipeline_result

    monkeypatch.setattr(
        demo_main,
        "execute_task_pipeline",
        fake_execute_task_pipeline,
    )

    events = [
        event
        async for event in demo_main.stream_events_optimized(
            task_id=f"terminal-{expected_status}",
            query="验证结构化终态",
            **stream_kwargs,
        )
    ]

    assert [event["event"] for event in events] == expected_event_types
    assert events[-1]["data"]["status"] == expected_status
    if expected_status == "failed":
        assert events[0]["data"]["error"] == "structured failure"
