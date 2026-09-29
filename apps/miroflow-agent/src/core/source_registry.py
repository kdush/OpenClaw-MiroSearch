# Copyright (c) 2025 MiroMind
# This source code is licensed under the Apache 2.0 License.

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse, urlsplit

STATUS_SNIPPET_ONLY = "snippet_only"
STATUS_FETCHED = "fetched"
STATUS_FETCH_FAILED = "fetch_failed"
# 抓取到达了页面但没有正文：既不是失败（HTTP 成功）也不是可引用的全文来源。
STATUS_FETCH_EMPTY = "fetch_empty"
_TRACKING_PARAMS = {"gclid", "fbclid", "msclkid", "igshid", "mc_cid", "mc_eid"}


def normalize_source_url(url: str) -> str:
    if not isinstance(url, str):
        return ""
    url = url.strip()
    if any(char.isspace() or ord(char) < 32 for char in url):
        return ""
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return ""
        port = parts.port
    except ValueError:
        return ""

    userinfo, separator, authority = parts.netloc.rpartition("@")
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    port_suffix = authority[authority.rfind(":") :] if port is not None else ""
    if (parts.scheme, port) in {("http", 80), ("https", 443)}:
        port_suffix = ""
    if authority.endswith(":"):
        port_suffix = ":"
    netloc = (userinfo + separator if separator else "") + host + port_suffix

    segments = parts.query.split("&")
    kept = [
        segment
        for segment in segments
        if not segment.split("=", 1)[0].startswith("utm_")
        and segment.split("=", 1)[0] not in _TRACKING_PARAMS
    ]
    normalized = f"{parts.scheme}://{netloc}{parts.path}"
    # 空查询及空参数段也可能参与签名，不能用 parse_qs/urlencode 重写。
    if kept and "?" in url.split("#", 1)[0]:
        normalized += "?" + "&".join(kept)
    return normalized


# 抓取正文提取：与 SourceEntry.content_ref 指向的存储位置配套使用。
_BODY_TEXT_KEYS = ("content", "extracted_info", "text", "markdown")
_CONTENT_REF_RE = re.compile(r"^/step_logs/(\d+)/metadata/result$")


def extract_scrape_body_text(payload: Any) -> str:
    """从抓取工具返回的 JSON 载荷里取出正文文本（取不到就返回空串）。"""
    if not isinstance(payload, dict):
        return ""
    for key in _BODY_TEXT_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def resolve_content_ref(content_ref: Any, step_logs: Any) -> str:
    """把 ``/step_logs/<i>/metadata/result`` 指针解析回抓取正文文本。

    ``step_logs`` 可以是 ``StepLog`` 对象序列，也可以是等价的 dict 序列；指针
    越界、格式不符或载荷里没有正文时一律返回空串——调用方据此判定"正文不可读"，
    不能把读不到的正文当成已裁决过的证据。
    """
    if not isinstance(content_ref, str):
        return ""
    match = _CONTENT_REF_RE.match(content_ref.strip())
    if match is None or step_logs is None:
        return ""
    try:
        step_log = step_logs[int(match.group(1))]
    except (IndexError, KeyError, TypeError):
        return ""
    metadata = (
        step_log.get("metadata")
        if isinstance(step_log, dict)
        else getattr(step_log, "metadata", None)
    )
    if not isinstance(metadata, dict):
        return ""
    return extract_scrape_body_text(metadata.get("result"))


def normalize_domain(url: str) -> str:
    if not url:
        return ""
    try:
        domain = urlparse(url).netloc.lower().strip()
    except (TypeError, ValueError):
        return ""
    return domain[4:] if domain.startswith("www.") else domain


@dataclass
class SourceEntry:
    source_id: int
    raw_url: str
    normalized_url: str
    domain: str
    title: str = ""
    snippet: str = ""
    status: str = STATUS_SNIPPET_ONLY
    modality: str = "text"
    discoveries: list[dict[str, Any]] = field(default_factory=list)
    first_seen_turn: int = 0
    last_seen_turn: int = 0
    content_ref: Optional[str] = None
    aliases: list[str] = field(default_factory=list)


