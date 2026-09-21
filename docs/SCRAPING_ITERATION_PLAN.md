[English](SCRAPING_ITERATION_PLAN.md) | [中文](SCRAPING_ITERATION_PLAN_zh.md)

# Web Scraping Capability Iteration Plan (v0.2.2 → v0.3.x)

Last updated: 2026-08-13
Current repository version: `v0.2.11`
Scraping-specific delivery version: `v0.2.5` (T1 / T2 / T3 / T4 / T5 / T6 / T7 / T8 tool changes shipped; E-class prompt collaboration remains incomplete; T9 pending)
Scope: Capability expansion of the MCP tool `scrape_url` (located at `libs/miroflow-tools/src/miroflow_tools/dev_mcp_servers/search_and_scrape_webpage.py`), enabling LLMs to proactively “open the full content” when `google_search` snippets are insufficient.

## 0. Progress Tracking

| Task | Topic | Status | Landing Version |
|------|-------|--------|-----------------|
| T1 | Shared `httpx.AsyncClient` + phased metrics | ✅ Done | v0.2.3 |
| T2 | Manual redirect loop + per-hop SSRF validation + max 5 hops | ✅ Done | v0.2.3 |
| T4 | Chinese encoding fallback (header → meta → charset_normalizer) | ✅ Done | v0.2.3 |
| T3 | PDF extraction + 20MB response size cap | ✅ Done | v0.2.4 |
| T5 | Direct pass-through for JSON / RSS / Atom / XML | ✅ Done | v0.2.4 |
| T6 | Primary `trafilatura` path + bs4 fallback | ✅ Done | v0.2.5-scrape |
| T7 | HTML table → markdown conversion | ✅ Done | v0.2.5-scrape |
| T8 | Sentence / paragraph boundary truncation | ✅ Done | v0.2.5-scrape |
| T9 | `scrape_urls` batch concurrency | ⏳ Pending | v0.3.0-scrape |

______________________________________________________________________

## 1. Background

The `google_search` family of tools returns only titles, summaries, and URLs — insufficient for deriving trustworthy conclusions on regulatory texts, official announcements, statistical bulletins, or long-form reports.
v0.2.2 shipped a minimal viable `scrape_url`: built on `httpx + BeautifulSoup`, supporting only HTML/XHTML/plain text, single-request mode, hard character-based truncation, no SSRF re-validation after redirects, and no PDF/JSON/RSS support.

Observed bottlenecks during real-world runs (e.g., “Shenzhen bus smoking incident”):

- PDF announcements / RSS feeds are outright rejected by the `content-type` allowlist.
- Some government sites serve `Content-Type: text/html; charset=GBK`, causing Chinese garbling under bs4’s default UTF-8 decoding.
- Long documents are truncated mid-sentence via hard character slicing, breaking LLM context continuity.
- Serial scraping of multiple URLs compounds latency (5 sites × 25s timeout = worst-case 125s), degrading end-to-end delivery latency.
- Redirect following is handled internally by httpx, exposing risk of landing on internal networks or cloud metadata services without explicit interception.

______________________________________________________________________

## 2. Gap Analysis (4 Dimensions)

### 2.1 Functional Dimension (A)

| ID | Current State | Target |
|----|---------------|--------|
| A1 | Supports only `text/html` / `application/xhtml+xml` / `text/plain` | Also cover PDF / JSON / RSS / Atom / XML, exposing unified schema to LLM |
| A2 | PDF responses rejected outright | Extract PDF text via `pdfminer.six`, preserving paragraph boundaries |
| A3 | JSON / RSS responses rejected outright | Direct pass-through: retain raw JSON + schema-sniffed summary; RSS/Atom return structured `entries` |

### 2.2 Quality Dimension (B)

| ID | Current State | Target |
|----|---------------|--------|
| B1 | bs4 + heuristic selectors for main content extraction — unstable recall | Introduce `trafilatura` as primary path, bs4 only as fallback |
| B2 | Direct `response.text` decoding relying on httpx auto-encoding detection | Add `charset_normalizer` + `<meta charset>` fallback, specifically for Chinese GBK/GB18030 sites |
| B3 | `<table>` collapses to plain text under `get_text()`, losing column alignment | Convert tables to markdown, preserving row/column alignment |
| B4 | Overlong content truncated via `text[:cap_chars]` hard cut | Truncate at paragraph / period / Chinese full-stop boundaries with backtracking; attach `truncated_reason` |

### 2.3 Performance & Throughput Dimension (C)

