import base64
import asyncio
import json
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import AsyncGenerator, Dict, List, Optional, Tuple

import gradio as gr
from dotenv import load_dotenv
from hydra import compose, initialize_config_dir
from omegaconf import DictConfig
from prompt_patch import apply_prompt_patch
from src.config.settings import expose_sub_agents_as_tools
from src.core.pipeline import create_pipeline_components, execute_task_pipeline
from utils import replace_chinese_punctuation

# Create global cleanup thread pool for operations that won't be affected by asyncio.cancel
cleanup_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cleanup")

logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv()


def _env_flag(name: str, default_value: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default_value
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default_value: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default_value
    try:
        return int(raw_value)
    except ValueError:
        return default_value


def _load_logo_data_uri() -> str:
    """加载本地 Logo 并转换为 data URI，避免依赖外部静态资源服务。"""
    logo_path = Path(__file__).resolve().parents[2] / "assets" / "mirologo.png"
    if not logo_path.exists():
        logger.warning("未找到本地 logo 文件: %s", logo_path)
        return ""
    try:
        logo_bytes = logo_path.read_bytes()
    except OSError as exc:
        logger.warning("读取本地 logo 失败: %s", exc)
        return ""
    encoded_logo = base64.b64encode(logo_bytes).decode("ascii")
    return f"data:image/png;base64,{encoded_logo}"


# 控制是否启用 demo prompt patch
ENABLE_PROMPT_PATCH = _env_flag("ENABLE_PROMPT_PATCH", True)
if ENABLE_PROMPT_PATCH:
    # 应用自定义系统提示补丁（注入 OpenClaw-MiroSearch 身份）
    apply_prompt_patch()

# 允许通过环境变量控制 DEMO_MODE，默认开启
DEMO_MODE_ENABLED = _env_flag("DEMO_MODE", True)
os.environ["DEMO_MODE"] = "1" if DEMO_MODE_ENABLED else "0"

# Global Hydra initialization flag
_hydra_initialized = False

DEFAULT_MAIN_AGENT_MAX_TURNS = 12
DEFAULT_KEEP_TOOL_RESULT = 2
DEFAULT_CONTEXT_COMPRESS_LIMIT = 2
DEFAULT_RETRY_WITH_SUMMARY = False
DEFAULT_LLM_TEMPERATURE = 0.2
DEFAULT_LLM_MAX_TOKENS = 2048
DEFAULT_BENCHMARK_NAME = "debug"
DEFAULT_RESEARCH_MODE = os.getenv("DEFAULT_RESEARCH_MODE", "balanced")
DEFAULT_SEARCH_PROFILE = os.getenv("DEFAULT_SEARCH_PROFILE", "searxng-first")
SEARCH_RESULT_NUM_CHOICES = [10, 20, 30]
DEFAULT_SEARCH_RESULT_NUM = _env_int(
    "DEFAULT_SEARCH_RESULT_NUM", SEARCH_RESULT_NUM_CHOICES[0]
)
SEARCH_RESULT_DISPLAY_MAX = max(1, _env_int("SEARCH_RESULT_DISPLAY_MAX", 30))
DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS = max(
    1, _env_int("DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS", 3)
)
MAX_VERIFICATION_MIN_SEARCH_ROUNDS = 8
DEFAULT_MODEL_NAME = os.getenv("DEFAULT_MODEL_NAME", "gpt-4o-mini")
DEFAULT_MODEL_TOOL_NAME = os.getenv("MODEL_TOOL_NAME", DEFAULT_MODEL_NAME)
DEFAULT_MODEL_FAST_NAME = os.getenv("MODEL_FAST_NAME", DEFAULT_MODEL_NAME)
DEFAULT_MODEL_THINKING_NAME = os.getenv("MODEL_THINKING_NAME", DEFAULT_MODEL_NAME)
DEFAULT_MODEL_SUMMARY_NAME = os.getenv("MODEL_SUMMARY_NAME", DEFAULT_MODEL_FAST_NAME)

RESEARCH_MODE_CHOICES = [
    "production-web",
    "verified",
    "research",
    "balanced",
    "quota",
    "thinking",
]

SEARCH_PROFILE_CHOICES = [
    "searxng-first",
    "serp-first",
    "multi-route",
    "parallel",
    "parallel-trusted",
    "searxng-only",
]

SEARCH_HISTORY_STORAGE_KEY = os.getenv(
    "SEARCH_HISTORY_STORAGE_KEY", "mirothinker.demo.search_history"
)
SEARCH_HISTORY_MAX_ITEMS = max(1, _env_int("SEARCH_HISTORY_MAX_ITEMS", 8))
SEARCH_HISTORY_TITLE = "最近搜索"
SEARCH_HISTORY_HINT = "仅保存在当前浏览器，点击可回填，删除不影响当前结果。"
SEARCH_HISTORY_EMPTY_TEXT = "还没有搜索历史，开始一次研究后会显示在这里。"

SEARCH_PROFILE_ENV_MAP: Dict[str, Dict[str, str]] = {
    "searxng-first": {
        "SEARCH_PROVIDER_ORDER": "searxng,serpapi,serper",
        "SEARCH_PROVIDER_MODE": "fallback",
    },
    "serp-first": {
        "SEARCH_PROVIDER_ORDER": "serpapi,searxng,serper",
        "SEARCH_PROVIDER_MODE": "fallback",
    },
    "multi-route": {
        "SEARCH_PROVIDER_ORDER": "serpapi,searxng,serper",
        "SEARCH_PROVIDER_MODE": "merge",
    },
    "parallel": {
        "SEARCH_PROVIDER_ORDER": "serpapi,searxng,serper",
        "SEARCH_PROVIDER_MODE": "parallel",
        "SEARCH_PROVIDER_PARALLEL_MAX_WAIT_MS": "4500",
        "SEARCH_PROVIDER_PARALLEL_MIN_SUCCESS": "1",
    },
    "parallel-trusted": {
        "SEARCH_PROVIDER_ORDER": "serpapi,searxng,serper",
        "SEARCH_PROVIDER_MODE": "parallel_conf_fallback",
        "SEARCH_PROVIDER_TRUSTED_ORDER": "serpapi,searxng,serper",
        "SEARCH_PROVIDER_PARALLEL_MAX_WAIT_MS": "4500",
        "SEARCH_PROVIDER_PARALLEL_MIN_SUCCESS": "1",
        "SEARCH_PROVIDER_FALLBACK_MAX_STEPS": "3",
        "SEARCH_CONFIDENCE_ENABLED": "1",
        "SEARCH_CONFIDENCE_SCORE_THRESHOLD": "0.62",
        "SEARCH_CONFIDENCE_MIN_RESULTS": "8",
        "SEARCH_CONFIDENCE_MIN_UNIQUE_DOMAINS": "5",
        "SEARCH_CONFIDENCE_MIN_PROVIDER_COVERAGE": "2",
        "SEARCH_CONFIDENCE_MIN_HIGH_CONF_HITS": "2",
        "SEARCH_CONFIDENCE_HIGH_CONF_DOMAINS": "reuters.com,apnews.com,bbc.com,aljazeera.com,state.gov,un.org,iaea.org,who.int",
    },
    "searxng-only": {
        "SEARCH_PROVIDER_ORDER": "searxng",
        "SEARCH_PROVIDER_MODE": "fallback",
    },
}

MODE_OVERRIDE_MAP = {
    "production-web": [
        "agent=prod_search_only",
        "agent.main_agent.max_turns=12",
        "agent.keep_tool_result=-1",
        "agent.context_compress_limit=0",
        "agent.retry_with_summary=false",
        f"llm.model_name={DEFAULT_MODEL_NAME}",
        f"+llm.model_tool_name={DEFAULT_MODEL_TOOL_NAME}",
        f"+llm.model_fast_name={DEFAULT_MODEL_FAST_NAME}",
        f"+llm.model_thinking_name={DEFAULT_MODEL_THINKING_NAME}",
        f"+llm.model_summary_name={DEFAULT_MODEL_SUMMARY_NAME}",
        "llm.max_tokens=2048",
        "+llm.max_retries=3",
        "+llm.retry_wait_seconds=3",
        "+llm.tool_result_max_chars=4000",
    ],
    "verified": [
        "agent=demo_verified_search",
        "agent.main_agent.max_turns=14",
        "agent.keep_tool_result=2",
        "agent.context_compress_limit=2",
        "agent.retry_with_summary=false",
        f"llm.model_name={DEFAULT_MODEL_NAME}",
        f"+llm.model_tool_name={DEFAULT_MODEL_TOOL_NAME}",
        f"+llm.model_fast_name={DEFAULT_MODEL_FAST_NAME}",
        f"+llm.model_thinking_name={DEFAULT_MODEL_THINKING_NAME}",
        f"+llm.model_summary_name={DEFAULT_MODEL_SUMMARY_NAME}",
        "llm.max_tokens=2048",
        "+llm.max_retries=3",
        "+llm.retry_wait_seconds=3",
        "+llm.tool_result_max_chars=3200",
    ],
    "research": [
        "agent=demo_search_only",
        "agent.main_agent.max_turns=8",
        "agent.keep_tool_result=2",
        "agent.context_compress_limit=2",
        "agent.retry_with_summary=false",
        f"llm.model_name={DEFAULT_MODEL_NAME}",
        f"+llm.model_tool_name={DEFAULT_MODEL_TOOL_NAME}",
        f"+llm.model_fast_name={DEFAULT_MODEL_FAST_NAME}",
        f"+llm.model_thinking_name={DEFAULT_MODEL_THINKING_NAME}",
        f"+llm.model_summary_name={DEFAULT_MODEL_SUMMARY_NAME}",
        "llm.max_tokens=2048",
        "+llm.max_retries=4",
        "+llm.retry_wait_seconds=6",
        "+llm.tool_result_max_chars=3000",
    ],
    "balanced": [
        "agent=demo_search_only",
        "agent.main_agent.max_turns=9",
        "agent.keep_tool_result=1",
        "agent.context_compress_limit=1",
        "agent.retry_with_summary=false",
        f"llm.model_name={DEFAULT_MODEL_NAME}",
        f"+llm.model_tool_name={DEFAULT_MODEL_TOOL_NAME}",
        f"+llm.model_fast_name={DEFAULT_MODEL_FAST_NAME}",
        f"+llm.model_thinking_name={DEFAULT_MODEL_THINKING_NAME}",
        f"+llm.model_summary_name={DEFAULT_MODEL_SUMMARY_NAME}",
        "llm.max_tokens=1536",
        "+llm.max_retries=2",
        "+llm.retry_wait_seconds=2",
        "+llm.tool_result_max_chars=2200",
    ],
    "quota": [
        "agent=demo_search_only",
        "agent.main_agent.max_turns=7",
        "agent.keep_tool_result=1",
        "agent.context_compress_limit=1",
        "agent.retry_with_summary=false",
        f"llm.model_name={DEFAULT_MODEL_FAST_NAME}",
        f"+llm.model_tool_name={DEFAULT_MODEL_FAST_NAME}",
        f"+llm.model_fast_name={DEFAULT_MODEL_FAST_NAME}",
        f"+llm.model_thinking_name={DEFAULT_MODEL_THINKING_NAME}",
        f"+llm.model_summary_name={DEFAULT_MODEL_SUMMARY_NAME}",
        "llm.max_tokens=1536",
        "+llm.max_retries=2",
        "+llm.retry_wait_seconds=2",
        "+llm.tool_result_max_chars=2200",
    ],
    "thinking": [
        "agent=demo_no_tools",
        "agent.main_agent.max_turns=6",
        "agent.keep_tool_result=0",
        "agent.context_compress_limit=1",
        "agent.retry_with_summary=false",
        f"llm.model_name={DEFAULT_MODEL_THINKING_NAME}",
        f"+llm.model_tool_name={DEFAULT_MODEL_TOOL_NAME}",
        f"+llm.model_fast_name={DEFAULT_MODEL_FAST_NAME}",
        f"+llm.model_thinking_name={DEFAULT_MODEL_THINKING_NAME}",
        f"+llm.model_summary_name={DEFAULT_MODEL_SUMMARY_NAME}",
        "llm.max_tokens=2048",
        "+llm.max_retries=2",
        "+llm.retry_wait_seconds=2",
        "+llm.tool_result_max_chars=2200",
    ],
}


def _read_env_int(name: str, default_value: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default_value
    try:
        return int(raw_value)
    except ValueError:
        return default_value


def _read_env_float(name: str, default_value: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default_value
    try:
        return float(raw_value)
    except ValueError:
        return default_value


def _read_env_bool(name: str, default_value: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default_value
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_research_mode(mode: Optional[str]) -> str:
    if not mode:
        mode = DEFAULT_RESEARCH_MODE
    normalized_mode = str(mode).strip().lower()
    if normalized_mode not in MODE_OVERRIDE_MAP:
        logger.warning("未知检索模式 %s，回退到 balanced", mode)
        return "balanced"
    return normalized_mode


def _normalize_search_profile(search_profile: Optional[str]) -> str:
    if not search_profile:
        search_profile = DEFAULT_SEARCH_PROFILE
    normalized_profile = str(search_profile).strip().lower()
    if normalized_profile not in SEARCH_PROFILE_ENV_MAP:
        logger.warning("未知检索源策略 %s，回退到 searxng-first", search_profile)
        return "searxng-first"
    return normalized_profile


def _normalize_search_result_num(result_num: Optional[int]) -> int:
    if result_num is None:
        result_num = DEFAULT_SEARCH_RESULT_NUM
    try:
        parsed = int(result_num)
    except (TypeError, ValueError):
        parsed = DEFAULT_SEARCH_RESULT_NUM
    if parsed in SEARCH_RESULT_NUM_CHOICES:
        return parsed
    return SEARCH_RESULT_NUM_CHOICES[0]


def _normalize_verification_min_search_rounds(min_rounds: Optional[int]) -> int:
    if min_rounds is None:
        min_rounds = DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS
    try:
        parsed = int(min_rounds)
    except (TypeError, ValueError):
        parsed = DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS
    return max(1, min(MAX_VERIFICATION_MIN_SEARCH_ROUNDS, parsed))


def _compose_profile_cache_key(
    mode: str,
    search_profile: str,
    search_result_num: int,
    verification_min_search_rounds: int,
) -> Tuple[str, str, int, int]:
    return mode, search_profile, search_result_num, verification_min_search_rounds


@contextmanager
def _temporary_env_vars(overrides: Dict[str, str]):
    """临时设置环境变量，确保不同检索策略互不影响。"""
    if not overrides:
        yield
        return

    previous_values: Dict[str, Optional[str]] = {}
    for key, value in overrides.items():
        previous_values[key] = os.environ.get(key)
        os.environ[key] = value

    try:
        yield
    finally:
        for key, old_value in previous_values.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value


def load_miroflow_config(config_overrides: Optional[object] = None) -> DictConfig:
    """
    Load the full MiroFlow configuration using Hydra, similar to how benchmarks work.
    """
    global _hydra_initialized

    # Get the path to the miroflow agent config directory
    miroflow_config_dir = Path(__file__).parent.parent / "miroflow-agent" / "conf"
    miroflow_config_dir = miroflow_config_dir.resolve()
    logger.debug(f"Config dir: {miroflow_config_dir}")

    if not miroflow_config_dir.exists():
        raise FileNotFoundError(
            f"MiroFlow config directory not found: {miroflow_config_dir}"
        )

    # Initialize Hydra if not already done
    if not _hydra_initialized:
        try:
            initialize_config_dir(
                config_dir=str(miroflow_config_dir), version_base=None
            )
            _hydra_initialized = True
        except Exception as e:
            logger.warning(f"Hydra already initialized or error: {e}")

    # Compose configuration with environment variable overrides
    overrides = []

    # Add environment variable based overrides (refer to scripts/debug.sh)
    llm_provider = os.getenv(
        "DEFAULT_LLM_PROVIDER", "qwen"
    )  # debug.sh defaults to qwen
    model_name = os.getenv(
        "DEFAULT_MODEL_NAME", DEFAULT_MODEL_NAME
    )  # debug.sh default model
    agent_set = os.getenv("DEFAULT_AGENT_SET", "demo_search_only")
    base_url = os.getenv("BASE_URL", "http://localhost:11434")
    api_key = os.getenv("API_KEY", "")  # API key for LLM endpoint
    logger.debug(f"LLM base_url: {base_url}")
    main_agent_max_turns = max(
        1, _read_env_int("MAIN_AGENT_MAX_TURNS", DEFAULT_MAIN_AGENT_MAX_TURNS)
    )
    keep_tool_result = _read_env_int("KEEP_TOOL_RESULT", DEFAULT_KEEP_TOOL_RESULT)
    keep_tool_result = max(-1, keep_tool_result)
    context_compress_limit = max(
        0, _read_env_int("CONTEXT_COMPRESS_LIMIT", DEFAULT_CONTEXT_COMPRESS_LIMIT)
    )
    retry_with_summary = _read_env_bool(
        "RETRY_WITH_SUMMARY", DEFAULT_RETRY_WITH_SUMMARY
    )
    llm_temperature = _read_env_float("LLM_TEMPERATURE", DEFAULT_LLM_TEMPERATURE)
    llm_temperature = min(2.0, max(0.0, llm_temperature))
    llm_max_tokens = max(256, _read_env_int("LLM_MAX_TOKENS", DEFAULT_LLM_MAX_TOKENS))
    benchmark_name = os.getenv("DEFAULT_BENCHMARK", DEFAULT_BENCHMARK_NAME).strip()
    if not benchmark_name:
        benchmark_name = DEFAULT_BENCHMARK_NAME

    # Map provider names to config files
    # Available configs: default.yaml, claude-3-7.yaml, gpt-5.yaml, qwen-3.yaml
    provider_config_map = {
        "anthropic": "claude-3-7",
        "openai": "gpt-5",
        "qwen": "qwen-3",
    }

    llm_config = provider_config_map.get(
        llm_provider, "qwen-3"
    )  # fallback to qwen-3 config
    overrides.extend(
        [
            f"llm={llm_config}",
            f"llm.provider={llm_provider}",
            f"llm.model_name={model_name}",
            f"llm.base_url={base_url}",
            f"llm.api_key={api_key}",
            f"agent={agent_set}",
            f"agent.main_agent.max_turns={main_agent_max_turns}",
            f"agent.keep_tool_result={keep_tool_result}",
            f"agent.context_compress_limit={context_compress_limit}",
            f"agent.retry_with_summary={str(retry_with_summary)}",
            f"llm.temperature={llm_temperature}",
            f"llm.max_tokens={llm_max_tokens}",
            f"benchmark={benchmark_name}",
        ]
    )

    # Add config overrides from request
    if config_overrides:
        if isinstance(config_overrides, list):
            overrides.extend([str(item) for item in config_overrides])
        elif isinstance(config_overrides, dict):
            for key, value in config_overrides.items():
                if isinstance(value, dict):
                    for subkey, subvalue in value.items():
                        overrides.append(f"{key}.{subkey}={subvalue}")
                else:
                    overrides.append(f"{key}={value}")

    try:
        cfg = compose(config_name="config", overrides=overrides)
        return cfg
    except Exception as e:
        logger.error(f"Failed to compose Hydra config: {e}")
        exit()


# Lazy loading for tool definitions to speed up page load
# 按检索模式缓存，支持通过参数动态切换
_preload_cache = {}
_preload_lock = threading.Lock()


def _ensure_preloaded(
    mode: str,
    search_profile: str,
    search_result_num: int,
    verification_min_search_rounds: int,
):
    """按检索模式与检索源策略懒加载 pipeline 组件。"""
    global _preload_cache
    resolved_result_num = _normalize_search_result_num(search_result_num)
    resolved_min_rounds = _normalize_verification_min_search_rounds(
        verification_min_search_rounds
    )
    cache_key = _compose_profile_cache_key(
        mode, search_profile, resolved_result_num, resolved_min_rounds
    )
    if cache_key in _preload_cache:
        return

    with _preload_lock:
        if cache_key in _preload_cache:
            return

        search_env = dict(
            SEARCH_PROFILE_ENV_MAP.get(
                search_profile, SEARCH_PROFILE_ENV_MAP["searxng-first"]
            )
        )
        search_env["SEARCH_RESULT_NUM"] = str(resolved_result_num)
        mode_overrides = list(MODE_OVERRIDE_MAP.get(mode, MODE_OVERRIDE_MAP["balanced"]))
        if mode == "verified":
            mode_overrides.append(
                f"agent.verification.min_search_rounds={resolved_min_rounds}"
            )
        logger.info(
            "Loading pipeline components | mode=%s | search_profile=%s | result_num=%s | min_rounds=%s | provider_order=%s | provider_mode=%s",
            mode,
            search_profile,
            resolved_result_num,
            resolved_min_rounds,
            search_env.get("SEARCH_PROVIDER_ORDER", ""),
            search_env.get("SEARCH_PROVIDER_MODE", ""),
        )
        with _temporary_env_vars(search_env):
            cfg = load_miroflow_config(mode_overrides)
            main_agent_tool_manager, sub_agent_tool_managers, output_formatter = (
                create_pipeline_components(cfg)
            )
            tool_definitions = asyncio.run(
                main_agent_tool_manager.get_all_tool_definitions()
            )
            if cfg.agent.sub_agents:
                tool_definitions += expose_sub_agents_as_tools(cfg.agent.sub_agents)

            sub_agent_tool_definitions = {
                name: asyncio.run(sub_agent_tool_manager.get_all_tool_definitions())
                for name, sub_agent_tool_manager in sub_agent_tool_managers.items()
            }

        _preload_cache[cache_key] = {
            "cfg": cfg,
            "main_agent_tool_manager": main_agent_tool_manager,
            "sub_agent_tool_managers": sub_agent_tool_managers,
            "output_formatter": output_formatter,
            "tool_definitions": tool_definitions,
            "sub_agent_tool_definitions": sub_agent_tool_definitions,
            "search_profile": search_profile,
            "search_result_num": resolved_result_num,
            "verification_min_search_rounds": resolved_min_rounds,
        }
        logger.info(
            "Pipeline components loaded successfully | mode=%s | search_profile=%s | result_num=%s | min_rounds=%s",
            mode,
            search_profile,
            resolved_result_num,
            resolved_min_rounds,
        )


class ThreadSafeAsyncQueue:
    """Thread-safe async queue wrapper"""

    def __init__(self):
        self._queue = asyncio.Queue()
        self._loop = None
        self._closed = False

    def set_loop(self, loop):
        self._loop = loop

    async def put(self, item):
        """Put data safely from any thread"""
        if self._closed:
            return
        await self._queue.put(item)

    def put_nowait_threadsafe(self, item):
        """Put data from other threads - use direct queue put for lower latency"""
        if self._closed or not self._loop:
            return
        # Use put_nowait directly instead of creating a task for lower latency
        self._loop.call_soon_threadsafe(lambda: self._queue.put_nowait(item))

    async def get(self):
        return await self._queue.get()

    def close(self):
        self._closed = True


def filter_google_search_organic(organic: List[dict]) -> List[dict]:
    """
    Filter google search organic results to remove unnecessary information
    """
    result = []
    for item in organic:
        result.append(
            {
                "title": item.get("title", ""),
                "link": item.get("link", ""),
            }
        )
    return result


def filter_google_search_payload(result_dict: dict) -> dict:
    """过滤 google_search 结果，保留可视化需要的核心字段与链路元信息。"""
    filtered_payload: Dict[str, object] = {
        "organic": filter_google_search_organic(result_dict.get("organic", []))
    }
    for key in [
        "provider",
        "provider_fallback",
        "route_trace",
        "confidence",
        "searchParameters",
    ]:
        value = result_dict.get(key)
        if value is not None:
            filtered_payload[key] = value
    return filtered_payload


def is_scrape_error(result: str) -> bool:
    """
    Check if the scrape result is an error
    """
    try:
        json.loads(result)
        return False
    except json.JSONDecodeError:
        return True


def filter_message(message: dict) -> dict:
    """
    Filter message to remove unnecessary information
    """
    if message["event"] == "tool_call":
        tool_name = message["data"].get("tool_name")
        tool_input = message["data"].get("tool_input")
        if (
            tool_name == "google_search"
            and isinstance(tool_input, dict)
            and "result" in tool_input
        ):
            try:
                result_dict = json.loads(tool_input["result"])
            except (TypeError, json.JSONDecodeError):
                result_dict = {}
            if isinstance(result_dict, dict) and "organic" in result_dict:
                filtered_result = filter_google_search_payload(result_dict)
                message["data"]["tool_input"]["result"] = json.dumps(
                    filtered_result, ensure_ascii=False
                )
        if (
            tool_name in ["scrape", "scrape_website"]
            and isinstance(tool_input, dict)
            and "result" in tool_input
        ):
            # if error, it can not be json
            if is_scrape_error(tool_input["result"]):
                message["data"]["tool_input"] = {"error": tool_input["result"]}
            else:
                message["data"]["tool_input"] = {}
    return message


async def stream_events_optimized(
    task_id: str,
    query: str,
    mode: Optional[str] = None,
    search_profile: Optional[str] = None,
    search_result_num: Optional[int] = None,
    verification_min_search_rounds: Optional[int] = None,
    disconnect_check=None,
) -> AsyncGenerator[dict, None]:
    """Optimized event stream generator that directly outputs structured events, no longer wrapped as SSE strings."""
    workflow_id = task_id
    resolved_mode = _normalize_research_mode(mode)
    resolved_search_profile = _normalize_search_profile(search_profile)
    resolved_search_result_num = _normalize_search_result_num(search_result_num)
    resolved_verification_min_rounds = _normalize_verification_min_search_rounds(
        verification_min_search_rounds
    )
    last_send_time = time.time()
    last_heartbeat_time = time.time()

    # Create thread-safe queue
    stream_queue = ThreadSafeAsyncQueue()
    stream_queue.set_loop(asyncio.get_event_loop())

    cancel_event = threading.Event()

    def run_pipeline_in_thread():
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            class ThreadQueueWrapper:
                def __init__(self, thread_queue, cancel_event):
                    self.thread_queue = thread_queue
                    self.cancel_event = cancel_event

                async def put(self, item):
                    if self.cancel_event.is_set():
                        logger.info("Pipeline cancelled, stopping execution")
                        return
                    self.thread_queue.put_nowait_threadsafe(filter_message(item))

            wrapper_queue = ThreadQueueWrapper(stream_queue, cancel_event)

            # Ensure pipeline components are loaded (lazy loading)
            _ensure_preloaded(
                resolved_mode,
                resolved_search_profile,
                resolved_search_result_num,
                resolved_verification_min_rounds,
            )
            cache_key = _compose_profile_cache_key(
                resolved_mode,
                resolved_search_profile,
                resolved_search_result_num,
                resolved_verification_min_rounds,
            )
            profile_cache = _preload_cache[cache_key]

            async def pipeline_with_cancellation():
                pipeline_task = asyncio.create_task(
                    execute_task_pipeline(
                        cfg=profile_cache["cfg"],
                        task_id=workflow_id,
                        task_description=query,
                        task_file_name=None,
                        main_agent_tool_manager=profile_cache["main_agent_tool_manager"],
                        sub_agent_tool_managers=profile_cache["sub_agent_tool_managers"],
                        output_formatter=profile_cache["output_formatter"],
                        stream_queue=wrapper_queue,
                        log_dir=os.getenv("LOG_DIR", "logs/api-server"),
                        tool_definitions=profile_cache["tool_definitions"],
                        sub_agent_tool_definitions=profile_cache["sub_agent_tool_definitions"],
                    )
                )

                async def check_cancellation():
                    while not cancel_event.is_set():
                        await asyncio.sleep(0.5)
                    logger.info("Cancel event detected, cancelling pipeline")
                    pipeline_task.cancel()

                cancel_task = asyncio.create_task(check_cancellation())

                try:
                    done, pending = await asyncio.wait(
                        [pipeline_task, cancel_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                    for task in done:
                        if task == pipeline_task:
                            try:
                                await task
                            except asyncio.CancelledError:
                                logger.info("Pipeline task was cancelled")
                except Exception as e:
                    logger.error(f"Pipeline execution error: {e}")
                    pipeline_task.cancel()
                    cancel_task.cancel()

            loop.run_until_complete(pipeline_with_cancellation())
        except Exception as e:
            if not cancel_event.is_set():
                logger.error(f"Pipeline error: {e}", exc_info=True)
                stream_queue.put_nowait_threadsafe(
                    {
                        "event": "error",
                        "data": {"error": str(e), "workflow_id": workflow_id},
                    }
                )
        finally:
            stream_queue.put_nowait_threadsafe(None)
            if "loop" in locals():
                loop.close()

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(run_pipeline_in_thread)

    try:
        while True:
            try:
                if disconnect_check and await disconnect_check():
                    logger.info("Client disconnected, stopping pipeline")
                    cancel_event.set()
                    break
                message = await asyncio.wait_for(stream_queue.get(), timeout=0.1)
                if message is None:
                    logger.info("Pipeline completed")
                    break
                yield message
                last_send_time = time.time()
            except asyncio.TimeoutError:
                current_time = time.time()
                if current_time - last_send_time > 300:
                    logger.info("Stream timeout")
                    break
                if future.done():
                    try:
                        message = stream_queue._queue.get_nowait()
                        if message is not None:
                            yield message
                            continue
                    except Exception:
                        break
                if current_time - last_heartbeat_time >= 15:
                    yield {
                        "event": "heartbeat",
                        "data": {"timestamp": current_time, "workflow_id": workflow_id},
                    }
                    last_heartbeat_time = current_time
    except Exception as e:
        logger.error(f"Stream error: {e}", exc_info=True)
        yield {
            "event": "error",
            "data": {"workflow_id": workflow_id, "error": f"Stream error: {str(e)}"},
        }
    finally:
        cancel_event.set()
        stream_queue.close()
        try:
            future.result(timeout=1.0)
        except Exception:
            pass
        executor.shutdown(wait=False)


# ========================= Gradio Integration =========================


def _init_render_state():
    return {
        "agent_order": [],
        "agents": {},  # agent_id -> {"agent_name": str, "tool_call_order": [], "tools": {tool_call_id: {...}}}
        "current_agent_id": None,
        "errors": [],
    }


def _format_think_content(text: str) -> str:
    """Convert <think> tags to readable markdown format."""
    import re

    # Replace <think> tags with blockquote format (no label)
    text = re.sub(r"<think>\s*", "\n> ", text)
    text = re.sub(r"\s*</think>", "\n", text)
    # Convert newlines within thinking to blockquote continuation
    lines = text.split("\n")
    result = []
    in_thinking = False
    for line in lines:
        if line.strip().startswith(">") and not in_thinking:
            in_thinking = True
            result.append(line)
        elif in_thinking and line.strip() and not line.startswith(">"):
            result.append(f"> {line}")
        else:
            if line.strip() == "" and in_thinking:
                in_thinking = False
            result.append(line)
    return "\n".join(result)


def _append_show_text(tool_entry: dict, delta: str):
    existing = tool_entry.get("content", "")
    # Skip "Final boxed answer" content (already shown in main response)
    if "Final boxed answer" in delta:
        return
    # Format think tags for display
    formatted_delta = _format_think_content(delta)
    tool_entry["content"] = existing + formatted_delta


def _is_empty_payload(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        stripped = value.strip()
        return stripped == "" or stripped in ("{}", "[]")
    if isinstance(value, (dict, list, tuple, set)):
        return len(value) == 0
    return False


def _format_search_results(tool_input: dict, tool_output: dict) -> str:
    """Format google_search results in a beautiful card layout."""
    lines = []

    # Get search query from input
    query = ""
    if isinstance(tool_input, dict):
        query = tool_input.get("q", "") or tool_input.get("query", "")

    # Parse results from output - handle multiple formats
    results = []
    provider_mode = ""
    providers_with_results: List[str] = []
    route_trace: List[dict] = []
    confidence_info: Dict[str, object] = {}
    fallback_errors: List[str] = []
    if isinstance(tool_output, dict):
        # Case 1: output has "result" field containing JSON string
        result_str = tool_output.get("result", "")
        if isinstance(result_str, str) and result_str.strip():
            try:
                result_data = json.loads(result_str)
                if isinstance(result_data, dict):
                    results = result_data.get("organic", [])
                    search_params = result_data.get("searchParameters", {})
                    if isinstance(search_params, dict):
                        provider_mode = str(
                            search_params.get("provider_mode", "")
                        ).strip()
                        providers_with_results = [
                            str(item)
                            for item in search_params.get("providers_with_results", [])
                        ]
                        raw_route_trace = result_data.get("route_trace") or search_params.get(
                            "route_trace"
                        )
                        if isinstance(raw_route_trace, list):
                            route_trace = [
                                item for item in raw_route_trace if isinstance(item, dict)
                            ]
                    raw_confidence = result_data.get("confidence")
                    if isinstance(raw_confidence, dict):
                        confidence_info = raw_confidence
                    raw_fallback = result_data.get("provider_fallback", [])
                    if isinstance(raw_fallback, list):
                        fallback_errors = [str(item) for item in raw_fallback if item]
            except json.JSONDecodeError:
                pass
        elif isinstance(result_str, dict):
            results = result_str.get("organic", [])

        # Case 2: output directly contains "organic" field
        if not results and "organic" in tool_output:
            results = tool_output.get("organic", [])

    if not results and not query:
        return ""

    # Build the card
    lines.append('<div class="search-card">')

    # Header with query
    if query:
        lines.append('<div class="search-header">')
        lines.append('<span class="search-icon">🔍</span>')
        lines.append(f'<span class="search-query">Search: "{query}"</span>')
        lines.append("</div>")

    # Results count
    if results:
        lines.append(f'<div class="search-count">≡ Found {len(results)} results</div>')
        if provider_mode:
            lines.append(
                f'<div class="search-count">检索模式: <strong>{provider_mode}</strong></div>'
            )
        if providers_with_results:
            providers_text = ", ".join(providers_with_results)
            lines.append(
                f'<div class="search-count">命中搜索源: <strong>{providers_text}</strong></div>'
            )
        if confidence_info:
            score = confidence_info.get("score")
            threshold = confidence_info.get("threshold")
            passed = confidence_info.get("passed")
            lines.append(
                f'<div class="search-count">置信度: <strong>{score}</strong> / 阈值 {threshold} / 通过={passed}</div>'
            )
        if route_trace:
            route_items = []
            for item in route_trace[:8]:
                phase = item.get("phase", "")
                provider = item.get("provider", "")
                status = item.get("status", "")
                count = item.get("result_count")
                suffix = f"({count})" if count is not None else ""
                route_items.append(f"{phase}:{provider}:{status}{suffix}")
            if route_items:
                lines.append(
                    f'<div class="search-count">链路跟踪: {" | ".join(route_items)}</div>'
                )
        if fallback_errors:
            lines.append(
                f'<div class="search-count">补检异常: {"; ".join(fallback_errors[:3])}</div>'
            )

        # Results list
        lines.append('<div class="search-results">')
        display_limit = min(len(results), SEARCH_RESULT_DISPLAY_MAX)
        for item in results[:display_limit]:
            title = item.get("title", "Untitled")
            link = item.get("link", "#")

            lines.append(f"""<a href="{link}" target="_blank" class="search-result-item">
                <span class="result-icon">🌐</span>
                <span class="result-title">{title}</span>
            </a>""")
        lines.append("</div>")
        if len(results) > display_limit:
            lines.append(
                f'<div class="search-count">仅展示前 {display_limit} 条，完整结果共 {len(results)} 条。</div>'
            )

    lines.append("</div>")

    return "\n".join(lines)


def _format_sogou_search_results(tool_input: dict, tool_output: dict) -> str:
    """Format sogou_search results in a beautiful card layout."""
    lines = []

    # Get search query from input
    query = ""
    if isinstance(tool_input, dict):
        query = tool_input.get("q", "") or tool_input.get("query", "")

    # Parse results from output - sogou uses "Pages" instead of "organic"
    results = []
    if isinstance(tool_output, dict):
        result_str = tool_output.get("result", "")
        if isinstance(result_str, str) and result_str.strip():
            try:
                result_data = json.loads(result_str)
                if isinstance(result_data, dict):
                    results = result_data.get("Pages", [])
            except json.JSONDecodeError:
                pass
        elif isinstance(result_str, dict):
            results = result_str.get("Pages", [])

        if not results and "Pages" in tool_output:
            results = tool_output.get("Pages", [])

    if not results and not query:
        return ""

    # Build the card
    lines.append('<div class="search-card">')

    # Header with query
    if query:
        lines.append('<div class="search-header">')
        lines.append('<span class="search-icon">🔍</span>')
        lines.append(f'<span class="search-query">Search: "{query}"</span>')
        lines.append("</div>")

    # Results count
    if results:
        lines.append(f'<div class="search-count">≡ Found {len(results)} results</div>')

        # Results list
        lines.append('<div class="search-results">')
        for item in results[:10]:  # Limit to 10 results
            title = item.get("title", "Untitled")
            link = item.get("url", item.get("link", "#"))

            lines.append(f"""<a href="{link}" target="_blank" class="search-result-item">
                <span class="result-icon">🌐</span>
                <span class="result-title">{title}</span>
            </a>""")
        lines.append("</div>")

    lines.append("</div>")

    return "\n".join(lines)


def _format_scrape_results(tool_input: dict, tool_output: dict) -> str:
    """Format scrape/webpage results in a card layout."""
    lines = []

    # Get URL
    url = ""
    if isinstance(tool_input, dict):
        url = tool_input.get("url", tool_input.get("link", ""))

    # Check for error
    if isinstance(tool_output, dict) and "error" in tool_output:
        lines.append('<div class="scrape-card scrape-error">')
        lines.append('<div class="scrape-header">')
        lines.append('<span class="scrape-icon">🌐</span>')
        lines.append(
            f'<span class="scrape-url">{url[:60]}{"..." if len(url) > 60 else ""}</span>'
        )
        lines.append("</div>")
        lines.append('<div class="scrape-status error">❌ Failed</div>')
        lines.append("</div>")
        return "\n".join(lines)

    # Success case
    lines.append('<div class="scrape-card">')
    if url:
        lines.append('<div class="scrape-header">')
        lines.append('<span class="scrape-icon">🌐</span>')
        lines.append(
            f'<span class="scrape-url">{url[:60]}{"..." if len(url) > 60 else ""}</span>'
        )
        lines.append("</div>")
        lines.append('<div class="scrape-status success">✓ Done</div>')
    lines.append("</div>")

    return "\n".join(lines)


def _render_markdown(state: dict) -> str:
    lines = []
    final_summary_lines = []  # Collect final summary content separately

    # Render errors first if any
    if state.get("errors"):
        for err in state["errors"]:
            lines.append(f'<div class="error-block">❌ {err}</div>')

    # Render all agents' content
    for agent_id in state.get("agent_order", []):
        agent = state["agents"].get(agent_id, {})
        agent_name = agent.get("agent_name", "")
        is_final_summary = agent_name == "Final Summary"

        for call_id in agent.get("tool_call_order", []):
            call = agent["tools"].get(call_id, {})
            tool_name = call.get("tool_name", "unknown_tool")

            # Show text / message - display directly
            if tool_name in ("show_text", "message"):
                content = call.get("content", "")
                if content:
                    if is_final_summary:
                        final_summary_lines.append(content)
                    else:
                        lines.append(content)
                continue

            tool_input = call.get("input", {})
            tool_output = call.get("output", {})
            has_input = not _is_empty_payload(tool_input)
            has_output = not _is_empty_payload(tool_output)

            # Special formatting for google_search
            if tool_name == "google_search" and (has_input or has_output):
                formatted = _format_search_results(tool_input, tool_output)
                if formatted:
                    lines.append(formatted)
                continue

            # Special formatting for sogou_search
            if tool_name == "sogou_search" and (has_input or has_output):
                formatted = _format_sogou_search_results(tool_input, tool_output)
                if formatted:
                    lines.append(formatted)
                continue

            # Special formatting for scrape/webpage tools
            if tool_name in (
                "scrape",
                "scrape_website",
                "scrape_webpage",
                "scrape_and_extract_info",
            ) and (has_input or has_output):
                formatted = _format_scrape_results(tool_input, tool_output)
                if formatted:
                    lines.append(formatted)
                continue

            # Special formatting for code execution tools
            if tool_name in ("python", "run_python_code") and (has_input or has_output):
                # Use pure Markdown to avoid HTML wrapper blocking Markdown rendering
                lines.append("\n---\n")
                lines.append("#### 💻 Code Execution\n")
                # Show code input - try multiple possible keys
                code = ""
                if isinstance(tool_input, dict):
                    code = tool_input.get("code") or tool_input.get("code_block") or ""
                elif isinstance(tool_input, str):
                    code = tool_input
                if code:
                    lines.append(f"\n```python\n{code}\n```\n")
                # Show output if available
                if has_output:
                    output = ""
                    if isinstance(tool_output, dict):
                        output = (
                            tool_output.get("result")
                            or tool_output.get("output")
                            or tool_output.get("stdout")
                            or ""
                        )
                    elif isinstance(tool_output, str):
                        output = tool_output
                    if isinstance(output, str) and output.strip():
                        lines.append("\n**Output:**\n")
                        lines.append(
                            f'\n```text\n{output[:1000]}{"..." if len(output) > 1000 else ""}\n```\n'
                        )
                lines.append("\n✅ Executed\n")
                continue

            # Other tools - show as compact card
            if has_input or has_output:
                target_lines = final_summary_lines if is_final_summary else lines
                target_lines.append('<div class="tool-card">')
                target_lines.append(f'<div class="tool-header">🔧 {tool_name}</div>')
                if has_input:
                    # Show brief input summary
                    if isinstance(tool_input, dict):
                        brief = ", ".join(
                            f"{k}: {str(v)[:30]}..."
                            if len(str(v)) > 30
                            else f"{k}: {v}"
                            for k, v in list(tool_input.items())[:2]
                        )
                        target_lines.append(f'<div class="tool-brief">{brief}</div>')
                if has_output:
                    target_lines.append('<div class="tool-status">✓ Done</div>')
                target_lines.append("</div>")

    # Add final summary with Markdown-based styling (no HTML wrapper to preserve Markdown rendering)
    if final_summary_lines:
        lines.append("\n\n---\n\n")  # Markdown horizontal rule as divider
        lines.append("## 📋 研究总结\n\n")
        lines.extend(final_summary_lines)

    return "\n".join(lines) if lines else "*等待开始研究...*"


def _update_state_with_event(state: dict, message: dict):
    event = message.get("event")
    data = message.get("data", {})
    if event == "start_of_agent":
        agent_id = data.get("agent_id")
        agent_name = data.get("agent_name", "unknown")
        if agent_id and agent_id not in state["agents"]:
            state["agents"][agent_id] = {
                "agent_name": agent_name,
                "tool_call_order": [],
                "tools": {},
            }
            state["agent_order"].append(agent_id)
        state["current_agent_id"] = agent_id
    elif event == "end_of_agent":
        # End marker, no special handling needed, keep structure
        state["current_agent_id"] = None
    elif event == "tool_call":
        tool_call_id = data.get("tool_call_id")
        tool_name = data.get("tool_name", "unknown_tool")
        agent_id = state.get("current_agent_id") or (
            state["agent_order"][-1] if state["agent_order"] else None
        )
        if not agent_id:
            return state
        agent = state["agents"].setdefault(
            agent_id, {"agent_name": "unknown", "tool_call_order": [], "tools": {}}
        )
        tools = agent["tools"]
        if tool_call_id not in tools:
            tools[tool_call_id] = {"tool_name": tool_name}
            agent["tool_call_order"].append(tool_call_id)
        entry = tools[tool_call_id]
        if tool_name == "show_text" and "delta_input" in data:
            delta = data.get("delta_input", {}).get("text", "")
            _append_show_text(entry, delta)
        elif tool_name == "show_text" and "tool_input" in data:
            ti = data.get("tool_input")
            text = ""
            if isinstance(ti, dict):
                text = ti.get("text", "") or (
                    (ti.get("result") or {}).get("text")
                    if isinstance(ti.get("result"), dict)
                    else ""
                )
            elif isinstance(ti, str):
                text = ti
            if text:
                _append_show_text(entry, text)
        else:
            # Distinguish between input and output:
            if "tool_input" in data:
                # Could be input (first time) or output with result (second time)
                ti = data["tool_input"]
                # If contains result, assign to output; otherwise assign to input
                if isinstance(ti, dict) and "result" in ti:
                    entry["output"] = ti
                else:
                    # Only update input if we don't already have valid input data, or if the new data is not empty
                    if "input" not in entry or not _is_empty_payload(ti):
                        entry["input"] = ti
    elif event == "message":
        # Same incremental text display as show_text, aggregated by message_id
        message_id = data.get("message_id")
        agent_id = state.get("current_agent_id") or (
            state["agent_order"][-1] if state["agent_order"] else None
        )
        if not agent_id:
            return state
        agent = state["agents"].setdefault(
            agent_id, {"agent_name": "unknown", "tool_call_order": [], "tools": {}}
        )
        tools = agent["tools"]
        if message_id not in tools:
            tools[message_id] = {"tool_name": "message"}
            agent["tool_call_order"].append(message_id)
        entry = tools[message_id]
        delta_content = (data.get("delta") or {}).get("content", "")
        if isinstance(delta_content, str) and delta_content:
            _append_show_text(entry, delta_content)
    elif event == "error":
        # Collect errors, display uniformly during rendering
        err_text = data.get("error") if isinstance(data, dict) else None
        if not err_text:
            try:
                err_text = json.dumps(data, ensure_ascii=False)
            except Exception:
                err_text = str(data)
        state.setdefault("errors", []).append(err_text)
    else:
        # Ignore heartbeat or other events
        pass

    return state


_CANCEL_FLAGS = {}
_CANCEL_LOCK = threading.Lock()


def _set_cancel_flag(task_id: str):
    with _CANCEL_LOCK:
        _CANCEL_FLAGS[task_id] = True


def _reset_cancel_flag(task_id: str):
    with _CANCEL_LOCK:
        _CANCEL_FLAGS[task_id] = False


async def _disconnect_check_for_task(task_id: str):
    with _CANCEL_LOCK:
        return _CANCEL_FLAGS.get(task_id, False)


def _spinner_markup(running: bool) -> str:
    if not running:
        return ""
    return (
        '\n\n<div style="display:flex;align-items:center;gap:8px;color:#555;margin-top:8px;">'
        '<div style="width:16px;height:16px;border:2px solid #ddd;border-top-color:#3b82f6;border-radius:50%;animation:spin 0.8s linear infinite;"></div>'
        "<span>生成中...</span>"
        "</div>\n<style>@keyframes spin{to{transform:rotate(360deg)}}</style>\n"
    )


async def gradio_run(
    query: str,
    mode: str,
    search_profile: str = DEFAULT_SEARCH_PROFILE,
    search_result_num: int = DEFAULT_SEARCH_RESULT_NUM,
    verification_min_search_rounds: int = DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS,
    ui_state: Optional[dict] = None,
):
    query = replace_chinese_punctuation(query or "")
    resolved_mode = _normalize_research_mode(mode)
    resolved_search_profile = _normalize_search_profile(search_profile)
    resolved_search_result_num = _normalize_search_result_num(search_result_num)
    resolved_verification_min_rounds = _normalize_verification_min_search_rounds(
        verification_min_search_rounds
    )
    task_id = str(uuid.uuid4())
    _reset_cancel_flag(task_id)
    if not ui_state:
        ui_state = {
            "task_id": task_id,
            "mode": resolved_mode,
            "search_profile": resolved_search_profile,
            "search_result_num": resolved_search_result_num,
            "verification_min_search_rounds": resolved_verification_min_rounds,
        }
    else:
        ui_state = {
            **ui_state,
            "task_id": task_id,
            "mode": resolved_mode,
            "search_profile": resolved_search_profile,
            "search_result_num": resolved_search_result_num,
            "verification_min_search_rounds": resolved_verification_min_rounds,
        }
    state = _init_render_state()
    # Initial: disable Run, enable Stop, and show spinner at bottom of text
    yield (
        _render_markdown(state) + _spinner_markup(True),
        gr.update(interactive=False),
        gr.update(interactive=True),
        ui_state,
    )
    async for message in stream_events_optimized(
        task_id,
        query,
        resolved_mode,
        resolved_search_profile,
        resolved_search_result_num,
        resolved_verification_min_rounds,
        lambda: _disconnect_check_for_task(task_id),
    ):
        # Skip heartbeat events - they don't need UI update
        event_type = message.get("event", "unknown")
        if event_type == "heartbeat":
            continue

        state = _update_state_with_event(state, message)
        md = _render_markdown(state)
        yield (
            md + _spinner_markup(True),
            gr.update(interactive=False),
            gr.update(interactive=True),
            ui_state,
        )
        # Small delay to allow Gradio to process the update
        await asyncio.sleep(0.01)
    # End: enable Run, disable Stop, remove spinner
    yield (
        _render_markdown(state),
        gr.update(interactive=True),
        gr.update(interactive=False),
        ui_state,
    )


async def run_research_once(
    query: str,
    mode: str,
    search_profile: str = DEFAULT_SEARCH_PROFILE,
    search_result_num: int = DEFAULT_SEARCH_RESULT_NUM,
    verification_min_search_rounds: int = DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS,
) -> str:
    """供外部智能体调用：输入问题+模式+检索源策略，返回最终 Markdown。"""
    query = replace_chinese_punctuation(query or "")
    resolved_mode = _normalize_research_mode(mode)
    resolved_search_profile = _normalize_search_profile(search_profile)
    resolved_search_result_num = _normalize_search_result_num(search_result_num)
    resolved_verification_min_rounds = _normalize_verification_min_search_rounds(
        verification_min_search_rounds
    )
    task_id = str(uuid.uuid4())
    _reset_cancel_flag(task_id)
    state = _init_render_state()
    async for message in stream_events_optimized(
        task_id,
        query,
        resolved_mode,
        resolved_search_profile,
        resolved_search_result_num,
        resolved_verification_min_rounds,
    ):
        state = _update_state_with_event(state, message)
    return _render_markdown(state)


async def run_research_once_v2(
    query: str,
    mode: str,
    search_profile: str = DEFAULT_SEARCH_PROFILE,
    search_result_num: int = DEFAULT_SEARCH_RESULT_NUM,
    verification_min_search_rounds: int = DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS,
) -> str:
    """扩展 API：支持按请求控制检索条数与最少检索轮次。"""
    return await run_research_once(
        query=query,
        mode=mode,
        search_profile=search_profile,
        search_result_num=search_result_num,
        verification_min_search_rounds=verification_min_search_rounds,
    )


def stop_current(ui_state: Optional[dict]):
    tid = (ui_state or {}).get("task_id")
    if tid:
        _set_cancel_flag(tid)
    # Immediately switch button availability: enable Run, disable Stop
    return (
        gr.update(interactive=True),
        gr.update(interactive=False),
    )


def build_demo():
    logo_data_uri = _load_logo_data_uri()

    custom_css = """
    /* ========== MiroThinker - Modern Clean Design ========== */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    
    /* Base */
    .gradio-container {
        max-width: 100% !important;
        margin: 0 !important;
        padding: 0 !important;
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
        background: #ffffff !important;
        min-height: 100vh;
    }
    
    footer { display: none !important; }
    
    /* ===== Top Navigation ===== */
    .top-nav {
        display: flex;
        align-items: center;
        justify-content: flex-start;
        padding: 16px 32px;
        border-bottom: 1px solid #f0f0f0;
        background: #ffffff;
    }
    
    .nav-left {
        display: flex;
        align-items: center;
        gap: 20px;
    }
    
    .nav-brand {
        display: flex;
        align-items: center;
        gap: 10px;
        font-weight: 600;
        font-size: 1.1em;
        color: #18181b;
    }

    .brand-logo {
        height: 32px;
        width: auto;
        max-width: 120px;
        object-fit: contain;
        display: block;
        flex-shrink: 0;
    }

    .nav-brand-text {
        line-height: 1.2;
        white-space: nowrap;
    }
    
    /* ===== Hero Section ===== */
    .hero-section {
        text-align: center;
        padding: 60px 24px 40px;
        max-width: 900px;
        margin: 0 auto;
    }
    
    .hero-title {
        font-size: 3em;
        font-weight: 700;
        background: linear-gradient(135deg, #10b981 0%, #14b8a6 50%, #06b6d4 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        margin: 0 0 16px 0;
        letter-spacing: -0.02em;
    }
    
    .hero-subtitle {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 16px;
        color: #71717a;
        font-size: 1em;
    }
    
    .hero-line {
        width: 40px;
        height: 1px;
        background: #d4d4d8;
    }
    
    /* ===== Input Section ===== */
    #input-section {
        max-width: 720px !important;
        margin: 0 auto 40px !important;
        background: #ffffff;
        border: 1px solid #e0e0e0;
        border-radius: 16px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.04);
    }
    
    #question-input {
        padding: 20px 24px !important;
        background: #ffffff !important;
        border: none !important;
    }
    
    #question-input textarea {
        background: #ffffff !important;
        border: none !important;
        font-size: 1.05em !important;
        line-height: 1.7 !important;
        color: #18181b !important;
        box-shadow: none !important;
    }
    
    #question-input textarea:focus {
        outline: none !important;
        box-shadow: none !important;
    }
    
    #question-input textarea::placeholder {
        color: #9ca3af !important;
    }

    #search-history-shell {
        padding: 0 24px 12px !important;
    }

    .search-history-card {
        border: 1px solid #e8ecef;
        border-radius: 14px;
        background: linear-gradient(180deg, #fcfffd 0%, #f7fbfa 100%);
        padding: 14px 16px;
    }

    .search-history-head {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 12px;
        margin-bottom: 10px;
    }

    .search-history-title {
        font-size: 0.92em;
        font-weight: 600;
        color: #1f2937;
        margin: 0;
    }

    .search-history-hint {
        font-size: 0.78em;
        color: #6b7280;
        line-height: 1.5;
        margin-top: 4px;
    }

    .search-history-clear {
        border: 1px solid #dbe3e8;
        background: #ffffff;
        color: #4b5563;
        border-radius: 999px;
        font-size: 0.78em;
        font-weight: 500;
        padding: 6px 12px;
        cursor: pointer;
        transition: all 0.2s ease;
        white-space: nowrap;
    }

    .search-history-clear:hover {
        border-color: #c7d2da;
        background: #f8fafc;
        color: #111827;
    }

    .search-history-list {
        display: flex;
        flex-direction: column;
        gap: 8px;
        max-height: 184px;
        overflow-y: auto;
        padding-right: 2px;
    }

    .search-history-item {
        display: flex;
        align-items: stretch;
        gap: 8px;
    }

    .search-history-entry {
        flex: 1;
        min-width: 0;
        border: 1px solid #e5eaee;
        background: #ffffff;
        border-radius: 12px;
        padding: 10px 12px;
        text-align: left;
        cursor: pointer;
        transition: all 0.2s ease;
    }

    .search-history-entry:hover {
        border-color: #b9e3d4;
        background: #f9fffc;
        transform: translateY(-1px);
    }

    .search-history-query {
        color: #111827;
        font-size: 0.9em;
        line-height: 1.5;
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
        overflow: hidden;
        word-break: break-word;
    }

    .search-history-meta {
        color: #6b7280;
        font-size: 0.76em;
        margin-top: 6px;
    }

    .search-history-delete {
        width: 38px;
        flex-shrink: 0;
        border: 1px solid #e5e7eb;
        background: #ffffff;
        color: #9ca3af;
        border-radius: 12px;
        cursor: pointer;
        transition: all 0.2s ease;
        font-size: 1.05em;
    }

    .search-history-delete:hover {
        border-color: #fecaca;
        background: #fff5f5;
        color: #dc2626;
    }

    .search-history-empty {
        border: 1px dashed #d8e2e8;
        border-radius: 12px;
        padding: 14px 12px;
        color: #6b7280;
        font-size: 0.82em;
        text-align: center;
        background: rgba(255, 255, 255, 0.72);
    }
    
    #btn-row {
        padding: 16px 24px !important;
        border-top: 1px solid #f0f0f0;
        gap: 12px !important;
    }
    
    #run-btn {
        background: linear-gradient(135deg, #10b981 0%, #14b8a6 100%) !important;
        color: #ffffff !important;
        border: none !important;
        border-radius: 10px !important;
        padding: 12px 24px !important;
        font-size: 0.95em !important;
        font-weight: 500 !important;
        cursor: pointer !important;
        transition: opacity 0.2s, transform 0.2s !important;
    }
    
    #run-btn:hover {
        opacity: 0.9 !important;
        transform: translateY(-1px) !important;
    }
    
    #stop-btn {
        background: #ffffff !important;
        color: #71717a !important;
        border: 1px solid #e5e5e5 !important;
        border-radius: 10px !important;
        padding: 12px 20px !important;
        font-size: 0.95em !important;
        font-weight: 500 !important;
        cursor: pointer !important;
        transition: all 0.2s !important;
    }
    
    #stop-btn:hover {
        color: #ef4444 !important;
        border-color: #fecaca !important;
        background: #fef2f2 !important;
    }
    
    /* ===== Output Section ===== */
    #output-section {
        max-width: 900px !important;
        margin: 0 auto !important;
        padding: 0 24px 60px !important;
    }
    
    .output-label {
        font-size: 0.85em;
        font-weight: 500;
        color: #71717a;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-bottom: 12px;
        padding: 0 4px;
    }
    
    #log-view {
        padding: 24px !important;
        min-height: 400px;
        max-height: 70vh;
        overflow-y: auto;
        background: #ffffff !important;
        border: 1px solid #e5e5e5 !important;
        border-radius: 16px !important;
    }
    
    #log-view h3 {
        font-size: 0.95em;
        font-weight: 600;
        color: #18181b;
        margin: 24px 0 16px 0;
        padding-bottom: 8px;
        border-bottom: 1px solid #f4f4f5;
    }
    
    #log-view h3:first-child {
        margin-top: 0;
    }
    
    /* Error block */
    .error-block {
        background: #fef2f2;
        border: 1px solid #fecaca;
        border-radius: 10px;
        padding: 12px 16px;
        margin: 12px 0;
        color: #dc2626;
        font-size: 0.9em;
    }
    
    /* Tool card */
    .tool-card {
        background: #fafafa;
        border: 1px solid #e5e5e5;
        border-radius: 10px;
        padding: 12px 16px;
        margin: 12px 0;
    }
    
    .tool-header {
        font-size: 0.9em;
        font-weight: 500;
        color: #3f3f46;
        margin-bottom: 4px;
    }
    
    .tool-brief {
        font-size: 0.8em;
        color: #71717a;
        margin-top: 4px;
    }
    
    .tool-status {
        font-size: 0.8em;
        color: #10b981;
        margin-top: 6px;
    }
    
    #log-view blockquote {
        background: linear-gradient(135deg, #f0fdf4 0%, #ecfeff 100%);
        border: none;
        border-left: 3px solid #10b981;
        padding: 16px 20px;
        margin: 16px 0;
        border-radius: 0 12px 12px 0;
        font-style: normal;
        color: #065f46;
        font-size: 0.9em;
        line-height: 1.7;
    }
    
    #log-view pre {
        background: #f8f9fa !important;
        color: #1e293b !important;
        border-radius: 8px !important;
        padding: 16px !important;
        font-size: 0.85em !important;
        line-height: 1.6 !important;
        overflow-x: auto;
        margin: 12px 0;
        border: 1px solid #e2e8f0;
    }
    
    #log-view pre code {
        background: transparent !important;
        color: #1e293b !important;
        font-family: 'SF Mono', 'Fira Code', 'JetBrains Mono', Consolas, monospace !important;
        font-size: inherit !important;
        padding: 0 !important;
        white-space: pre-wrap;
        word-break: break-word;
    }
    
    #log-view code {
        font-family: 'SF Mono', 'Fira Code', 'JetBrains Mono', Consolas, monospace !important;
        background: #f1f5f9 !important;
        color: #1e293b !important;
        padding: 2px 6px !important;
        border-radius: 4px !important;
        font-size: 0.9em !important;
    }
    
    #log-view p {
        line-height: 1.7;
        color: #3f3f46;
    }
    
    #log-view::-webkit-scrollbar {
        width: 6px;
    }
    
    #log-view::-webkit-scrollbar-track {
        background: transparent;
    }
    
    #log-view::-webkit-scrollbar-thumb {
        background: #e5e5e5;
        border-radius: 3px;
    }
    
    #log-view::-webkit-scrollbar-thumb:hover {
        background: #d4d4d8;
    }
    
    /* ===== Footer ===== */
    .app-footer {
        text-align: center;
        padding: 24px;
        color: #a1a1aa;
        font-size: 0.85em;
        border-top: 1px solid #f0f0f0;
    }
    
    /* ===== Loading Spinner ===== */
    @keyframes spin {
        to { transform: rotate(360deg); }
    }
    
    .loading-indicator {
        display: inline-flex;
        align-items: center;
        gap: 10px;
        color: #10b981;
        font-size: 0.9em;
        padding: 12px 0;
    }
    
    .loading-indicator::before {
        content: '';
        width: 16px;
        height: 16px;
        border: 2px solid #d1fae5;
        border-top-color: #10b981;
        border-radius: 50%;
        animation: spin 0.8s linear infinite;
    }
    
    /* ===== Search Results Card ===== */
    .search-card {
        background: #ffffff;
        border: 1px solid #e5e5e5;
        border-radius: 12px;
        margin: 16px 0;
        overflow: hidden;
    }
    
    .search-header {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 14px 18px;
        background: #fafafa;
        border-bottom: 1px solid #f0f0f0;
    }
    
    .search-icon {
        font-size: 1em;
        color: #10b981;
    }
    
    .search-query {
        font-size: 0.9em;
        color: #3f3f46;
        font-weight: 500;
    }
    
    .search-count {
        padding: 10px 18px;
        font-size: 0.8em;
        color: #71717a;
        background: #fafafa;
        border-bottom: 1px solid #f0f0f0;
    }
    
    .search-results {
        padding: 8px 0;
    }
    
    .search-result-item {
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 12px 18px;
        text-decoration: none;
        color: #3f3f46;
        font-size: 0.9em;
        transition: background 0.15s;
        border-left: 3px solid transparent;
    }
    
    .search-result-item:hover {
        background: #f9fafb;
        border-left-color: #10b981;
    }
    
    .result-icon {
        font-size: 1em;
        flex-shrink: 0;
        opacity: 0.6;
    }
    
    .result-title {
        flex: 1;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
    
    /* ===== Scrape Card ===== */
    .scrape-card {
        background: #ffffff;
        border: 1px solid #e5e5e5;
        border-radius: 10px;
        margin: 12px 0;
        padding: 12px 16px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
    }
    
    .scrape-card.scrape-error {
        border-color: #fecaca;
        background: #fef2f2;
    }
    
    .scrape-header {
        display: flex;
        align-items: center;
        gap: 10px;
        flex: 1;
        min-width: 0;
    }
    
    .scrape-icon {
        font-size: 1em;
        opacity: 0.6;
    }
    
    .scrape-url {
        font-size: 0.85em;
        color: #52525b;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
    
    .scrape-status {
        font-size: 0.8em;
        padding: 4px 10px;
        border-radius: 6px;
        flex-shrink: 0;
    }
    
    .scrape-status.success {
        background: #ecfdf5;
        color: #059669;
    }
    
    .scrape-status.error {
        background: #fef2f2;
        color: #dc2626;
    }
    
    /* ===== Final Summary Section ===== */
    .final-summary-divider {
        height: 1px;
        background: linear-gradient(to right, transparent, #e5e5e5, transparent);
        margin: 32px 0;
    }
    
    .final-summary-section {
        background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%);
        border: 1px solid #e2e8f0;
        border-radius: 16px;
        padding: 24px;
        margin-top: 16px;
    }
    
    .final-summary-header {
        font-size: 1.1em;
        font-weight: 600;
        color: #1e293b;
        margin-bottom: 16px;
        padding-bottom: 12px;
        border-bottom: 2px solid #3b82f6;
        display: inline-block;
    }
    
    .final-summary-content {
        color: #334155;
        line-height: 1.8;
    }
    
    .final-summary-content h1,
    .final-summary-content h2,
    .final-summary-content h3 {
        color: #1e293b;
        margin-top: 1.5em;
        margin-bottom: 0.5em;
    }
    
    .final-summary-content h1 { font-size: 1.4em; }
    .final-summary-content h2 { font-size: 1.2em; }
    .final-summary-content h3 { font-size: 1.1em; }
    
    .final-summary-content p {
        margin: 0.8em 0;
    }
    
    .final-summary-content ul,
    .final-summary-content ol {
        margin: 0.8em 0;
        padding-left: 1.5em;
    }
    
    .final-summary-content li {
        margin: 0.4em 0;
    }
    
    .final-summary-content a {
        color: #3b82f6;
        text-decoration: none;
    }
    
    .final-summary-content a:hover {
        text-decoration: underline;
    }
    
    .final-summary-content code {
        background: #e2e8f0;
        padding: 2px 6px;
        border-radius: 4px;
        font-family: 'SF Mono', 'Fira Code', monospace;
        font-size: 0.9em;
    }
    
    .final-summary-content pre {
        background: #1e293b;
        color: #e2e8f0;
        padding: 16px;
        border-radius: 8px;
        overflow-x: auto;
    }
    
    .final-summary-content pre code {
        background: transparent;
        padding: 0;
        color: inherit;
    }
    
    .final-summary-content table {
        width: 100%;
        border-collapse: collapse;
        margin: 1em 0;
    }
    
    .final-summary-content th,
    .final-summary-content td {
        padding: 10px 12px;
        border: 1px solid #e2e8f0;
        text-align: left;
    }
    
    .final-summary-content th {
        background: #f1f5f9;
        font-weight: 600;
    }
    
    .final-summary-content blockquote {
        border-left: 4px solid #3b82f6;
        margin: 1em 0;
        padding: 0.5em 1em;
        background: #f8fafc;
        color: #475569;
    }
    
    /* ===== Code Execution Card ===== */
    .code-card {
        background: #1e1e2e;
        border: 1px solid #313244;
        border-radius: 12px;
        margin: 12px 0;
        padding: 16px;
        overflow: hidden;
    }
    
    .code-header {
        font-size: 0.9em;
        font-weight: 600;
        color: #cdd6f4;
        margin-bottom: 12px;
        display: flex;
        align-items: center;
        gap: 8px;
    }
    
    .code-card pre {
        background: #11111b !important;
        border-radius: 8px;
        padding: 12px 16px;
        margin: 8px 0;
        overflow-x: auto;
        font-family: 'SF Mono', 'Fira Code', 'JetBrains Mono', Consolas, monospace !important;
        font-size: 0.85em;
        line-height: 1.5;
    }
    
    .code-card code {
        background: transparent !important;
        color: #cdd6f4 !important;
        font-family: 'SF Mono', 'Fira Code', 'JetBrains Mono', Consolas, monospace !important;
    }
    
    .code-output-label {
        font-size: 0.8em;
        color: #a6adc8;
        margin-top: 12px;
        margin-bottom: 4px;
    }
    
    .code-status {
        font-size: 0.8em;
        color: #a6e3a1;
        margin-top: 8px;
        text-align: right;
    }
    
    /* ===== Responsive ===== */
    @media (max-width: 768px) {
        .hero-title {
            font-size: 2em;
        }

        .brand-logo {
            height: 28px;
            max-width: 96px;
        }
        
        .hero-section {
            padding: 40px 16px 24px;
        }
        
        .input-wrapper, .output-wrapper {
            padding: 0 16px;
        }

        #search-history-shell {
            padding: 0 16px 12px !important;
        }

        .search-history-head {
            flex-direction: column;
        }

        .search-history-clear {
            width: 100%;
        }
        
        #log-view {
            max-height: 50vh;
        }
    }
    """

    # 统一使用本地 logo，避免外部资源依赖。
    if logo_data_uri:
        favicon_head = f'<link rel="icon" href="{logo_data_uri}">'
        nav_logo_html = (
            f'<img src="{logo_data_uri}" class="brand-logo" '
            'alt="OpenClaw-MiroSearch logo" />'
        )
    else:
        favicon_head = '<link rel="icon" href="https://dr.miromind.ai/favicon.ico?v=2">'
        nav_logo_html = ""

    history_panel_html = f"""
        <div id="search-history-wrapper" class="search-history-card">
            <div class="search-history-head">
                <div>
                    <div class="search-history-title">{SEARCH_HISTORY_TITLE}</div>
                    <div class="search-history-hint">{SEARCH_HISTORY_HINT}</div>
                </div>
                <button
                    id="search-history-clear"
                    class="search-history-clear"
                    type="button"
                    hidden
                >
                    清空历史
                </button>
            </div>
            <div id="search-history-panel" class="search-history-list"></div>
        </div>
    """

    history_script = f"""
    <script>
    (() => {{
        const STORAGE_KEY = {json.dumps(SEARCH_HISTORY_STORAGE_KEY, ensure_ascii=False)};
        const MAX_ITEMS = {SEARCH_HISTORY_MAX_ITEMS};
        const EMPTY_TEXT = {json.dumps(SEARCH_HISTORY_EMPTY_TEXT, ensure_ascii=False)};
        const SELECTORS = {{
            textarea: "#question-input textarea",
            runButton: "#run-btn",
            historyPanel: "#search-history-panel",
            clearButton: "#search-history-clear",
            historyWrapper: "#search-history-wrapper",
        }};

        const escapeHtml = (value) => String(value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");

        const loadHistory = () => {{
            try {{
                const rawValue = window.localStorage.getItem(STORAGE_KEY);
                if (!rawValue) {{
                    return [];
                }}
                const parsedValue = JSON.parse(rawValue);
                if (!Array.isArray(parsedValue)) {{
                    return [];
                }}
                return parsedValue.filter((item) => item && typeof item.query === "string");
            }} catch (error) {{
                console.warn("读取搜索历史失败", error);
                return [];
            }}
        }};

        const saveHistory = (historyItems) => {{
            try {{
                window.localStorage.setItem(
                    STORAGE_KEY,
                    JSON.stringify(historyItems.slice(0, MAX_ITEMS))
                );
            }} catch (error) {{
                console.warn("保存搜索历史失败", error);
            }}
        }};

        const formatTime = (isoTime) => {{
            if (!isoTime) {{
                return "刚刚";
            }}
            const dateValue = new Date(isoTime);
            if (Number.isNaN(dateValue.getTime())) {{
                return "刚刚";
            }}
            return dateValue.toLocaleString("zh-CN", {{
                month: "2-digit",
                day: "2-digit",
                hour: "2-digit",
                minute: "2-digit",
            }});
        }};

        const renderHistory = () => {{
            const panelElement = document.querySelector(SELECTORS.historyPanel);
            const clearButtonElement = document.querySelector(SELECTORS.clearButton);
            if (!panelElement || !clearButtonElement) {{
                return false;
            }}

            const historyItems = loadHistory();
            clearButtonElement.hidden = historyItems.length === 0;

            if (historyItems.length === 0) {{
                panelElement.innerHTML = `<div class="search-history-empty">${{escapeHtml(EMPTY_TEXT)}}</div>`;
                return true;
            }}

            panelElement.innerHTML = historyItems
                .map((item) => {{
                    const query = escapeHtml(item.query);
                    const itemId = escapeHtml(item.id || item.saved_at || item.query);
                    const savedAt = escapeHtml(formatTime(item.saved_at));
                    return `
                        <div class="search-history-item">
                            <button
                                type="button"
                                class="search-history-entry"
                                data-history-action="restore"
                                data-history-id="${{itemId}}"
                            >
                                <div class="search-history-query">${{query}}</div>
                                <div class="search-history-meta">上次搜索：${{savedAt}}</div>
                            </button>
                            <button
                                type="button"
                                class="search-history-delete"
                                data-history-action="delete"
                                data-history-id="${{itemId}}"
                                aria-label="删除这条搜索历史"
                                title="删除这条搜索历史"
                            >
                                ×
                            </button>
                        </div>
                    `;
                }})
                .join("");
            return true;
        }};

        const getCurrentQuery = () => {{
            const textareaElement = document.querySelector(SELECTORS.textarea);
            if (!textareaElement) {{
                return "";
            }}
            return textareaElement.value.trim();
        }};

        const setCurrentQuery = (query) => {{
            const textareaElement = document.querySelector(SELECTORS.textarea);
            if (!textareaElement) {{
                return;
            }}

            const descriptor = Object.getOwnPropertyDescriptor(
                window.HTMLTextAreaElement.prototype,
                "value"
            );
            if (descriptor && descriptor.set) {{
                descriptor.set.call(textareaElement, query);
            }} else {{
                textareaElement.value = query;
            }}
            textareaElement.dispatchEvent(new Event("input", {{ bubbles: true }}));
            textareaElement.dispatchEvent(new Event("change", {{ bubbles: true }}));
            textareaElement.focus();
            textareaElement.setSelectionRange(query.length, query.length);
        }};

        const addHistoryItem = (query) => {{
            const normalizedQuery = query.trim();
            if (!normalizedQuery) {{
                return;
            }}

            const nextItem = {{
                id: `${{Date.now()}}-${{Math.random().toString(36).slice(2, 8)}}`,
                query: normalizedQuery,
                saved_at: new Date().toISOString(),
            }};
            const nextHistory = [
                nextItem,
                ...loadHistory().filter((item) => item.query !== normalizedQuery),
            ];
            saveHistory(nextHistory);
            renderHistory();
        }};

        const deleteHistoryItem = (historyId) => {{
            const nextHistory = loadHistory().filter(
                (item) => String(item.id || item.saved_at || item.query) !== historyId
            );
            saveHistory(nextHistory);
            renderHistory();
        }};

        const clearHistory = () => {{
            window.localStorage.removeItem(STORAGE_KEY);
            renderHistory();
        }};

        const restoreHistoryItem = (historyId) => {{
            const historyItem = loadHistory().find(
                (item) => String(item.id || item.saved_at || item.query) === historyId
            );
            if (!historyItem) {{
                return;
            }}
            setCurrentQuery(historyItem.query);
        }};

        const handleDocumentClick = (event) => {{
            const actionElement = event.target.closest("[data-history-action]");
            if (actionElement && actionElement.closest(SELECTORS.historyWrapper)) {{
                const actionName = actionElement.dataset.historyAction;
                const historyId = actionElement.dataset.historyId || "";
                if (actionName === "restore") {{
                    event.preventDefault();
                    restoreHistoryItem(historyId);
                }}
                if (actionName === "delete") {{
                    event.preventDefault();
                    deleteHistoryItem(historyId);
                }}
                return;
            }}

            const clearButtonElement = event.target.closest(SELECTORS.clearButton);
            if (clearButtonElement) {{
                event.preventDefault();
                clearHistory();
                return;
            }}

            const runButtonElement = event.target.closest(SELECTORS.runButton);
            if (runButtonElement) {{
                const currentQuery = getCurrentQuery();
                if (currentQuery) {{
                    addHistoryItem(currentQuery);
                }}
            }}
        }};

        const ensureHistoryMounted = () => {{
            if (renderHistory()) {{
                return;
            }}
            window.setTimeout(ensureHistoryMounted, 120);
        }};

        if (!window.__miroSearchHistoryInitialized) {{
            window.__miroSearchHistoryInitialized = true;
            document.addEventListener("click", handleDocumentClick, true);
            if (document.readyState === "loading") {{
                document.addEventListener("DOMContentLoaded", ensureHistoryMounted, {{ once: true }});
            }} else {{
                ensureHistoryMounted();
            }}
        }}
    }})();
    </script>
    """
    demo_head = f"{favicon_head}{history_script}"

    with gr.Blocks(
        css=custom_css,
        title="OpenClaw-MiroSearch - 深度研究",
        theme=gr.themes.Base(),
        head=demo_head,
    ) as demo:
        # Top Navigation
        gr.HTML(f"""
            <nav class="top-nav">
                <div class="nav-left">
                    <div class="nav-brand">
                        {nav_logo_html}
                        <span class="nav-brand-text">OpenClaw-MiroSearch 深度研究</span>
                    </div>
                </div>
            </nav>
        """)

        # Hero Section
        gr.HTML("""
            <div class="hero-section">
                <h1 class="hero-title">深度研究，洞察未来</h1>
                <div class="hero-subtitle">
                    <span class="hero-line"></span>
                    不止于聊天，用可验证的检索与推理完成研究任务。
                    <span class="hero-line"></span>
                </div>
            </div>
        """)

        # Input Section
        with gr.Column(elem_id="input-section"):
            inp = gr.Textbox(
                lines=4,
                placeholder="请输入你的研究问题...",
                show_label=False,
                elem_id="question-input",
            )
            gr.HTML(history_panel_html, elem_id="search-history-shell")
            mode_selector = gr.Dropdown(
                label="检索模式",
                choices=RESEARCH_MODE_CHOICES,
                value=_normalize_research_mode(DEFAULT_RESEARCH_MODE),
                info="verified=多轮校验(高质量源) / research=质量优先 / balanced=推荐默认 / quota=额度优先 / thinking=纯思考 / production-web=生产风格",
            )
            search_profile_selector = gr.Dropdown(
                label="检索源策略",
                choices=SEARCH_PROFILE_CHOICES,
                value=_normalize_search_profile(DEFAULT_SEARCH_PROFILE),
                info="searxng-first=默认 / serp-first=Serp优先 / multi-route=串行聚合 / parallel=并发聚合 / parallel-trusted=并发+置信不足串行高信源补检 / searxng-only=仅SearXNG",
            )
            search_result_num_selector = gr.Dropdown(
                label="单轮检索条数",
                choices=SEARCH_RESULT_NUM_CHOICES,
                value=_normalize_search_result_num(DEFAULT_SEARCH_RESULT_NUM),
                info="每次 google_search 聚合返回的结果上限，建议 20 或 30 用于交叉验证。",
            )
            verification_min_rounds_selector = gr.Slider(
                minimum=1,
                maximum=MAX_VERIFICATION_MIN_SEARCH_ROUNDS,
                step=1,
                label="最少检索轮次（verified 生效）",
                value=_normalize_verification_min_search_rounds(
                    DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS
                ),
                info="仅在 verified 模式下用于强制多轮检索门槛。",
            )
            with gr.Row(elem_id="btn-row"):
                stop_btn = gr.Button(
                    "⏹ 停止",
                    elem_id="stop-btn",
                    variant="stop",
                    interactive=False,
                    scale=1,
                )
                run_btn = gr.Button(
                    "开始研究 ➤", elem_id="run-btn", variant="primary", scale=2
                )

        # Output Section
        with gr.Column(elem_id="output-section"):
            gr.HTML('<div class="output-label">研究进度</div>')
            out_md = gr.Markdown("*等待开始研究...*", elem_id="log-view")

        # 供 API 调用的隐藏输出
        api_output = gr.Markdown(visible=False)
        api_btn = gr.Button(value="api-run", visible=False)

        # State
        ui_state = gr.State(
            {
                "task_id": None,
                "mode": _normalize_research_mode(DEFAULT_RESEARCH_MODE),
                "search_profile": _normalize_search_profile(DEFAULT_SEARCH_PROFILE),
                "search_result_num": _normalize_search_result_num(
                    DEFAULT_SEARCH_RESULT_NUM
                ),
                "verification_min_search_rounds": _normalize_verification_min_search_rounds(
                    DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS
                ),
            }
        )

        # Event handlers
        run_btn.click(
            fn=gradio_run,
            inputs=[
                inp,
                mode_selector,
                search_profile_selector,
                search_result_num_selector,
                verification_min_rounds_selector,
                ui_state,
            ],
            outputs=[out_md, run_btn, stop_btn, ui_state],
            api_name="run_research_stream",
        )
        stop_btn.click(fn=stop_current, inputs=[ui_state], outputs=[run_btn, stop_btn])
        api_btn.click(
            fn=run_research_once,
            inputs=[inp, mode_selector, search_profile_selector],
            outputs=[api_output],
            api_name="run_research_once",
        )
        gr.Button(value="api-run-v2", visible=False).click(
            fn=run_research_once_v2,
            inputs=[
                inp,
                mode_selector,
                search_profile_selector,
                search_result_num_selector,
                verification_min_rounds_selector,
            ],
            outputs=[api_output],
            api_name="run_research_once_v2",
        )

        # Footer
        gr.HTML("""
            <div class="app-footer">
                由 AI 生成，请对关键信息进行复核。
            </div>
        """)

    return demo


if __name__ == "__main__":
    demo = build_demo()
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))
    demo.queue().launch(server_name=host, server_port=port)
