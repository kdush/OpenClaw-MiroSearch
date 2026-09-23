[English](CHANGELOG.md) | [中文](CHANGELOG_zh.md)

# Changelog

This file documents all significant changes to this project.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/).

## \[Unreleased\]

### Fixed

- **Summary failures no longer drop the report**: when the final summarization model returns an unusable result (timeout, rate limit, content filtering, …) and no further retry remains (product profiles with `retry_with_summary=false`, or the final evaluation round), the pipeline now delivers a degraded report assembled deterministically from the evidence gathered during research, marking the conclusion section in bold as product copy 「系统说明」, instead of failing the whole task with `Final summary produced no usable answer.`. Quality metadata gains a `degraded_report_fallback` issue, and such results stay out of the shared cache so a one-off summarization hiccup is not frozen into a long-lived result.
- **Placeholder conclusion blocks are no longer rendered**: when `\boxed{}` is missing, the placeholder string is no longer pushed to the frontend as a body block (it would be rewritten into a 「未收敛」 hint contradicting the delivered report); the conclusion shown to users always comes from the conclusion section of the report body.

### Changed

- **Brand rename**: the project is renamed to 谛听 / Diting, updating the header logo and favicon, page titles, skill package and script names, Docker image names, SearXNG instance name, export filename prefix (`diting-conclusion.*`) and documentation wording; upstream MiroThinker / MiroFlow attribution and historical records are unchanged.
- **Report layout**: final reports drop token/billing noise and truncated URLs; pending leads become a 「未跟进」 summary; the detailed level may append a content analysis and a Mermaid relationship map (see `docs/REPORT_LAYOUT.md`).
- **Result area visual tightening**: on wide screens (≥1200px) the result area widens to 960px while body paragraphs keep a 42em measure; the static 「研究进度」 heading in the result area and Gradio's default streaming progress bar are removed, leaving the status line in the body as the single in-progress indicator.
- **Unified progress status wording**: running status lines converge on one colloquial vocabulary (「准备中 / 正在分析 / 正在检索 / 正在调用工具 / 正在交叉校验 / 正在生成报告」), dropping duplicated or internal phrasings such as 「研究进行中」 and 「第 N 回合」 and rephrasing search rounds as 「已完成 N 次检索」; tool names use Chinese display names and `scrape_url` is added, so status lines and tool cards no longer expose English tool ids; the first empty frame reads 「研究已启动…」 instead of duplicating the status line; terminal states gain visible copy — 「已停止」 after a stop and 「研究中断：原因」 on failure (deduplicated against the error event) — and completion or a cache hit no longer leaves a running state behind.
- **Elapsed time on the status line**: 「已用 m:ss」 is shown at the right of the status line, incremented client-side every second (without depending on server frames); after a stop it freezes at the moment of the stop instead of counting on.
- **Deep-space theme and search row**: the page background becomes a near-black void (`#000208`) with localized nebulae, overlaid with a parallax starfield and a meteor canvas; the search box becomes a translucent glass capsule (max width 584px), and 「开始研究 / 停止 / 设置」 collapse into three 36px round ghost icons inside the input (their labels kept in the DOM as tooltips and accessible names), with start and stop mutually exclusive per run state (magnifier ↔ stop square) and no wrapping on narrow screens. The settings modal loses its double box, its input surfaces switch to a layer lighter than the card with a light outline, and field descriptions are raised to a 12px floor.

### Compatibility

- **Skill package rename**: `skills/openclaw-mirosearch/` is renamed to `skills/diting/`, its invocation script to `scripts/call_diting.py`, and its packaged file to `skills/diting.zip` (archive root `diting/`); callers that installed the old skill must reinstall it from the new path.
- **Image name change**: the default image names in Compose and `scripts/deploy/build_images.sh` become `diting:latest` and `diting-api:latest`; existing deployments must rebuild or explicitly override `IMAGE_TAG_*`.

## \[0.2.11\] - 2026-08-13

### Changed

- **Unified Pipeline Result Protocol**: CLI, benchmark, Gradio, and Worker now uniformly read task results by mapping fields, explicitly distinguishing `completed`, `failed`, and `cancelled`, and consistently propagating `result_quality`.
- **Unified API Valid Parameters**: Request defaults are parsed only once; cache keys, task metadata, queue payloads, and Worker overrides all use the same set of valid parameters; hard budget in research mode takes precedence over output length strategy.
- **Unified LLM Multi-Stage Routing**: OpenAI and Anthropic share the same semantic model definitions and token limits for `main`, `tool`, `thinking`, `fast`, and `summary` stages.

### Fixed

- **Summary Evidence and Quality Judgment**: Only explicitly marked tool results are trimmed; final summary instructions and the latest evidence are preserved; empty summaries no longer masquerade as successful; unformatted main content lacking a closing `\\boxed{}` is downgraded per formatting rules and returned.
- **Gradio Backend and Caller Isolation**: Public entry points with `BACKEND_MODE=api` now route through the API Server; cache keys incorporate retrieval depth parameters; cancellations execute directionally by `caller_id`, preventing cross-caller accidental cancellation.
- **Task Resource Isolation and Cleanup**: Local tasks no longer reuse stateful `ToolManager`; idempotent resource cleanup is performed uniformly across success, failure, cancellation, and initialization exception paths.
- **Search Key Rotation**: SerpAPI, Serper, and Tavily now uniformly support multi-Key rotation, 429 cooldown handling, and bounded retries, preventing single-Key rate limiting from degrading the entire retrieval pipeline.
- **Secure Defaults**: APIs continue to fail closed when no Token is configured; test, Compose examples, and deployment documentation explicitly select either development bypass or production Token mode.

### Compatibility