| ID | Current State | Target |
|----|---------------|--------|
| C1 | New `httpx.AsyncClient` instantiated per call | Share singleton client, reuse connection pool; track request / parse / extraction metrics |
| C2 | Only supports `scrape_url(url)` single-item | Add `scrape_urls(urls: list[str], ...)` with semaphore-constrained concurrent scraping |

### 2.4 Security & Stability Dimension (D)

| ID | Current State | Target |
|----|---------------|--------|
| D1 | Input SSRF + scheme validation applied | Maintain status quo (already implemented) |
| D2 | httpx default `follow_redirects=True` handles redirection internally | Switch to `follow_redirects=False`, manually loop up to 5 hops |
| D3 | No secondary SSRF validation after redirect resolution | Run `_is_private_or_loopback_host` on *every* hop’s `Location` target |
| D4 | Response body capped only by `max_chars` post-decoding | Enforce ≤20MB body limit *during streaming* (to prevent memory DoS) |

### 2.5 LLM Collaboration Dimension (E)

| ID | Current State | Target |
|----|---------------|--------|
| E1 | LLM sees only `scrape_url(url, max_chars)` in prompt | Explicitly state in system prompt / tool description: “If snippet insufficient → immediately call `scrape_url`”, and enforce inclusion of verbatim source excerpts per citation |
| E2 | Tool documentation only in English | Bilingual key-use-case descriptions (regulatory notices, statistical bulletins, news full-text) |

> Note: E-class changes are tightly coupled with prompt engineering and are not tracked as standalone T1–T9 tasks. They have not been completed and remain follow-up work; the shipped status of T1–T8 refers only to the scraping-tool changes listed below.

______________________________________________________________________

## 3. Task List (T1–T9)

> Order: First unify “accessibility” (T3/T5), then shore up “reliability” (T2), then elevate “quality” (T4/T6/T7/T8), finally scale “throughput” (T1/T9).
> T1 and T2 may be merged into one commit (shared client + manual redirect loop are naturally cohesive).

### T1 \[C1\] scrape_url shared httpx AsyncClient + phased timing metrics ✅

- Goal: Reuse the connection pool and expose request / parse / extraction timing metrics
- Design:
  - Module-level `_SCRAPE_CLIENT: httpx.AsyncClient | None`, lazy-initialized on first call
  - Close on process exit through `atexit`
  - Record `t_request_ms / t_parse_ms / t_extract_ms` per call, exposed in returned JSON `metrics` field
- Test:
  - Multiple consecutive calls instantiate only one client, verifying shared-client reuse
- Rollback: Removing `metrics` field has no LLM impact; reverting to per-call client restores prior behavior
- Risk: Module reload may break singleton client config and leak connections; requires graceful container shutdown coordination

### T2 \[D2/D3\] Manual redirect loop, per-hop SSRF validation + 5-hop cap ✅

- Goal: Block redirects to `169.254.169.254` / private networks / loopback addresses
- Design:
  - Set `follow_redirects=False`
  - While loop, max 5× 30x redirects, each validating `_is_private_or_loopback_host(redirect_target)`
  - Any hop flagged private → immediate failure with `error="redirect_blocked"`
- Test:
  - Mock server returning 302 → `169.254.169.254`: `scrape_url` rejects
  - Mock server returning 6× 302: hits cap and rejects
- Rollback: Re-enable `follow_redirects=True`
- Risk: Some sites rely on third-party CDN redirects; whitelist extension must be configurable

### T3 \[A2\] PDF content extraction (pdfminer) + 20MB response cap

- Goal: Enable text extraction from government announcements, whitepapers, and other PDFs
- Design:
  - Extend content-type allowlist to include `application/pdf`
  - Stream-read response; abort if exceeds `SCRAPE_MAX_BODY_BYTES` (default 20MB)
  - Extract text with `pdfminer.six`
  - Text normalization: strip `\f` and join hyphenated line breaks
  - Add return fields: `content_kind="pdf"`, `pages`, `bytes_read`
- Test:
  - Real PDF (e.g., Shenzhen Statistics Bureau bulletin sample): extracted text contains key statistical definitions
  - Oversized PDF (>20MB): correctly rejected
- Rollback: Remove `application/pdf` from allowlist → HTML-only mode
- Risk: `pdfminer` fails on scanned PDFs (no OCR); annotate `text_quality="empty"` to guide LLM user feedback

### T4 \[B2\] Chinese encoding fallback (charset_normalizer / meta charset estimation) ✅

- Goal: Fix garbled Chinese on sites serving `text/html; charset=GBK` under httpx’s default UTF-8
- Design:
  - Use `response.content` (bytes), prioritize charset from HTTP header
  - If absent, parse `<meta charset=...>` or `<meta http-equiv="Content-Type">` from HTML head
  - If neither present, apply `charset_normalizer.from_bytes(...).best()`
  - For `application/json` / RSS, retain UTF-8 / XML declaration logic
