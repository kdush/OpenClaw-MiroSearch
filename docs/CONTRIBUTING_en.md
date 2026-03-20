# Contributing Guide

Thank you for contributing to OpenClaw-MiroSearch.

> 📄 中文版：[CONTRIBUTING.md](./CONTRIBUTING.md)

## Development Environment

1. Install Python 3.10+ and `uv`
2. Clone the repository and install dependencies:

```bash
cd apps/gradio-demo && uv sync
cd ../miroflow-agent && uv sync
cd ../../libs/miroflow-tools && uv sync
```

## Local Validation

Before submitting, run:

```bash
# Repository root
just format
just lint

# Demo compilation check
cd apps/gradio-demo && uv run python -m py_compile main.py

# Agent tests
cd ../miroflow-agent && uv run pytest
```

## Branch & Commit Conventions

- Submit PRs based on the `dev` branch
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/), with scope recommended
- Examples:
  - `feat(search): add concurrent retrieval and confidence-based supplemental search`
  - `docs(readme): restructure deployment and API documentation`

## Configuration & Security

- Never commit real API keys, secrets, or internal network addresses
- Use `.env.example` as the configuration template
- When adding new config options, always update the corresponding `.env.example` and documentation

## Documentation Requirements

- New features must include documentation updates:
  - Root `README.md` (public overview)
  - Sub-module README (usage details)
  - Topic-specific docs under `docs/` when necessary

## Pull Request Requirements

PR descriptions must include at minimum:

- Change objective and background
- Impact scope (modules / interfaces / configuration)
- Verification method (commands + results)
- Screenshots if UI changes are involved
