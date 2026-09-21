# OpenClaw-MiroSearch

[English](README.md) | [中文](README_zh.md)

OpenClaw-MiroSearch is an agentic research service built on MiroThinker. It combines multi-provider web search, full-page extraction, multi-step reasoning, source verification, asynchronous execution, and structured Markdown reports.

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
- [OpenClaw skill](skills/openclaw-mirosearch/SKILL.md)

## Development

Python 3.12+ is required for the primary applications and library.

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
