"""来源状态与重定向身份：抓取结果如何落到注册表。

评审阻塞项回归：
- ``success=true`` 但正文为空（``empty_success``）曾被当成 ``fetched``，于是展示成
  「已抓取全文」并取得引用 / M3 独立计数资格；
- 重定向两端若各自先被登记成两条条目，``mark_fetched(original, final_url=target)``
  会留下两个 ``source_id`` 且都标为已抓取，使 References 与独立来源计数重复。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.orchestrator import Orchestrator  # noqa: E402
from src.core.source_registry import (  # noqa: E402
    SourceRegistry,
    resolve_content_ref,
)
from src.io.report_structure import ReportStructureValidator  # noqa: E402


class _FakeTaskLog:
    """够用的 task_log 桩：真实 SourceRegistry + 真实 step_logs 语义。"""

    def __init__(self):
        self.source_registry = SourceRegistry()
        self.step_logs = []

    def log_step(self, level, name, content, metadata=None):
        self.step_logs.append(
            {
                "level": level,
                "name": name,
                "content": content,
                "metadata": metadata or {},
            }
        )


def _orchestrator():
    orch = Orchestrator.__new__(Orchestrator)
    orch.task_log = _FakeTaskLog()
    return orch


def _scrape_payload(*, success: bool, content: str = "", url: str, **extra):
    payload = {"success": success, "url": url, "content": content}
    payload.update(extra)
    return {"result": json.dumps(payload, ensure_ascii=False)}


def _search_hit(registry: SourceRegistry, url: str, *, turn: int) -> None:
    registry.register_search_hits(
        {"provider": "serper", "organic": [{"link": url, "snippet": f"{url} 摘要"}]},
        turn=turn,
    )


class TestEmptySuccessIsNotFullText:
    def test_empty_success_does_not_mark_fetched(self):
        orch = _orchestrator()
        _search_hit(orch.task_log.source_registry, "https://e.com/a", turn=1)

        orch._record_scrape_source_state(
            "scrape_url",
            _scrape_payload(success=True, content="", url="https://e.com/a"),
            2,
            {"url": "https://e.com/a"},
        )

        entry = orch.task_log.source_registry.entries[0]
        assert entry.status == "snippet_only"
        # 不显示成「已抓取全文」
        assert entry.status != "fetched"
        reference = ReportStructureValidator.build_source_references(
            orch.task_log.source_registry.to_dict()
        )
        assert "已抓取全文" not in reference

    def test_empty_success_on_unseen_url_is_explicit_empty_state(self):
        orch = _orchestrator()

        orch._record_scrape_source_state(
            "scrape_url",
            _scrape_payload(success=True, content="", url="https://e.com/new"),
            1,
            {"url": "https://e.com/new"},
        )

        entry = orch.task_log.source_registry.entries[0]
        assert entry.status == "fetch_empty"
        # 空抓取不取得引用资格
        assert (
            ReportStructureValidator.citable_sources(
                orch.task_log.source_registry.to_dict()
            )
            == []
        )

    def test_empty_success_stores_no_content_ref(self):
        orch = _orchestrator()
        _search_hit(orch.task_log.source_registry, "https://e.com/a", turn=1)

        orch._record_scrape_source_state(
            "scrape_url",
            _scrape_payload(success=True, content="   ", url="https://e.com/a"),
            2,
            {"url": "https://e.com/a"},
        )

        entry = orch.task_log.source_registry.entries[0]
        assert entry.content_ref is None
        # 没有正文可读 → 裁决阶段也读不到
        assert resolve_content_ref(entry.content_ref, orch.task_log.step_logs) == ""


class TestEvidenceScrapeStoresReadableBody:
    def test_evidence_marks_fetched_and_body_is_resolvable(self):
        orch = _orchestrator()
        _search_hit(orch.task_log.source_registry, "https://e.com/a", turn=1)

        orch._record_scrape_source_state(
            "scrape_url",
            _scrape_payload(
                success=True, content="正文明确反驳了该主张。", url="https://e.com/a"
            ),
            2,
            {"url": "https://e.com/a"},
        )

        entry = orch.task_log.source_registry.entries[0]
        assert entry.status == "fetched"
        assert entry.content_ref is not None
        # content_ref 必须真的能解析回正文，否则 M3 只能裁决摘要
        assert resolve_content_ref(entry.content_ref, orch.task_log.step_logs) == (
            "正文明确反驳了该主张。"
        )

    def test_failure_marks_fetch_failed(self):
        orch = _orchestrator()
        _search_hit(orch.task_log.source_registry, "https://e.com/a", turn=1)

        orch._record_scrape_source_state(
            "scrape_url",
            _scrape_payload(success=False, url="https://e.com/a", error="timeout"),
            2,
            {"url": "https://e.com/a"},
        )

        assert orch.task_log.source_registry.entries[0].status == "fetch_failed"


class TestRedirectIdentityInOrchestrator:
    """两种登记顺序都要收敛成同一原始来源、同一编号。"""

    def test_original_registered_first(self):
        orch = _orchestrator()
        registry = orch.task_log.source_registry
        _search_hit(registry, "https://e.com/a", turn=1)
        _search_hit(registry, "https://e.com/b", turn=1)
        assert len(registry.entries) == 2

        orch._record_scrape_source_state(
            "scrape_url",
            _scrape_payload(
                success=True,
                content="正文",
                url="https://e.com/a",
                final_url="https://e.com/b",
                redirect_chain=["https://e.com/mid"],
            ),
            2,
            {"url": "https://e.com/a"},
        )

        assert len(registry.entries) == 1
        entry = registry.entries[0]
        assert entry.source_id == 1
        assert entry.status == "fetched"
        assert registry.find("https://e.com/b") is entry
        assert registry.find("https://e.com/mid") is entry
        # References 只列一条、可引用来源只有一个 → M3 独立来源计数不重复
        snapshot = registry.to_dict()
        assert len(ReportStructureValidator.citable_sources(snapshot)) == 1
        reference_lines = [
            line
            for line in ReportStructureValidator.build_source_references(
                snapshot
            ).splitlines()
            if line.startswith("- [")
        ]
        assert len(reference_lines) == 1

    def test_target_registered_first(self):
        orch = _orchestrator()
        registry = orch.task_log.source_registry
        _search_hit(registry, "https://e.com/b", turn=1)
        _search_hit(registry, "https://e.com/a", turn=2)

        orch._record_scrape_source_state(
            "scrape_url",
            _scrape_payload(
                success=True,
                content="正文",
                url="https://e.com/a",
                final_url="https://e.com/b",
            ),
            3,
            {"url": "https://e.com/a"},
        )

        assert len(registry.entries) == 1
        entry = registry.entries[0]
        # 保留已发布的最小编号，不重排其余编号
        assert entry.source_id == 1
        assert registry.find("https://e.com/a") is entry
        assert registry.find("https://e.com/b") is entry
        # 两条检索命中的溯源信息都保留下来
        assert len(entry.discoveries) == 2