@dataclass
class SourceRegistry:
    entries: list[SourceEntry] = field(default_factory=list)
    # 下一个待发布编号。必须随快照一起持久化：并入（重定向合并）会退役一个编号，
    # 但退役编号只体现在这个计数器里——只按现存条目的 max(source_id)+1 重建，
    # 恢复后就会把退役编号重新发给新来源，使旧引用（如 [2]）错指到别的来源。
    # 它是 dataclass 字段（而非私有属性）是刻意的：``TaskLog.to_dict/to_json``
    # 走 ``dataclasses.asdict``，只认字段；私有计数器不会被序列化。
    next_source_id: int = 1

    def __post_init__(self) -> None:
        self.entries = [
            SourceEntry(**entry) if isinstance(entry, dict) else entry
            for entry in self.entries
        ]
        self._index = {entry.normalized_url: entry for entry in self.entries}
        for entry in self.entries:
            for alias in entry.aliases:
                self._index.setdefault(alias, entry)
        # 编号单调递增且永不回收：并入/移除条目后，新来源不得复用已发布过的编号。
        # 快照里的计数器优先（它记录了已退役编号），条目本身只是下界。
        counter = self.next_source_id if type(self.next_source_id) is int else 1
        entries_floor = max((entry.source_id + 1 for entry in self.entries), default=1)
        self.next_source_id = max(counter, 1, entries_floor)

    def find(self, url: str) -> Optional[SourceEntry]:
        return self._index.get(normalize_source_url(url))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def register_search_hits(self, parsed: dict[str, Any], turn: int) -> None:
        params = parsed.get("searchParameters")
        params = params if isinstance(params, dict) else {}
        provider = parsed.get("provider") or params.get("provider") or ""
        if provider == "multi-route":
            providers = params.get("providers_with_results")
            provider = (
                providers[0]
                if isinstance(providers, list) and len(providers) == 1
                else ""
            )
        provider = provider if isinstance(provider, str) else ""
        for key in ("organic", "Pages"):
            hits = parsed.get(key)
            if not isinstance(hits, list):
                continue
            for item in hits:
                if not isinstance(item, dict):
                    continue
                entry = self._ensure_entry(item.get("link") or item.get("url"), turn)
                if entry is None:
                    continue
                for name in ("title", "snippet"):
                    value = item.get(name)
                    if not getattr(entry, name) and isinstance(value, str):
                        setattr(entry, name, value)
                discoveries = item.get("discoveries")
                if not isinstance(discoveries, list) or not discoveries:
                    discoveries = [
                        {"provider": provider, "position": item.get("position")}
                    ]
                for discovery in discoveries:
                    if not isinstance(discovery, dict):
                        continue
                    hit_provider = discovery.get("provider")
                    position = discovery.get("position")
                    entry.discoveries.append(
                        {
                            "provider": hit_provider
                            if isinstance(hit_provider, str)
                            else "",
                            "turn": turn,
                            "position": position if type(position) is int else None,
                        }
                    )

    def mark_fetched(
        self,
        url: str,
        final_url: Optional[str] = None,
        content_ref: Optional[str] = None,
        *,
        turn: int = 0,
        redirect_chain: Optional[list[str]] = None,
        title: str = "",
        body_text: Optional[str] = None,
    ) -> None:
        """登记一次「确实抓到正文」的抓取。

        ``body_text`` 显式给出且为空时按空抓取处理（见 ``mark_fetch_empty``）：
        ``fetched`` 表示"有全文可读"，空结果不得取得该状态——否则会被展示成
        「已抓取全文」并取得引用/独立计数资格。
        """
        if body_text is not None and not str(body_text).strip():
            self.mark_fetch_empty(url, turn=turn)
            return

        if not normalize_source_url(url):
            return
        normalized_urls = list(
            dict.fromkeys(
                normalized
                for value in [url, *(redirect_chain or []), final_url]
                if (normalized := normalize_source_url(value))
            )
        )
        related = [
            entry
            for entry in self.entries
            if entry.normalized_url in normalized_urls
            or any(alias in normalized_urls for alias in entry.aliases)
        ]
        if not related:
            entry = self._ensure_entry(url, turn)
            if entry is None:
                return
            related = [entry]
        for entry in related:
            for value in [entry.normalized_url, *entry.aliases]:
                if value not in normalized_urls:
                    normalized_urls.append(value)
        # 重定向两端可能已被各自登记成两条条目；同一原始来源只能有一个规范身份，
        # 否则 References 与 M3 独立来源计数都会重复。规范条目取编号最小的那条
        # （最早发现），其余编号不重排、不回收。
        canonical = min(related, key=lambda entry: entry.source_id)
        for entry in related:
            if entry is not canonical:
                self._fold_into(entry, canonical)
        # 其余 URL 一律互为别名，并让它们继续解析到规范条目。
        canonical.aliases = []
        for value in normalized_urls:
            if value != canonical.normalized_url:
                canonical.aliases.append(value)
                self._index[value] = canonical
        canonical.status = STATUS_FETCHED
        canonical.last_seen_turn = max(canonical.last_seen_turn, turn)
        if content_ref and not canonical.content_ref:
            canonical.content_ref = content_ref
        if not canonical.title and isinstance(title, str):
            canonical.title = title

    def mark_fetch_empty(self, url: str, *, turn: int = 0) -> None:
        """抓取到达页面但没有正文：不得升级为 ``fetched``。

        原本只有搜索摘要的来源保持 ``snippet_only``（摘要仍可引用、但只能算
        "仅摘要"）；从未登记过的 URL 记为明确的 ``fetch_empty``——既不可引用，
        也不计入独立来源。
        """
        entry = self._ensure_entry(url, turn)
        if entry is None or entry.status == STATUS_FETCHED:
            return
        entry.last_seen_turn = max(entry.last_seen_turn, turn)
        if entry.status == STATUS_SNIPPET_ONLY and entry.discoveries:
            return
        entry.status = STATUS_FETCH_EMPTY

    def mark_fetch_failed(self, url: str, *, turn: int = 0) -> None:
        entry = self._ensure_entry(url, turn)
        if entry is not None and entry.status != STATUS_FETCHED:
            entry.status = STATUS_FETCH_FAILED

    def _fold_into(self, duplicate: "SourceEntry", canonical: "SourceEntry") -> None:
        """把重复条目并入规范条目，并让旧 URL/别名继续解析到规范条目。"""
        for discovery in duplicate.discoveries:
            if discovery not in canonical.discoveries:
                canonical.discoveries.append(discovery)
        for name in ("title", "snippet"):
            if not getattr(canonical, name) and getattr(duplicate, name):
                setattr(canonical, name, getattr(duplicate, name))
        if duplicate.content_ref and not canonical.content_ref:
            canonical.content_ref = duplicate.content_ref
        for value in [duplicate.normalized_url, *duplicate.aliases]:
            self._index[value] = canonical
        # 按身份移除：dataclass 的 __eq__ 逐字段比较，用 remove() 可能误删同值条目。
        self.entries = [entry for entry in self.entries if entry is not duplicate]

    def _ensure_entry(self, url: str, turn: int) -> Optional[SourceEntry]:
        normalized = normalize_source_url(url)
        if not normalized:
            return None
        entry = self._index.get(normalized)
        if entry is None:
            entry = SourceEntry(
                source_id=self.next_source_id,
                raw_url=url,
                normalized_url=normalized,
                domain=normalize_domain(url),
                first_seen_turn=turn,
                last_seen_turn=turn,
            )
            self.next_source_id += 1
            self.entries.append(entry)
            self._index[normalized] = entry
        else:
            entry.last_seen_turn = max(entry.last_seen_turn, turn)
        return entry