- **Gradio Public Interface Migration**: The 7th array item of HTTP `run_research_once` is now fixed as `caller_id`; legacy 6-item calls must append a stable caller identifier or an empty string.
- **Tightened Cancellation Interface**: Legacy `stop_current` with an empty array for global cancellation is no longer supported; callers must supply the `caller_id` used to create the task, or use FastAPI’s `task_id`-based cancellation endpoint.

### Testing

- **Local Full Regression Passed**:
  - `apps/api-server`: `186 passed, 13 skipped`
  - `apps/gradio-demo`: `108 passed`
  - `apps/miroflow-agent`: `157 passed, 7 skipped`
  - `libs/miroflow-tools`: `106 passed`
- **Static Checks Passed**: All 61 Python files modified in this change passed Ruff check and format check; `git diff --check` passed.

## \[0.2.10\] - 2026-05-24

### Added

- **Gradio Research Conclusion Export MVP**: A new export format selector, export button, and download file component were added to the result area, supporting Markdown (`.md`), PDF (`.pdf`), and Word (`.docx`) formats; exported filenames include a random suffix to prevent overwrite collisions during concurrent exports within the same second.
- **Export Functionality Regression Tests**: New tests cover Markdown / PDF / Word file generation, visibility of the export button and downloadable file, friendly error returns on export failure, and uniqueness of filenames for concurrent exports within the same second.

### Changed

- **Default Model Switched to Qwen Series**: Gradio’s default model changed to `qwen/qwen3.6-plus`; fast / summary models default to `qwen/qwen3.6-35b-a3b`; `.env.example` updated to reflect OpenRouter + Qwen example configuration.
- **Gradio Intermediate Process Display Optimization**: Non-final-summary `message` / `show_text` content is now displayed as collapsible thinking cards, reducing visual noise in the main content area upon refresh and replay.

### Fixed

- **OpenAI / OpenRouter Compatible Model Empty Response Handling**: `OpenAIClient` falls back to reading `message.reasoning` / `message.reasoning_content` when `message.content` is empty, accommodating reasoning-only responses from Qwen / DeepSeek; reasoning tokens are disabled for summary / fast model requests on OpenRouter to reduce risk of empty final summary content.
- **Search Failure Returns Structured Error Explicitly**: `google_search` returns `success=false`, `error`, empty `organic/results`, and search source exception details when all search sources fail or return empty results, preventing upper layers from misinterpreting empty results as success.
- **Gradio Search Failure Display**: Search result rendering now displays “Retrieval Failed”, error details, search source exceptions, and trace links, improving observability for troubleshooting.
- **Export Failure No Longer Throws Frontend Traceback**: On export file write failure, a hidden download component is returned alongside a “Export Failed” label, while a warning log is written.

### Deployment

- **Deployed to `tower`**: Source code synchronized to `tower:/mnt/user/appdata/openclaw-mirosearch`; `openclaw-mirosearch-api:latest` and `openclaw-mirosearch:latest` rebuilt; `api`, `worker`, and `app` services rolled-restarted.
- **`searxng` Service Restored**: The missing `openclaw-mirosearch-searxng-1` container was restarted on `tower`, and health checks confirmed success.

### Testing

- **Local Regression Passed**:
  - `apps/gradio-demo`: `31 passed`
  - `libs/miroflow-tools`: `28 passed`
  - `apps/miroflow-agent`: `15 passed`
  - Target file ruff check: passed
- **Remote Deployment Validation Passed**: On `tower`, `app`, `api`, `worker`, `searxng`, and `valkey` all reported `healthy`; API `/health` returned `{"status":"ok","version":"0.1.0"}`; Gradio `/gradio_api/info` included `/run_research_stream`; SearXNG `/healthz` returned `OK`.

## \[0.2.9\] - 2026-05-24

### Fixed

- **SSE Stream Sends `done` Immediately After `final_output`**: Fixed prior SSE generator behavior that unnecessarily blocked one additional Redis read after the final event batch arrived before sending `done`; clients now receive `done` immediately after `final_output`, with no extra delay.
- **`TaskEventSink` Automatically Advances Task Status to `COMPLETED` Upon Receiving `final_output`**: Task result and completion status are now atomically written within the same event processing frame, eliminating inconsistency between status and result.

### Added

- **Orchestrator Adds `_emit_final_output` Method**: After final summary generation, `stream.update` emits a `final_output` event carrying structured result `{"markdown": ...}`.
- **`compose.yaml` / `compose.host-network.yaml` Add `BACKEND_MODE=api`**: The app service explicitly declares backend mode, avoiding accidental use of embedded mode at container startup.

### Testing

- Added `test_sse_stream.py`: Verifies `done` sent immediately after final event batch, and race condition handling (`final_output` arrives before status update).
- Added `test_task_event_sink.py`: Verifies `final_output` event stores result and advances status to `COMPLETED`.
- Added `test_orchestrator_final_output.py`: Verifies `_emit_final_output` correctly emits `markdown` field.
- Added `test_compose_config.py`: Verifies both compose files contain `BACKEND_MODE: api`.

## \[0.2.8\] - 2026-05-08

### Fixed

- **Gradio Page Auto-Reconnects Task on Refresh via `?task_id=`**: `demo.load` now bridges the browser URL’s `task_id` to the backend via frontend values; `reconnect_or_init` simultaneously supports legacy request parameter invocation, preventing fallback to idle state after refresh.
- **OpenClaw-MiroSearch Invocation Script Downgrades Retry to Avoid Back-and-Forth Loops**: Added monotonic downgrade step calculation; when convergence is not achieved, the script no longer recursively oscillates between adjacent strategies.

### Changed

- **Invocation Script Prioritizes FastAPI SSE for Progress Tracking**: `call_openclaw_mirosearch.py` prioritizes listening to `/v1/research/{task_id}/stream`, falling back to polling if unavailable, and automatically downgrades and retries upon detecting non-convergent results.
- **Skill Documentation Augmented with Script Retry Behavior**: Clarifies SSE progress display, non-convergent result determination, and downgrade retry order.

