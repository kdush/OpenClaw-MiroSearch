# OpenClaw-MiroSearch Demo (Gradio)

This directory provides a Web Demo and external API entry point.

- **Web Interface**: Interactive Q&A with tunable parameters
- **API**: Programmatic access for other agents/scripts

> 📄 中文文档：[README.md](./README.md)

## 1. Installation

```bash
cd apps/gradio-demo
uv sync
```

## 2. Configuration

```bash
cp .env.example .env
```

Minimum required configuration:

```bash
# LLM Gateway (OpenAI-compatible)
BASE_URL="https://api.longcat.chat/openai"
API_KEY="<your_longcat_key>"

# Search sources (at least one)
SEARXNG_BASE_URL="http://127.0.0.1:27080"
SERPAPI_API_KEY="<your_serpapi_key>"
SERPER_API_KEY="<your_serper_key>"
```

Optional:

```bash
# Default dropdown options
DEFAULT_RESEARCH_MODE="balanced"
DEFAULT_SEARCH_PROFILE="parallel-trusted"
```

Notes:

- The demo default mode is `balanced`
- Search history stores both "query + result details" in browser local storage; clicking history restores query and result

## 3. Launch

```bash
uv run main.py
```

Default listening address: `http://127.0.0.1:8080`

### Docker Compose One-Click Deployment

Run from repository root:

```bash
cp .env.compose.example .env.compose
docker compose --env-file .env.compose up -d --build
```

See also: [`docs/DEPLOY_DOCKER_COMPOSE.md`](../../docs/DEPLOY_DOCKER_COMPOSE.md)

## 4. Page-Switchable Configuration

### Research Mode (`mode`)

- `production-web`
- `verified`
- `research`
- `balanced`
- `quota`
- `thinking`

### Search Profile (`search_profile`)

- `searxng-first`
- `serp-first`
- `multi-route`
- `parallel`
- `parallel-trusted`
- `searxng-only`

Recommendations:

- Default: `balanced + parallel-trusted`
- Strict validation: `verified + parallel-trusted`
- Quota-saving: `quota + searxng-only`

### New Control Options

- `search_result_num`: Results per search round (10/20/30)
- `verification_min_search_rounds`: Minimum search rounds (shown and effective only in `verified` mode)
- `output_detail_level`: Output length (`compact/balanced/detailed`)

## 5. API Usage

### 5.1 Single Research (Final Markdown)

1. Submit task:

```bash
curl -sS -H 'Content-Type: application/json' \
  -d '{"data":["Which Chinese companies have released OpenClaw variants?","balanced","parallel-trusted"]}' \
  'http://127.0.0.1:8080/gradio_api/call/run_research_once'
```

Get `event_id` from response.

2. Pull result:

```bash
curl -sS "http://127.0.0.1:8080/gradio_api/call/run_research_once/<event_id>"
```

Unified request takes 6 parameters:

```json
{"data":["<query>","<mode>","<search_profile>",20,3,"<output_detail_level>"]}
```

### 5.2 Stop Current Task

```bash
curl -sS -H 'Content-Type: application/json' \
  -d '{"data":[]}' \
  'http://127.0.0.1:8080/gradio_api/call/stop_current'
```

### 5.3 View API Metadata

```bash
curl -sS 'http://127.0.0.1:8080/gradio_api/info'
```

## 6. Relationship with Production Mode

- Demo is the "visual interaction layer" — core capabilities are still provided by `apps/miroflow-agent` and `libs/miroflow-tools`.
- For production usage, you can keep the `run_research_once` interface with fixed parameters, without needing the frontend.

## 7. Troubleshooting

- Page stuck at `Waiting to start research...` for a long time:
  - Call `stop_current` to clear stuck tasks
  - Check LLM and search engine configuration in `.env`
- Page stays in "Generating..." for long:
  - Check stage heartbeat labels (search/reasoning/verification/summary) to confirm progress
  - Stale `running` tasks are periodically reconciled to `failed` by the background reaper
- Search history shows query title but no detailed result:
  - The page now captures results from both hidden sync output and visible markdown output
  - If local storage is near quota, it auto-compresses history and prioritizes keeping latest detailed result
- Results have too much noise:
  - Use `verified` or `balanced` mode
  - Set `search_profile` to `parallel-trusted`
