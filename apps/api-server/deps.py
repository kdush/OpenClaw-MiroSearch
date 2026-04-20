"""依赖注入：保留兼容性函数。

重构后任务状态管理已迁移到 TaskStore，此文件仅保留：
- cleanup_stale_tasks: 兼容旧代码的清理函数（现在由 TaskStore TTL 处理）
- set_last_run_metrics / get_last_run_metrics: 兼容旧代码的指标函数
"""

import threading
from typing import Optional

# 最近一次完成任务的 run_metrics（兼容旧代码）
_last_run_metrics: Optional[dict] = None
_last_run_metrics_lock = threading.Lock()


def cleanup_stale_tasks() -> int:
    """清理已完成且超过保留期的任务。

    重构后由 TaskStore TTL 自动处理，此函数保留兼容性。
    """
    # 不再需要手动清理，TaskStore 使用 Redis TTL
    return 0


def set_last_run_metrics(metrics: dict) -> None:
    """更新最近一次任务的 run_metrics。"""
    global _last_run_metrics
    with _last_run_metrics_lock:
        _last_run_metrics = metrics


def get_last_run_metrics() -> Optional[dict]:
    """获取最近一次任务的 run_metrics。"""
    with _last_run_metrics_lock:
        return _last_run_metrics