### Testing

- **Local Regression Passed**:
  - `apps/gradio-demo`: `54 passed`
  - `apps/gradio-demo` ruff changed-file check: passed
  - `call_openclaw_mirosearch.py` / script test file py_compile: passed
- **Remote Legacy Container Validation Passed**: Synced to `tower:/root/openclaw-mirosearch` and patched `openclaw-mirosearch-app-1` in-place; Gradio `28080/gradio_api/info` and API `8090/health` both returned `200`; app container status was `running healthy`.

## \[0.2.7\] - 2026-05-02

### Added

- **New Standalone `searxng` Skill**: Provides a lightweight search entry point suitable for cost-sensitive invocation scenarios, including `agents/openai.yaml`, `scripts/searxng.py`, and `SKILL.md`.
- **New Aggregated Skill Bundle `openclaw-search-skills-bundle.zip`**: Bundles multiple search-related skills.
- **New `openclaw-mirosearch/agents/openai.yaml`**: Adapts to OpenClaw Agent skill invocation conventions.

### Changed

- **README Switched from Gradio API Examples to FastAPI REST API Examples**: Added `caller_id` parameter explanation, task cancellation method, `/health` health check, full `/v1/research` path, and SSE streaming documentation.
- **`SKILL.md` Refactored into Core Convention Format**: Emphasizes FastAPI closure as the recommended approach and simplifies v0.2.2 legacy change notes.

## \[0.2.6\] - 2026-05-02

### Fixed

- **Prevent `tool_definitions` in Failure Summary and Summary Stages**: Failure summary stage does not require tool invocation; summary stage prohibits tool invocation to avoid 404 errors from gateways like OpenRouter whose models do not support tool use.
- **Fallback to Full Answer Text When `\boxed{}` Not Found**: Ensures compatibility with models like qwen3.6 that omit `\boxed{}` formatting, preventing unnecessary retries or "not converged" warnings triggered by `FORMAT_ERROR_MESSAGE`.

## \[0.2.5\] - 2026-04-27

> This release delivers T6 + T7 + T8 from [`docs/SCRAPING_ITERATION_PLAN.md`](./SCRAPING_ITERATION_PLAN.md):
> `trafilatura` primary path + `bs4` fallback, HTML table-to-markdown conversion, and intelligent sentence / paragraph boundary truncation.

### Added

- **T6 \[B1\] `scrape_url` Introduces `trafilatura` Primary Path**: HTML body extraction prioritizes `trafilatura.extract(output_format="markdown", include_tables=True, include_comments=False, favor_recall=True)`; falls back to existing `bs4` path if empty or unavailable; new `SCRAPE_USE_TRAFILATURA` toggle enables quick rollback.
- **T7 \[B3\] HTML Table-to-Markdown Conversion**: The `bs4` fallback path converts `<table>` elements to Markdown tables, preserving row/column structure and preventing loss of columns in statistical tables or regulatory annexes during plain-text extraction.
- **T8 \[B4\] Intelligent Sentence / Paragraph Boundary Truncation**: Overlong bodies no longer use hard `text[:cap_chars]` slicing; instead, they preferentially truncate at paragraph breaks, line feeds, or Chinese/English sentence-ending punctuation; returns new `truncation` metadata.
- **Three New `scrape_url` Unit Tests**: Cover `trafilatura` primary path parameters, fallback table Markdown fidelity, and soft-boundary truncation metadata.

### Changed

- **Explicit `trafilatura` Dependency**: `libs/miroflow-tools` runtime dependency now includes `trafilatura`, propagated by `uv sync` to api / worker / demo runtime environments.
- **Truncation Length Semantics Adjusted**: `content_length` may be less than `max_chars` during truncation to ensure content ends at natural boundaries.

### Testing

- **Local Regression Passed**:
  - `libs/miroflow-tools`: `27 passed`
  - `apps/api-server`: `94 passed, 13 skipped`
  - `apps/gradio-demo`: `39 passed`
  - `apps/miroflow-agent`: `39 passed, 7 skipped`
- **Local Docker End-to-End Validation Passed**: Using `COMPOSE_ENV_FILE=.env.compose.local-e2e`, `app + api + worker` rebuilt; real task SSE `tool_call` confirmed hitting `scrape_url`, successfully generating a complete answer from `https://www.iana.org/about`.

## \[0.2.4\] - 2026-04-27

### Added

- **T3 \[A1/A2\] `scrape_url` Supports PDF Extraction and Response Body Size Limit**: Added `SCRAPE_MAX_BODY_BYTES` (default 20MB) and switched to streaming response body reads; `application/pdf` now returns `content_kind="pdf"`, `pages`, `bytes_read`, `text_quality`, enabling direct scraping of statistical bulletins, regulatory PDFs, and announcement attachments.
- **T5 \[A3\] `scrape_url` Supports JSON / RSS / Atom / XML Structured Passthrough**: Added whitelist for `application/json`, `text/json`, `application/rss+xml`, `application/atom+xml`, `application/xml`, `text/xml`; returns structured fields like `json_type` / `json_keys`, `feed_title` / `entries`, `xml_root`, facilitating direct LLM consumption of API / Feed data.
- **XML Declaration Encoding Detection**: Added XML declaration encoding detection beyond header / meta / `charset_normalizer`, reducing garbled text in XML / Feed scraping.
- **Explicit `pdfminer-six` Dependency**: `libs/miroflow-tools` and dependent applications’ lockfiles now explicitly declare `pdfminer-six`, preventing future dependency tree changes from causing runtime PDF extraction failures.
- **Four New `scrape_url` Unit Tests**: Cover PDF body extraction, oversized response rejection, JSON structured return, and RSS structured return; also updated legacy PDF content-type rejection test case to “bad PDF parsing failure” semantics.

