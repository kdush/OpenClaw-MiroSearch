"""M3 结论核验验收：主张 → 来源 → 支持/反驳/未知 → 独立来源计数。

覆盖路线图《阶段 C》列出的 M3 fixture：同 URL 两引擎命中、跨域转载、
两来源支持同一主张、两个可信来源互相反驳、摘要与全文冲突、无依据结论。
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.claim_verification import (  # noqa: E402
    ClaimSupportMap,
    ClaimVerdict,
    adjudicate_claim_support,
    build_claim_support_prompt,
    extract_claims_from_report,
    independent_support,
    parse_claim_support_map,
    render_independent_support,
    validate_claim_map,
)


def _entry(source_id, url, *, status="fetched", domain=None, discoveries=None):
    return {
        "source_id": source_id,
        "raw_url": url,
        "normalized_url": url,
        "domain": domain or url.split("//", 1)[1].split("/", 1)[0],
        "title": f"T{source_id}",
        "snippet": "",
        "status": status,
        "modality": "text",
        "discoveries": (
            discoveries
            if discoveries is not None
            else [{"provider": "serper", "turn": 1, "position": source_id}]
        ),
        "first_seen_turn": 1,
        "last_seen_turn": 1,
        "content_ref": None,
        "aliases": [],
    }


def _registry(*entries):
    return {"entries": list(entries)}


class TestIndependentSupportFixtures:
    def test_same_url_two_engines_counts_once(self):
        """同 URL 被两个引擎命中：注册表已合并，只算一个原始来源。"""
        registry = _registry(
            _entry(
                1,
                "https://e.com/a",
                discoveries=[
                    {"provider": "searxng", "turn": 1, "position": 1},
                    {"provider": "serper", "turn": 1, "position": 3},
                ],
            )
        )
        verdict = ClaimVerdict(claim="X", support=[1])

        support = independent_support(verdict, registry)

        assert support.count == 1
        assert support.certain is True

    def test_cross_domain_republication_merges_via_origin_groups(self):
        """跨域转载由裁决给出的同一原文分组合并计数。"""
        registry = _registry(_entry(1, "https://a.com/x"), _entry(2, "https://b.com/x"))
        verdict = ClaimVerdict(claim="X", support=[1, 2], origin_groups=[[1, 2]])

        support = independent_support(verdict, registry)

        assert support.count == 1
        assert support.certain is True

    def test_two_independent_sources_support(self):
        registry = _registry(_entry(1, "https://a.com/x"), _entry(2, "https://b.com/y"))
        verdict = ClaimVerdict(claim="X", support=[1, 2])

        support = independent_support(verdict, registry)

        assert support.count == 2
        assert support.certain is True
        assert render_independent_support(support) == "2 个独立来源支持"

    def test_two_credible_sources_contradict_each_other(self):
        registry = _registry(_entry(1, "https://a.com/x"), _entry(2, "https://b.com/y"))
        verdict = ClaimVerdict(claim="X", support=[1], refute=[2])

        assert independent_support(verdict, registry).count == 1
        # 反驳来源必须保留在映射里，不被合并进支持
        assert verdict.refute == [2]

    def test_snippet_only_support_is_uncertain(self):
        """摘要与全文冲突：仅摘要的来源不能支撑精确的独立来源数。"""
        registry = _registry(
            _entry(1, "https://a.com/x", status="snippet_only"),
            _entry(2, "https://b.com/y", status="fetched"),
        )
        verdict = ClaimVerdict(claim="X", support=[1, 2])

        support = independent_support(verdict, registry)

        assert support.certain is False
        assert "仅摘要" in support.reason
        assert render_independent_support(support).startswith("未核实")

    def test_unsourced_claim_reports_insufficient(self):
        registry = _registry(_entry(1, "https://a.com/x"))
        verdict = ClaimVerdict(claim="X", support=[])

        support = independent_support(verdict, registry)

        assert support.count == 0
        assert render_independent_support(support) == "未核实（来源不足）"

    def test_same_domain_different_origins_is_flagged(self):
        """不同原始来源共用域名，可能是未识别的转载。"""
        registry = _registry(_entry(1, "https://a.com/x"), _entry(2, "https://a.com/y"))
        verdict = ClaimVerdict(claim="X", support=[1, 2])

        support = independent_support(verdict, registry)

        assert support.count == 2
        assert support.certain is False
        assert "转载" in support.reason

    def test_below_minimum_reports_insufficient(self):
        registry = _registry(_entry(1, "https://a.com/x"))
        verdict = ClaimVerdict(claim="X", support=[1])

        assert (
            render_independent_support(independent_support(verdict, registry))
            == "来源不足（1 个独立来源，未达 2 个门槛）"
        )


class TestParsingAndValidation:
    def test_malformed_payload_falls_back_to_empty_map(self):
        assert parse_claim_support_map(None).claims == []
        assert parse_claim_support_map({"claims": "nope"}).claims == []
        assert parse_claim_support_map({"claims": [{"support": [1]}]}).claims == []

    def test_bad_source_ids_are_dropped(self):
        claim_map = parse_claim_support_map(
            {"claims": [{"claim": "X", "support": [1, "2", 0, -3, 1]}]}
        )
        assert claim_map.claims[0].support == [1]

    def test_singleton_origin_groups_are_dropped(self):
        claim_map = parse_claim_support_map(
            {
                "claims": [
                    {"claim": "X", "support": [1, 2], "origin_groups": [[1], [1, 2]]}
                ]
            }
        )
        assert claim_map.claims[0].origin_groups == [[1, 2]]

    def test_validate_flags_unregistered_and_conflicting_ids(self):
        registry = _registry(_entry(1, "https://a.com/x"))
        claim_map = ClaimSupportMap(
            claims=[ClaimVerdict(claim="X", support=[1, 9], refute=[1])]
        )

        issues = validate_claim_map(claim_map, registry)

        assert "claim_1_unregistered_source:support:9" in issues
        assert "claim_1_conflicting_verdict:1" in issues

    def test_validate_passes_clean_map(self):
        registry = _registry(_entry(1, "https://a.com/x"), _entry(2, "https://b.com/y"))
        claim_map = ClaimSupportMap(
            claims=[ClaimVerdict(claim="X", support=[1], refute=[2])]
        )

        assert validate_claim_map(claim_map, registry) == []


class TestAdjudication:
    @pytest.mark.asyncio
    async def test_parses_model_json(self):
        async def call_llm(_prompt):
            return '{"claims": [{"claim": "X", "support": [1]}]}'

        registry = _registry(_entry(1, "https://a.com/x"))
        result = await adjudicate_claim_support(
            call_llm, claims=["X"], source_registry=registry
        )

        assert result.claims[0].support == [1]

    @pytest.mark.asyncio
    async def test_strips_code_fence(self):
        async def call_llm(_prompt):
            return '```json\n{"claims": [{"claim": "X", "support": [1]}]}\n```'

        registry = _registry(_entry(1, "https://a.com/x"))
        result = await adjudicate_claim_support(
            call_llm, claims=["X"], source_registry=registry
        )

        assert result.claims[0].support == [1]

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_empty_map(self):
        async def boom(_prompt):
            raise RuntimeError("network down")

        result = await adjudicate_claim_support(
            boom, claims=["X"], source_registry=_registry()
        )

        assert result.claims == []

    @pytest.mark.asyncio
    async def test_unparsable_output_falls_back_to_empty_map(self):
        async def call_llm(_prompt):
            return "抱歉，我无法判断。"

        result = await adjudicate_claim_support(
            call_llm, claims=["X"], source_registry=_registry()
        )

        assert result.claims == []

    def test_prompt_marks_sources_untrusted_and_forbids_counts(self):
        registry = _registry(
            _entry(1, "https://a.com/x", status="snippet_only"),
            _entry(9, "ftp://bad", status="weird"),
        )
        prompt = build_claim_support_prompt(["X"], registry)

        assert "不可信" in prompt
        assert "不要" in prompt and "计数" in prompt
        # 不可引用来源不得进入裁决视图
        assert '"source_id": 9' not in prompt
        assert '"source_id": 1' in prompt


class TestReportStructureBridge:
    def test_mechanical_check_is_exposed_on_report_structure(self):
        from src.io.report_structure import ReportStructureValidator

        registry = _registry(_entry(1, "https://a.com/x"))
        claim_map = ClaimSupportMap(claims=[ClaimVerdict(claim="X", support=[9])])

        issues = ReportStructureValidator.validate_claim_support_map(
            claim_map, registry
        )

        assert "claim_1_unregistered_source:support:9" in issues


class TestClaimExtraction:
    """M3 接线前置：从报告正文机械抽取主张，抽不到就返回空（不编造）。"""

    def test_prefers_conclusion_section_bullets(self):
        report = (
            "# 报告\n\n## 背景\n\n- 背景段落不应被当作结论主张。\n\n"
            "## 结论\n\n- 2025 年全球出货量同比增长 12%。\n"
            "- 该品类毛利率下滑至 18%。\n"
        )

        claims = extract_claims_from_report(report, limit=5)

        assert claims == [
            "2025 年全球出货量同比增长 12%。",
            "该品类毛利率下滑至 18%。",
        ]

    def test_strips_citation_markers_from_claims(self):
        report = "## 结论\n\n- 营收同比增长 12% [1][3]，主要由海外市场驱动。\n"

        claims = extract_claims_from_report(report)

        assert claims == ["营收同比增长 12%，主要由海外市场驱动。"]

    def test_sentence_split_when_no_bullets(self):
        report = "## 总结\n\n营收增长 12%。利润率降至 18%。这两句都是可核验主张。\n"

        claims = extract_claims_from_report(report, limit=5)

        assert "营收增长 12%。" in claims
        assert "利润率降至 18%。" in claims

    def test_respects_limit(self):
        report = "## 结论\n\n" + "".join(
            f"- 第 {i} 条可核验主张内容。\n" for i in range(9)
        )

        assert len(extract_claims_from_report(report, limit=3)) == 3

    def test_returns_empty_for_blank_or_unparsable(self):
        assert extract_claims_from_report("") == []
        assert extract_claims_from_report("   \n\n") == []
        # 全部短于最小长度阈值 → 宁可不核验
        assert extract_claims_from_report("## 结论\n\n- 好。\n- 行。\n") == []

    def test_deduplicates_repeated_claims(self):
        report = "## 结论\n\n- 营收增长 12%。\n- 营收增长 12%。\n"

        assert extract_claims_from_report(report) == ["营收增长 12%。"]
