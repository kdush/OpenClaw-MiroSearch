# Deployment Guide

[中文](./DEPLOY_zh.md)

This guide deploys the `v0.2.11` runtime with Docker Compose. The default
topology runs `app`, `api`, `worker`, `valkey`, and `searxng`.

## Prerequisites

- Docker Engine 24 or newer
- Docker Compose v2
- an OpenAI-compatible LLM endpoint and API key
- optional search-provider keys for broader retrieval coverage

## Prepare configuration

```bash
cp .env.compose.example .env.compose
```

At minimum, replace the placeholder values for:

```dotenv
BASE_URL=https://your-llm-gateway.example/v1
API_KEY=replace_with_your_llm_key
```

Review the copied file before starting. In particular, keep only one effective
definition of each authentication key (`API_TOKENS`, `API_BEARER_TOKEN`, and
`AUTH_DISABLED`) and select one mode explicitly. If a template revision contains
repeated definitions, the last dotenv assignment may win, which is too easy to
misread during deployment.

### Local development authentication

Use this only while all published ports remain loopback-only:

```dotenv
BIND_HOST=127.0.0.1
AUTH_DISABLED=1
API_TOKENS=
API_BEARER_TOKEN=
```

### Shared or production authentication

Generate a strong token outside the repository, then set the same token for the
FastAPI server and the default Gradio API client:

```dotenv
BIND_HOST=127.0.0.1
AUTH_DISABLED=0
API_TOKENS=replace_with_a_random_strong_token
API_BEARER_TOKEN=replace_with_the_same_token
```

Multiple server tokens may be comma-separated in `API_TOKENS`.
`API_BEARER_TOKEN` must match one of them. Do not commit `.env.compose`.

The FastAPI service is fail-closed: with no configured tokens and without
`AUTH_DISABLED=1`, protected endpoints return `503`. Missing or invalid request
credentials return `401`.

## Choose retrieval defaults

All values remain configurable in `.env.compose`. A conservative starting point
for networks where overseas providers may be unstable is:

```dotenv
DEFAULT_RESEARCH_MODE=balanced
DEFAULT_SEARCH_PROFILE=searxng-first
DEFAULT_SEARCH_RESULT_NUM=20
DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS=3
```

For networks with reliable access to multiple configured providers, use
`DEFAULT_SEARCH_PROFILE=parallel-trusted`. Add `SERPAPI_API_KEY`,
`SERPER_API_KEY`, or `TAVILY_API_KEY` only for providers you intend to use.

## Start the stack

```bash
docker compose --env-file .env.compose up -d --build
```

The default published addresses are:

| Service | Address |
| --- | --- |
| Gradio app | `http://127.0.0.1:8080` |
| FastAPI | `http://127.0.0.1:8090` |
| SearXNG | `http://127.0.0.1:27080` |

`worker` and `valkey` are internal services without published host ports. Port
values can be changed through `APP_PORT`, `API_PORT`, and
`SEARXNG_HOST_PORT`.

## Verify the deployment

```bash
docker compose ps
docker compose logs --tail=100 api worker
curl http://127.0.0.1:8090/health
curl http://127.0.0.1:8080/gradio_api/info
```

The health response reports `API_VERSION`. Its code default is `0.2.0` and is
independent from the project release `v0.2.11`.

For an authenticated request:

```bash
curl -X POST http://127.0.0.1:8090/v1/research \
  -H "Authorization: Bearer ${API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"query":"Summarize the latest retrieval research."}'
```

See [API Specification](./API_SPEC.md) for polling and SSE examples.

## Network exposure

Compose binds `app`, `api`, and `searxng` to `127.0.0.1` by default. Prefer a
trusted reverse proxy with TLS and keep this loopback binding. If direct host
exposure is unavoidable, set `BIND_HOST=0.0.0.0` only after enabling tokens and
restricting access with a firewall.

Set `TRUST_PROXY=1` only behind a trusted proxy that overwrites
`X-Forwarded-For`; otherwise rate limiting can trust spoofed client addresses.
FastAPI request limiting defaults to `RATE_LIMIT_RPM=30`.

For environments that cannot use Docker bridge networking, the repository also
provides `compose.host-network.yaml`:

```bash
docker compose -f compose.host-network.yaml \
  --env-file .env.compose up -d --build
```

Host networking removes normal port isolation. Review local port conflicts and
firewall rules before using it.

## Operations

Read logs:

```bash
docker compose logs -f app api worker
```

Restart application services without deleting data:

```bash
docker compose restart app api worker
```

Stop the stack while retaining named volumes:

```bash
docker compose down
```

Valkey task data and SearXNG cache live in named volumes. Removing volumes also
removes persisted tasks, events, results, and cache; back up required data
before any volume-removal operation.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Protected API returns `503` | Configure `API_TOKENS`, or explicitly use `AUTH_DISABLED=1` for local development |
| Protected API returns `401` | Verify the Bearer token and `API_BEARER_TOKEN` match a configured server token |
| Tasks remain `queued` | Check `worker` health and its connection to `valkey` |
| Gradio cannot submit tasks | Check `BACKEND_MODE=api`, `API_BASE_URL`, and client token configuration |
| Search returns few results | Check SearXNG health, provider keys, and the selected search profile |
| Requests return `429` | Reduce request rate or adjust the deployment's bounded rate-limit policy |