### Changed

- **Manual Redirect Chain Switched to Streaming Response**: `_fetch_with_manual_redirects` now uses `stream=True` and promptly `aclose()` on intermediate 30x hops, reducing connection leak risk.
- **Body Normalization Path Unified**: HTML / text / PDF / Feed all reuse the same text normalization logic, reducing newline noise and extraction result jitter.

### Testing

- **Local Regression Passed**:
  - `libs/miroflow-tools`: `24 passed`
  - `apps/api-server`: `18 passed`
  - `apps/gradio-demo`: `20 passed`
  - `apps/miroflow-agent`: `16 passed`
- **Local Docker End-to-End Validation Passed**: With `app + api + worker + searxng + valkey` all `healthy`, IANA official site responsibilities summary sample completed a real task; final state `completed`, `search_rounds=1`, total task duration ~`49.1s`, `timeout_count=0`, `rate_limit_429_count=0`.

## \[0.2.3\] - 2026-04-27

### Added

- **T1 \[C1\] `scrape_url` Shares `httpx.AsyncClient` + Per-Stage Timing Metrics**: Module-level `_SCRAPE_CLIENT` lazily initialized and closed via `atexit`; returned JSON adds `metrics: {t_request_ms, t_parse_ms, t_extract_ms, redirect_hops}`, enabling TCP/TLS reuse across multiple URL scrapes in one LLM research round, drastically lowering tail latency.
- **T2 \[D2/D3\] Manual Redirect Loop + Per-Hop SSRF Check + Max 5 Hops**: Default `follow_redirects=False`, with custom 30x following; each hop revalidates scheme + `_is_private_or_loopback_host`; redirects to private networks or exceeding `SCRAPE_MAX_REDIRECT_HOPS` (default 5) immediately return `error="redirect_blocked: ..."`, retaining `redirect_chain` field.
- **T4 \[B2\] Chinese Encoding Fallback (header → meta → charset_normalizer)**: Byte-path decoding follows four-tier fallback: Content-Type charset → `<meta charset>` / `<meta http-equiv>` → `charset_normalizer.from_bytes(...).best()` → utf-8(replace); adds `encoding` field to fix GBK / GB18030 garbling on government sites.
- **Proxy/TUN fake-ip DNS Compatibility Toggle**: New `SCRAPE_PROXY_FAKE_IP_CIDRS` allows explicit domain resolution to trusted fake-ip subnets (e.g., `198.18.0.0/15`); applies only to domain resolution results, not IP literals, which remain subject to SSRF blocking.
- Added 11 unit tests covering: metrics field / redirect chain following / redirect private network rejection / >5-hop rejection / GBK header decoding / meta charset fallback / charset_normalizer fallback / shared client reuse / fake-ip DNS default rejection / fake-ip explicit allowance / IP literal rejection.

## \[0.2.2\] - 2026-04-26

### Added

- **MCP Tool `scrape_url` Prototype** (libs/miroflow-tools): Built on `httpx + BeautifulSoup`, enabling LLMs to actively “open pages to view full content” when `google_search` snippets are insufficient.
  - Supports only http(s) absolute URLs; content-type whitelist: `text/html` / `application/xhtml+xml` / `text/plain`.
  - SSRF protection: rejects loopback / private / link-local / multicast / reserved hosts.
  - Configurable timeout (default 25s), `max_chars` truncation (default 10000, hard cap 30000), and User-Agent.
  - Prioritizes `article` / `main` / `[role=main]` / `#content` containers for body extraction; falls back to full text.
  - Five unit tests cover: non-http scheme, empty URL, private network SSRF, HTML body extraction, non-HTML content-type rejection.
  - Registered in `apps/miroflow-agent/src/utils/parsing_utils.py`’s `TARGET_TOOLS` set, auto-correcting LLM misspellings of server names.
  - Detailed follow-up iteration plan in [`docs/SCRAPING_ITERATION_PLAN.md`](./SCRAPING_ITERATION_PLAN.md) (T1–T9).
- **Stage Heartbeat Mirrored to stderr**: `Orchestrator._emit_stage_heartbeat` and `AnswerGenerator._emit_stage_heartbeat` add deduplicated named events + `logger.info` to stderr, enabling direct observation of long-task progress via `docker logs`.

### Fixed

- **Critical API Mode Regression: Worker Completely Ignores Retrieval Parameters Submitted by Demo** (apps/api-server)
  - Symptom: Users selecting `verified + parallel-trusted + 20 results + detailed` in demo, then switching to `BACKEND_MODE=api`, actually run hardcoded `agent=demo_search_only`, causing queries that previously produced research summaries to fall back to “not converged” placeholder text.
  - Root Cause: `apps/api-server/services/pipeline_runtime.py`’s `build_config_overrides` determined config solely from process env vars like `DEFAULT_LLM_PROVIDER` / `AGENT_CONFIG`, discarding five `RequestLike` fields: `mode`, `search_profile`, `search_result_num`, `verification_min_search_rounds`, `output_detail_level`.
  - Fix: Added `apps/api-server/services/profile_resolver.py`, aligning strategy with gradio-demo’s `_ensure_preloaded`.
    - Reuses same `SEARCH_PROFILE_ENV_MAP` (searxng-first / serp-first / multi-route / parallel / parallel-trusted / searxng-only).
    - Reuses same mode → hydra overrides mapping (production-web / verified / research / balanced / quota / thinking), with all tunable constants read at runtime via `os.getenv`.
    - Reuses same `output_detail_level` → max_turns / keep_tool_result / max_tokens mapping.
  - `pipeline_runtime` refactored:
    - `build_config_overrides` now returns `(search_env, hydra_overrides)` tuple.
    - `create_runtime_components` creates components within `_temporary_env_vars` context, ensuring retrieval MCP subprocesses inherit correct `SEARCH_PROVIDER_*` configs from process env.
    - Added `asyncio.Lock` to serialize component creation, preventing worker multi-task concurrency from overwriting process-level env.
