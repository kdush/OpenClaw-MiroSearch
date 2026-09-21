# API Specification

[中文](./API_SPEC_zh.md)

This document defines the current public API contract. The FastAPI service is
the primary integration surface; the Gradio endpoints are compatibility APIs
for existing clients.

The project release is `v0.2.11`. The API service reports an independently
configurable `API_VERSION`, whose code default is `0.2.0`.

## FastAPI base URL

The Docker Compose default is:

```text
http://127.0.0.1:8090
```

OpenAPI documentation is available at `/docs` and `/redoc`.

## Authentication and rate limiting

All `/v1/*` endpoints require a Bearer token unless unauthenticated development
mode is explicitly enabled. `/health` is public.

```http
Authorization: Bearer <token>
```

The authentication policy is fail-closed:

- if `API_TOKENS` is empty and `AUTH_DISABLED` is not `1`, protected endpoints
  return `503`;
- if tokens are configured but the request has no valid token, protected
  endpoints return `401`;
- `AUTH_DISABLED=1` is for local development only.

Rate limiting is enabled by default at `30` requests per minute. Health and API
documentation paths bypass the limiter. See [Deployment Guide](./DEPLOY.md) for
production settings.

## Endpoint summary

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/v1/research` | Submit a research task |
| `GET` | `/v1/research/{task_id}` | Read task status, metadata, and result |
| `GET` | `/v1/research/{task_id}/stream` | Stream task events over SSE |
| `POST` | `/v1/research/{task_id}/cancel` | Cancel one task |
| `POST` | `/v1/research/cancel?caller_id=...` | Cancel active tasks for one caller |
| `GET` | `/v1/metrics/last` | Read the latest run metrics |
| `GET` | `/health` | Read public service health |

## Submit a research task

`POST /v1/research`

```bash
curl -X POST http://127.0.0.1:8090/v1/research \
  -H "Authorization: Bearer ${API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What changed in retrieval-augmented generation this year?",
    "mode": "balanced",
    "search_profile": "parallel-trusted",
    "search_result_num": 20,
    "verification_min_search_rounds": 3,
    "output_detail_level": "balanced",
    "caller_id": "client-session-42"
  }'
```

### Request fields

| Field | Required | Allowed values or meaning |
| --- | --- | --- |
| `query` | Yes | Non-empty research question |
| `mode` | No | `balanced`, `verified`, `research`, `production-web`, `quota`, `thinking` |
| `search_profile` | No | `searxng-first`, `serp-first`, `multi-route`, `parallel`, `parallel-trusted`, `searxng-only` |
| `search_result_num` | No | `10`, `20`, or `30` |
| `verification_min_search_rounds` | No | Integer from `1` through `8`; verification behavior is mode-dependent |
| `output_detail_level` | No | `compact`, `balanced`, or `detailed` |
| `caller_id` | No | Stable caller identifier used for scoped cancellation |

Omitted or `null` policy fields use the deployment's `DEFAULT_*` values. The
code-level safe fallbacks are `balanced`, `searxng-first`, `20`, `3`, and
`detailed`, but deployment configuration may override them.

### Response

A newly queued task returns:

```json
{
  "task_id": "2d66d941-0ce8-4f35-b0fb-92102bdfd5ea",
  "status": "accepted"
}
```

A cache hit returns a new task record with status `cached`:

```json
{
  "task_id": "cached-9fba276d-2867-4985-96e2-99a957c615e8",
  "status": "cached"
}
```

The submit response never embeds the report body. Retrieve it through the task
status endpoint or the SSE stream, including for cache hits.

## Read task status and result

`GET /v1/research/{task_id}`

```bash
curl -H "Authorization: Bearer ${API_TOKEN}" \
  http://127.0.0.1:8090/v1/research/${TASK_ID}
