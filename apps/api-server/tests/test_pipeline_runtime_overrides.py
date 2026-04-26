"""PipelineRuntime.build_config_overrides 集成测试。

验证 worker 路径与 demo 路径在 mode/search_profile/result_num/min_rounds/detail
五个维度上行为一致：mode_overrides 被正确注入到 hydra overrides；search_env 包含
检索源策略所需的进程环境变量；不再硬编码 ``agent=demo_search_only``。
"""

from services.pipeline_runtime import PipelineRuntime, RequestLike


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