- **Added 55 Unit Tests**:
  - 47 `test_profile_resolver.py`: Full branch coverage of `normalize_*` / `build_search_env` / `build_mode_overrides` / `build_full_overrides`.
  - 8 `test_pipeline_runtime_overrides.py`: Verify `RequestLike` five fields are correctly passed, and base LLM overrides vs mode_overrides ordering is correct.
- **Worker Cancel Path Robustness Fix** (apps/api-server/workers/research_worker.py)
  - `check_cancel` coroutine adds startup INFO log (confirming watcher launch); single Redis read exceptions emit only warning and continue polling, avoiding silent exit and permanent loss of cancel signals.
  - `pending` task cleanup path now uses `asyncio.wait_for(timeout=10s)`: when downstream code swallows `CancelledError` (e.g., `pipeline.py` except branches), worker no longer hangs; it abandons after max 10s and proceeds to return `cancelled` status.
  - Added 2 tests: `test_cancel_watcher_survives_redis_errors` (watcher survives Redis flakiness), `test_cancel_path_with_unresponsive_pipeline` (unresponsive pipeline abandoned after timeout window).
- **Dockerfile Defaults to Domestic apt Mirror** (apps/api-server, apps/gradio-demo)
  - Added `APT_MIRROR` build-arg, default `mirrors.tuna.tsinghua.edu.cn`, sed-replacing `deb.debian.org` and `security.debian.org` in `/etc/apt/sources.list*`.
  - Compatible with deb822 (trixie+ `/etc/apt/sources.list.d/debian.sources`) and legacy `sources.list`.
  - To restore official sources: `docker compose build --build-arg APT_MIRROR= api worker`.
- **Compose File Build Section Adds `network: host`** (compose.yaml, compose.host-network.yaml)
  - `build.network: host` for `app`/`api`/`worker` services, allowing build phase to use host network directly.
  - Solves issues where docker0 bridge cannot access external apt/pip repos in environments like `tower` (host can connect but build container cannot).
- **New Build Script** `scripts/deploy/build_images.sh`
  - Directly invokes `docker build --network=host -f ... -t ...`, bypassing `docker compose build` requiring interactive authorization for `network.host` entitlement under BuildKit (fails in non-TTY SSH contexts).
  - Supports environment variable overrides: `APT_MIRROR` / `PIP_INDEX_URL` / `IMAGE_TAG_API` / `IMAGE_TAG_DEMO`.
  - Usage: `scripts/deploy/build_images.sh [api|demo|all]`.

### Added

- **Demo Power-Cycle Reconnection (gradio-demo)**: Research tasks can resume full progress after browser refresh or network interruption via URL `?task_id=xxx`, without losing intermediate results.
  - Added `BACKEND_MODE=api` backend mode; when enabled, demo no longer executes pipeline in its own process but submits each search as a task to `api-server`.
  - Added `apps/gradio-demo/api_client.py`: Lightweight HTTP/SSE client built on `aiohttp`, wrapping `create_task` / `get_task` / `cancel_task` / `stream_task_events` endpoints; hand-rolled SSE parser avoids new dependencies.
  - After task creation, server `task_id` is synced to `?task_id=xxx` via CSS-hidden `<textarea id="gr-task-id-bridge">` + `MutationObserver` (`history.replaceState`, no page navigation).
  - `demo.load(reconnect_or_init)` automatically takes over from URL `query_params["task_id"]`: all non-empty task states are rebuilt via the SSE UI; `api-server` replays historical events from the start of the Redis Stream and blocks while waiting for new events; rendering after a refresh exactly matches the live viewing experience.
  - “Stop” button in API mode additionally calls `POST /v1/research/{task_id}/cancel`, triggering collaborative worker termination.
  - Retains `BACKEND_MODE=local` default and original in-process execution path for backward compatibility.
- **Non-Convergent Task Message Localization**: Detects pipeline fallback messages (`No \boxed{} content found in the final answer.` / `Task incomplete - reached maximum turns ...`) and rewrites them in the research summary area as “This round of retrieval failed to converge on a credible conclusion within the allowed number of rounds... Suggest downgrading mode or retrying”, improving readability and preventing user misinterpretation of demo failure.
- **21 New Unit Tests**:
  - 16 `test_api_client.py`: BACKEND_MODE switching / SSE chunk parsing (default event name, multi-line data, comments, id/retry, non-JSON fallback) / 4 endpoints + SSE stream end-to-end (using local aiohttp test server).
  - 4 `test_render_markdown.py`: Coverage of `_humanize_pipeline_fallback` and `_build_summary_section` fallback message rewriting.
  - 1 `test_render_markdown.py`: Confirms normal summary blocks are not erroneously modified.

### Changed

- `.env.compose.example` and `apps/gradio-demo/.env.example` added `BACKEND_MODE` / `API_BASE_URL` / `API_BEARER_TOKEN` configuration items with Chinese comments.

### Notes

- In Gradio 5, components with `visible=False` are excluded from DOM; thus `task_id_box` must be `visible=True` + CSS-shifted off-screen to be accessible to JS bridge. Related comments and CSS rules have been added.

## \[0.2.1\] - 2026-04-23

### Added

- **Clickable Research Report Citations** (gradio-demo): Numeric citations in research summary text like `[2]`, `[5]`, `[9]` are automatically converted to HTML anchor links pointing to corresponding real URLs in the report’s “References / 参考文献” section, opening original sources in new tabs.
  - Automatically recognizes various reference section titles (`**References**` / `## 参考文献` / `## References` / `## 引用` / `## Sources`, etc.).
  - Parses entries like `[N] Title. URL` to build id → url mapping.
  - Skips `[N]` inside fenced code blocks and inline code to avoid false positives.
  - Trims ASCII and common Chinese punctuation from URL tails automatically.
  - Reference section itself remains unchanged; internal `[N]` are not nested into links.
  - Three unit tests cover normal replacement, absence of reference section, and code block protection.

