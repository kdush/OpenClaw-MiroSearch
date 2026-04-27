# OpenClaw-MiroSearch

<p align="center">
  <img src="assets/mirologo.png" alt="OpenClaw-MiroSearch Logo" width="320" />
</p>

OpenClaw-MiroSearch is an open-source web retrieval engineering project for agent scenarios, designed to provide controllable cost, configurable routing, and programmable API interfaces.

> 📄 中文文档：[README_zh.md](./README_zh.md)

## Project Goals

- Lower search cost with local SearXNG and optional commercial search sources
- Improve result stability with parallel search, confidence evaluation, and high-trust supplemental search
- Make system integration easier with a unified API for OpenClaw and other agents

## Upstream & License

This project is modified from [MiroMindAI/MiroThinker](https://github.com/MiroMindAI/MiroThinker). The repository retains the original license requirements while adding engineering improvements for OpenClaw/Agent toolchain integration. Compatible with existing search channels (SearXNG, SerpAPI, Serper) and the original MiroFlow toolchain.

- License: [`LICENSE`](LICENSE)

## Implemented Features

- **6 research modes**: `production-web` / `verified` / `research` / `balanced` (default) / `quota` / `thinking`
- **6 search routing profiles**: `searxng-first` / `serp-first` / `multi-route` / `parallel` / `parallel-trusted` / `searxng-only`
- **Multi-source search**: SearXNG, SerpAPI, Serper — with parallel aggregation and confidence-based supplemental retrieval
- **Unified API**: `run_research_once` with 6 parameters for full control over mode, routing, depth, and output detail
- **Runtime observability**: stage heartbeat (search/reasoning/verification/summary), stale-task auto-reconciliation
- **Independent API server**: FastAPI-based `apps/api-server/` with standard REST endpoints (`/v1/research`), Bearer Token auth, and request rate limiting
- **Result caching**: in-memory LRU + TTL cache to avoid redundant search/LLM costs for identical queries
- **Multi-key rotation**: LLM and search API keys support pool rotation with 429-aware backoff
- **Model failback**: automatic fallback to secondary model on consecutive primary model failures
- **CI regression gate**: GitHub Actions `run-tests.yml` with 60+ automated tests across 3 apps

> For full API specification and parameter reference, see [`docs/API_SPEC.md`](docs/API_SPEC.md)
>
> For architecture overview and data flow diagrams, see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)

<p align="center"><img src="assets/demo-screenshot.png" alt="Demo Screenshot" width="900" /></p>

## Quick Start

### 1. Install Dependencies

```bash
cd apps/gradio-demo
uv sync
```

### 2. Initialize Configuration

```bash
cp .env.example .env
```

Minimal `.env` example:

```bash
# OpenAI-compatible LLM gateway
BASE_URL="https://api.longcat.chat/openai"
API_KEY="<your_longcat_key>"
DEFAULT_LLM_PROVIDER="openai" # openai / anthropic / qwen
DEFAULT_MODEL_NAME="gpt-4o-mini"
MODEL_TOOL_NAME="gpt-4o-mini"
MODEL_FAST_NAME="gpt-4o-mini"
MODEL_THINKING_NAME="gpt-4o-mini"
MODEL_SUMMARY_NAME="gpt-4o-mini"

# Search sources (configure at least one)
SEARXNG_BASE_URL="http://127.0.0.1:27080"
SERPAPI_API_KEY="<your_serpapi_key>"
SERPER_API_KEY="<your_serper_key>"

# Default execution strategy
DEFAULT_RESEARCH_MODE="balanced"
DEFAULT_SEARCH_PROFILE="parallel-trusted"
```

Model configuration notes:

- `DEFAULT_LLM_PROVIDER` controls provider routing (`openai` / `anthropic` / `qwen`)
- `DEFAULT_MODEL_NAME` is the default primary model
- Per-stage model variables:
  - `MODEL_TOOL_NAME`: tool-calling stage
  - `MODEL_FAST_NAME`: fast stage
  - `MODEL_THINKING_NAME`: deep-thinking stage
  - `MODEL_SUMMARY_NAME`: summarization stage
- Fallback rules:
  - If `MODEL_TOOL_NAME`, `MODEL_FAST_NAME`, or `MODEL_THINKING_NAME` is unset, it falls back to `DEFAULT_MODEL_NAME`
  - If `MODEL_SUMMARY_NAME` is unset, it falls back to `MODEL_FAST_NAME`

### 3. Start Service

```bash
uv run main.py
```

Default address: `http://127.0.0.1:8080`

### 4. Health Check

