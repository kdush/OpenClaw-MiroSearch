# Copyright (c) 2025 MiroMind
"""Unit tests for Round-5 Conflicts / hotspot report structure gates."""

from src.io.report_structure import ReportStructureValidator


DETAILED_OK = """
## TL;DR / 结论（标明置信度）

多方对事件说法冲突；当前置信度：中。核心事实需交叉核验后才能定论。

## Conclusions

综合公开报道，双方叙事在打击结果、地点与伤亡上存在明显分歧。

## 冲突与不确定 / Conflicts & Uncertainties

- 甲方宣称命中能源设施并引发火灾；乙方称拦截成功、设施正常。
- 伤亡数字各方不一，尚无独立核实。
- 跨境存储主张与本地实体主张相互矛盾，缺少审计公开材料。

## 时间线 / Timeline

- 2026-09-18：甲方发布袭击声明
- 2026-09-18：乙方发布拦截声明
- 2026-09-19：媒体转述双方说法，无独立现场确认

## Evidence

据路透社 2026-09-18 报道 [1]；据沙特官方通讯社同日声明 [2]；
胡塞媒体声明见 [3]。数字均带来源，未独立核实者不计入“已确认”。

## 已确认 vs 未确认 / Confirmed vs Unconfirmed

- **已确认**：双方均就“当日有袭击宣称/拦截宣称”发声。
- **未确认**：具体命中、起火、伤亡、跨境数据流向。

## References

1. https://example.com/reuters-2026-09-18
2. https://example.com/spa-2026-09-18
3. https://example.com/houthi-claim
"""


DETAILED_MISSING_CONFLICTS = """
## TL;DR

Something happened with medium confidence stated here clearly enough.

## Conclusions

A long enough conclusion about the event with background context and analysis
that exceeds one hundred fifty characters for the validator length check to pass.

## Evidence

Evidence with citations [1][2][3] and enough characters to satisfy the minimum
evidence length requirement for detailed reports in the structure validator.

## 时间线 / Timeline

- 2026-09-01: event A
- 2026-09-02: event B with enough detail for min length checks to pass here

## 已确认 vs 未确认 / Confirmed vs Unconfirmed

- confirmed: statements issued
- unconfirmed: damage extent

## References

1. source a
2. source b
3. source c
"""


# H3-style: strong Conflicts + sources woven in, but no dedicated Evidence heading
DETAILED_MISSING_EVIDENCE_HEADING = """
## TL;DR / 结论（标明置信度）

双方对袭击结果说法冲突；置信度：中。利雅得机场油罐火灾已目击，因果未定。

## Conclusions

胡塞宣称命中利雅得与延布敏感设施；沙特称导弹被拦截、袭击企图被挫败。
综合多家媒体，目前无法裁决是否物理命中。

## 冲突与不确定 / Conflicts & Uncertainties

- 胡塞发言人萨里称两次行动“取得成功”并引发大火。
- 联军发言人称射向利雅得的一枚弹道导弹被成功拦截摧毁。
- 据法新社记者目击，哈立德国王机场附近印有阿美标志的油罐起火，
  但中东之眼（MEE）称尚不清楚火灾与所报袭击是否相关。
- 路透社转述沙特政府媒体官员未立即回应置评请求。

## 时间线 / Timeline

- 2026-09-19：胡塞发布袭击声明；沙特晚间发布拦截声明
- 2026-09-16：Industrial Info 核实延布 YASREF 炼厂仍在运营，旧图造谣被辟谣
- 2026-09-17：美联社报道沙特拦截弹库存告急

## 已确认 vs 未确认 / Confirmed vs Unconfirmed

- **已确认**：双方均发布声明；AFP 目击油罐火灾。
- **未确认**：是否命中、延布起火、伤亡数字。

## References

1. https://example.com/afp-riyadh-fire
2. https://example.com/spa-intercept
3. https://example.com/industrial-info-yasref
"""


def test_detailed_requires_conflicts_section():
    valid, issues, meta = ReportStructureValidator.validate_structure(
        DETAILED_OK, "detailed"
    )
    assert valid, issues
    assert "conflicts" in meta["found_sections"]
    assert meta["has_conflicts_section"] is True


def test_bilingual_conflict_headings_accepted():
    """Round 8: presented heading 争议与不确定 must satisfy the gate too."""
    for heading in (
        "## Conflicts & Uncertainties",
        "## 冲突与不确定",
        "## 争议与不确定",
        "## 争议与不确定 / Conflicts & Uncertainties",
        "## 来源分歧",
    ):
        text = DETAILED_OK.replace(
            "## 冲突与不确定 / Conflicts & Uncertainties", heading
        )
        valid, issues, meta = ReportStructureValidator.validate_structure(
            text, "detailed"
        )
        assert valid, f"heading={heading!r} issues={issues}"
        assert "conflicts" in meta["found_sections"]


def test_detailed_fails_without_conflicts():
    valid, issues, meta = ReportStructureValidator.validate_structure(
        DETAILED_MISSING_CONFLICTS, "detailed"
    )
    assert not valid
    assert "conflicts" in meta["missing_required"]
    assert any("conflicts" in i for i in issues)