### Fixed

- **Compose Worker Startup Command**: `compose.yaml` `api-worker` service startup command changed to `.venv/bin/python worker.py`, avoiding incorrect Python interpreter selection when container `PATH` doesn’t point to uv-managed interpreter, ensuring arq Worker stability.

## \[0.2.0\] - 2026-04-20

### Added

- **Asynchronous Task Queue**: Implemented async research task scheduling using arq + Valkey.
  - `services/task_store.py`: Valkey-persisted task metadata, event stream, results, and cancellation flags.
  - `services/task_queue.py`: arq task enqueuing wrapper and connection pool management.
  - `services/task_event_sink.py`: Pipeline event → persistent event stream adapter.
  - `services/pipeline_runtime.py`: Hydra config factory managing Pipeline component lifecycle.
  - `workers/research_worker.py`: arq Worker consuming tasks and executing full Pipeline.
  - `worker.py`: Worker entry script with LLM config diagnostic logging.
  - `settings.py`: Pydantic unified config management (Valkey / queue / Worker / API).
- **SSE Streaming Endpoint Refactor**: Switched to `sse_starlette`, fixing `ServerSentEvent` serialization bug.
- **Task Status Query**: `GET /v1/research/{task_id}` returns full task metadata and event count.
- **Request Parameter Expansion**: `search_result_num`, `verification_min_search_rounds` now configurable by caller.
- **Docker Build Optimization**: BuildKit cache mounts, PyPI mirror acceleration.
- **Compose Orchestration**: Added worker / valkey service definitions, fixed `working_dir` path.
- **24 New Tests Added**:
  - `test_research_queue_api` (7): Task enqueue, cache hit, status query, cancellation.
  - `test_research_worker` (3): Worker success/cancellation/failure scenarios.
  - `test_sse_stream` (6): SSE full lifecycle, 404, incremental reads.
  - `test_task_store` (8): TaskStore integration tests.

### Changed

- `POST /v1/research` response changed from synchronous result return to async enqueue (returns `task_id` + `status: accepted`).
- Research endpoint changed from single-process blocking to Worker async execution, supporting concurrent multi-tasking.
- `deps.py` simplified: removed in-memory task management logic, replaced with TaskStore/TaskQueue service layer injection.
- `.env.compose.example` added Valkey, task queue, and Worker configuration items.

### Removed

- Removed redundant English documentation copies (ARCHITECTURE_en / CONTRIBUTING_en / SECURITY_en / CODE_OF_CONDUCT_en).
- Removed outdated documents: GOVERNANCE / RELEASE / QA / SUPPORT / LOCAL-TOOL-DEPLOYMENT / AI_AGENT_INTEGRATION.
- Removed completed plan documents.

## \[0.1.14\] - 2026-04-05

### Changed

- api-server pipeline preload logic rewritten: aligned with gradio-demo’s `load_miroflow_config` pattern, correctly handling Hydra global initialization state.
- api-server added to `compose.yaml`: `api` service listens on port 8090, running in parallel with `app` (Gradio).
- `_build_config_overrides` reads LLM config from environment variables, supporting `DEFAULT_LLM_PROVIDER` / `DEFAULT_MODEL_NAME` / `BASE_URL` / `API_KEY`.
- Sub-agent tool definitions exposed: `_ensure_pipeline_loaded` automatically calls `expose_sub_agents_as_tools`.

### Fixed

- api-server security audit fixes for 7 issues:
  - Fixed task management memory leak: `cleanup_stale_tasks` periodically cleans completed tasks; `finish_task` records `finished_at` timestamp.
  - Fixed exception information leakage: pipeline exceptions return generic error messages, hiding internal stack traces.
  - Replaced deprecated `asyncio.get_event_loop()` with `asyncio.get_running_loop()`.
  - Replaced deprecated `app.on_event` with FastAPI `lifespan` context manager, integrating periodic cleanup tasks.
  - Added `mode`, `search_profile`, `output_detail_level` enum validation to `ResearchRequest`.
  - Rate-limiting middleware 429 responses hide internal config (`RATE_LIMIT_RPM`), exporting `cleanup_rate_limit_buckets` for periodic cleanup.
- Dockerfile (gradio-demo / api-server) CMD added `--frozen`, fixing container startup failure when `uv run` attempts dependency download without external network.

## \[0.1.13\] - 2026-04-05

### Added

- api-server request rate-limiting middleware: in-memory sliding window counter (`SlidingWindowCounter`), rate-limited by IP or Bearer Token.
- Rate-limiting config: `RATE_LIMIT_ENABLED` (default enabled), `RATE_LIMIT_RPM` (default 30 RPM).
- Paths `/health`, `/docs`, etc. automatically bypass rate limiting.
- api-server Dockerfile: containerized config aligned with gradio-demo, HEALTHCHECK targeting `/health`.
- Six rate-limiting middleware regression tests (within quota pass, over-quota 429, bypass paths, disabled mode, independent key counting).
- `.env.example` added rate-limiting and caching config explanations.

## \[0.1.12\] - 2026-04-05

### Added

- New `ResultCache` class (`src/cache/result_cache.py`): In-memory LRU + TTL result cache; identical `query+mode+profile+detail_level` hits avoid repeated search quota and LLM token consumption.
- `gradio-demo` `run_research_once` integrated result cache: checks cache at entry, writes back on completion.
- `api-server` `POST /v1/research` integrated result cache: returns `status=cached` immediately on hit.
- Cache config controlled via env vars `RESULT_CACHE_MAX_SIZE` (default 128) and `RESULT_CACHE_TTL_SECONDS` (default 3600).
- Eleven `ResultCache` regression tests (LRU eviction, TTL expiry, key determinism, invalidate, clear).

