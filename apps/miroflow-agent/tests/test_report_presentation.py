"""Tests for user-facing report presentation cleanup."""

from src.io.report_presentation import (
    compact_pending_lead_trail,
    drop_incomplete_reference_lines,
    ensure_content_analysis_and_topology,
    prepare_user_facing_report,
    reshape_report_for_consumer,
    strip_diagnostic_noise,
)


MESSY = """
============================== Final Answer ==============================
## TL;DR / 结论（标明置信度）

双方说法冲突；置信度：中。

## Conclusions

综合公开报道，袭击是否造成实质破坏仍有争议，需要更多来源交叉核验。

## 冲突与不确定 / Conflicts & Uncertainties

- 甲方宣称命中能源设施并引发火灾。
- 乙方称拦截成功、设施运转正常。
- 伤亡数字各方不一，尚无独立核实。

## Evidence

据路透社报道 [1]。

## References

1. https://example.com/reuters-2026-09-18
2. https://www

## 线索追踪 / Lead Trail

### Lead 1: What are the most important unresolved aspects of the attack?
**来源**: query_seed (Turn 0)
**优先级**: 0.65
**状态**: pending

--------------------- Extracted Result ---------------------
boxed text

--------------------- Token Usage & Cost ---------------------
Total Input Tokens: 123
Pricing is disabled - no cost information available
-----------------------------------------------------
"""


def test_strip_diagnostic_noise_removes_token_blocks():
    cleaned = strip_diagnostic_noise(MESSY)
    assert "Token Usage" not in cleaned
    assert "Pricing is disabled" not in cleaned
    assert "Extracted Result" not in cleaned
    assert "TL;DR" in cleaned


def test_drop_incomplete_reference_lines():
    text = "## References\n\n1. https://example.com/ok\n2. https://www\n"
    out = drop_incomplete_reference_lines(text)
    assert "example.com/ok" in out
    assert "https://www\n" not in out + "\n" or out.strip().endswith("ok")


def test_compact_pending_only_trail():
    trail = """## body

## 线索追踪 / Lead Trail

### Lead 1: Long English seed question about unresolved conflicts?
**来源**: query_seed (Turn 0)
**优先级**: 0.65
**状态**: pending
"""
    out = compact_pending_lead_trail(trail)
    assert "未跟进" in out
    assert "**状态**: pending" not in out


def test_topology_added_when_conflicts_present():
    body = """## TL;DR

结论足够长用于测试。

## 冲突与不确定 / Conflicts & Uncertainties

- 甲方宣称命中。
- 乙方称拦截成功。

## References

1. https://example.com/a
"""
    out = ensure_content_analysis_and_topology(body, detail_level="detailed")
    assert "内容分析" in out
    assert "```mermaid" in out
    assert "flowchart" in out


def test_prepare_user_facing_report_end_to_end():
    out = prepare_user_facing_report(MESSY, detail_level="detailed")
    assert "Token Usage" not in out
    assert "Pricing is disabled" not in out
    assert "https://www\n" not in out
    assert "## 结论" in out
    assert "证据与来源" in out
    assert "未跟进" in out  # lead trail compacted into deep dive
    assert "内容分析" in out
    assert "```mermaid" in out
    assert out.index("## 结论") < out.index("证据与来源")


def test_prepare_consumer_layout_glance_first():
    out = prepare_user_facing_report(MESSY, detail_level="detailed")
    assert "## 结论" in out
    assert "证据与来源" in out
    assert out.index("## 结论") < out.index("证据与来源")
    assert "深入了解" in out
    # nested analysis demoted under deep dive
    assert "### 内容分析" in out or "内容分析" in out


def test_prepare_compact_skips_deep_dive():
    out = prepare_user_facing_report(MESSY, detail_level="compact")
    assert "## 结论" in out
    assert "深入了解" not in out
    assert "```mermaid" not in out


def test_reshape_keeps_conflict_short():
    body = """## TL;DR
短结论。

## 冲突与不确定
- a啊
- b吧
- c从
- d的

## References
1. https://example.com/x
"""
    out = reshape_report_for_consumer(body, detail_level="balanced")
    assert "## 争议与不确定" in out
    # at most 3 bullets under conflict
    conflict = out.split("## 争议与不确定", 1)[1].split("## ", 1)[0]
    bullets = [ln for ln in conflict.splitlines() if ln.strip().startswith("-")]
    assert len(bullets) <= 3


def test_reshape_prefers_bold_answer_over_fallback():
    """**答案：** lines must not be mistaken for bullets (empty glance bug)."""
    raw = """# 1+1 等于几？

**答案：1+1 = 2**

## 基本解释

- 算术上 1+1=2
"""
    out = prepare_user_facing_report(raw, detail_level="compact")
    assert "暂无法" not in out
    conclusion = out.split("## 结论", 1)[1].split("##", 1)[0]
    assert "1+1" in conclusion and "2" in conclusion


