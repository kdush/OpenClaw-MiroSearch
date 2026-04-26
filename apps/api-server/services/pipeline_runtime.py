"""PipelineRuntime: pipeline 运行时组件管理。

关键问题:
当前 _ensure_pipeline_loaded() 会缓存 ToolManager / OutputFormatter 等运行时对象，
但 ToolManager.set_task_log() 会写入任务级状态，跨任务并发复用存在风险。

改造原则:
1. 可缓存层：Hydra overrides、配置快照、tool definitions
2. 每任务新建层：ToolManager、sub_agent_tool_managers、OutputFormatter、TaskEventSink、TaskLog、Orchestrator
"""

import asyncio
import logging
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from omegaconf import DictConfig

from services.profile_resolver import build_full_overrides
from settings import settings

logger = logging.getLogger("api-server.pipeline_runtime")


@contextmanager
def _temporary_env_vars(overrides: Dict[str, str]):
    """临时设置进程环境变量，确保不同检索源策略互不影响。

    与 ``apps/gradio-demo/main.py`` 内同名函数行为完全一致：进入上下文时备份
    并写入新值；退出上下文时恢复原值（None 视为未设置，需 ``pop`` 删除）。
    """
    if not overrides:
        yield
        return

    previous: Dict[str, Optional[str]] = {}
    for key, value in overrides.items():
        previous[key] = os.environ.get(key)
        os.environ[key] = value
    try:
        yield
    finally:
        for key, old in previous.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


@dataclass
class RequestLike:
    """请求参数接口（用于类型提示）。"""

    query: str
    mode: str
    search_profile: str
    search_result_num: int
    verification_min_search_rounds: int
    output_detail_level: str