## \[0.1.11\] - 2026-04-05

### Added

- New `apps/api-server/`: Standalone FastAPI-based HTTP API layer, decoupled from Gradio.
- `POST /v1/research`: Submit research task, return `task_id`.
- `GET /v1/research/{task_id}/stream`: SSE streaming for real-time task progress.
- `POST /v1/research/{task_id}/cancel`: Cancel specific task.
- `POST /v1/research/cancel`: Batch-cancel by `caller_id`.
- `GET /v1/metrics/last`: Reuse `RunMetrics`, return metrics from most recent task.
- `GET /health`: Health check endpoint.
- Bearer Token auth middleware: configured via `API_TOKENS` env var; empty value skips auth (dev mode).
- Nine api-server regression tests (health check, auth, param validation, 404 paths).
- GitHub Actions `run-tests.yml` added api-server job.

## \[0.1.10\] - 2026-04-05

### Added

- Structured run metrics: new `RunMetrics` dataclass aggregates 429 count, timeout count, key switch count, model route hits, search rounds, etc., written to `TaskLog` at task end.
- `OpenAIClient` telemetry: `_create_message` automatically collects `rate_limit_429`, `timeout`, `key_switch`, `model_route` metrics.
- `Orchestrator` telemetry: increments `search_rounds` counter when search tools return valid links.
- `pipeline.py` aggregates `total_duration_ms` and `stage_durations` into `run_metrics` at task end, emitting `run_metrics` event via `stream_queue`.
- Gradio Demo added `GET /api/metrics_last` endpoint returning structured run metrics from most recent task.
- Model-level failback (lightweight): `OpenAIClient` adds `model_fallback_name` config and `activate_fallback()` method; automatic switch to backup model on consecutive primary model failures.
- `Orchestrator` main and sub-agent loops: on consecutive LLM failures reaching threshold, prioritize failback attempt; reset counter on success and continue.
- Minimal regression gate: added `test_output_detail_level_routing.py` (4) and `test_model_failback.py` (4) pytest regression tests.
- GitHub Actions added `run-tests.yml` workflow, auto-running miroflow-agent and gradio-demo tests on PR and push to main.

### Changed

- `.env.example` (gradio-demo) and `.env.compose.example` added `MODEL_FALLBACK_NAME` config explanation.

## \[0.1.9\] - 2026-03-21

### Added

- New `KeyPool` generic module (`libs/miroflow-tools`): Thread-safe API Key rotation pool supporting round-robin allocation, 429 rate-limit marking and cooldown, and returning shortest remaining cooldown time when all keys exhausted.
- LLM Key pool rotation: `openai_client.py` supports `OPENAI_API_KEYS=key1,key2,key3` env var; auto-switches to next key on 429 retry.
- 429-aware backoff enhancement: detects `openai.RateLimitError`, reads `Retry-After` header; exponential backoff only as fallback when all keys exhausted.
- Search tool Key rotation: `search_and_scrape_webpage.py`, `serper_mcp_server.py`, `searching_google_mcp_server.py` support `SERPER_API_KEYS` / `SERPAPI_API_KEYS` multi-key env vars.
- Session-level API task isolation: `stop_current_api` supports optional `caller_id` param for directional cancellation; `run_research_once` adds optional `caller_id` param.
- Active task table changed from `Set[task_id]` to `{task_id: caller_id}` mapping, eliminating global broadcast cancellation.

### Changed

- `.env.example` (miroflow-agent / gradio-demo) and `.env.compose.example` added multi-key config explanations.
- Skill docs (`SKILL.md` / `api.md` / `usage.md`) updated with `caller_id` directional cancellation explanation.
- Invocation script `call_openclaw_mirosearch.py` added `--caller-id` param.
- Skill bundle `openclaw-mirosearch.zip` repackaged.

## \[0.1.8\] - 2026-03-20

### Fixed

- Fixed demo mode “Research Summary” region rendering error showing only one `\boxed{}` sentence: `prompt_patch.py` no longer overwrites full report text with boxed content; instead preserves full text and removes markers.

### Changed

- `detailed` tier token limits universally increased: `summary_max_tokens` / `max_tokens` 8192→16384, `verification_max_tokens` 6144→12288, `tool_result_max_chars` 12000→20000, `max_turns` 16→20.
- `detailed` tier research report prompt rewritten: added core principles “full retention, deduplication and consolidation, no compression”, requiring all round retrieval info to appear, duplicate info across rounds to be merged, and absolute prohibition of omission for length control.
- `detailed` tier report body target length 6000→12000 chars, minimum section count 10→12.
- `detailed` tier summary too-short retry threshold 1800→5000 chars, `balanced` tier retry threshold 900→1500 chars.
- Base summary prompt (`prompt_patch.py`) removed concision preference, adopting “full retention over concision” principle.
- Expansion prompt strengthened: requires per-round information coverage check; final report must be longer and more complete than any single round retrieval output.

## \[0.1.7\] - 2025-03-20

### Added

- New deployment and invocation guidance split by network environment: distinguishes “Mainland China without proxy” and “Overseas / with proxy” recommended search strategies.
- New SearXNG override config file `deploy/searxng/settings.yml`, supporting environment-based search engine enable/disable.
- Skill docs added network environment routing section, enabling direct selection of `search_profile` by region and link stability post-install.

### Changed

- Docker Compose deployment doc added “engine reachability self-check” command and parameter suggestions, reducing “all timeouts” misdiagnosis.
- AI Agent integration doc added network-aware routing suggestion: start with `searxng-first` on unknown networks, upgrade to `parallel-trusted` once stable.
- OpenClaw Skill (`usage/install/skill-install/SKILL.md`) updated to prioritize network-environment decision.

