"""PipelineRuntime.build_config_overrides 集成测试。

验证 worker 路径与 demo 路径在 mode/search_profile/result_num/min_rounds/detail
五个维度上行为一致：mode_overrides 被正确注入到 hydra overrides；search_env 包含
检索源策略所需的进程环境变量；不再硬编码 ``agent=demo_search_only``。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.pipeline_runtime import PipelineRuntime, RequestLike
from services.profile_resolver import (
    build_mode_overrides,
    resolve_effective_research_params,
)

MODE_HARD_BUDGET_PATHS = (
    ("agent.main_agent.max_turns", ("agent", "main_agent", "max_turns")),
    ("llm.max_tokens", ("llm", "max_tokens")),
    ("agent.keep_tool_result", ("agent", "keep_tool_result")),
    ("agent.context_compress_limit", ("agent", "context_compress_limit")),
    ("llm.tool_result_max_chars", ("llm", "tool_result_max_chars")),
)


def _override_int(overrides: list[str], key: str) -> int:
    prefix = f"{key}="
    for override in reversed(overrides):
        normalized = override.lstrip("+")
        if normalized.startswith(prefix):
            return int(normalized[len(prefix) :])
    raise KeyError(f"未找到 override：{key}")


def _config_value(cfg, path: tuple[str, ...]):
    value = cfg
    for part in path:
        value = value[part]
    return value


def _make_request(**kwargs) -> RequestLike:
    defaults = dict(
        query="hello",
        mode="balanced",
        search_profile="searxng-first",
        search_result_num=20,
        verification_min_search_rounds=3,
        output_detail_level="detailed",
    )
    defaults.update(kwargs)
    return RequestLike(**defaults)


class TestBuildConfigOverrides:
    @pytest.mark.parametrize("mode", ["quota", "research"])
    @pytest.mark.parametrize("detail_level", ["compact", "detailed"])
    def test_composed_config_keeps_detail_marker_and_mode_hard_budgets(
        self,
        mode,
        detail_level,
    ):
        runtime = PipelineRuntime()
        _, overrides = runtime.build_config_overrides(
            _make_request(mode=mode, output_detail_level=detail_level)
        )

        cfg = runtime.load_hydra_config(overrides)
        expected_mode_overrides = build_mode_overrides(mode)

        assert cfg.agent.output_detail_level == detail_level
        for override_key, config_path in MODE_HARD_BUDGET_PATHS:
            assert _config_value(cfg, config_path) == _override_int(
                expected_mode_overrides,
                override_key,
            )

    def test_worker_overrides_match_effective_deployment_defaults(self, monkeypatch):
        monkeypatch.setenv("DEFAULT_RESEARCH_MODE", "verified")
        monkeypatch.setenv("DEFAULT_SEARCH_PROFILE", "multi-route")
        monkeypatch.setenv("DEFAULT_SEARCH_RESULT_NUM", "30")
        monkeypatch.setenv("DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS", "7")
        monkeypatch.setenv("DEFAULT_OUTPUT_DETAIL_LEVEL", "compact")
        effective = resolve_effective_research_params()
        runtime = PipelineRuntime()

        env, overrides = runtime.build_config_overrides(
            RequestLike(query="hello", **effective.as_dict())
        )

        assert env["SEARCH_PROVIDER_MODE"] == "merge"
        assert env["SEARCH_RESULT_NUM"] == "30"
        assert "agent=demo_verified_search" in overrides
        assert "agent.verification.min_search_rounds=7" in overrides
        assert "++agent.output_detail_level=compact" in overrides

    def test_returns_tuple_of_env_and_overrides(self):
        runtime = PipelineRuntime()
        req = _make_request()
        result = runtime.build_config_overrides(req)
        assert isinstance(result, tuple) and len(result) == 2
        env, overrides = result
        assert isinstance(env, dict)
        assert isinstance(overrides, list)

    def test_verified_mode_propagates_min_rounds(self):
        runtime = PipelineRuntime()
        req = _make_request(
            mode="verified",
            search_profile="parallel-trusted",
            verification_min_search_rounds=6,
        )
        env, overrides = runtime.build_config_overrides(req)
        # mode 决定 agent yaml
        assert "agent=demo_verified_search" in overrides
        # min_search_rounds 仅在 verified 时追加
        assert "agent.verification.min_search_rounds=6" in overrides
        # parallel-trusted profile 注入置信度阈值 env
        assert env["SEARCH_PROVIDER_MODE"] == "parallel_conf_fallback"
        assert "SEARCH_CONFIDENCE_ENABLED" in env

    def test_balanced_mode_does_not_inject_min_rounds(self):
        runtime = PipelineRuntime()
        req = _make_request(mode="balanced", verification_min_search_rounds=8)
        _, overrides = runtime.build_config_overrides(req)
        assert not any("verification.min_search_rounds" in o for o in overrides)
        assert "agent=demo_search_only" in overrides

    def test_output_detail_level_compact_overrides(self):
        runtime = PipelineRuntime()
        req = _make_request(output_detail_level="compact")
        _, overrides = runtime.build_config_overrides(req)
        assert "++agent.output_detail_level=compact" in overrides

    def test_search_result_num_injected_into_env(self):
        runtime = PipelineRuntime()
        req = _make_request(search_result_num=30)
        env, _ = runtime.build_config_overrides(req)
        assert env["SEARCH_RESULT_NUM"] == "30"

    def test_invalid_mode_falls_back_to_balanced(self):
        runtime = PipelineRuntime()
        req = _make_request(mode="not-a-real-mode")
        _, overrides = runtime.build_config_overrides(req)
        # balanced 默认 → demo_search_only
        assert "agent=demo_search_only" in overrides

    def test_base_llm_overrides_present(self, monkeypatch):
        monkeypatch.setenv("DEFAULT_LLM_PROVIDER", "qwen")
        monkeypatch.setenv("BASE_URL", "http://example.com/v1")
        monkeypatch.setenv("API_KEY", "sk-xxx")
        runtime = PipelineRuntime()
        _, overrides = runtime.build_config_overrides(_make_request())
        assert "llm=qwen-3" in overrides
        assert "llm.provider=qwen" in overrides
        assert "llm.base_url=http://example.com/v1" in overrides
        assert "llm.api_key=sk-xxx" in overrides
        assert "llm.async_client=true" in overrides

    def test_blank_stage_model_env_falls_back_before_hydra_compose(
        self,
        monkeypatch,
    ):
        """空白 MODEL_* 不得被提升为显式空 Hydra 配置。"""
        monkeypatch.setenv("DEFAULT_MODEL_NAME", "base-model")
        monkeypatch.setenv("MODEL_FAST_NAME", "fast-model")
        monkeypatch.setenv("MODEL_SUMMARY_NAME", "   ")
        runtime = PipelineRuntime()

        _, overrides = runtime.build_config_overrides(_make_request(mode="balanced"))
        cfg = runtime.load_hydra_config(overrides)

        assert cfg.llm.model_name == "base-model"
        assert cfg.llm.model_fast_name == "fast-model"
        assert cfg.llm.model_summary_name == "fast-model"

    def test_worker_can_disable_async_llm_override(self, monkeypatch):
        monkeypatch.setattr(
            "settings.settings.worker.force_async_llm_client", False, raising=False
        )
        runtime = PipelineRuntime()
        _, overrides = runtime.build_config_overrides(_make_request())
        assert "llm.async_client=false" in overrides

    def test_mode_overrides_appear_after_base_to_take_precedence(self):
        """mode_overrides 必须排在 base llm overrides 之后，才能覆盖同名字段。"""
        runtime = PipelineRuntime()
        _, overrides = runtime.build_config_overrides(_make_request(mode="balanced"))
        # 找到 base 的 llm.provider 与 mode 的 agent= 的位置
        base_idx = next(
            (i for i, o in enumerate(overrides) if o.startswith("llm.provider=")), -1
        )
        mode_idx = next(
            (i for i, o in enumerate(overrides) if o.startswith("agent=")), -1
        )
        assert 0 <= base_idx < mode_idx

    @pytest.mark.asyncio
    async def test_component_discovery_failure_closes_all_created_managers(
        self,
        monkeypatch,
    ):
        """工具定义发现失败时，Pipeline 尚未接管的 manager 必须立即清理。"""
        runtime = PipelineRuntime()
        main_tm = MagicMock()
        main_tm.get_all_tool_definitions = AsyncMock(return_value=[])
        main_tm.aclose = AsyncMock()
        sub_tm = MagicMock()
        sub_tm.get_all_tool_definitions = AsyncMock(
            side_effect=RuntimeError("sub discovery failed")
        )
        sub_tm.aclose = AsyncMock()
        cfg = SimpleNamespace(agent=SimpleNamespace(sub_agents={}))

        monkeypatch.setattr(
            runtime,
            "build_config_overrides",
            lambda _req: ({}, []),
        )
        monkeypatch.setattr(
            runtime,
            "load_hydra_config",
            lambda _overrides: cfg,
        )

        with (
            patch(
                "src.core.pipeline.create_pipeline_components",
                return_value=(main_tm, {"researcher": sub_tm}, MagicMock()),
            ),
            pytest.raises(RuntimeError, match="sub discovery failed"),
        ):
            await runtime.create_runtime_components(_make_request())

        main_tm.aclose.assert_awaited_once()
        sub_tm.aclose.assert_awaited_once()
