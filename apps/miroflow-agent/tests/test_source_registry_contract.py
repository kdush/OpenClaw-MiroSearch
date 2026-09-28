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