### Fixed

- Fixed widespread timeout issue in restricted network scenarios caused by SearXNG’s default engine set (avoided full timeout via reachable engine set).
- Fixed container restart loop caused by missing `server.secret_key` in custom SearXNG config.

## \[0.1.6\] - 2025-03-20

### Added

- New `docs/ARCHITECTURE.md` and `docs/ARCHITECTURE_en.md` (with Mermaid system architecture, data flow sequence, and deployment topology diagrams).
- New core document English versions: `CONTRIBUTING_en.md`, `SECURITY_en.md`, `CODE_OF_CONDUCT_en.md`.
- Cross-references between Chinese and English docs; `docs/README.md` index updated synchronously.
- New `.github/CODEOWNERS` code ownership config.
- Root README (CN/EN) added architecture doc links and Demo screenshot placeholders.

### Changed

- Root README feature list shortened from verbose enumeration to summary list, with detailed parameters referenced from `docs/API_SPEC.md`.
- Root README routing parameter list shortened, referencing Agent README and API_SPEC.
- `libs/miroflow-tools/README.md` reduced from 933 lines to ~365 lines, removing duplicate code examples.
- `docs/QA.md` rewritten: annotated historical doc nature, unified Chinese, removed duplicates.
- `docs/SECURITY.md` added GitHub Security Advisories as primary vulnerability reporting channel.

### Fixed

- Fixed YAML front matter format in `.github/ISSUE_TEMPLATE/bug_report.md` and `feature_request.md`.
- Corrected 5 date errors in CHANGELOG and 1 in ROADMAP (2026 → 2025).

### Removed

- Removed redundant root-level `README_en.md` (content merged into `README.md`).

## \[0.1.5\] - 2025-03-20

### Added

- Root README defaulted to English entry, with new `README_zh.md` Chinese toggle doc.
- Root README added model config explanation, supplementing `DEFAULT_LLM_PROVIDER` and role-specific model variables.
- OpenClaw skill docs split into install / usage sections, adding simple vs deep search routing suggestions.
- Root README added changelog summary section linking to full history.

### Changed

- Unified doc entry: default audience is English readers; Chinese docs are separate toggle pages.
- Skill invocation instructions extracted from install docs, reducing install/use confusion.

## \[0.1.4\] - 2025-03-20

### Added

- Unified interface `run_research_once` added sixth parameter: `output_detail_level` (`compact/balanced/detailed`).
- Summary stage added “research report mode” prompt override strategy, constraining output length and structural density per tier.
- OpenClaw skill docs and invocation scripts support `output_detail_level` parameter.
- Added “stage log heartbeat” passthrough and frontend display: stage (search/reasoning/verification/summary), round, search round count.
- Added stale task inspection thread: `running` tasks with prolonged inactivity auto-converge to `failed`.

### Changed

- Three-tier output semantics clarified: `compact=concise` / `balanced=moderate` / `detailed=extended report`.
- `detailed` tier default increased summary and verification token limits, enhancing long-text capacity.
- `run_research_once` default rendering strategy changed to follow `output_detail_level`.
- Demo default search mode set to `balanced`.
- UI “minimum search rounds (effective for verified)” hidden by default, shown and effective only in `verified` mode.
- Docs fully converged to unified interface description, removing `run_research_once_v2` remnants from demo docs.

### Fixed

- Fixed upper-bound constraint issue causing “detailed tier still too short” (implicit `max_tokens` limit on `summary_max_tokens`).
- Fixed research-scenario output compression by short-answer templates.
- Fixed occasional browser local search history saving only title, not result details (added visible result area fallback capture and tiered compression persistence).

### Security

- Demo skill package download entry added URL/path security constraints, limiting protocol and path traversal risks.

## \[0.1.2\] - 2025-03-19

### Changed

- External research interface unified to single endpoint: `run_research_once` (legacy dual endpoints converged).
- `run_research_once` standardized to five-parameter input: `query/mode/search_profile/search_result_num/verification_min_search_rounds`.
- UI default rendering: “comprehensive result first + process collapsed”; API default rendering: “comprehensive result only”.
- OpenClaw skill docs and invocation scripts updated to unified single-interface spec.

### Fixed

- Reduced multi-draft exposure in `verified` multi-round search causing multi-section report UX issues.
- Corrected `stop_current` path in docs to `/gradio_api/call/stop_current`.

## \[0.1.1\] - 2025-03-19

### Added

- Demo input area added browser local search history (`localStorage`), supporting refill, per-entry deletion, and bulk clear.
- Added prompt safety regression tests, preventing scenario-specific hard-coded contamination in cross-validation templates.

### Changed

- Cross-validation and follow-up prompts rewritten in generic terms, removing domain-specific example word contamination.
- Main flow and summary/verification model routing optimized, enhancing high-tier model involvement in critical judgment steps.
- Added model routing observation logs (`requested`/`responded`) for verifying actual model hits.

## \[0.1.0\] - 2025-03-18

### Added

- Released OpenClaw-MiroSearch’s first usable baseline version (MVP).
- Added research modes: `production-web`, `verified`, `research`, `balanced`, `quota`, `thinking`.
- Added search routing: `searxng-first`, `serp-first`, `multi-route`, `parallel`, `parallel-trusted`, `searxng-only`.
- Added concurrent aggregation and high-confidence source re-checking capability (`parallel-trusted`).
- Added OpenClaw skill package: `skills/openclaw-mirosearch/`.
- Added standalone deployment `compose.yaml` (`app + searxng + valkey`).

### Changed

- Root `README` refactored into open-source project-oriented doc, with full doc index.
- Demo page supports mode and search source strategy selection, exposing key search parameters.

### Fixed

- Fixed long-running tasks potentially stuck in `running` final state (task final-state guard).
- Added consecutive LLM failure protection, preventing hang from empty-response retries.
