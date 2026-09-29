"""M3 结论核验验收：主张 → 来源 → 支持/反驳/未知 → 独立来源计数。

覆盖路线图《阶段 C》列出的 M3 fixture：同 URL 两引擎命中、跨域转载、
两来源支持同一主张、两个可信来源互相反驳、摘要与全文冲突、无依据结论。
"""

import json
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
    adjudication_source_view,
    build_claim_support_prompt,
    extract_claims_from_report,
    independent_support,
    parse_claim_support_map,
    render_independent_support,
    sanitize_claim_map,
    validate_claim_map,
)
from src.core.source_registry import resolve_content_ref  # noqa: E402


def _entry(
    source_id,
    url,
    *,
    status="fetched",
    domain=None,
    discoveries=None,
    snippet="",
    content_ref=None,
):
    return {
        "source_id": source_id,
        "raw_url": url,
        "normalized_url": url,
        "domain": domain or url.split("//", 1)[1].split("/", 1)[0],
        "title": f"T{source_id}",
        "snippet": snippet,
        "status": status,
        "modality": "text",
        "discoveries": (
            discoveries
            if discoveries is not None
            else [{"provider": "serper", "turn": 1, "position": source_id}]
        ),
        "first_seen_turn": 1,
        "last_seen_turn": 1,
        "content_ref": content_ref,
        "aliases": [],
    }


def _registry(*entries):
    return {"entries": list(entries)}


# 确定数要求"支持依据能在该来源实际传入的正文片段里逐字核对到"，所以凡是要断言
# certain=True 的用例，都得同时给出 evidence 与对应的 body_excerpts。
_GROUNDED_EVIDENCE = "通报确认该说法成立"


