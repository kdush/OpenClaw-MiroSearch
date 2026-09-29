"""阶段 A-5 / M2 验收：引用准入与 References 契约。

对应路线图《阶段 B 验收清单》：无有效来源、引用未登记降级、References 状态标注、
恶意摘要不得注入 HTML、以及"不造 URL"。
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.io.report_structure import ReportStructureValidator  # noqa: E402


def _entry(
    source_id,
    url,
    *,
    status="snippet_only",
    title="",
    snippet="",
    discoveries=None,
    aliases=None,
):
    return {
        "source_id": source_id,
        "raw_url": url,
        "normalized_url": url,
        "domain": "e.com",
        "title": title,
        "snippet": snippet,
        "status": status,
        "modality": "text",
        "discoveries": (
            discoveries
            if discoveries is not None
            else [{"provider": "serper", "turn": 1, "position": 1}]
        ),
        "first_seen_turn": 1,
        "last_seen_turn": 1,
        "content_ref": None,
        "aliases": aliases or [],
    }


def _registry(*entries):
    return {"entries": list(entries)}


class TestCitableSources:
    """A-5：报告只允许引用注册表内、确实返回过的来源。"""

    def test_snippet_only_with_discovery_is_citable(self):
        registry = _registry(_entry(1, "https://e.com/a", title="T"))
        assert [
            s["source_id"] for s in ReportStructureValidator.citable_sources(registry)
        ] == [1]

    def test_entry_without_discovery_and_not_fetched_is_excluded(self):
        registry = _registry(_entry(1, "https://e.com/a", discoveries=[]))
        assert ReportStructureValidator.citable_sources(registry) == []

    def test_fetched_entry_without_discovery_is_citable(self):
        registry = _registry(
            _entry(1, "https://e.com/a", status="fetched", discoveries=[])
        )
        assert len(ReportStructureValidator.citable_sources(registry)) == 1

    def test_unknown_status_and_bad_id_are_excluded(self):
        registry = _registry(
            _entry(1, "https://e.com/a", status="weird"),
            _entry(0, "https://e.com/b"),
            {**_entry(2, "https://e.com/c"), "source_id": "2"},
        )
        assert ReportStructureValidator.citable_sources(registry) == []

    def test_non_http_url_is_excluded(self):
        registry = _registry(_entry(1, "ftp://e.com/a"))
        assert ReportStructureValidator.citable_sources(registry) == []


class TestBuildSourceReferences:
    """Q2：标题 + 可读摘要 + 链接 + 状态。"""

    def test_status_labels_and_snippet_rendered(self):
        registry = _registry(
            _entry(1, "https://e.com/a", title="A", snippet="摘要一"),
            _entry(2, "https://e.com/b", status="fetched", title="B"),
            _entry(3, "https://e.com/c", status="fetch_failed", title="C"),
        )
        refs = ReportStructureValidator.build_source_references(registry)

        assert "- [1] [A](<https://e.com/a>) — 仅摘要" in refs
        assert "- [2] [B](<https://e.com/b>) — 已抓取全文（不代表事实核实）" in refs
        assert "- [3] [C](<https://e.com/c>) — 抓取失败（仅摘要）" in refs
        assert "摘要一" in refs
        assert "未返回摘要。" in refs

    def test_no_citable_sources_is_stated_honestly(self):
        refs = ReportStructureValidator.build_source_references(_registry())
        assert "未获得可引用来源" in refs

    def test_malicious_snippet_cannot_inject_html(self):
        registry = _registry(
            _entry(
                1,
                "https://e.com/a",
                title="<img src=x onerror=alert(1)>",
                snippet="<script>alert(2)</script>",
            )
        )
        refs = ReportStructureValidator.build_source_references(registry)

        assert "<script" not in refs
        assert "<img" not in refs
        assert "&lt;script" in refs


class TestEnforceCitations:
    """M2：内联引用校验——不造 URL、未登记降级。"""

    def test_registered_citation_is_kept(self):
        registry = _registry(_entry(1, "https://e.com/a", title="A"))
        text = "结论 [1]。\n\n## References\n\n- [1] [A](<https://e.com/a>) — 仅摘要\n"

        result, issues = ReportStructureValidator.enforce_citations(text, registry)

        assert "[1]" in result
        assert "引用未登记" not in result
        assert "unregistered_citation:1" not in issues

    def test_unregistered_citation_is_downgraded_and_reported(self):
        registry = _registry(_entry(1, "https://e.com/a", title="A"))
        text = "结论 [9]。\n\n## References\n\n- [1] [A](<https://e.com/a>) — 仅摘要\n"

        result, issues = ReportStructureValidator.enforce_citations(text, registry)

        assert "（引用未登记）" in result
        assert "unregistered_citation:9" in issues

    def test_empty_registry_flags_no_citable_sources_and_fabricates_nothing(self):
        text = "结论 [1]。\n"

        result, issues = ReportStructureValidator.enforce_citations(
            text, _registry(), include_references=False
        )

        assert "no_citable_sources" in issues
        assert "http" not in result
        assert "（引用未登记）" in result

    def test_mismatched_reference_target_is_downgraded(self):
        registry = _registry(_entry(1, "https://e.com/a", title="A"))
        text = (
            "结论 [1]。\n\n"
            "## References\n\n"
            "- [1] [A](<https://evil.example/x>) — 仅摘要\n"
        )

        result, issues = ReportStructureValidator.enforce_citations(text, registry)

        assert "citation_target_mismatch:1" in issues
        assert "（引用链接不匹配）" in result

    def test_references_block_is_appended_when_requested(self):
        registry = _registry(_entry(1, "https://e.com/a", title="A"))

        result, _ = ReportStructureValidator.enforce_citations("结论 [1]。", registry)

        assert "## References" in result
        assert "- [1] [A](<https://e.com/a>) — 仅摘要" in result
