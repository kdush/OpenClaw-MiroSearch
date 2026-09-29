"""阶段 A 数据契约验收：来源注册表（A-2 / A-3 / A-4）。

对应路线图《阶段 B 验收清单》中与来源注册表相关的场景：
仅搜索命中、抓取成功/失败、同 URL 多 provider 命中、重定向、无有效来源，
以及"历史压缩后来源与引用映射仍可用"（注册表不依赖 LLM message_history）。
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.source_registry import (  # noqa: E402
    SourceRegistry,
    normalize_source_url,
)
from src.io.report_structure import ReportStructureValidator  # noqa: E402


class TestUrlNormalization:
    """A-4：只处理 scheme/host 大小写、fragment 与明确的跟踪参数。"""

    def test_scheme_and_host_lowercased_path_preserved(self):
        assert (
            normalize_source_url("HTTPS://Example.COM/A/B?x=1#frag")
            == "https://example.com/A/B?x=1"
        )

    def test_fragment_is_dropped(self):
        assert normalize_source_url("https://e.com/a#section-2") == "https://e.com/a"

    def test_tracking_params_are_dropped(self):
        assert (
            normalize_source_url("https://e.com/a?utm_source=x&gclid=y&id=5")
            == "https://e.com/a?id=5"
        )

    def test_content_distinguishing_params_are_kept(self):
        # 评审裁决 5：不删可能区分内容的查询参数
        assert (
            normalize_source_url("https://e.com/a?id=5&page=2")
            == "https://e.com/a?id=5&page=2"
        )

    def test_path_case_is_not_lowercased(self):
        # 评审裁决 5：不照搬 jev-search 对整个路径转小写
        assert (
            normalize_source_url("https://e.com/CaseSensitive/Path")
            == "https://e.com/CaseSensitive/Path"
        )

    def test_default_port_dropped_and_custom_port_kept(self):
        assert normalize_source_url("https://e.com:443/a") == "https://e.com/a"
        assert normalize_source_url("http://e.com:80/a") == "http://e.com/a"
        assert normalize_source_url("https://e.com:8443/a") == "https://e.com:8443/a"

    def test_rejects_non_http_and_unparsable_values(self):
        for value in ("ftp://e.com/a", "not a url", "https://", "", "   "):
            assert normalize_source_url(value) == ""

    def test_rejects_embedded_whitespace_and_control_chars(self):
        assert normalize_source_url("https://e.com/a b") == ""
        assert normalize_source_url("https://e.com/a\nb") == ""


class TestRegistryContract:
    """A-2/A-3：检索命中 ≠ 可引用来源 ≠ 独立证据。"""

    def test_same_url_from_two_providers_is_one_source(self):
        registry = SourceRegistry()
        registry.register_search_hits(
            {
                "provider": "searxng",
                "organic": [{"link": "https://e.com/a", "title": "T"}],
            },
            turn=1,
        )
        registry.register_search_hits(
            {"provider": "serper", "organic": [{"link": "https://e.com/a"}]},
            turn=2,
        )

        # 同一 URL 被两个引擎命中不增加独立来源数
        assert len(registry.entries) == 1
        entry = registry.entries[0]
        assert entry.source_id == 1
        assert [d["provider"] for d in entry.discoveries] == ["searxng", "serper"]
        # 先到的标题不被后续空标题覆盖
        assert entry.title == "T"

    def test_search_hit_starts_as_snippet_only(self):
        registry = SourceRegistry()
        registry.register_search_hits(
            {
                "provider": "serper",
                "organic": [{"link": "https://e.com/a", "snippet": "S"}],
            },
            turn=1,
        )
        entry = registry.entries[0]
        assert entry.status == "snippet_only"
        assert entry.snippet == "S"
        assert entry.domain == "e.com"
        assert entry.modality == "text"

    def test_redirect_chain_merges_into_one_entry_with_aliases(self):
        registry = SourceRegistry()
        registry.register_search_hits(
            {"provider": "serper", "organic": [{"link": "https://e.com/a"}]},
            turn=1,
        )
        registry.mark_fetched(
            "https://e.com/a",
            final_url="https://e.com/b",
            redirect_chain=["https://e.com/mid"],
            turn=2,
        )

        assert len(registry.entries) == 1
        entry = registry.entries[0]
        assert entry.source_id == 1
        assert entry.status == "fetched"
        assert set(entry.aliases) == {"https://e.com/b", "https://e.com/mid"}
        # 重定向两端都能解析回同一条目、同一编号
        assert registry.find("https://e.com/b") is entry
        assert registry.find("https://e.com/mid") is entry

    def test_fetch_failure_does_not_override_fetched(self):
        registry = SourceRegistry()
        registry.mark_fetched("https://e.com/a", turn=1)
        registry.mark_fetch_failed("https://e.com/a", turn=2)
        assert registry.entries[0].status == "fetched"

    def test_mark_fetch_failed_records_status(self):
        registry = SourceRegistry()
        registry.register_search_hits(
            {"provider": "serper", "organic": [{"link": "https://e.com/a"}]},
            turn=1,
        )
        registry.mark_fetch_failed("https://e.com/a", turn=2)
        assert registry.entries[0].status == "fetch_failed"

    def test_invalid_urls_are_not_registered(self):
        registry = SourceRegistry()
        registry.register_search_hits(
            {
                "provider": "serper",
                "organic": [
                    {"link": "ftp://e.com/a"},
                    {"link": ""},
                    {"link": "https://e.com/ok"},
                ],
            },
            turn=1,
        )
        assert [e.normalized_url for e in registry.entries] == ["https://e.com/ok"]

    def test_registry_survives_serialization_round_trip(self):
        """历史压缩丢掉旧工具 JSON 后，来源与编号映射仍须可用。"""
        registry = SourceRegistry()
        registry.register_search_hits(
            {
                "provider": "serper",
                "organic": [
                    {"link": "https://e.com/a"},
                    {"link": "https://e.com/b"},
                ],
            },
            turn=1,
        )
        snapshot = registry.to_dict()

        restored = SourceRegistry(**snapshot)

        assert [entry.source_id for entry in restored.entries] == [1, 2]
        assert restored.find("https://e.com/b").source_id == 2
        # 编号不重排：还原后再注册新来源继续递增
        restored.register_search_hits(
            {"provider": "serper", "organic": [{"link": "https://e.com/c"}]},
            turn=5,
        )
        assert [entry.source_id for entry in restored.entries] == [1, 2, 3]

    def test_unsorted_and_tracking_variants_dedupe(self):
        """同文多 URL：跟踪参数/fragment/大小写差异不应产生重复来源。"""
        registry = SourceRegistry()
        registry.register_search_hits(
            {
                "provider": "serper",
                "organic": [
                    {"link": "https://E.com/a?utm_source=x#top"},
                    {"link": "https://e.com/a"},
                ],
            },
            turn=1,
        )
        assert len(registry.entries) == 1


class TestEmptyFetchContract:
    """空抓取（HTTP 成功但无正文）不得取得 fetched 状态。"""

    def test_mark_fetch_empty_keeps_discovered_source_as_snippet_only(self):
        registry = SourceRegistry()
        registry.register_search_hits(
            {"provider": "serper", "organic": [{"link": "https://e.com/a"}]}, turn=1
        )

        registry.mark_fetch_empty("https://e.com/a", turn=2)

        entry = registry.entries[0]
        assert entry.status == "snippet_only"
        # 摘要仍可引用，但不能显示成已抓取全文
        assert entry.source_id in {
            source["source_id"]
            for source in ReportStructureValidator.citable_sources(registry.to_dict())
        }
        assert "已抓取全文" not in ReportStructureValidator.build_source_references(
            registry.to_dict()
        )

    def test_mark_fetch_empty_on_unseen_url_is_not_citable(self):
        registry = SourceRegistry()

        registry.mark_fetch_empty("https://e.com/new", turn=1)

        assert registry.entries[0].status == "fetch_empty"
        assert ReportStructureValidator.citable_sources(registry.to_dict()) == []

    def test_mark_fetch_empty_does_not_downgrade_fetched(self):
        registry = SourceRegistry()
        registry.mark_fetched("https://e.com/a", turn=1)

        registry.mark_fetch_empty("https://e.com/a", turn=2)

        assert registry.entries[0].status == "fetched"

    def test_mark_fetched_with_blank_body_delegates_to_empty_state(self):
        """显式告知"没有正文"时，注册表自己也不得给出 fetched。"""
        registry = SourceRegistry()
        registry.register_search_hits(
            {"provider": "serper", "organic": [{"link": "https://e.com/a"}]}, turn=1
        )

        registry.mark_fetched("https://e.com/a", body_text="   ", turn=2)

        assert registry.entries[0].status == "snippet_only"


class TestRedirectCanonicalIdentity:
    """重定向两端各自先登记：必须收敛成同一原始来源的单一规范身份。"""

    @staticmethod
    def _register_pair(registry, first, second):
        for turn, url in enumerate((first, second), 1):
            registry.register_search_hits(
                {"provider": "serper", "organic": [{"link": url}]}, turn=turn
            )

    def test_original_registered_first_merges(self):
        registry = SourceRegistry()
        self._register_pair(registry, "https://e.com/a", "https://e.com/b")
        assert len(registry.entries) == 2

        registry.mark_fetched("https://e.com/a", final_url="https://e.com/b", turn=3)

        assert len(registry.entries) == 1
        entry = registry.entries[0]
        assert entry.source_id == 1
        assert entry.status == "fetched"
        assert registry.find("https://e.com/a") is entry
        assert registry.find("https://e.com/b") is entry
        assert len(entry.discoveries) == 2

    def test_target_registered_first_merges(self):
        registry = SourceRegistry()
        self._register_pair(registry, "https://e.com/b", "https://e.com/a")

        registry.mark_fetched("https://e.com/a", final_url="https://e.com/b", turn=3)

        assert len(registry.entries) == 1
        entry = registry.entries[0]
        # 编号取最早发布的那条，其余编号不重排
        assert entry.source_id == 1
        assert registry.find("https://e.com/a") is entry
        assert registry.find("https://e.com/b") is entry
        assert len(entry.discoveries) == 2

    def test_merged_ids_are_not_reused(self):
        """并入后释放的编号不得被新来源复用，否则旧引用会指向错来源。"""
        registry = SourceRegistry()
        self._register_pair(registry, "https://e.com/a", "https://e.com/b")
        registry.mark_fetched("https://e.com/a", final_url="https://e.com/b", turn=3)

        registry.register_search_hits(
            {"provider": "serper", "organic": [{"link": "https://e.com/c"}]}, turn=4
        )

        assert [entry.source_id for entry in registry.entries] == [1, 3]

    def test_merged_ids_survive_serialization_round_trip(self):
        """合并 → 快照 → 恢复 → 新来源：退役编号不得在恢复后复活。

        并入（重定向合并）退役了编号 2，而编号 2 只存在于 ``next_source_id``
        计数器里——快照若不带上它，恢复后就只按现存条目的 ``max(source_id)+1``
        重建，把 2 重新发给新来源，使旧引用 [2] 错指到别的来源。
        """
        registry = SourceRegistry()
        self._register_pair(registry, "https://e.com/a", "https://e.com/b")
        registry.mark_fetched("https://e.com/a", final_url="https://e.com/b", turn=3)
        assert [entry.source_id for entry in registry.entries] == [1]

        snapshot = registry.to_dict()
        assert snapshot["next_source_id"] == 3

        restored = SourceRegistry(**snapshot)
        restored.register_search_hits(
            {"provider": "serper", "organic": [{"link": "https://e.com/c"}]}, turn=4
        )

        # 与同进程注册结果一致：新来源拿到 3，而不是复活的 2
        assert [entry.source_id for entry in restored.entries] == [1, 3]
        assert restored.find("https://e.com/c").source_id == 3
        # 二次往返仍然单调递增，不因再次快照而回退
        assert SourceRegistry(**restored.to_dict()).to_dict()["next_source_id"] == 4