def test_section_kind_direct_answer_is_glance():
    from src.io.report_presentation import _section_kind, reshape_report_for_consumer

    assert _section_kind("## 直接答案") == "glance"
    assert _section_kind("Direct Answer") == "glance"
    assert _section_kind("答案") == "glance"
    raw = """## 直接答案

1+1=2

## References
1. https://example.com/x
"""
    out = reshape_report_for_consumer(raw, detail_level="compact")
    assert "## 结论" in out
    assert "1+1=2" in out.split("## 结论", 1)[1].split("##", 1)[0]
    assert "深入了解" not in out


def test_section_kind_reference_headings_are_sources():
    from src.io.report_presentation import _section_kind

    for heading in ("## References", "## 参考文献", "## 参考资料", "## 来源列表"):
        assert _section_kind(heading) == "sources", heading


def test_prepare_does_not_use_confidence_as_answer():
    """Re-preparing a consumer report must keep 1+1=2, not promote 置信度."""
    raw = """## 结论

1+1=2

<!-- confidence:high -->
**置信度：高**
"""
    out = prepare_user_facing_report(raw, detail_level="compact")
    conclusion = out.split("## 结论", 1)[1].split("##", 1)[0]
    assert "1+1=2" in conclusion
    # answer line itself must not be only the confidence label
    body_lines = [
        ln.strip()
        for ln in conclusion.splitlines()
        if ln.strip() and not ln.strip().startswith("<!--") and "置信度" not in ln
    ]
    assert any("1+1" in ln for ln in body_lines)


class TestClaimTopology:
    """Q4：只画已验证的结论—来源关系，未知/反驳用不同标记。"""

    @staticmethod
    def _registry(*entries):
        return {"entries": list(entries)}

    @staticmethod
    def _entry(source_id, url, *, status="fetched", domain=None):
        return {
            "source_id": source_id,
            "raw_url": url,
            "normalized_url": url,
            "domain": domain or url.split("//", 1)[1].split("/", 1)[0],
            "title": f"T{source_id}",
            "snippet": "",
            "status": status,
            "modality": "text",
            "discoveries": [{"provider": "serper", "turn": 1, "position": source_id}],
            "first_seen_turn": 1,
            "last_seen_turn": 1,
            "content_ref": None,
            "aliases": [],
        }

    def test_draws_support_refute_and_unknown_edges(self):
        from src.core.claim_verification import ClaimSupportMap, ClaimVerdict

        registry = self._registry(
            self._entry(1, "https://a.com/x"),
            self._entry(2, "https://b.com/y"),
            self._entry(3, "https://c.com/z"),
        )
        claim_map = ClaimSupportMap(
            claims=[ClaimVerdict(claim="主张一", support=[1], refute=[2], unknown=[3])]
        )

        out = ensure_content_analysis_and_topology(
            "## 结论\n\nX 与 Y 存在争议。\n",
            detail_level="detailed",
            claim_map=claim_map,
            source_registry=registry,
        )

        assert "```mermaid" in out
        assert "-->|支持|" in out
        assert "==>|反驳|" in out
        assert "-.->|未知|" in out
        assert 'S1["[1] a.com"]' in out
        assert "主张一" in out

    def test_unverified_claim_points_at_evidence_gap(self):
        from src.core.claim_verification import ClaimSupportMap, ClaimVerdict

        registry = self._registry(
            self._entry(1, "https://a.com/x", status="snippet_only")
        )
        claim_map = ClaimSupportMap(
            claims=[ClaimVerdict(claim="仅有摘要的主张", support=[1])]
        )

        out = ensure_content_analysis_and_topology(
            "## 结论\n\n有待核实的说法。\n",
            detail_level="detailed",
            claim_map=claim_map,
            source_registry=registry,
        )

        assert "-.-> G" in out
        assert "证据缺口" in out
        assert "未核实" in out

    def test_caption_does_not_imply_fact_checking(self):
        from src.core.claim_verification import ClaimSupportMap, ClaimVerdict

        registry = self._registry(self._entry(1, "https://a.com/x"))
        claim_map = ClaimSupportMap(claims=[ClaimVerdict(claim="主张", support=[1])])

        out = ensure_content_analysis_and_topology(
            "## 结论\n\n正文。\n",
            detail_level="detailed",
            claim_map=claim_map,
            source_registry=registry,
        )

        assert "不代表事实已核实" in out

    def test_unregistered_source_is_not_drawn(self):
        from src.core.claim_verification import ClaimSupportMap, ClaimVerdict

        registry = self._registry(self._entry(1, "https://a.com/x"))
        claim_map = ClaimSupportMap(claims=[ClaimVerdict(claim="主张", support=[1, 9])])

        out = ensure_content_analysis_and_topology(
            "## 结论\n\n正文。\n",
            detail_level="detailed",
            claim_map=claim_map,
            source_registry=registry,
        )

        assert 'S1["[1] a.com"]' in out
        assert "S9[" not in out

    def test_conflict_fallback_still_applies_without_claim_map(self):
        body = (
            "## 冲突与不确定 / Conflicts & Uncertainties\n\n"
            "- 甲方宣称命中能源设施。\n"
            "- 乙方称拦截成功。\n"
        )

        out = ensure_content_analysis_and_topology(body, detail_level="detailed")

        assert "```mermaid" in out
        assert "议题争议" in out