- Test:
  - A GBK charset in the response header decodes Chinese text correctly
  - When the response header has no charset, `<meta charset>` provides the fallback
- Rollback: Direct `response.text` usage restores prior behavior
- Risk: Rare binary HTML (with BOM) requires special handling

### T5 \[A3\] Direct pass-through & structured return for JSON / RSS / Atom / XML

- Goal: Surface structured entries (title/link/publish date) to LLM
- Design:
  - Extend content-type allowlist to `application/json`, `application/rss+xml`, `application/atom+xml`, `application/xml`, `text/xml`, `text/json`
  - JSON: retain first N KB raw string + `json.loads` + schema sniffing (top-level array/object)
  - RSS / Atom: parse with `xml.etree.ElementTree`, return `feed_title`, `entries: [{title,link,published,summary}]`, max 50 entries
  - Add output field `content_kind in {"html","pdf","json","rss","atom","xml","text"}`
- Test:
  - GitHub `releases.atom` → ≥5 entries returned
  - Government RSS feed → critical fields populated
  - JSON endpoints (e.g., NWS / open data portals) → raw payload + parsed summary coexist
- Rollback: Remove new MIME types → fall back to legacy path
- Risk: malformed XML may fail `ElementTree` parsing; decode the response through the T4 path before parsing

### T6 \[B1\] Introduce `trafilatura` primary path + bs4 fallback ✅

- Goal: Significantly improve main-content recall (`trafilatura` outperforms bs4 selectors on Chinese news/government sites across multi-source testing)
- Design:
  - `from trafilatura import extract`, params: `output_format="markdown"`, `include_tables=True`, `include_comments=False`, `favor_recall=True`
  - Fallback to existing `_extract_main_text` if `trafilatura.extract()` returns empty
  - Declare `trafilatura` explicitly in `libs/miroflow-tools/pyproject.toml`, propagated to api/worker/demo images via `uv sync`
- Test:
  - Local fixtures (`tests/fixtures/news_*.html`) compare recall length; `trafilatura` must not meaningfully underperform bs4
- Rollback: Feature flag `SCRAPE_USE_TRAFILATURA=false` reverts to original path
- Risk: `trafilatura` increases image size (~10MB due to `lxml`), must be noted in image optimization checklist

### T7 \[B3\] HTML table → markdown conversion, preserving column alignment ✅

- Goal: Retain column alignment for regulatory annexes, statistical tables, comparison tables
- Design:
  - In bs4 fallback path: invoke `_table_to_markdown(table)` on `<table>` nodes, mapping `<td>` to markdown columns
  - Use the first extracted row as the Markdown header and pad shorter rows to the detected column count
  - In `trafilatura` path: enable `include_tables=True` → native markdown table output
- Test:
  - 3-column × 5-row table → markdown output preserves column count and names
  - Nested tables → no crash; flattened to outer-level structure
- Rollback: Skip table conversion → plain-text only
- Risk: Complex tables, including merged cells and cross-page tables, may lose layout fidelity

### T8 \[B4\] Truncation at sentence/period/paragraph boundaries instead of hard character cut ✅

- Goal: Prevent LLM context fragmentation during citation
- Design:
  - When reaching `cap_chars`, backtrack to nearest `\n\n`, `。`, `.`, `!`, `?`, `！`, `？` — select farthest valid boundary among three categories
  - Attach metadata: `truncation: {strategy: "soft_boundary", original_chars: N, returned_chars: M}`
- Test:
  - Truncated long text ends precisely at punctuation or paragraph boundary
  - Truncation never expands content (only shortens)
- Rollback: Restore `text[:cap_chars]`
- Risk: Extremely long punctuation-free text (e.g., scraped English wall-of-text) falls back to hard cut; requires fallback path

### T9 \[C2\] Add `scrape_urls(urls,...)` batch concurrency tool

- Goal: Enable LLM to concurrently scrape multiple URLs in one round — reduce RTT for “3 candidate sources” from serial 75s → parallel 25s
- Design:
  - New tool `scrape_urls(urls: list[str], max_chars=8000, concurrency=4)`, max 8 concurrent requests
  - Internally use `asyncio.Semaphore(concurrency)`; reuse T1’s shared client
  - Individual failures do not block entire batch; return `[{url, success, ...}, ...]`
  - Overall timeout: `SCRAPE_BATCH_TIMEOUT_SECONDS` (default 60s)
- Test:
  - 4 URLs, 1 returns 5xx → remaining 3 still yield content
  - > 8 URLs → return first 8 results
