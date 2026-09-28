# Copyright (c) 2025 MiroMind
# This source code is licensed under the Apache 2.0 License.

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse, urlsplit

STATUS_SNIPPET_ONLY = "snippet_only"
STATUS_FETCHED = "fetched"
STATUS_FETCH_FAILED = "fetch_failed"
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

    def __post_init__(self) -> None:
        self.entries = [
            SourceEntry(**entry) if isinstance(entry, dict) else entry
            for entry in self.entries
        ]
        self._index = {entry.normalized_url: entry for entry in self.entries}
        for entry in self.entries:
            for alias in entry.aliases:
                self._index.setdefault(alias, entry)

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
    ) -> None:
        urls = [url, *(redirect_chain or []), final_url]
        normalized_urls = list(
            dict.fromkeys(
                normalized
                for value in urls
                if (normalized := normalize_source_url(value))
            )
        )
        if not normalize_source_url(url):
            return
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
        # 已发布的编号不重排；已登记的重定向两端互为别名并共享抓取状态。
        for entry in related:
            entry.aliases = [
                value for value in normalized_urls if value != entry.normalized_url
            ]
            entry.status = STATUS_FETCHED
            entry.last_seen_turn = max(entry.last_seen_turn, turn)
            if content_ref and not entry.content_ref:
                entry.content_ref = content_ref
            if not entry.title and isinstance(title, str):
                entry.title = title
        primary_urls = {entry.normalized_url for entry in related}
        for value in normalized_urls:
            if value not in primary_urls:
                self._index[value] = related[0]

    def mark_fetch_failed(self, url: str, *, turn: int = 0) -> None:
        entry = self._ensure_entry(url, turn)
        if entry is not None and entry.status != STATUS_FETCHED:
            entry.status = STATUS_FETCH_FAILED

    def _ensure_entry(self, url: str, turn: int) -> Optional[SourceEntry]:
        normalized = normalize_source_url(url)
        if not normalized:
            return None
        entry = self._index.get(normalized)
        if entry is None:
            entry = SourceEntry(
                source_id=len(self.entries) + 1,
                raw_url=url,
                normalized_url=normalized,
                domain=normalize_domain(url),
                first_seen_turn=turn,
                last_seen_turn=turn,
            )
            self.entries.append(entry)
            self._index[normalized] = entry
        else:
            entry.last_seen_turn = max(entry.last_seen_turn, turn)
        return entry
