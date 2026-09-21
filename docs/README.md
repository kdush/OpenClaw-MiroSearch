# Documentation

[English](README.md) | [中文](README_zh.md)

## Start here

- [Project README](../README.md): overview and shortest working path.
- [Deployment](DEPLOY.md): Docker Compose, source setup, authentication, networking, and troubleshooting.
- [API specification](API_SPEC.md): FastAPI contract and Gradio compatibility API.

## Core reference

- [Architecture](ARCHITECTURE.md): components, data flow, persistence, and isolation boundaries.
- [Roadmap](ROADMAP.md): current baseline and planned milestones.
- [Scraping iteration plan](SCRAPING_ITERATION_PLAN.md): shipped T1-T8 work and pending T9 batch scraping.
- [Changelog](CHANGELOG.md): released changes by version.

## Operations and governance

- [Security policy](SECURITY.md)
- [Contributing guide](CONTRIBUTING.md)
- [Code of conduct](CODE_OF_CONDUCT.md)

## Module documentation

- [FastAPI server](../apps/api-server/README.md)
- [Gradio demo](../apps/gradio-demo/README.md)
- [Agent core](../apps/miroflow-agent/README.md)
- [MiroFlow tools](../libs/miroflow-tools/README.md)
- [Deployment assets](../deploy/README.md)
- [Trace collection](../apps/collect-trace/README.md)
- [Trace visualization](../apps/visualize-trace/README.md)
- [LobeHub compatibility](../apps/lobehub-compatibility/README.md)

## Integration packages

- [OpenClaw-MiroSearch skill](../skills/openclaw-mirosearch/SKILL.md)
- [SearXNG skill](../skills/searxng/SKILL.md)

## Historical design records

Files under `plans/` and `superpowers/` record implementation decisions at a point in time. They are not current product documentation; when they conflict with code or the documents above, the current code and API specification take precedence.
