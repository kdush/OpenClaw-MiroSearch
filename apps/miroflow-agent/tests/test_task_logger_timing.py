from pathlib import Path
import sys


# 确保测试可直接导入项目源码
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.logging.task_logger import TaskLog


def test_record_stage_timing_updates_trace_data_and_summary():
    task_log = TaskLog(task_id="timing-test", start_time="2026-03-20 12:00:00")

    task_log.record_stage_timing(
        "pipeline.total",
        1234,
        metadata={"status": "success"},
    )
    task_log.record_stage_timing(
        "pipeline.total",
        4321,
        metadata={"status": "success"},
    )

    stage_timings = task_log.trace_data.get("stage_timings", [])
    assert len(stage_timings) == 2
    assert stage_timings[0]["stage_name"] == "pipeline.total"
    assert stage_timings[0]["metadata"]["status"] == "success"
    assert stage_timings[1]["duration_ms"] == 4321

    stage_summary = task_log.trace_data.get("stage_timing_summary", {})
    assert stage_summary["pipeline.total"]["count"] == 2
    assert stage_summary["pipeline.total"]["total_duration_ms"] == 5555
    assert stage_summary["pipeline.total"]["max_duration_ms"] == 4321
    assert stage_summary["pipeline.total"]["last_duration_ms"] == 4321

    summary_text = task_log.format_stage_timing_summary()
    assert "pipeline.total" in summary_text
    assert "total=5555ms" in summary_text
