# Repository Guidelines

This file provides essential information for agentic coding agents operating in this repository.

## Project Structure

| Directory | Purpose |
|-----------|---------|
| `apps/miroflow-agent/` | Core agent runtime and configuration |
| `apps/api-server/` | FastAPI independent HTTP API server |
| `apps/gradio-demo/` | Gradio web demo application |
| `apps/collect-trace/` | Trace collection scripts |
| `apps/visualize-trace/` | Trace visualization tools |
| `apps/lobehub-compatibility/` | LobeHub compatibility and parser validation |
| `libs/miroflow-tools/` | Shared tool framework |
| `conf/agent/` | YAML configuration files |
| `assets/` | Brand images and assets |

## Build, Test, and Development Commands

### Repository-level (root)

```bash
just lint          # Lint all files (auto-fixes)
just sort-imports  # Sort imports
just format        # Format code
just format-md     # Format markdown files
just precommit     # Run all precommit checks
```

### Single Application

```bash
# Install dependencies
cd apps/miroflow-agent && uv sync

# Run tests (generates report.html and htmlcov/)
cd apps/miroflow-agent && uv run pytest
cd libs/miroflow-tools && uv run pytest
```

### Running Single Tests

```bash
# Run a specific test file
cd apps/miroflow-agent && uv run pytest tests/test_example.py

# Run a specific test function
cd apps/miroflow-agent && uv run pytest tests/test_example.py::test_function_name

# Run tests matching a pattern
cd apps/miroflow-agent && uv run pytest -k "test_name_pattern"

# Run tests with specific markers (libs/miroflow-tools)
cd libs/miroflow-tools && uv run pytest -m "unit"
cd libs/miroflow-tools && uv run pytest -m "not slow"
```

### Running the Agent Locally

```bash
cd apps/miroflow-agent
uv run python main.py llm=qwen-3 agent=mirothinker_v1.5_keep5_max200 llm.base_url=http://localhost:61002/v1
```

## Code Style Guidelines

### General Rules

- **Python version**: 3.12+
- **Indentation**: 4 spaces (no tabs)
- **Line length**: 88 characters (ruff default)
- **End-of-file**: newline required

### Naming Conventions

| Type | Convention | Example |
|------|------------|---------|
| Modules/Functions | `snake_case` | `my_module.py`, `get_data()` |
| Classes | `CapWords` | `MyClass` |
| Constants | `UPPER_CASE` | `MAX_RETRIES` |
| Private functions | `_snake_case` | `_internal_func()` |

### Imports

- Use `ruff` for import sorting: `just sort-imports`
- Group imports: standard library, third-party, local
- Use absolute imports for package modules
- Avoid wildcard imports (`from x import *`)

```python
# Standard library
import os
from typing import Optional

# Third-party
import requests
from rich import print

# Local
from miroflow_tools.utils import helper
from miroflow_agent.config import Settings
```

### Type Hints

- Use explicit type hints for function parameters and return values
- Use `Optional[X]` instead of `X | None` for compatibility

```python
def process_item(item_id: str, options: Optional[dict] = None) -> list[str]:
    pass
```

### Error Handling

- Catch specific exceptions, avoid bare `except:`
- Use logging for errors, not print statements
- Include context in error messages

```python
try:
    result = api.get_data(item_id)
except requests.RequestException as e:
    logger.error(f"Failed to fetch data for {item_id}: {e}")
    raise APIError(f"Data fetch failed: {e}") from e
```

## Testing Guidelines

### Test Framework

- Framework: `pytest` + `pytest-asyncio`
- Coverage: `pytest-cov`
- Reports: `pytest-html` generates `report.html`
- Parallel execution: `pytest-xdist` (`-n=auto`)

### Test Locations

- Agent tests: `apps/miroflow-agent/tests/`
- Tools tests: `libs/miroflow-tools/src/test/`

### Test Markers (miroflow-tools)

| Marker | Purpose |
|--------|---------|
| `unit` | Unit tests (fast) |
| `integration` | Integration tests (may be slow) |
| `slow` | Slow tests (skip with `-m "not slow"`) |
| `requires_api_key` | Tests requiring real API credentials |

## Security

- **NEVER** commit API keys, tokens, or secrets
- Use `.env` files (gitignored) for secrets
- Copy from `.env.example` and fill in actual values

## Commit Guidelines

- Use imperative mood: "Add feature" not "Added feature"
- Use conventional commit format: `type(scope): description`
- Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`

## Configuration File Locations

- Agent configs: `conf/agent/*.yaml`
- LLM configs: `conf/llm/*.yaml`
- Benchmark configs: `conf/benchmark/*.yaml`
- Runtime settings: `src/config/settings.py`
