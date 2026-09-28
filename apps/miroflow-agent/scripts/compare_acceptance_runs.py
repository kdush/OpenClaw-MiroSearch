#!/usr/bin/env python3
# Copyright (c) 2025 MiroMind
"""路线图 §7 基线对比：把候选验收运行与阶段 A 冻结基线逐项对照。

**不调用 LLM、不需要任何凭据**——只读两份 ``acceptance_results.json`` 做差分。
因此 LLM 额度受限时仍可运行：拿基线自比即得到控制组（全零差异），
用来证明工具本身正确；额度恢复后把 ``--candidate`` 指向新跑的目录即可出对比表。

对比维度（路线图 §7）：
    无依据结论数、错误引用数、冲突处理、p50/p95 耗时、
    模型调用与 token、检索/抓取量。
「实际供应商费用」按基线文档约定人工回填，本工具不估算，只在报告中占位提示。

用法::

    # 控制组：基线自比，应全部为 0
    python scripts/compare_acceptance_runs.py \\
        --baseline docs/acceptance/baseline-2026-09-24 \\
        --candidate docs/acceptance/baseline-2026-09-24

    # 真实对比（额度恢复后）
    python scripts/compare_acceptance_runs.py \\
        --baseline docs/acceptance/baseline-2026-09-24 \\
        --candidate docs/acceptance/artifacts/<new-run> \\
        --out docs/acceptance/artifacts/<new-run>/COMPARISON.md
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
DEFAULT_BASELINE = REPO / "docs" / "acceptance" / "baseline-2026-09-24"

RESULTS_FILENAME = "acceptance_results.json"

# 指标方向：True = 越小越好（回归即变差），False = 越大越好。
# None = 仅描述、不判定好坏（如状态、计数型中性指标）。
_LOWER_IS_BETTER = {
    "duration_seconds": True,
    "search_rounds": True,
    "search_attempts": True,
    "scrape_count": True,
    "follow_up_searches": True,
    "llm_calls": True,
    "input_tokens": True,
    "output_tokens": True,
    "unsourced_numbers": True,
    "wrong_citations": True,
    "citation_count": False,
    "registry_entries": False,
    "sources_discovered": False,
}


def _resolve_results(path: Path) -> Path:
    """接受目录或直接的 JSON 文件路径。"""
    if path.is_dir():
        candidate = path / RESULTS_FILENAME
        if not candidate.exists():
            raise FileNotFoundError(f"目录下没有 {RESULTS_FILENAME}: {path}")
        return candidate
    if path.exists():
        return path
    raise FileNotFoundError(f"路径不存在: {path}")


def load_run(path: Path) -> Dict[str, Dict[str, Any]]:
    """读入一次验收运行的逐用例记录（case_id -> record）。"""
    data = json.loads(_resolve_results(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"验收结果应为 case_id -> record 的映射: {path}")
    # 允许包裹一层 {"results": {...}} 形态，向后兼容。
    if set(data.keys()) == {"results"} and isinstance(data["results"], dict):
        data = data["results"]
    return data


def _count_llm_calls(routes: Any) -> int:
    """model_route_hits 是 {model: {model: n}}；累加全部调用数。"""
    total = 0
    if not isinstance(routes, dict):
        return total
    for per_model in routes.values():
        if isinstance(per_model, dict):
            total += sum(v for v in per_model.values() if isinstance(v, (int, float)))
        elif isinstance(per_model, (int, float)):
            total += int(per_model)
    return total


def case_metrics(record: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """从单条用例记录抽出 §7 可比指标；记录缺失时返回 None。"""
    if not record:
        return None
    metrics = record.get("metrics") or {}
    tokens = record.get("token_usage") or {}
    gate = record.get("gate_evaluation") or {}
    structure = gate.get("structure_metadata") or {}
    issues = gate.get("structure_issues") or []
    registry = record.get("source_registry") or {}

    return {
        "status": record.get("status"),
        "duration_seconds": record.get("duration_seconds"),
        "search_rounds": metrics.get("search_rounds"),
        "search_attempts": metrics.get("search_attempts"),
        "scrape_count": metrics.get("scrape_count"),
        "follow_up_searches": metrics.get("follow_up_searches"),
        "llm_calls": _count_llm_calls(metrics.get("model_route_hits")),
        "input_tokens": tokens.get("total_input_tokens"),
        "output_tokens": tokens.get("total_output_tokens"),
        "sources_discovered": len(record.get("source_urls") or []),
        "registry_entries": len(registry) if isinstance(registry, dict) else None,
        "citation_count": structure.get("citation_count"),
        "unsourced_numbers": len(structure.get("unsourced_number_warnings") or []),
        "wrong_citations": sum(
            1 for i in issues if str(i).startswith("citation_target_mismatch")
        ),
        "has_conflicts_section": structure.get("has_conflicts_section"),
        "claim_verification_ran": bool(record.get("claim_verification_ran")),
        "has_claim_topology": bool(record.get("has_claim_topology")),
    }


def percentile(values: List[float], q: float) -> Optional[float]:
    """最近秩（nearest-rank）百分位；样本为 1 时直接返回该值。"""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = math.ceil(q / 100.0 * len(ordered))
    idx = max(0, min(len(ordered) - 1, rank - 1))
    return ordered[idx]


def aggregate(per_case: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """把逐用例指标汇总为运行级指标（p50/p95、总量）。"""
    durations = [
        c["duration_seconds"]
        for c in per_case.values()
        if isinstance(c.get("duration_seconds"), (int, float))
    ]

    def _sum(key: str) -> int:
        return sum(
            c[key] for c in per_case.values() if isinstance(c.get(key), (int, float))
        )

    return {
        "cases": len(per_case),
        "p50_duration_seconds": percentile(durations, 50),
        "p95_duration_seconds": percentile(durations, 95),
        "total_duration_seconds": round(sum(durations), 1) if durations else None,
        "total_llm_calls": _sum("llm_calls"),
        "total_input_tokens": _sum("input_tokens"),
        "total_output_tokens": _sum("output_tokens"),
        "total_search_attempts": _sum("search_attempts"),
        "total_scrape_count": _sum("scrape_count"),
        "total_unsourced_numbers": _sum("unsourced_numbers"),
        "total_wrong_citations": _sum("wrong_citations"),
    }


def _delta(baseline: Any, candidate: Any) -> Optional[float]:
    if isinstance(baseline, (int, float)) and isinstance(candidate, (int, float)):
        return candidate - baseline
    return None


def compare(
    baseline: Dict[str, Dict[str, Any]],
    candidate: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """生成逐用例与运行级对比结果。"""
    case_ids = sorted(set(baseline) | set(candidate))
    per_case: Dict[str, Any] = {}
    for cid in case_ids:
        b = case_metrics(baseline.get(cid))
        c = case_metrics(candidate.get(cid))
        deltas: Dict[str, Any] = {}
        if b and c:
            for key in _LOWER_IS_BETTER:
                d = _delta(b.get(key), c.get(key))
                if d is not None:
                    deltas[key] = d
        per_case[cid] = {
            "baseline": b,
            "candidate": c,
            "delta": deltas,
            "only_in": (
                "baseline"
                if cid not in candidate
                else "candidate"
                if cid not in baseline
                else None
            ),
        }

    b_agg = aggregate(
        {
            cid: m
            for cid, m in ((c, case_metrics(baseline.get(c))) for c in baseline)
            if m
        }
    )
    c_agg = aggregate(
        {
            cid: m
            for cid, m in ((c, case_metrics(candidate.get(c))) for c in candidate)
            if m
        }
    )
    agg_delta = {key: _delta(b_agg.get(key), c_agg.get(key)) for key in b_agg}
    return {
        "cases": per_case,
        "baseline_aggregate": b_agg,
        "candidate_aggregate": c_agg,
        "aggregate_delta": agg_delta,
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def _fmt_delta(value: Any) -> str:
    if value is None:
        return "—"
    if value == 0:
        return "0"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}" if isinstance(value, float) else f"{sign}{value}"


def _verdict(key: str, delta: Optional[float]) -> str:
    if delta is None or delta == 0:
        return "="
    lower_better = _LOWER_IS_BETTER.get(key)
    if lower_better is None:
        return "~"
    improved = (delta < 0) if lower_better else (delta > 0)
    return "改善" if improved else "回归"


def render_markdown(
    result: Dict[str, Any], *, baseline_label: str, candidate_label: str
) -> str:
    """把对比结果渲染成 markdown（可直接贴进 PR 描述）。"""
    lines: List[str] = []
    lines.append("# §7 基线对比报告")
    lines.append("")
    lines.append(f"- **基线：** `{baseline_label}`")
    lines.append(f"- **候选：** `{candidate_label}`")
    lines.append(
        "- **口径：** 路线图 §7；本工具不调用 LLM，不估算供应商费用（人工回填）。"
    )
    lines.append("")

    lines.append("## 运行级汇总")
    lines.append("")
    lines.append("| 指标 | 基线 | 候选 | 变化 | 判定 |")
    lines.append("|---|---|---|---|---|")
    b_agg = result["baseline_aggregate"]
    c_agg = result["candidate_aggregate"]
    d_agg = result["aggregate_delta"]
    labels = [
        ("用例数", "cases"),
        ("p50 耗时(s)", "p50_duration_seconds"),
        ("p95 耗时(s)", "p95_duration_seconds"),
        ("总耗时(s)", "total_duration_seconds"),
        ("LLM 调用数", "total_llm_calls"),
        ("输入 token", "total_input_tokens"),
        ("输出 token", "total_output_tokens"),
        ("检索次数", "total_search_attempts"),
        ("抓取次数", "total_scrape_count"),
        ("无依据数字", "total_unsourced_numbers"),
        ("错误引用数", "total_wrong_citations"),
    ]
    for label, key in labels:
        lines.append(
            f"| {label} | {_fmt(b_agg.get(key))} | {_fmt(c_agg.get(key))} | "
            f"{_fmt_delta(d_agg.get(key))} | {_verdict(key, d_agg.get(key))} |"
        )
    lines.append("")

    lines.append("## 逐用例")
    lines.append("")
    lines.append(
        "| 用例 | 状态 | 耗时(s) | 检索 | 抓取 | LLM 调用 | token(in/out) | 引用 | 无依据 | 错误引用 |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for cid, entry in result["cases"].items():
        c = entry["candidate"] or entry["baseline"]
        if c is None:
            continue
        mark = (
            " *(仅候选)*"
            if entry["only_in"] == "candidate"
            else (" *(仅基线)*" if entry["only_in"] == "baseline" else "")
        )
        tokens = f"{_fmt(c.get('input_tokens'))}/{_fmt(c.get('output_tokens'))}"
        lines.append(
            f"| {cid}{mark} | {_fmt(c.get('status'))} | {_fmt(c.get('duration_seconds'))} | "
            f"{_fmt(c.get('search_attempts'))} | {_fmt(c.get('scrape_count'))} | "
            f"{_fmt(c.get('llm_calls'))} | {tokens} | {_fmt(c.get('citation_count'))} | "
            f"{_fmt(c.get('unsourced_numbers'))} | {_fmt(c.get('wrong_citations'))} |"
        )
    lines.append("")

    # 新增能力（候选独有）——M3/Q4 接线后的可观察证据
    new_signals = [
        (cid, e["candidate"])
        for cid, e in result["cases"].items()
        if e["candidate"]
        and (
            e["candidate"].get("claim_verification_ran")
            or e["candidate"].get("has_claim_topology")
            or e["candidate"].get("registry_entries")
        )
    ]
    if new_signals:
        lines.append("## 候选新增信号（M1/M3/Q4）")
        lines.append("")
        lines.append("| 用例 | 注册表条目 | 结论核验已跑 | 结论—来源拓扑 |")
        lines.append("|---|---|---|---|")
        for cid, c in new_signals:
            lines.append(
                f"| {cid} | {_fmt(c.get('registry_entries'))} | "
                f"{_fmt(c.get('claim_verification_ran'))} | "
                f"{_fmt(c.get('has_claim_topology'))} |"
            )
        lines.append("")

    lines.append("## 未覆盖维度")
    lines.append("")
    lines.append(
        "- **实际供应商费用：** 按基线文档约定人工从 provider 控制台回填，本工具不估算。"
    )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="路线图 §7 基线对比（不依赖 LLM）")
    parser.add_argument(
        "--baseline",
        default=str(DEFAULT_BASELINE),
        help=f"基线目录或 acceptance_results.json（默认 {DEFAULT_BASELINE}）",
    )
    parser.add_argument(
        "--candidate",
        required=True,
        help="候选运行目录或 acceptance_results.json",
    )
    parser.add_argument("--out", default="", help="把 markdown 写入该路径（默认打印）")
    parser.add_argument("--json-out", default="", help="可选：同时写出机器可读 JSON")
    args = parser.parse_args()

    baseline_path = Path(args.baseline)
    candidate_path = Path(args.candidate)
    baseline = load_run(baseline_path)
    candidate = load_run(candidate_path)

    result = compare(baseline, candidate)
    markdown = render_markdown(
        result,
        baseline_label=str(baseline_path),
        candidate_label=str(candidate_path),
    )

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[out] JSON 已写入 {args.json_out}")

    if args.out:
        Path(args.out).write_text(markdown, encoding="utf-8")
        print(f"[out] markdown 已写入 {args.out}")
    else:
        print(markdown)

    # 退出码：出现回归（方向已知且变差）即非零，便于 CI 卡口。
    regressions = [
        (cid, key)
        for cid, entry in result["cases"].items()
        for key, d in entry["delta"].items()
        if _verdict(key, d) == "回归"
    ]
    if regressions:
        print(f"\n[verdict] REGRESSIONS={len(regressions)}")
        for cid, key in regressions[:20]:
            print(f"  - {cid}: {key}")
        sys.exit(1)
    print("\n[verdict] PASS（无已知方向性回归）")
    sys.exit(0)


if __name__ == "__main__":
    main()