def test_enforce_structure_adds_conflicts_placeholder():
    fixed = ReportStructureValidator.enforce_structure(
        DETAILED_MISSING_CONFLICTS, "detailed"
    )
    assert "Conflicts" in fixed or "冲突" in fixed
    valid, issues, meta = ReportStructureValidator.validate_structure(fixed, "detailed")
    assert "conflicts" in meta["found_sections"]
    # After auto-fix, conflicts should no longer be missing
    assert "conflicts" not in meta["missing_required"]


def test_template_includes_conflicts_for_detailed():
    tmpl = ReportStructureValidator.get_structure_template("detailed")
    assert "Conflicts" in tmpl
    assert "冲突" in tmpl
    assert "Timeline" in tmpl or "时间线" in tmpl
    assert "Confirmed" in tmpl or "已确认" in tmpl


def test_detailed_has_more_required_sections_than_compact():
    compact = ReportStructureValidator.REQUIRED_SECTIONS["compact"]
    detailed = ReportStructureValidator.REQUIRED_SECTIONS["detailed"]
    assert len(detailed) > len(compact)
    detailed_required = [s.name for s in detailed if s.required]
    assert "conflicts" in detailed_required
    assert "timeline" in detailed_required
    assert "confirmed" in detailed_required


def test_bilingual_evidence_headings_accepted():
    """Round 6: CN thematic Evidence headings must satisfy the gate."""
    for heading in (
        "## Evidence",
        "## 证据",
        "## 证据与来源",
        "## 临床证据",
        "## 四、临床证据与安全性争议",
        "## Evidence（证据含来源与日期）",
    ):
        text = DETAILED_OK.replace("## Evidence", heading)
        valid, issues, meta = ReportStructureValidator.validate_structure(
            text, "detailed"
        )
        assert valid, f"heading={heading!r} issues={issues}"
        assert "evidence" in meta["found_sections"]


def test_detailed_fails_without_evidence_heading():
    valid, issues, meta = ReportStructureValidator.validate_structure(
        DETAILED_MISSING_EVIDENCE_HEADING, "detailed"
    )
    assert not valid
    assert "evidence" in meta["missing_required"]
    assert "missing_evidence" in issues


def test_enforce_structure_adds_evidence_from_inline_sources():
    """Round 6 auto-repair: promote inlined AFP/Reuters cues into Evidence."""
    fixed = ReportStructureValidator.enforce_structure(
        DETAILED_MISSING_EVIDENCE_HEADING, "detailed"
    )
    assert "Evidence" in fixed or "证据" in fixed
    valid, issues, meta = ReportStructureValidator.validate_structure(fixed, "detailed")
    assert "evidence" in meta["found_sections"]
    assert "evidence" not in meta["missing_required"]
    assert valid, issues


def test_normalize_near_miss_conflict_heading():
    """Round 8: rename bare ## 冲突 to canonical Conflicts heading."""
    text = """
## TL;DR / 结论（标明置信度）

多方说法冲突；置信度：中。需要交叉核验后才能定论完整表述。

## Conclusions

综合公开报道，双方叙事在打击结果、地点与伤亡上存在明显分歧与背景说明。

## 冲突

- 甲方宣称命中能源设施并引发火灾；乙方称拦截成功、设施正常运行。
- 伤亡数字各方不一，尚无独立核实，口径冲突明显。
- 跨境存储主张与本地实体主张相互矛盾，缺少审计公开材料支撑。

## 时间线 / Timeline

- 2026-09-18：甲方发布袭击声明
- 2026-09-18：乙方发布拦截声明
- 2026-09-19：媒体转述双方说法，无独立现场确认

## Evidence

据路透社 2026-09-18 报道 [1]；据沙特官方通讯社同日声明 [2]；
胡塞媒体声明见 [3]。数字均带来源，未独立核实者不计入“已确认”。

## 已确认 vs 未确认 / Confirmed vs Unconfirmed

- **已确认**：双方均就“当日有袭击宣称/拦截宣称”发声。
- **未确认**：具体命中、起火、伤亡、跨境数据流向。

## References

1. https://example.com/reuters-2026-09-18
2. https://example.com/spa-2026-09-18
3. https://example.com/houthi-claim
"""
    fixed = ReportStructureValidator.enforce_structure(text, "detailed")
    assert "Conflicts" in fixed or "冲突与不确定" in fixed
    valid, issues, meta = ReportStructureValidator.validate_structure(fixed, "detailed")
    assert "conflicts" in meta["found_sections"]
    assert valid, issues


def test_inject_timeline_heading_above_dated_bullets():
    """Round 8: surgical Timeline heading inject without full regenerate."""
    text = DETAILED_OK.replace(
        "## 时间线 / Timeline\n\n- 2026-09-18：甲方发布袭击声明\n"
        "- 2026-09-18：乙方发布拦截声明\n"
        "- 2026-09-19：媒体转述双方说法，无独立现场确认\n",
        "- 2026-09-18：甲方发布袭击声明\n"
        "- 2026-09-18：乙方发布拦截声明\n"
        "- 2026-09-19：媒体转述双方说法，无独立现场确认\n",
    )
    valid_before, _, meta_before = ReportStructureValidator.validate_structure(
        text, "detailed"
    )
    assert not valid_before
    assert "timeline" in meta_before["missing_required"]
    fixed = ReportStructureValidator.enforce_structure(text, "detailed")
    assert "Timeline" in fixed or "时间线" in fixed
    valid, issues, meta = ReportStructureValidator.validate_structure(fixed, "detailed")
    assert "timeline" in meta["found_sections"]
    assert valid, issues
