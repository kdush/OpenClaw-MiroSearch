"""路线图 §7 基线对比工具（``scripts/compare_acceptance_runs.py``）的回归测试。

该工具是 §7「与阶段 A 基线对比」这一完成标准的唯一执行入口，且刻意不依赖 LLM，
因此必须有独立测试钉住：自比全零、方向判定（改善/回归）、缺失字段与缺失用例
的降级行为、百分位口径。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
REPO = Path(__file__).resolve().parents[3]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import compare_acceptance_runs as cmp  # noqa: E402

BASELINE_DIR = REPO / "docs" / "acceptance" / "baseline-2026-09-24"


def _record(
    *,
    status: str = "completed",
    duration: float = 100.0,
    searches: int = 5,
    scrapes: int = 2,
    follow_ups: int = 1,
    llm_calls: int = 8,
    tokens_in: int = 1000,
    tokens_out: int = 200,
    source_urls: int = 10,
    registry: int = 0,
    citation_count: int = 3,
    unsourced: int = 2,
    wrong_citations: int = 0,
    has_conflicts: bool = True,
    claim_verification_ran: bool = False,
    has_claim_topology: bool = False,
) -> dict:
    """构造一条最小可比的验收记录（字段名与 harness 产物一致）。"""
    return {
        "status": status,
        "duration_seconds": duration,
        "metrics": {
            "search_rounds": searches,
            "search_attempts": searches,
            "scrape_count": scrapes,
            "follow_up_searches": follow_ups,
            "model_route_hits": {"glm-5.3-flash": {"glm-5.3-flash": llm_calls}},
        },
        "token_usage": {
            "total_input_tokens": tokens_in,
            "total_output_tokens": tokens_out,
        },
        "source_urls": [f"https://example.com/{i}" for i in range(source_urls)],
        "source_registry": {str(i): {} for i in range(registry)},
        "gate_evaluation": {
            "structure_metadata": {
                "citation_count": citation_count,
                "unsourced_number_warnings": [str(i) for i in range(unsourced)],
                "has_conflicts_section": has_conflicts,
            },
            "structure_issues": (
                [f"citation_target_mismatch:{i}" for i in range(wrong_citations)]
            ),
        },
        "claim_verification_ran": claim_verification_ran,
        "has_claim_topology": has_claim_topology,
    }


class TestLoadRun:
    def test_accepts_directory(self, tmp_path: Path):
        (tmp_path / cmp.RESULTS_FILENAME).write_text(
            json.dumps({"A": _record()}), encoding="utf-8"
        )
        assert set(cmp.load_run(tmp_path).keys()) == {"A"}

    def test_accepts_direct_json_file(self, tmp_path: Path):
        f = tmp_path / "run.json"
        f.write_text(json.dumps({"A": _record(), "B": _record()}), encoding="utf-8")
        assert set(cmp.load_run(f).keys()) == {"A", "B"}

    def test_unwraps_results_envelope(self, tmp_path: Path):
        f = tmp_path / "run.json"
        f.write_text(json.dumps({"results": {"A": _record()}}), encoding="utf-8")
        assert set(cmp.load_run(f).keys()) == {"A"}

    def test_missing_results_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            cmp.load_run(tmp_path)

    def test_non_mapping_payload_raises(self, tmp_path: Path):
        f = tmp_path / "run.json"
        f.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        with pytest.raises(ValueError):
            cmp.load_run(f)


class TestCaseMetrics:
    def test_extracts_and_counts(self):
        m = cmp.case_metrics(_record(llm_calls=11, unsourced=4, wrong_citations=2))
        assert m is not None
        assert m["llm_calls"] == 11
        assert m["unsourced_numbers"] == 4
        assert m["wrong_citations"] == 2
        assert m["sources_discovered"] == 10

    def test_none_record_yields_none(self):
        assert cmp.case_metrics(None) is None

    def test_record_without_gate_evaluation_degrades_gracefully(self):
        rec = _record()
        rec.pop("gate_evaluation")
        m = cmp.case_metrics(rec)
        assert m is not None
        assert m["citation_count"] is None
        assert m["unsourced_numbers"] == 0
        assert m["wrong_citations"] == 0

    def test_llm_calls_tolerates_flat_route_shape(self):
        rec = _record()
        rec["metrics"]["model_route_hits"] = {"glm-5.3-flash": 7}
        assert cmp.case_metrics(rec)["llm_calls"] == 7

    def test_llm_calls_tolerates_missing_routes(self):
        rec = _record()
        rec["metrics"].pop("model_route_hits")
        assert cmp.case_metrics(rec)["llm_calls"] == 0


class TestPercentile:
    def test_empty_is_none(self):
        assert cmp.percentile([], 50) is None

    def test_single_sample(self):
        assert cmp.percentile([42.0], 95) == 42.0

    def test_nearest_rank_p50_p95(self):
        values = [10.0, 20.0, 30.0, 40.0]
        assert cmp.percentile(values, 50) == 20.0
        assert cmp.percentile(values, 95) == 40.0

    def test_unsorted_input_is_handled(self):
        assert cmp.percentile([40.0, 10.0, 30.0, 20.0], 50) == 20.0


class TestAggregate:
    def test_totals_and_percentiles(self):
        per_case = {
            "A": cmp.case_metrics(_record(duration=10.0, searches=2, llm_calls=3)),
            "B": cmp.case_metrics(_record(duration=30.0, searches=4, llm_calls=5)),
        }
        agg = cmp.aggregate({k: v for k, v in per_case.items() if v})
        assert agg["cases"] == 2
        assert agg["total_llm_calls"] == 8
        assert agg["total_search_attempts"] == 6
        assert agg["p50_duration_seconds"] == 10.0
        assert agg["p95_duration_seconds"] == 30.0
        assert agg["total_duration_seconds"] == 40.0

    def test_empty_is_zeroed(self):
        agg = cmp.aggregate({})
        assert agg["cases"] == 0
        assert agg["total_llm_calls"] == 0
        assert agg["p50_duration_seconds"] is None


class TestCompare:
    def test_self_compare_is_all_zero(self):
        run = {"A": _record(), "B": _record(duration=250.0, unsourced=5)}
        result = cmp.compare(run, run)
        for entry in result["cases"].values():
            assert entry["delta"], "self-compare must still emit the metric keys"
            assert all(d == 0 for d in entry["delta"].values())
        assert all(d == 0 for d in result["aggregate_delta"].values())

    def test_regression_and_improvement_directions(self):
        baseline = {"A": _record(searches=5, unsourced=6)}
        candidate = {"A": _record(searches=9, unsourced=1)}
        delta = cmp.compare(baseline, candidate)["cases"]["A"]["delta"]
        assert delta["search_attempts"] == 4
        assert delta["unsourced_numbers"] == -5
        # 检索量上升 = 回归；无依据数字下降 = 改善
        assert cmp._verdict("search_attempts", delta["search_attempts"]) == "回归"
        assert cmp._verdict("unsourced_numbers", delta["unsourced_numbers"]) == "改善"

    def test_missing_case_is_flagged(self):
        baseline = {"A": _record(), "B": _record()}
        candidate = {"A": _record(), "C": _record()}
        result = cmp.compare(baseline, candidate)
        assert result["cases"]["B"]["only_in"] == "baseline"
        assert result["cases"]["B"]["candidate"] is None
        assert result["cases"]["C"]["only_in"] == "candidate"
        assert result["cases"]["C"]["baseline"] is None

    def test_delta_skipped_when_either_side_missing_field(self):
        baseline = {"A": _record()}
        candidate = {"A": _record()}
        candidate["A"]["token_usage"] = {}
        delta = cmp.compare(baseline, candidate)["cases"]["A"]["delta"]
        assert "input_tokens" not in delta
        assert delta["search_attempts"] == 0


class TestRenderMarkdown:
    def test_contains_aggregate_and_case_tables(self):
        run = {
            "H1": _record(
                registry=12, claim_verification_ran=True, has_claim_topology=True
            )
        }
        md = cmp.render_markdown(
            cmp.compare(run, run), baseline_label="base", candidate_label="cand"
        )
        assert "# §7 基线对比报告" in md
        assert "## 运行级汇总" in md
        assert "## 逐用例" in md
        assert "p50 耗时(s)" in md

    def test_new_signals_section_lists_registry_and_topology(self):
        baseline = {"H1": _record()}
        candidate = {
            "H1": _record(
                registry=12, claim_verification_ran=True, has_claim_topology=True
            )
        }
        md = cmp.render_markdown(
            cmp.compare(baseline, candidate), baseline_label="b", candidate_label="c"
        )
        assert "## 候选新增信号（M1/M3/Q4）" in md
        assert "结论核验已跑" in md

    def test_no_new_signals_section_when_absent(self):
        run = {"A": _record()}
        md = cmp.render_markdown(
            cmp.compare(run, run), baseline_label="b", candidate_label="c"
        )
        assert "## 候选新增信号" not in md


class TestFrozenBaselineIntegration:
    """用真实冻结基线做控制组——工具必须能复现基线文档里的数字。"""

    @pytest.mark.skipif(
        not (BASELINE_DIR / cmp.RESULTS_FILENAME).exists(),
        reason="冻结基线不在本机",
    )
    def test_control_group_is_zero_and_reproduces_documented_totals(self):
        run = cmp.load_run(BASELINE_DIR)
        assert {"A", "B", "H1"}.issubset(set(run.keys()))
        result = cmp.compare(run, run)
        assert all(d == 0 for d in result["aggregate_delta"].values())

        agg = result["candidate_aggregate"]
        # BASELINE.md 记录：A/B/H1 = 2/11/13 次 LLM 调用 → 26
        assert agg["total_llm_calls"] == 26
        # B + H1 输入 token 合计（A 为 8008）→ 276956
        assert agg["total_input_tokens"] == 276956
        # H1 门禁记录 8 个无依据数字
        assert agg["total_unsourced_numbers"] == 8