class TestClaimVerificationPipeline:
    """M3×Q4 接线：编排层真实入口必须吃下 claim_map 与 source_registry。"""

    @staticmethod
    def _entry(source_id, url, *, status="fetched"):
        return {
            "source_id": source_id,
            "raw_url": url,
            "normalized_url": url,
            "domain": url.split("//", 1)[1].split("/", 1)[0],
            "title": f"T{source_id}",
            "snippet": "",
            "status": status,
            "modality": "text",
            "discoveries": [{"provider": "serper", "turn": 1, "position": source_id}],
            "first_seen_turn": 1,
            "last_seen_turn": 1,
            "content_ref": None,
            "aliases": [],
        }

    def test_prepare_threads_claim_map_into_topology(self):
        from src.core.claim_verification import ClaimSupportMap, ClaimVerdict

        registry = {"entries": [self._entry(1, "https://a.com/x")]}
        claim_map = ClaimSupportMap(
            claims=[ClaimVerdict(claim="营收同比增长 12%。", support=[1])]
        )

        out = prepare_user_facing_report(
            "## 结论\n\n营收同比增长 12%。\n",
            detail_level="detailed",
            claim_map=claim_map,
            source_registry=registry,
        )

        assert "```mermaid" in out
        assert "-->|支持|" in out
        assert "营收同比增长 12%。" in out
        assert 'S1["[1] a.com"]' in out

    def test_sanitized_map_draws_no_contradictory_edges(self):
        """畸形裁决（同一来源既支持又反驳）经净化后只画一种边。"""
        from src.core.claim_verification import (
            ClaimSupportMap,
            ClaimVerdict,
            sanitize_claim_map,
        )

        registry = {
            "entries": [
                self._entry(1, "https://a.com/x"),
                self._entry(2, "https://b.com/y"),
            ]
        }
        raw = ClaimSupportMap(
            claims=[ClaimVerdict(claim="主张", support=[1], refute=[1])]
        )
        clean = sanitize_claim_map(raw, ["主张"], registry)

        out = ensure_content_analysis_and_topology(
            "## 结论\n\n正文。\n",
            detail_level="detailed",
            claim_map=clean,
            source_registry=registry,
        )

        assert "-->|支持|" not in out
        assert "==>|反驳|" not in out
        assert "-.->|未知|" in out

    def test_no_certain_count_when_body_never_adjudicated(self):
        """正文没进过裁决：即使 status=fetched 也不得写"N 个独立来源支持"。"""
        from src.core.claim_verification import ClaimSupportMap, ClaimVerdict

        registry = {
            "entries": [
                self._entry(1, "https://a.com/x"),
                self._entry(2, "https://b.com/y"),
            ]
        }
        claim_map = ClaimSupportMap(
            claims=[ClaimVerdict(claim="主张", support=[1, 2])],
            bodies_adjudicated=set(),
        )

        out = ensure_content_analysis_and_topology(
            "## 结论\n\n正文。\n",
            detail_level="detailed",
            claim_map=claim_map,
            source_registry=registry,
        )

        assert "独立来源支持" not in out
        assert "未核实" in out
        assert "证据缺口" in out

    def test_prepare_without_claim_map_adds_no_claim_edges(self):
        out = prepare_user_facing_report(
            "## 结论\n\n营收同比增长 12%。\n", detail_level="detailed"
        )

        assert "```mermaid" not in out
        assert "-->|支持|" not in out

    def test_extracted_claims_feed_topology_end_to_end(self):
        """extract_claims_from_report → claim_map → prepare 的真实链路。"""
        from src.core.claim_verification import (
            ClaimSupportMap,
            ClaimVerdict,
            extract_claims_from_report,
        )

        report = "## 结论\n\n- 营收同比增长 12% [1]。\n- 毛利率下滑至 18% [2]。\n"
        claims = extract_claims_from_report(report, limit=5)
        assert len(claims) == 2

        registry = {
            "entries": [
                self._entry(1, "https://a.com/x"),
                self._entry(2, "https://b.com/y", status="snippet_only"),
            ]
        }
        claim_map = ClaimSupportMap(
            claims=[
                ClaimVerdict(claim=claims[0], support=[1]),
                ClaimVerdict(claim=claims[1], support=[2]),
            ]
        )

        out = prepare_user_facing_report(
            report,
            detail_level="detailed",
            claim_map=claim_map,
            source_registry=registry,
        )

        assert "```mermaid" in out
        assert "营收同比增长 12%" in out
        assert "毛利率下滑至 18%" in out
        # 只有摘要的来源必须落进证据缺口
        assert "证据缺口" in out
