# Copyright (c) 2025 MiroMind
# This source code is licensed under the Apache 2.0 License.

import asyncio

import hydra
from omegaconf import DictConfig, OmegaConf

# Import from the new modular structure
from src.core.pipeline import (
    create_pipeline_components,
    execute_task_pipeline,
)
from src.logging.task_logger import bootstrap_logger

# Configure logger and get the configured instance
logger = bootstrap_logger()


async def amain(cfg: DictConfig) -> dict:
    """Asynchronous main function."""

    logger.info(OmegaConf.to_yaml(cfg))

    # Create pipeline components using the factory function
    main_agent_tool_manager, sub_agent_tool_managers, output_formatter = (
        create_pipeline_components(cfg)
    )

    # Define task parameters
    task_id = "task_example"
    task_description = "What is the title of today's arxiv paper in computer science?"
    task_file_name = ""

    # Execute task using the pipeline
    pipeline_result = await execute_task_pipeline(
        cfg=cfg,
        task_id=task_id,
        task_file_name=task_file_name,
        task_description=task_description,
        main_agent_tool_manager=main_agent_tool_manager,
        sub_agent_tool_managers=sub_agent_tool_managers,
        output_formatter=output_formatter,
        log_dir=cfg.debug_dir,
    )

    status = pipeline_result.get("status")
    final_summary = pipeline_result.get("final_summary", "")
    final_boxed_answer = pipeline_result.get("final_boxed_answer", "")
    log_file_path = pipeline_result.get("log_file_path", "")
    error = pipeline_result.get("error")
    result_quality = pipeline_result.get("result_quality")

    if status == "completed":
        logger.info(
            "Task %s completed | boxed_answer=%s | log=%s | quality=%s",
            task_id,
            final_boxed_answer,
            log_file_path,
            result_quality,
        )
    elif status == "failed":
        logger.error(
            "Task %s failed: %s",
            task_id,
            error or final_summary or "Unknown pipeline error",
        )
    elif status == "cancelled":
        logger.warning(
            "Task %s cancelled: %s",
            task_id,
            error or final_summary or "Pipeline execution was cancelled",
        )
    else:
        logger.error(
            "Task %s returned invalid pipeline status: %s",
            task_id,
            status,
        )

    return pipeline_result


@hydra.main(config_path="conf", config_name="config", version_base=None)
def main(cfg: DictConfig) -> None:
    pipeline_result = asyncio.run(amain(cfg))
    raise SystemExit(0 if pipeline_result.get("status") == "completed" else 1)


if __name__ == "__main__":
    main()
