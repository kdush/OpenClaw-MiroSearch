"""Worker 入口脚本。

启动方式:
    uv run python worker.py
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# 确保 miroflow-agent 的 src 在 import 路径中
_AGENT_ROOT = Path(__file__).resolve().parents[2] / "miroflow-agent"
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

load_dotenv()

from arq import run_worker
from settings import settings
from workers.research_worker import WorkerSettings

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s][%(name)s][%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("api-server.worker")


def _log_llm_config():
    """启动时打印 LLM 相关配置，方便排查连接问题。"""
    resolved_values = {
        "WORKER_FORCE_ASYNC_LLM_CLIENT": str(
            settings.worker.force_async_llm_client
        ).lower(),
    }
    keys = [
        "BASE_URL", "API_KEY", "DEFAULT_LLM_PROVIDER", "DEFAULT_MODEL_NAME",
        "MODEL_TOOL_NAME", "MODEL_FAST_NAME", "MODEL_THINKING_NAME",
        "MODEL_SUMMARY_NAME", "MODEL_FALLBACK_NAME", "AGENT_CONFIG",
        "SEARXNG_BASE_URL", "VALKEY_HOST", "VALKEY_PORT",
        "WORKER_FORCE_ASYNC_LLM_CLIENT",
    ]
    for k in keys:
        v = resolved_values.get(k, os.getenv(k, ""))
        # API_KEY 只显示前 8 位
        if "KEY" in k and len(v) > 8:
            v = v[:8] + "..."
        logger.info("  %s = %s", k, v or "(empty)")


def main():
    """启动 worker。"""
    logger.info("Starting research worker...")
    logger.info("Queue: %s", WorkerSettings.queue_name)
    logger.info("Max jobs: %s", WorkerSettings.max_jobs)
    logger.info("Job timeout: %s seconds", WorkerSettings.job_timeout)
    logger.info("--- LLM / Env Config ---")
    _log_llm_config()
    logger.info("--- End Config ---")

    run_worker(WorkerSettings)


if __name__ == "__main__":
    main()