```bash
curl -sS 'http://127.0.0.1:8080/gradio_api/info'
```

## API Usage Example

Unified interface with 6 parameters:

```bash
BASE_URL="http://127.0.0.1:8080"
QUERY="Which Chinese companies have released OpenClaw variants?"
MODE="verified"
PROFILE="parallel-trusted"
RESULT_NUM=30
MIN_ROUNDS=4
DETAIL_LEVEL="balanced" # compact / balanced / detailed

EVENT_ID=$(curl -sS -H 'Content-Type: application/json' \
  -d "{\"data\":[\"$QUERY\",\"$MODE\",\"$PROFILE\",$RESULT_NUM,$MIN_ROUNDS,\"$DETAIL_LEVEL\"]}" \
  "$BASE_URL/gradio_api/call/run_research_once" | python3 -c 'import sys,json;print(json.load(sys.stdin)["event_id"])')

curl -sS "$BASE_URL/gradio_api/call/run_research_once/$EVENT_ID"
```

Stop current task:

```bash
curl -sS -H 'Content-Type: application/json' \
  -d '{"data":[]}' \
  "$BASE_URL/gradio_api/call/stop_current"
```

## For OpenClaw / AI Agents

Project positioning:

- Provides web research capability callable by upper-layer agents
- Supports four-dimensional control: mode, routing, search depth, and output detail
- Uses SSE terminal events so agents can determine task completion

Recommended agent calling loop:

1. Call `GET /gradio_api/info` for health check
1. Initiate `run_research_once`
1. Poll for `event: complete`
1. Consume only final Markdown from `complete`

Skill guidance:

- Simple search, single-fact lookup, and cost-first usage: use the `searxng` skill
  - Link: <https://clawhub.ai/abk234/searxng>
- Deep research or high-quality retrieval: use the `openclaw-mirosearch` skill
  - Skill docs: [`skills/openclaw-mirosearch/SKILL.md`](skills/openclaw-mirosearch/SKILL.md)
  - Usage docs: [`skills/openclaw-mirosearch/references/usage.md`](skills/openclaw-mirosearch/references/usage.md)

Skill acquisition and installation:

- Repository: `skills/openclaw-mirosearch/`
- Packaged file: `skills/openclaw-mirosearch.zip`
- Installation guide: [`skills/openclaw-mirosearch/references/skill-install.md`](skills/openclaw-mirosearch/references/skill-install.md)
- API docs: [`skills/openclaw-mirosearch/references/api.md`](skills/openclaw-mirosearch/references/api.md)
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
- OpenClaw skill package: [`skills/openclaw-mirosearch/SKILL.md`](skills/openclaw-mirosearch/SKILL.md)

## Open Source Collaboration

- Contributing, governance, support & release: [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md)
- Security policy: [`docs/SECURITY.md`](docs/SECURITY.md)
- Code of conduct: [`docs/CODE_OF_CONDUCT.md`](docs/CODE_OF_CONDUCT.md)
- Changelog: [`docs/CHANGELOG.md`](docs/CHANGELOG.md)

## Development Validation

```bash
# Repository root
just format
just lint

# Demo startup
cd apps/gradio-demo && uv sync && uv run main.py

# Agent tests
cd apps/miroflow-agent && uv run pytest

# API server tests
cd apps/api-server && uv run pytest tests/ -v
```

## Roadmap

See: [`docs/ROADMAP.md`](docs/ROADMAP.md)

Current planning is divided into the following phases:

- `v0.2.0` (production-ready) ✅: SearchProvider protocol, async task queue (arq + Valkey), SSE streaming, persistent cache, Docker Compose orchestration
- `v0.2.4` (current) ✅: `scrape_url` redirect SSRF hardening, shared `httpx.AsyncClient`, PDF / JSON / RSS / Atom / XML support
- `v0.2.5` (scraping quality + observability): T6–T8 in [`docs/SCRAPING_ITERATION_PLAN.md`](docs/SCRAPING_ITERATION_PLAN.md) — `trafilatura`, HTML table markdown, smart truncation, Prometheus metrics, eval pipeline in CI, multi-source RRF ranking, multilingual retrieval optimization
- `v0.3.0` (batch scraping + site-friendliness): T9 in [`docs/SCRAPING_ITERATION_PLAN.md`](docs/SCRAPING_ITERATION_PLAN.md) — batch `scrape_urls`, quotas and rate limiting, robots.txt validation
- `v1.0.0` (ecosystem distribution): Helm Chart / one-click cloud deploy, skill versioned release, compatibility matrix auto-verification