class PipelineRuntime:
    """Pipeline 运行时管理器。

    管理配置加载和运行时组件创建，确保并发安全。
    """

    def __init__(self):
        self._hydra_initialized = False
        self._hydra_lock = threading.Lock()
        self._config_cache: Dict[str, DictConfig] = {}
        self._config_cache_lock = threading.Lock()
        # 串行化 env 修改 + 组件创建：worker 进程内多个 asyncio 任务可能并发请求，
        # 而检索源相关 env 是进程级的，需保证 _temporary_env_vars 上下文期间不被覆盖。
        self._components_lock = asyncio.Lock()

    def _get_conf_dir(self) -> str:
        """获取配置目录。"""
        conf_dir = settings.agent_conf_dir
        if not conf_dir:
            conf_dir = str(Path(__file__).resolve().parents[2] / "miroflow-agent" / "conf")
        return conf_dir

    def _init_hydra(self) -> None:
        """初始化 Hydra（仅一次）。"""
        if self._hydra_initialized:
            return

        with self._hydra_lock:
            if self._hydra_initialized:
                return

            from hydra import initialize_config_dir

            conf_dir = self._get_conf_dir()
            try:
                initialize_config_dir(config_dir=conf_dir, version_base=None)
                self._hydra_initialized = True
            except Exception as e:
                logger.warning("Hydra 初始化状态: %s", e)
                self._hydra_initialized = True  # 可能已初始化

    def build_config_overrides(self, req: RequestLike) -> Tuple[Dict[str, str], List[str]]:
        """根据请求参数构建 (search_env, hydra_overrides)。

        与 gradio-demo 的 ``_ensure_preloaded`` 行为对齐：
        1. 先按 ``DEFAULT_LLM_PROVIDER`` 选定 base llm yaml；
        2. 再用 ``profile_resolver.build_full_overrides`` 注入 mode + 输出篇幅档位
           的 hydra 覆盖（``agent=...``、``llm.model_name=...``、轮次/token 等）；
        3. 同步返回检索源策略对应的进程环境变量字典，供 ``_temporary_env_vars`` 使用。
        """
        llm_provider = os.getenv("DEFAULT_LLM_PROVIDER", "qwen")
        provider_config_map = {
            "anthropic": "claude-3-7",
            "openai": "gpt-5",
            "qwen": "qwen-3",
        }
        llm_config = provider_config_map.get(llm_provider, "qwen-3")

        base_overrides: List[str] = [
            f"llm={llm_config}",
            f"llm.provider={llm_provider}",
            f"llm.base_url={os.getenv('BASE_URL', 'http://localhost:11434')}",
            f"llm.api_key={os.getenv('API_KEY', '')}",
            f"llm.async_client={str(settings.worker.force_async_llm_client).lower()}",
        ]

        search_env, mode_overrides = build_full_overrides(
            mode=req.mode,
            search_profile=req.search_profile,
            search_result_num=req.search_result_num,
            verification_min_search_rounds=req.verification_min_search_rounds,
            output_detail_level=req.output_detail_level,
        )

        # mode_overrides 内含 agent=... / llm.model_name=... 等；放在 base 之后
        # 以便覆盖 base llm yaml 中的同名字段，与 demo 行为一致。
        return search_env, base_overrides + mode_overrides

    def load_hydra_config(self, overrides: List[str]) -> DictConfig:
        """加载 Hydra 配置（带缓存）。"""
        cache_key = "|".join(overrides)

        with self._config_cache_lock:
            if cache_key in self._config_cache:
                return self._config_cache[cache_key]

        self._init_hydra()

        from hydra import compose

        cfg = compose(config_name="config", overrides=overrides)

        with self._config_cache_lock:
            self._config_cache[cache_key] = cfg

        return cfg

    def get_cached_config(self, req: RequestLike) -> DictConfig:
        """获取缓存的配置（不创建运行时组件）。"""
        _, overrides = self.build_config_overrides(req)
        return self.load_hydra_config(overrides)

    async def create_runtime_components(
        self,
        req: RequestLike,
    ) -> Tuple[DictConfig, Any, Dict[str, Any], Any, List[Dict], Dict[str, List[Dict]]]:
        """创建运行时组件（每任务新建）。

        与 gradio-demo 的本地路径一致：在 ``_temporary_env_vars`` 上下文中创建
        组件，使检索 MCP 子进程从进程 env 继承到正确的 ``SEARCH_PROVIDER_*`` 配置；
        同时用 ``_components_lock`` 串行化，避免并发任务彼此覆盖进程级 env。

        Returns:
            (cfg, main_tm, sub_tms, output_fmt, tool_defs, sub_tool_defs)
        """
        from src.core.pipeline import create_pipeline_components

        search_env, overrides = self.build_config_overrides(req)

        async with self._components_lock:
            with _temporary_env_vars(search_env):
                cfg = self.load_hydra_config(overrides)

                # 每任务新建组件
                main_tm, sub_tms, output_fmt = create_pipeline_components(cfg)

                # 获取工具定义（部分实现会在此时启动 MCP stdio 子进程，故仍需在 env 上下文内）
                tool_defs = await main_tm.get_all_tool_definitions()
                sub_tool_defs: Dict[str, List[Dict]] = {}
                for name, tm in sub_tms.items():
                    sub_tool_defs[name] = await tm.get_all_tool_definitions()

                # 如有子代理，暴露为工具
                if cfg.agent.sub_agents:
                    from src.core.sub_agent import expose_sub_agents_as_tools
                    tool_defs += expose_sub_agents_as_tools(cfg.agent.sub_agents)

        return cfg, main_tm, sub_tms, output_fmt, tool_defs, sub_tool_defs

    def get_log_dir(self) -> str:
        """获取日志目录。"""
        return settings.log_dir


# 全局单例
_pipeline_runtime: Optional[PipelineRuntime] = None
_pipeline_runtime_lock = threading.Lock()


def get_pipeline_runtime() -> PipelineRuntime:
    """获取 PipelineRuntime 单例。"""
    global _pipeline_runtime

    if _pipeline_runtime is not None:
        return _pipeline_runtime

    with _pipeline_runtime_lock:
        if _pipeline_runtime is not None:
            return _pipeline_runtime
        _pipeline_runtime = PipelineRuntime()
        return _pipeline_runtime