- Rollback: Comment out `@mcp.tool()` decorator to disable
- Risk: Batch scraping amplifies IP blocking risk; robots.txt check or backoff must follow in v0.3

______________________________________________________________________

## 4. Iteration Cadence & Version Mapping

| Version | Tasks | Objective | Status |
|---------|-------|-----------|--------|
| **v0.2.2** (shipped) | Minimal `scrape_url` + SSRF guard + unit tests | Enable LLM to “open HTML full content” | ✅ shipped |
| **v0.2.3** (shipped) | T2 + T1 + T4 | Security closure + encoding robustness + shared client + metrics | ✅ shipped |
| **v0.2.4** (shipped) | T3 + T5 | Connect PDF / JSON / RSS / Atom / XML ingestion paths | ✅ shipped |
| **v0.2.5** (shipped) | T6 + T7 + T8 | Elevate content quality & table fidelity; integrate `trafilatura` | ✅ shipped |
| **v0.3.0** | T9 + quota rate limiting + robots.txt validation | Batch scraping & external site friendliness | ⏳ |

> This cadence aligns with but does not conflict with `docs/ROADMAP.md`’s “v0.2.5 (quality enhancement + observability)”; evaluation frameworks and observability belong to the main ROADMAP track, while this plan focuses exclusively on scraping tool capabilities.

______________________________________________________________________

## 5. Acceptance Pathway

Each task delivery must pass the following baseline:

1. **Unit Tests**: `uv run pytest libs/miroflow-tools/src/test/test_search_and_scrape_webpage_guards.py -v`
1. **Integration Validation**: Re-run the following queries in demo / api-server; final Markdown *must* contain **verbatim excerpts from `scrape_url`** — not just `google_search` snippets:
   - Regulatory: “Which regulations were applied in the Shenzhen bus smoking incident? Provide the exact regulatory text.”
   - Statistical: “What is the 2024 permanent resident population of Shenzhen, and where is that figure published?”
   - Announcement: “List compliance penalty cases issued by the State Administration for Market Regulation in January 2026.”
1. **Regression Gate**: Add `scrape_url` mock test cases to CI `run-tests.yml` to prohibit SSRF redirect bypass and oversized body acceptance.
1. **Observability**: After each version’s canary rollout, verify `scrape_url` appears in `stage_heartbeat`; aggregate hit rate / avg latency from worker logs.

______________________________________________________________________

## 6. Rollback Plan

| Risk Point | Rollback Strategy |
|------------|-------------------|
| Singleton client connection leak | Revert the shared-client implementation to construct and close a client per call |
| `trafilatura` extraction regression | Environment variable `SCRAPE_USE_TRAFILATURA=false` → revert to bs4 path |
| `pdfminer` timeout / memory explosion | Environment variable `SCRAPE_ENABLE_PDF=false` → disable PDF pipeline |
| Batch scraping triggers IP ban (T9 pending) | After T9 is implemented, remove the `scrape_urls` tool registration to take it offline |

______________________________________________________________________

## 7. Acceptance Demo Queries (Reference Table)

| Query | Expected Tool Call Sequence | Expected Final Output Characteristics |
|-------|------------------------------|----------------------------------------|
| “Which regulations were applied in the Shenzhen bus smoking incident?” | `google_search` → `scrape_url(regulatory text URL)` | Report includes full clause text e.g., “Article N of the XX Regulation …”, not just keywords |
| “What is the 2024 permanent resident population of Shenzhen?” | `google_search` → `scrape_url(statistical bulletin PDF)` | Report cites exact number + bulletin title, tagged with PDF provenance |
| “Latest release notes for GitHub kubernetes/kubernetes” | `google_search` → `scrape_url(.../releases.atom)` | Report lists top 5 releases in `entries` format |
| “OpenAI Q1 2026 financial report” (planned T9 demo) | `google_search` → `scrape_urls([official site, SEC filing, news])` | After T9 ships, report cross-references ≥3 sources, citing real URLs via `[N]` |

______________________________________________________________________

## 8. Deployment Notes

- All changes strictly target `dev_mcp_servers`; production MCP protocol fields remain untouched.
- Pending E-class follow-up: update `apps/miroflow-agent/src/prompts/` so long-form scenarios found through `google_search` require at least one `scrape_url` call. This prompt behavior is not part of the shipped T1–T8 tool changes.
- Image layer packaging prioritizes `--no-cache-dir` and BuildKit cache mounts to prevent bloat.
- No external API (FastAPI, Gradio, Skill package) exposes `scrape_url` directly: it remains an internal LLM agent tool; external contracts stay stable.
