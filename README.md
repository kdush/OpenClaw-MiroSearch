# Diting (谛听)

<p align="center">
  <img src="assets/diting_logo.png" alt="Diting Logo" width="320" />
</p>

[English](README.md) | [中文](README_zh.md)

Diting (谛听) is an open-source agentic research service built on MiroThinker. It combines multi-provider web search, full-page extraction, multi-step reasoning, source verification, asynchronous execution, and structured Markdown reports.

Current stable release: **v0.2.11**. See the [Changelog](docs/CHANGELOG.md) for released changes and the [Roadmap](docs/ROADMAP.md) for planned work.

## Current capabilities

- Six research modes: `balanced`, `verified`, `research`, `production-web`, `quota`, and `thinking`.
- Six search profiles: `searxng-first`, `serp-first`, `multi-route`, `parallel`, `parallel-trusted`, and `searxng-only`.
- Search providers: SearXNG, SerpAPI, Serper, Tavily, and optional Sogou integration.
- Full-page extraction for HTML, text, PDF, JSON, RSS, Atom, and XML, with redirect SSRF checks, response-size limits, encoding recovery, table preservation, and natural-boundary truncation.
- FastAPI task service backed by arq and Valkey, with persistent task metadata, event streams, results, cancellation, and result caching.
- Gradio UI with API-backed reconnect, progress display, clickable citations, and Markdown/PDF/Word export.
- OpenAI-compatible and Anthropic model clients, stage-specific model routing, key rotation, bounded retries, and model failback.
- Fail-closed FastAPI authentication, request rate limiting, caller-scoped cancellation, and per-task tool isolation.

Planned features such as batch `scrape_urls`, Prometheus/Grafana, RRF ranking, an MCP adapter, and Helm packaging are not implemented yet; they are tracked in the [Roadmap](docs/ROADMAP.md).

## Architecture

The default Docker Compose stack contains five services:

| Service | Purpose | Default host port |
|---|---|---:|
| `app` | Gradio UI and compatibility API | 8080 |
| `api` | FastAPI research API | 8090 |
| `worker` | arq research worker | — |
| `valkey` | Queue, task state, event streams, and cache | internal only |
| `searxng` | Self-hosted search provider | 27080 |

See [Architecture](docs/ARCHITECTURE.md) for data flow and module boundaries.

## Quick start

Requirements: Docker with Compose v2, an OpenAI-compatible or Anthropic LLM endpoint, and at least one usable search provider.

```bash
cp .env.compose.example .env.compose
# Edit .env.compose and replace placeholder LLM/search credentials.
docker compose --env-file .env.compose up -d --build
docker compose ps
```

The example binds published ports to `127.0.0.1` and explicitly enables unauthenticated local development with `AUTH_DISABLED=1`. For shared or production access, set `AUTH_DISABLED=0`, configure a strong `API_TOKENS` value, set the same token as `API_BEARER_TOKEN` for the Gradio API backend, and place TLS termination in front of the service.

Check the stack:

```bash
curl -sS http://127.0.0.1:8090/health
curl -sS http://127.0.0.1:8080/gradio_api/info
curl -sS http://127.0.0.1:27080/healthz
```

For source-based setup, host-network deployment, authentication, and troubleshooting, see [Deployment](docs/DEPLOY.md).

## FastAPI example

Submit a task:

```bash
curl -sS -X POST http://127.0.0.1:8090/v1/research \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "Compare the latest practical quantum-computing milestones",
    "mode": "verified",
    "search_profile": "parallel-trusted",
    "search_result_num": 30,
    "verification_min_search_rounds": 4,
    "output_detail_level": "balanced",
    "caller_id": "example-client"
  }'
```

The response contains `task_id` and `status`. Poll or stream the task:

```bash
curl -sS http://127.0.0.1:8090/v1/research/<task_id>
curl -sS -N http://127.0.0.1:8090/v1/research/<task_id>/stream
```

When authentication is enabled, add `Authorization: Bearer <token>` to protected requests. The full contract is in [API Specification](docs/API_SPEC.md).

## Recommended profiles

| Intent | Mode | Search profile | Suggested depth |
|---|---|---|---|
| General research | `balanced` | `parallel-trusted` | 20 results |
| Fact verification | `verified` | `parallel-trusted` | 30 results, 4 rounds |
| Cost-sensitive search | `quota` | `searxng-only` | 10-20 results |
| Mainland China without a stable proxy | `balanced` | `searxng-first` | 20 results |

These are client recommendations, not immutable server defaults. Omitted optional fields are resolved from the deployment's `DEFAULT_*` environment variables.

## Documentation

- [Documentation index](docs/README.md)
- [API specification](docs/API_SPEC.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Deployment](docs/DEPLOY.md)
- [Roadmap](docs/ROADMAP.md)
- [Changelog](docs/CHANGELOG.md)
- [Security](docs/SECURITY.md)
- [Contributing](docs/CONTRIBUTING.md)
- [Diting skill](skills/diting/SKILL.md)

## Development

Python 3.12+ is required for the primary applications and library.

```bash
# Install app dependencies
cd apps/gradio-demo && uv sync
cd ../miroflow-agent && uv sync
cd ../../libs/miroflow-tools && uv sync
```

If you need legacy compatibility or want to reuse the Demo UI directly, Gradio API remains available:

```bash
BASE_URL="http://127.0.0.1:8080"
curl -sS "$BASE_URL/gradio_api/info"
```

## For OpenClaw / AI Agents

Project positioning:

- Provides web research capability callable by upper-layer agents
- Supports four-dimensional control: mode, routing, search depth, and output detail
- Uses SSE terminal events so agents can determine task completion

Recommended agent calling loop:

1. Call `GET /health` for health check
1. Submit `POST /v1/research`
1. Poll `GET /v1/research/{task_id}` or subscribe to `GET /v1/research/{task_id}/stream`
1. When `status=completed` or `cached`, consume only the final Markdown

Skill guidance:

- Simple search, single-fact lookup, and cost-first usage: use the repository-distributed `searxng` skill
  - Repository: `skills/searxng/`
  - Packaged file: `skills/searxng.zip`
- Deep research or high-quality retrieval: use the `diting` skill
  - Skill docs: [`skills/diting/SKILL.md`](skills/diting/SKILL.md)
  - Usage docs: [`skills/diting/references/usage.md`](skills/diting/references/usage.md)

Skill acquisition and installation:

- Recommended dual-skill bundle: `skills/openclaw-search-skills-bundle.zip`
- Simple search skill: `skills/searxng/`
- Repository: `skills/diting/`
- Packaged file: `skills/diting.zip`
- Installation guide: [`skills/diting/references/skill-install.md`](skills/diting/references/skill-install.md)
- API docs: [`skills/diting/references/api.md`](skills/diting/references/api.md)
- AI Agent integration: [`docs/API_SPEC.md`](docs/API_SPEC.md)

## Recommended Configuration Baseline

- **Default production**: `mode=balanced` + `search_profile=parallel-trusted`
- **High-risk fact-checking**: `mode=verified` + `search_profile=parallel-trusted`
- **Quota-priority**: `mode=quota` + `search_profile=searxng-only`
- **Verification depth**: `search_result_num=30` + `verification_min_search_rounds=4`

> For the full list of routing environment variables, see [`apps/miroflow-agent/README.md`](apps/miroflow-agent/README.md#4-检索路由配置) and [`docs/API_SPEC.md`](docs/API_SPEC.md)

## Changelog

- Release `0.2.4` highlights:
  - `scrape_url` now supports PDF extraction with a 20MB streamed body limit
  - JSON / RSS / Atom / XML payloads can pass through with structured fields (`json_keys`, `feed_title`, `entries`, `xml_root`)
  - Redirect handling now uses streamed responses and closes intermediate 30x hops eagerly
  - Local Docker end-to-end verification passed on the `app + api + worker + searxng + valkey` stack
  - See [`docs/SCRAPING_ITERATION_PLAN.md`](docs/SCRAPING_ITERATION_PLAN.md) for the full T1–T9 scraping roadmap
- Release `0.2.2` highlights:
  - API-mode regression fix: `mode` / `search_profile` / `search_result_num` / `verification_min_search_rounds` / `output_detail_level` are now respected end-to-end via `services/profile_resolver.py`
  - Demo crash-recovery: `BACKEND_MODE=api` plus `?task_id=xxx` URL bridge — refresh / disconnect resumes the same task via SSE replay
  - MCP tool `scrape_url`: lightweight `httpx + BeautifulSoup` scraper with SSRF guard so the LLM can "open the page" when `google_search` snippets are insufficient
  - Worker cancel watcher hardened against Redis hiccups; unresponsive pipelines are abandoned after a 10s timeout
  - Dockerfile uses a domestic apt mirror by default; compose builds run with `network: host`; `scripts/deploy/build_images.sh` bypasses BuildKit's `network.host` entitlement prompt
- Release `0.2.1` highlights:
  - Clickable `[N]` references in research summaries pointing to the report's References / 参考文献 section
  - `api-worker` startup command pinned to `.venv/bin/python` for reliable arq worker boot
- Release `0.2.0` highlights:
  - Async task queue (arq + Valkey), persistent SSE event streams, cache and metadata persistence
  - `SearchProvider` Protocol + `ProviderRegistry` (Serper / SerpAPI / SearXNG)
- Full history: [`docs/CHANGELOG.md`](docs/CHANGELOG.md)

## Documentation Index

- Overview: [`docs/README.md`](docs/README.md)
- Architecture: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- Deployment: [`docs/DEPLOY.md`](docs/DEPLOY.md)
- API spec & Agent integration: [`docs/API_SPEC.md`](docs/API_SPEC.md)
- Roadmap: [`docs/ROADMAP.md`](docs/ROADMAP.md)
- Changelog: [`docs/CHANGELOG.md`](docs/CHANGELOG.md)
- Demo docs: [`apps/gradio-demo/README.md`](apps/gradio-demo/README.md)
- API server docs: [`apps/api-server/README.md`](apps/api-server/README.md)
- Agent docs: [`apps/miroflow-agent/README.md`](apps/miroflow-agent/README.md)
- Tools docs: [`libs/miroflow-tools/README.md`](libs/miroflow-tools/README.md)
- Diting skill package: [`skills/diting/SKILL.md`](skills/diting/SKILL.md)

## Open Source Collaboration

- Contributing, governance, support & release: [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md)
- Security policy: [`docs/SECURITY.md`](docs/SECURITY.md)
- Code of conduct: [`docs/CODE_OF_CONDUCT.md`](docs/CODE_OF_CONDUCT.md)
- Changelog: [`docs/CHANGELOG.md`](docs/CHANGELOG.md)


## Development Validation

```bash
# Repository-wide checks
just lint
just sort-imports
just format
just format-md

# Focused test suites
cd apps/miroflow-agent && uv sync && uv run pytest
cd apps/api-server && uv sync && AUTH_DISABLED=1 uv run pytest
cd apps/gradio-demo && uv sync && uv run pytest
cd libs/miroflow-tools && uv sync && uv run pytest
```

## License and upstream

This repository is derived from [MiroMindAI/MiroThinker](https://github.com/MiroMindAI/MiroThinker) and retains its license requirements. See [LICENSE](LICENSE) and the upstream attribution in the project history.