```

```json
{
  "task_id": "2d66d941-0ce8-4f35-b0fb-92102bdfd5ea",
  "status": "completed",
  "meta": {
    "task_id": "2d66d941-0ce8-4f35-b0fb-92102bdfd5ea",
    "status": "completed",
    "caller_id": "client-session-42",
    "query": "What changed in retrieval-augmented generation this year?",
    "mode": "balanced",
    "search_profile": "parallel-trusted",
    "search_result_num": 20,
    "verification_min_search_rounds": 3,
    "output_detail_level": "balanced",
    "created_at": 0,
    "started_at": 0,
    "finished_at": 0,
    "current_stage": "",
    "error": null
  },
  "result": "# Research report...",
  "event_count": 12,
  "result_quality": {
    "format_valid": true,
    "fallback_used": false,
    "issues": [],
    "answer_available": true
  }
}
```

Task statuses are `queued`, `running`, `completed`, `failed`, `cancelled`, and
`cached`. Clients should treat `completed`, `failed`, `cancelled`, and `cached`
as terminal states.

## Stream task events

`GET /v1/research/{task_id}/stream`

```bash
curl -N -H "Authorization: Bearer ${API_TOKEN}" \
  http://127.0.0.1:8090/v1/research/${TASK_ID}/stream
```

The response uses Server-Sent Events. Persisted pipeline events are replayed in
order, heartbeats keep idle connections alive, and the stream ends with a
`done` event:

```text
event: done
data: {"status":"completed"}
```

For completed or cached tasks, consume the persisted `final_output` event or
read `result` from the status endpoint. For failed or cancelled tasks, inspect
the terminal event and `done` payload.

## Cancel tasks

Cancel a single queued or running task:

```bash
curl -X POST -H "Authorization: Bearer ${API_TOKEN}" \
  http://127.0.0.1:8090/v1/research/${TASK_ID}/cancel
```

Cancel all queued or running tasks for one caller:

```bash
curl -X POST -H "Authorization: Bearer ${API_TOKEN}" \
  "http://127.0.0.1:8090/v1/research/cancel?caller_id=client-session-42"
```

`caller_id` is required and must not be blank. This endpoint never falls back
to global cancellation.

```json
{
  "cancelled": 1,
  "task_ids": ["2d66d941-0ce8-4f35-b0fb-92102bdfd5ea"]
}
```

## Metrics and health

`GET /v1/metrics/last` is protected and returns the latest persisted run
metrics, or a `no_data` response before any run has completed.

`GET /health` is public:

```json
{
  "status": "ok",
  "version": "0.2.0"
}
```

The version shown above is the code default for `API_VERSION`, not the project
release number.

## Error handling

| Status | Meaning |
| --- | --- |
| `400` | Task is not cancellable in its current state |
| `401` | Bearer token is missing or invalid |
| `404` | Task does not exist or has expired |
| `422` | Request validation failed |
| `429` | Request rate limit was exceeded |
| `503` | Authentication is not configured, or the task queue is unavailable |

Clients should retry transient `429` and `503` responses with bounded backoff.
Do not retry validation or authentication failures without changing the
request or configuration.

## Gradio compatibility API

The Gradio service defaults to `http://127.0.0.1:8080`. These endpoints remain
for UI and legacy integrations; new service integrations should use FastAPI.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/gradio_api/call/run_research_once` | Submit the seven-item compatibility request |
| `GET` | `/gradio_api/call/run_research_once/{event_id}` | Poll the Gradio event stream |
| `POST` | `/gradio_api/call/stop_current` | Cancel by `caller_id` |
| `POST` | `/gradio_api/call/stop_current_by_caller` | Cancel by `caller_id` |
| `GET` | `/gradio_api/info` | Read Gradio endpoint metadata |

```json
{
  "data": [
    "<query>",
    "<mode>",
    "<search_profile>",
    20,
    3,
    "<output_detail_level>",
    "<caller_id>"
  ]
}
```

The seventh array item is `caller_id`. Empty cancellation identifiers are
rejected rather than interpreted as a global stop request.
