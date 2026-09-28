# Diting API Server

[English](README.md) | [中文](README_zh.md)

The standalone FastAPI layer for OpenClaw-MiroSearch. It submits research jobs
to an `arq` worker and keeps task metadata, events, cancellation flags, and
results in Valkey.

## Architecture

- **API process**: validates requests, checks the shared result cache, enqueues
  jobs, serves task snapshots, and streams progress over SSE.
- **Worker process**: consumes the queue and runs `execute_task_pipeline()`.
- **Valkey**: provides the queue, task store, event stream, and result cache.

Both the API and worker processes are required for research jobs to complete.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/v1/research` | Submit a research job |
| `GET` | `/v1/research/{task_id}` | Read a task snapshot |
| `GET` | `/v1/research/{task_id}/stream` | Stream task events with SSE |
| `POST` | `/v1/research/{task_id}/cancel` | Cancel one task |
| `POST` | `/v1/research/cancel` | Cancel tasks for a required `caller_id` |
| `GET` | `/v1/metrics/last` | Read the latest completed-run metrics |
| `GET` | `/health` | Check API health |

See the [API specification](../../docs/API_SPEC.md) for request and response
contracts.

## Local Development

Valkey must be reachable before starting the processes.

```bash
cd apps/api-server
cp .env.example .env
uv sync

# Terminal 1: API
uv run python main.py

# Terminal 2: worker
uv run python worker.py
```

The code defaults to `127.0.0.1:8090`. OpenAPI is available at `/docs` and
`/redoc`.

## Authentication

Authentication is fail-closed:

- Set `API_TOKENS` to one or more comma-separated Bearer tokens for protected
  endpoints.
- If `API_TOKENS` is empty and `AUTH_DISABLED` is not `1`, protected endpoints
  return `503`.
- Use `AUTH_DISABLED=1` only for local development.
- `/health` remains public.

```bash
curl -X POST http://127.0.0.1:8090/v1/research \
  -H "Authorization: Bearer replace_with_a_random_token" \
  -H "Content-Type: application/json" \
  -d '{"query":"Recent progress in quantum computing"}'
```

When Gradio uses `BACKEND_MODE=api`, its `API_BEARER_TOKEN` must match one of
the server's `API_TOKENS`.

## Docker Compose

From the repository root:

```bash
cp .env.compose.example .env.compose
docker compose --env-file .env.compose up -d --build
```

See the [deployment guide](../../docs/DEPLOY.md) for service topology and
production considerations.

## Key Configuration

| Area | Variables |
| --- | --- |
| API | `API_HOST`, `API_PORT`, `API_VERSION` |
| Authentication | `API_TOKENS`, `AUTH_DISABLED` |
| Valkey | `VALKEY_HOST`, `VALKEY_PORT`, `VALKEY_PASSWORD` |
| Queue/store | `TASK_QUEUE_REDIS_DB`, `TASK_STORE_REDIS_DB`, `TASK_QUEUE_NAME` |
| Retention | `TASK_RESULT_TTL_SECONDS`, `TASK_METADATA_TTL_SECONDS`, `RESULT_CACHE_TTL_SECONDS` |
| Worker | `ARQ_JOB_TIMEOUT_SECONDS`, `ARQ_WORKER_MAX_JOBS`, `ARQ_WORKER_MAX_TRIES`, `ARQ_RETRY_DEFER_SECONDS` |

Defaults are defined in [`settings.py`](settings.py); deployment-specific
values belong in environment files.

## Tests

```bash
cd apps/api-server
uv run pytest
```