def _grounded(claim, support, *, refute=None, unknown=None, origin_groups=None):
    """构造"依据可在正文片段中核对到"的裁决结果，连同它对应的 body_excerpts。"""
    support = list(support)
    verdict = ClaimVerdict(
        claim=claim,
        support=support,
        refute=list(refute or []),
        unknown=list(unknown or []),
        evidence={sid: _GROUNDED_EVIDENCE for sid in support},
        origin_groups=list(origin_groups or []),
    )
    return verdict, {sid: _GROUNDED_EVIDENCE for sid in support}


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
        verdict, excerpts = _grounded("X", [1])

        support = independent_support(
            verdict, registry, bodies_adjudicated={1}, body_excerpts=excerpts
        )

        assert support.count == 1
        assert support.certain is True

    def test_cross_domain_republication_merges_via_origin_groups(self):
        """跨域转载由裁决给出的同一原文分组合并计数。"""
        registry = _registry(_entry(1, "https://a.com/x"), _entry(2, "https://b.com/x"))
        verdict, excerpts = _grounded("X", [1, 2], origin_groups=[[1, 2]])

        support = independent_support(
            verdict, registry, bodies_adjudicated={1, 2}, body_excerpts=excerpts
        )

        assert support.count == 1
        assert support.certain is True

    def test_two_independent_sources_support(self):
        registry = _registry(_entry(1, "https://a.com/x"), _entry(2, "https://b.com/y"))
        verdict, excerpts = _grounded("X", [1, 2])

        support = independent_support(
            verdict, registry, bodies_adjudicated={1, 2}, body_excerpts=excerpts
        )

        assert support.count == 2
        assert support.certain is True
        assert render_independent_support(support) == "2 个独立来源支持"

    def test_fetched_without_adjudicated_body_is_not_certain(self):
        """抓取成功但正文没进裁决：不得凭 status=fetched 升级为确定支持数。

        这正是"摘要支持、正文反驳"能被误报成"N 个独立来源支持"的路径。
        """
        registry = _registry(
            _entry(1, "https://a.com/x", snippet="摘要支持该主张"),
            _entry(2, "https://b.com/y", snippet="摘要支持该主张"),
        )
        verdict = ClaimVerdict(claim="X", support=[1, 2])

        support = independent_support(verdict, registry, bodies_adjudicated=set())

        assert support.certain is False
        assert "正文未参与裁决" in support.reason
        assert render_independent_support(support).startswith("未核实")
        assert "独立来源支持" not in render_independent_support(support)

    def test_only_body_backed_sources_make_the_count_certain(self):
        """一个来源的正文进了裁决、另一个没有 → 整体仍不给精确 N。"""
        registry = _registry(_entry(1, "https://a.com/x"), _entry(2, "https://b.com/y"))
        verdict = ClaimVerdict(claim="X", support=[1, 2])

        support = independent_support(verdict, registry, bodies_adjudicated={1})

        assert support.count == 2
        assert support.certain is False
        assert "正文未参与裁决" in support.reason

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
        verdict, excerpts = _grounded("X", [1])

        assert (
            render_independent_support(
                independent_support(
                    verdict,
                    registry,
                    bodies_adjudicated={1},
                    body_excerpts=excerpts,
                )
            )
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


class TestBodyAwareAdjudication:
    """M3 阻塞项：裁决输入必须含抓取正文，计数口径必须与之对齐。"""

    @staticmethod
    def _logs(*bodies):
        return [{"metadata": {"result": {"content": body}}} for body in bodies]

    def test_prompt_carries_bounded_body_excerpt_from_content_ref(self):
        registry = _registry(
            _entry(1, "https://a.com/x", content_ref="/step_logs/0/metadata/result")
        )
        prompt = build_claim_support_prompt(
            ["X"],
            registry,
            body_resolver=lambda ref: resolve_content_ref(
                ref, self._logs("正文" * 400)
            ),
        )

        # 长度受控：只截前 600 字
        assert ("正文" * 300) in prompt
        assert ("正文" * 400) not in prompt
        assert "以 body_excerpt 为准" in prompt

    def test_source_with_unreadable_body_has_no_excerpt(self):
        registry = _registry(
            _entry(1, "https://a.com/x", content_ref="/step_logs/9/metadata/result"),
            _entry(2, "https://b.com/y", snippet="仅摘要"),
        )

        view, bodies = adjudication_source_view(
            registry, body_resolver=lambda ref: resolve_content_ref(ref, [])
        )

        assert all("body_excerpt" not in item for item in view)
        assert bodies == {}
        assert [item["snippet"] for item in view] == ["", "仅摘要"]

    def test_resolve_content_ref_rejects_dangling_and_malformed_pointers(self):
        logs = self._logs("真正文")
        assert resolve_content_ref("/step_logs/0/metadata/result", logs) == "真正文"
        assert resolve_content_ref("/step_logs/5/metadata/result", logs) == ""
        assert resolve_content_ref("/step_logs/x/metadata/result", logs) == ""
        assert resolve_content_ref("https://e.com/a", logs) == ""
        assert resolve_content_ref(None, logs) == ""
        assert resolve_content_ref("/step_logs/0/metadata/result", None) == ""
        # 指针有效但载荷里没有正文 → 视为不可读
        assert (
            resolve_content_ref("/step_logs/0/metadata/result", [{"metadata": {}}])
            == ""
        )

    @pytest.mark.asyncio
    async def test_snippet_supports_body_refutes_gives_no_certain_count(self):
        """摘要支持、正文反驳：正文进过 prompt，模型据正文判反驳，不输出确定支持数。"""
        logs = self._logs("该说法与官方通报不符。", "同样与官方通报不符。")
        registry = _registry(
            _entry(
                1,
                "https://a.com/x",
                snippet="据称该说法成立",
                content_ref="/step_logs/0/metadata/result",
            ),
            _entry(
                2,
                "https://b.com/y",
                snippet="据称该说法成立",
                content_ref="/step_logs/1/metadata/result",
            ),
        )
        captured = {}

        async def call_llm(prompt):
            captured["prompt"] = prompt
            return json.dumps(
                {
                    "claims": [
                        {"claim": "X", "support": [], "refute": [1, 2], "unknown": []}
                    ]
                }
            )

        result = await adjudicate_claim_support(
            call_llm,
            claims=["X"],
            source_registry=registry,
            body_resolver=lambda ref: resolve_content_ref(ref, logs),
        )

        assert "与官方通报不符" in captured["prompt"]
        assert result.bodies_adjudicated == {1, 2}
        verdict = result.claims[0]
        assert verdict.support == []
        assert verdict.refute == [1, 2]
        support = independent_support(
            verdict, registry, bodies_adjudicated=result.bodies_adjudicated
        )
        assert support.count == 0
        rendered = render_independent_support(support)
        assert rendered == "未核实（来源不足）"
        assert "独立来源支持" not in rendered

    @pytest.mark.asyncio
    async def test_unreadable_body_keeps_support_uncertain(self):
        """正文不可读取：即便模型判支持，也不能输出确定的独立来源数。"""
        registry = _registry(
            _entry(1, "https://a.com/x", content_ref="/step_logs/7/metadata/result"),
            _entry(2, "https://b.com/y", content_ref="/step_logs/8/metadata/result"),
        )

        async def call_llm(_prompt):
            return '{"claims": [{"claim": "X", "support": [1, 2]}]}'

        result = await adjudicate_claim_support(
            call_llm,
            claims=["X"],
            source_registry=registry,
            body_resolver=lambda ref: resolve_content_ref(ref, []),
        )

        assert result.bodies_adjudicated == set()
        support = independent_support(
            result.claims[0], registry, bodies_adjudicated=result.bodies_adjudicated
        )
        assert support.certain is False
        assert "正文未参与裁决" in support.reason
        rendered = render_independent_support(support)
        assert rendered.startswith("未核实")
        assert "独立来源支持" not in rendered

    @pytest.mark.asyncio
    async def test_readable_body_keeps_two_source_support_certain(self):
        """正例：两条来源的正文都进了裁决，且支持依据都能在片段里核对到 → 确定数。"""
        logs = self._logs("通报确认该说法成立。", "另一份通报亦确认。")
        registry = _registry(
            _entry(1, "https://a.com/x", content_ref="/step_logs/0/metadata/result"),
            _entry(2, "https://b.com/y", content_ref="/step_logs/1/metadata/result"),
        )

        async def call_llm(_prompt):
            return json.dumps(
                {
                    "claims": [
                        {
                            "claim": "X",
                            "support": [1, 2],
                            "evidence": {
                                "1": "通报确认该说法成立",
                                "2": "另一份通报亦确认",
                            },
                        }
                    ]
                }
            )

        result = await adjudicate_claim_support(
            call_llm,
            claims=["X"],
            source_registry=registry,
            body_resolver=lambda ref: resolve_content_ref(ref, logs),
        )

        assert result.bodies_adjudicated == {1, 2}
        support = independent_support(
            result.claims[0],
            registry,
            bodies_adjudicated=result.bodies_adjudicated,
            body_excerpts=result.body_excerpts,
        )
        assert support.certain is True
        assert render_independent_support(support) == "2 个独立来源支持"

    @pytest.mark.asyncio
    async def test_refutation_beyond_excerpt_boundary_gives_no_certain_count(self):
        """反证落在 600 字截取边界之外：摘要支持、正文反驳，不得报确定支持数。

        正文开头是无关背景，占满 600 字截取窗口，明确反驳在其后——反驳部分没有
        进入 prompt。``body_excerpt`` 非空只说明"窗口里有内容"，裁决据此返回的
        support 依据其实来自 snippet，在片段里核对不上，因此只能给"未核实"。
        """
        background = "无关背景" * 200  # 800 字，占满 600 字截取窗口
        body = background + "该说法与官方通报不符。"
        logs = self._logs(body, body)
        registry = _registry(
            _entry(
                1,
                "https://a.com/x",
                snippet="据称该说法成立",
                content_ref="/step_logs/0/metadata/result",
            ),
            _entry(
                2,
                "https://b.com/y",
                snippet="据称该说法成立",
                content_ref="/step_logs/1/metadata/result",
            ),
        )
        captured = {}

        async def call_llm(prompt):
            captured["prompt"] = prompt
            return json.dumps(
                {
                    "claims": [
                        {
                            "claim": "X",
                            "support": [1, 2],
                            "evidence": {
                                "1": "据称该说法成立",
                                "2": "据称该说法成立",
                            },
                        }
                    ]
                }
            )

        result = await adjudicate_claim_support(
            call_llm,
            claims=["X"],
            source_registry=registry,
            body_resolver=lambda ref: resolve_content_ref(ref, logs),
        )

        # 边界证明：反驳句根本没进 prompt，摘要进了
        assert "与官方通报不符" not in captured["prompt"]
        assert "据称该说法成立" in captured["prompt"]
        assert result.bodies_adjudicated == {1, 2}
        assert set(result.body_excerpts) == {1, 2}

        verdict = result.claims[0]
        assert verdict.support == [1, 2]
        support = independent_support(
            verdict,
            registry,
            bodies_adjudicated=result.bodies_adjudicated,
            body_excerpts=result.body_excerpts,
        )
        assert support.count == 2
        assert support.certain is False
        assert "支持依据未能在传入的正文片段中核对" in support.reason
        rendered = render_independent_support(support)
        assert rendered.startswith("未核实")
        assert "独立来源支持" not in rendered

    def test_evidence_outside_passed_excerpt_is_not_grounded(self):
        """依据来自 snippet 而非 body_excerpt → 计数不确定（直接走计数路径）。"""
        registry = _registry(
            _entry(1, "https://a.com/x", snippet="摘要说成立"),
            _entry(2, "https://b.com/y", snippet="摘要说成立"),
        )
        verdict = ClaimVerdict(
            claim="X",
            support=[1, 2],
            evidence={1: "摘要说成立", 2: "摘要说成立"},
        )
        excerpts = {1: "无关背景片段", 2: "无关背景片段"}

        support = independent_support(
            verdict,
            registry,
            bodies_adjudicated={1, 2},
            body_excerpts=excerpts,
        )

        assert support.certain is False
        assert "支持依据未能在传入的正文片段中核对" in support.reason

        # 依据确实出现在各自片段里时才恢复确定数
        grounded = ClaimVerdict(
            claim="X",
            support=[1, 2],
            evidence={1: "无关背景片段", 2: "无关背景片段"},
        )
        assert (
            independent_support(
                grounded,
                registry,
                bodies_adjudicated={1, 2},
                body_excerpts=excerpts,
            ).certain
            is True
        )


class TestSanitizeClaimMap:
    """M3 阻塞项：畸形裁决必须在出稿前被收敛，不得进入支持度与拓扑。"""

    def test_claim_not_in_input_is_dropped(self):
        registry = _registry(_entry(1, "https://a.com/x"))
        raw = ClaimSupportMap(
            claims=[
                ClaimVerdict(claim="模型自己加的结论", support=[1]),
                ClaimVerdict(claim="输入主张", support=[1]),
            ]
        )

        clean = sanitize_claim_map(raw, ["输入主张"], registry)

        assert [verdict.claim for verdict in clean.claims] == ["输入主张"]

    def test_unregistered_source_ids_are_removed_from_all_buckets(self):
        registry = _registry(_entry(1, "https://a.com/x"))
        raw = ClaimSupportMap(
            claims=[ClaimVerdict(claim="X", support=[1, 9], refute=[9], unknown=[9])]
        )

        clean = sanitize_claim_map(raw, ["X"], registry)

        assert clean.claims[0].support == [1]
        assert clean.claims[0].refute == []
        assert clean.claims[0].unknown == []

    def test_same_source_in_support_and_refute_becomes_unknown(self):
        """同一来源同时判支持与反驳 → 只保留 unknown，拓扑不再画两种边。"""
        registry = _registry(_entry(1, "https://a.com/x"), _entry(2, "https://b.com/y"))
        raw = ClaimSupportMap(
            claims=[ClaimVerdict(claim="X", support=[1], refute=[1, 2])]
        )

        clean = sanitize_claim_map(raw, ["X"], registry)

        assert clean.claims[0].support == []
        assert clean.claims[0].refute == [2]
        assert clean.claims[0].unknown == [1]

    def test_duplicate_claims_keep_first_only(self):
        registry = _registry(_entry(1, "https://a.com/x"))
        raw = ClaimSupportMap(
            claims=[
                ClaimVerdict(claim="X", support=[1]),
                ClaimVerdict(claim="X", refute=[1]),
            ]
        )

        clean = sanitize_claim_map(raw, ["X"], registry)

        assert len(clean.claims) == 1
        assert clean.claims[0].support == [1]

    def test_evidence_and_origin_groups_are_filtered(self):
        registry = _registry(_entry(1, "https://a.com/x"), _entry(2, "https://b.com/y"))
        raw = ClaimSupportMap(
            claims=[
                ClaimVerdict(
                    claim="X",
                    support=[1, 2],
                    evidence={1: "依据", 9: "表外依据"},
                    origin_groups=[[1, 2], [1, 9]],
                )
            ]
        )

        clean = sanitize_claim_map(raw, ["X"], registry)

        assert clean.claims[0].evidence == {1: "依据"}
        assert clean.claims[0].origin_groups == [[1, 2]]

    def test_bodies_adjudicated_is_restricted_to_citable_ids(self):
        registry = _registry(_entry(1, "https://a.com/x"))
        raw = ClaimSupportMap(
            claims=[ClaimVerdict(claim="X", support=[1])],
            bodies_adjudicated={1, 9},
        )

        clean = sanitize_claim_map(raw, ["X"], registry)

        assert clean.bodies_adjudicated == {1}

    def test_validate_flags_claim_outside_input_and_duplicates(self):
        registry = _registry(_entry(1, "https://a.com/x"))
        raw = ClaimSupportMap(
            claims=[
                ClaimVerdict(claim="表外结论", support=[1]),
                ClaimVerdict(claim="X", support=[1]),
                ClaimVerdict(claim="X", support=[1]),
            ]
        )

        issues = validate_claim_map(raw, registry, claims=["X"])

        assert "claim_1_not_in_input" in issues
        assert "claim_3_duplicate_claim" in issues


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
