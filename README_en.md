# OpenClaw-MiroSearch

<p align="center">
  <img src="assets/mirologo.png" alt="OpenClaw-MiroSearch Logo" width="320" />
</p>

OpenClaw-MiroSearch is an open-source联网检索 (web search) engineering project for agent scenarios, providing controllable cost, configurable routing, and programmable API interfaces.

> 📄 中文文档：[README.md](./README.md)

## Project Goals

- **Lower search costs**: Support local SearXNG and optional commercial search sources
- **Improve result stability**: Support parallel search, confidence evaluation, and high-trust source supplemental search
- **Facilitate system integration**: Provide unified API for OpenClaw and other agents

## Upstream & License

This project is modified from [MiroMindAI/MiroThinker](https://github.com/MiroMindAI/MiroThinker).

- Attribution: [`docs/OPEN_SOURCE_ATTRIBUTION.md`](docs/OPEN_SOURCE_ATTRIBUTION.md)
- License: [`LICENSE`](LICENSE)

## Implemented Features

### Research Modes (`mode`)

- `production-web`
- `verified`
- `research`
- `balanced`
- `quota`
- `thinking`

Default recommendation: `balanced`

### Search Profiles (`search_profile`)

- `searxng-first`
- `serp-first`
- `multi-route`
- `parallel`
- `parallel-trusted`
- `searxng-only`

Key strategy explanation:

- `parallel`: Multi-route parallel search with deduplication
- `parallel-trusted`: Parallel search with confidence evaluation; if insufficient, sequential supplemental search based on high-trust source order

### Search Source Compatibility

- SearXNG
- SerpAPI
- Serper

### API Endpoints

- `POST /gradio_api/call/run_research_once`
- `GET /gradio_api/call/run_research_once/{event_id}`
- `POST /gradio_api/call/stop_current`
- `GET /gradio_api/info`

### Runtime Observability & Self-Healing

- Stage heartbeat: the "Generating..." UI shows phase (search/reasoning/verification/summary), turn, and search round
- Stale-task reconciliation: background checker periodically converges long-stale inactive `running` tasks to `failed`
- Parameter scope: `verification_min_search_rounds` is shown and effective only when `mode=verified`

## Project Structure

- `apps/gradio-demo/`: Web entry and API service
- `apps/miroflow-agent/`: Agent execution and configuration
- `libs/miroflow-tools/`: MCP tools and search routing implementation
- `assets/`: Brand and static assets
- `skills/openclaw-mirosearch/`: OpenClaw skill package

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

# Search sources (configure at least one)
SEARXNG_BASE_URL="http://127.0.0.1:27080"
SERPAPI_API_KEY="<your_serpapi_key>"
SERPER_API_KEY="<your_serper_key>"

# Default execution strategy
DEFAULT_RESEARCH_MODE="balanced"
DEFAULT_SEARCH_PROFILE="parallel-trusted"
```

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

Unified interface (6 parameters):

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
- Uses SSE terminal events to ensure agents can determine task completion

Recommended agent calling loop:

1. Call `GET /gradio_api/info` for health check
2. Initiate `run_research_once`
3. Poll for `event: complete`
4. Consume only final Markdown from `complete`

Skill acquisition and installation:

- Repository: `skills/openclaw-mirosearch/`
- Packaged file: `skills/openclaw-mirosearch.zip`
- Installation guide: [`skills/openclaw-mirosearch/references/skill-install.md`](skills/openclaw-mirosearch/references/skill-install.md)
- API docs: [`skills/openclaw-mirosearch/references/api.md`](skills/openclaw-mirosearch/references/api.md)
- AI Agent integration: [`docs/AI_AGENT_INTEGRATION.md`](docs/AI_AGENT_INTEGRATION.md)

## Routing Parameter Reference

Environment variables controlling search behavior:

- `SEARCH_PROVIDER_ORDER`
- `SEARCH_PROVIDER_MODE`: `fallback | merge | parallel | parallel_conf_fallback`
- `SEARCH_PROVIDER_TRUSTED_ORDER`
- `SEARCH_PROVIDER_PARALLEL_MAX_WAIT_MS`
- `SEARCH_PROVIDER_PARALLEL_MIN_SUCCESS`
- `SEARCH_PROVIDER_FALLBACK_MAX_STEPS`
- `SEARCH_RESULT_NUM`
- `SEARCH_CONFIDENCE_ENABLED`
- `SEARCH_CONFIDENCE_SCORE_THRESHOLD`
- `SEARCH_CONFIDENCE_MIN_RESULTS`
- `SEARCH_CONFIDENCE_MIN_UNIQUE_DOMAINS`
- `SEARCH_CONFIDENCE_MIN_PROVIDER_COVERAGE`
- `SEARCH_CONFIDENCE_MIN_HIGH_CONF_HITS`
- `SEARCH_CONFIDENCE_HIGH_CONF_DOMAINS`

## Recommended Configuration Baseline

- Default production baseline: `mode=balanced` + `search_profile=parallel-trusted`
- High-risk fact-checking: `mode=verified` + `search_profile=parallel-trusted`
- Quota-priority scenario: `mode=quota` + `search_profile=searxng-only`
- Verification depth recommendation: `search_result_num=30` + `verification_min_search_rounds=4`

## Documentation Index

- Overview: [`docs/README.md`](docs/README.md)
- Docker Compose deployment: [`docs/DEPLOY_DOCKER_COMPOSE.md`](docs/DEPLOY_DOCKER_COMPOSE.md)
- Demo docs: [`apps/gradio-demo/README.md`](apps/gradio-demo/README.md)
- Agent docs: [`apps/miroflow-agent/README.md`](apps/miroflow-agent/README.md)
- Tools docs: [`libs/miroflow-tools/README.md`](libs/miroflow-tools/README.md)
- Local tool deployment: [`docs/LOCAL-TOOL-DEPLOYMENT.md`](docs/LOCAL-TOOL-DEPLOYMENT.md)
- OpenClaw skill package: [`skills/openclaw-mirosearch/SKILL.md`](skills/openclaw-mirosearch/SKILL.md)
- Roadmap: [`docs/ROADMAP.md`](docs/ROADMAP.md)
- API spec: [`docs/API_SPEC.md`](docs/API_SPEC.md)

## Open Source Collaboration

- Contributing guide: [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md)
- Security policy: [`docs/SECURITY.md`](docs/SECURITY.md)
- Code of conduct: [`docs/CODE_OF_CONDUCT.md`](docs/CODE_OF_CONDUCT.md)
- Changelog: [`docs/CHANGELOG.md`](docs/CHANGELOG.md)
- Support: [`docs/SUPPORT.md`](docs/SUPPORT.md)
- Governance: [`docs/GOVERNANCE.md`](docs/GOVERNANCE.md)
- Release process: [`docs/RELEASE.md`](docs/RELEASE.md)

## Development Validation

```bash
# Repository root
just format
just lint

# Demo startup
cd apps/gradio-demo && uv sync && uv run main.py

# Agent tests
cd apps/miroflow-agent && uv run pytest
```

## Roadmap

See: [`docs/ROADMAP.md`](docs/ROADMAP.md)

Current planning divided into four phases:

- Phase A (releasable baseline): API finalization, minimal regression testing, release `v0.1.0`
- Phase B (production-ready): Multi-key rotation, model fallback, observability metrics, release `v0.2.0`
- Phase C (quality enhancement): Digital fact cross-validation and口径 unification, release `v0.3.0`
- Phase D (ecosystem distribution): OpenClaw skill release and one-click deployment template, release `v1.0.0`
