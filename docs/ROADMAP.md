# Roadmap

[中文](./ROADMAP_zh.md)

This roadmap separates the implemented `v0.2.11` baseline from planned work.
Release-by-release history belongs in the [Changelog](./CHANGELOG.md).

## Current baseline: `v0.2.11`

The repository currently implements:

- a FastAPI research-task API with polling, SSE replay, scoped cancellation,
  metrics, fail-closed Bearer authentication, and request rate limiting;
- arq-based asynchronous execution with Valkey-backed queue, task metadata,
  events, results, quality metadata, and shared result cache;
- a Gradio UI with a compatibility API and an API-backed reconnect path;
- configurable research modes, search profiles, depth, verification rounds,
  output detail, and stage-specific model routing;
- multi-provider retrieval through SearXNG and configured commercial search
  services, including bounded fallback and key rotation;
- the single-URL scraping safety and content-extraction work tracked as T1-T8
  in the [Scraping Iteration Plan](./SCRAPING_ITERATION_PLAN.md).

The codebase uses MCP internally for tool subprocesses, but it does not ship a
public MCP server that external clients can connect to. The adapter described
below is planned, not implemented.

## `v0.3.0`: Site-friendly batch scraping

Planned outcome: complete scraping task T9 without weakening current single-URL
safety controls.

- add bounded multi-URL scraping with per-item failure isolation;
- honor `robots.txt` decisions before fetching eligible pages;
- add per-site concurrency and request-rate controls;
- use bounded backoff for `429` and transient `5xx` responses;
- cover batch timeout, SSRF, redirects, limits, and partial failure with
  automated tests.

## `v0.4.0`: Quality evaluation and observability

Planned outcome: make research quality and runtime behavior measurable before
changing retrieval policy further.

- establish a versioned evaluation set and reproducible scoring workflow;
- measure answer availability, citation coverage, source diversity, freshness,
  latency, and cost-related counters;
- expose task-stage and provider-level telemetry with stable field semantics;
- add regression thresholds that can run in CI without requiring every test to
  call paid external services.

## `v0.5.0`: Standard MCP adapter

Planned outcome: provide a supported MCP-facing integration layer over the
existing research runtime.

- support local `stdio` and remote Streamable HTTP transports;
- expose a small, stable surface across MCP tools, resources, and prompts;
- map MCP calls to the existing asynchronous task lifecycle instead of
  duplicating the research engine;
- define authentication, authorization boundaries, structured errors,
  cancellation, timeouts, and process lifecycle behavior;
- add protocol contract and compatibility tests for both transports;
- publish deployment and client-integration documentation only after the server
  is runnable and tested.

## `v1.0`: Supported ecosystem

Planned outcome: turn the stable runtime and MCP adapter into a maintainable
integration ecosystem.

- publish compatibility and deprecation policies;
- provide versioned examples and integration templates;
- define extension points for providers, tools, and deployment profiles;
- document upgrade, backup, recovery, and operational support expectations.

## Planning rules

- A roadmap item moves to the changelog only after code and automated checks
  exist.
- Documentation must distinguish current behavior from proposed design.
- Public contracts should reuse the existing task and result model wherever
  possible.
- Security defaults remain fail-closed as new transports and deployment modes
  are added.