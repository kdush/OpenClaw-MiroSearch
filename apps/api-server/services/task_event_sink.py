"""TaskEventSink: 事件写入适配器。

execute_task_pipeline() 当前只要求 stream_queue 提供 async def put(item)。
本模块通过 TaskEventSink 适配该接口，将 pipeline 事件转换为持久化事件流写入。

行为要求:
1. 将事件写入 miro:task:{task_id}:events
2. 对 stage_heartbeat、tool_call、start_of_agent 等事件同步更新任务快照中的阶段字段
3. 对 run_metrics 事件同步写入 miro:metrics:last
4. 对 final_output 事件可选写入任务结果缓存
"""

import logging
from typing import Any, Dict, Optional

from services.task_store import TaskStore, TaskStatus

logger = logging.getLogger("api-server.task_event_sink")


class TaskEventSink:
    """事件写入适配器，适配 pipeline 的 stream_queue 接口。"""

    def __init__(
        self,
        task_store: TaskStore,
        task_id: str,
        store_result_on_final: bool = True,
    ):
        """
        Args:
            task_store: 任务存储实例
            task_id: 任务 ID
            store_result_on_final: 是否在 final_output 事件时存储结果
        """
        self._store = task_store
        self._task_id = task_id
        self._store_result_on_final = store_result_on_final
        self._cancelled = False

    async def put(self, item: Dict[str, Any]) -> None:
        """写入事件到持久化事件流。

        Args:
            item: 事件数据，格式为 {"event": str, "data": dict}
        """
        if self._cancelled:
            return

        if not isinstance(item, dict):
            logger.warning("Invalid event item type: %s", type(item))
            return

        event_type = item.get("event", "message")
        data = item.get("data", {})

        try:
            # 写入事件流
            await self._store.append_event(self._task_id, event_type, data)

            # 根据事件类型更新任务快照
            await self._handle_event_side_effects(event_type, data)

        except Exception as e:
            logger.error("Failed to write event %s: %s", event_type, e, exc_info=True)

    async def _handle_event_side_effects(self, event_type: str, data: Dict[str, Any]) -> None:
        """处理事件的副作用：更新阶段、存储结果等。"""

        # stage_heartbeat: 更新当前阶段
        if event_type == "stage_heartbeat":
            phase = str(data.get("phase") or "").strip()
            detail = str(data.get("detail") or "").strip()
            if phase and detail:
                await self._store.update_task_stage(self._task_id, f"{phase}:{detail}")
            elif phase:
                await self._store.update_task_stage(self._task_id, phase)

        # start_of_agent: 更新阶段为 agent 启动
        elif event_type == "start_of_agent":
            agent_name = str(data.get("agent_name") or data.get("agent") or "unknown").strip()
            await self._store.update_task_stage(self._task_id, f"agent:{agent_name}")

        # tool_call: 更新阶段为工具调用
        elif event_type == "tool_call":
            tool_name = str(data.get("tool_name") or data.get("tool") or "unknown").strip()
            await self._store.update_task_stage(self._task_id, f"tool:{tool_name}")

        # run_metrics: 持久化最近运行指标
        elif event_type == "run_metrics":
            await self._store.set_last_run_metrics(data)

        # final_output: 存储最终结果
        elif event_type == "final_output" and self._store_result_on_final:
            markdown = data.get("markdown", "")
            if markdown:
                await self._store.store_result(self._task_id, markdown)

    def cancel(self) -> None:
        """标记取消，后续事件不再写入。"""
        self._cancelled = True

    def is_cancelled(self) -> bool:
        """检查是否已取消。"""
        return self._cancelled
