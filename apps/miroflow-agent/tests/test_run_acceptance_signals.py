"""验收 harness 的证据信号与分批汇总回归测试。

两处缺陷（2026-09-29 真实验收运行时发现）：

1. ``has_claim_topology`` 原先用 ``raw_summary``（注入前的正文）判定，而 Q4 的
   关系拓扑由 ``prepare_user_facing_report`` 注入、只可能出现在交付文本里 ——
   实测 16 个用例全部为 ``False``，把 Q4 的落地证据系统性漏记。
2. 汇总文件原先只写本次调用的用例；deep/hotspot 用例单个 3–7 分钟、必然分批跑，
   先跑的用例会从汇总里消失，而对比工具正是读这个文件。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_acceptance_live as harness  # noqa: E402

FENCE = "```mermaid"
TOPOLOGY = f'## 关系拓扑 / Relationship Map\n\n{FENCE}\nflowchart TB\n  G["证据缺口 / 待核实"]\n```\n'


def test_claim_topology_is_measured_on_delivered_report():
    """回归：Q4 拓扑只存在于交付文本里，拿 raw 判定会恒为 False。"""
    signals = harness._evidence_signals(TOPOLOGY, "模型自己写的正文，没有图")

    assert signals["has_claim_topology"] is True
    # 旧口径（raw）在同一组输入下必然漏记
    assert signals["has_claim_topology_raw"] is False


def test_claim_topology_true_without_gap_node():
    """有拓扑但没有证据缺口节点时，仍应记为「交付了拓扑」。

    旧口径把 ``证据缺口`` 与 mermaid 相与，而该节点只在「存在未获确定支持的
    结论」时才由 Q4 插入，因此旧口径还会额外漏记一部分真实拓扑。
    """
    presented = f"## 关系拓扑\n\n{FENCE}\nflowchart TB\n  C1 --> S1\n```\n"

    signals = harness._evidence_signals(presented, "")

    assert signals["has_claim_topology"] is True
    assert signals["has_claim_gap_node"] is False


def test_gap_node_recorded_separately_from_topology():
    signals = harness._evidence_signals(TOPOLOGY, "")

    assert signals["has_claim_topology"] is True
    assert signals["has_claim_gap_node"] is True


def test_agent_written_mermaid_is_recorded_as_raw_only():
    """模型自发画图 ≠ Q4 注入生效，两者必须可区分。"""
    signals = harness._evidence_signals(
        "交付文本被后处理成无图形态", f"{FENCE}\nflowchart TD\nA-->B\n```"
    )

    assert signals["has_claim_topology"] is False
    assert signals["has_claim_topology_raw"] is True


def test_evidence_signals_tolerate_empty_text():
    signals = harness._evidence_signals("", "")

    assert signals == {
        "has_claim_topology": False,
        "has_claim_gap_node": False,
        "has_claim_topology_raw": False,
    }


def _record(case_id: str, **extra: object) -> dict:
    return {"case_id": case_id, "status": "completed", **extra}


def test_batched_runs_merge_into_combined_results(tmp_path: Path):
    """回归：第二批运行不得让第一批的用例从汇总里消失。"""
    (tmp_path / "case_A.json").write_text(json.dumps(_record("A")), encoding="utf-8")

    merged = harness._merge_results_with_existing(tmp_path, {"B": _record("B")})

    assert set(merged) == {"A", "B"}


def test_merge_lets_current_run_override_same_case(tmp_path: Path):
    (tmp_path / "case_A.json").write_text(
        json.dumps(_record("A", duration_seconds=1.0)), encoding="utf-8"
    )

    merged = harness._merge_results_with_existing(
        tmp_path, {"A": _record("A", duration_seconds=2.0)}
    )

    assert merged["A"]["duration_seconds"] == 2.0


def test_merge_ignores_corrupt_case_files(tmp_path: Path):
    (tmp_path / "case_A.json").write_text(json.dumps(_record("A")), encoding="utf-8")
    (tmp_path / "case_B.json").write_text("{ not json", encoding="utf-8")

    merged = harness._merge_results_with_existing(tmp_path, {})

    assert set(merged) == {"A"}


def test_h5_pair_note_requires_both_cases():
    assert harness._h5_pair_note({"H5A": _record("H5A")}) is None
    assert harness._h5_pair_note({}) is None


def test_h5_pair_note_reports_profile_and_provider_divergence():
    results = {
        "H5A": _record(
            "H5A",
            metrics={"search_provider_hits": {"searxng": 0}},
            effective_config={"search_profile": "searxng-only"},
        ),
        "H5B": _record(
            "H5B",
            metrics={"search_provider_hits": {"serper": 3}},
            effective_config={"search_profile": "parallel-trusted"},
        ),
    }

    note = harness._h5_pair_note(results)

    assert note is not None
    assert note["profiles_differ"] is True
    assert note["provider_sets_differ"] is True
    assert note["h5a_profile"] == "searxng-only"
