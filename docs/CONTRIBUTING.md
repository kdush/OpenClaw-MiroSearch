[English](CONTRIBUTING.md) | [中文](CONTRIBUTING_zh.md)

# Contributing Guide

Thank you for contributing to OpenClaw-MiroSearch.

## Development Environment

1. Install Python 3.12+ and `uv`.
1. Clone the repository and install dependencies:

```bash
cd apps/gradio-demo && uv sync
cd ../miroflow-agent && uv sync
cd ../../libs/miroflow-tools && uv sync
```

## Local Validation

Run the relevant checks before submitting a change:

```bash
# Repository root
just format
just lint

# Demo compilation check
cd apps/gradio-demo && uv run python -m py_compile main.py

# Agent tests
cd ../miroflow-agent && uv run pytest

# Shared tool tests
cd ../../libs/miroflow-tools && uv run pytest
```

## Branch and Commit Conventions

- Open pull requests against the `dev` branch unless a maintainer requests otherwise.
- Follow [Conventional Commits](https://www.conventionalcommits.org/) and include a scope: `type(scope): description`.
- Keep frontend and backend changes in separate, single-purpose commits when both are involved.
- Examples:
  - `feat(search): add concurrent retrieval`
  - `docs(readme): restructure deployment guidance`

## Configuration and Security

- Never commit real API keys, secrets, or private network addresses.
- Use `.env.example` as the configuration template.
- When adding a configuration option, update the corresponding `.env.example` and documentation.
- The API fails closed: if `API_TOKENS` is empty and `AUTH_DISABLED != 1`, protected endpoints return `503`.
- Use `AUTH_DISABLED=1` only for local development. Production and shared deployments must configure strong `API_TOKENS`.

## Documentation Requirements

New features must include the relevant documentation updates:

- Root `README.md` for the public overview
- A submodule README for detailed usage
- Topic-specific documentation under `docs/` when needed
- Both English and Chinese counterparts for a paired document

## Pull Request Requirements

A pull request description must include at least:

- The objective and background of the change
- The affected modules, interfaces, and configuration
- Verification commands and results
- Screenshots for user-interface changes

## Governance

### Roles

- **Maintainers:** manage releases, merge pull requests, and advance the roadmap.
- **Contributors:** propose improvements through issues and pull requests.

### Decision Process

1. Record requirements or problems in an issue.
1. Discuss and review solutions in the issue or pull request.
1. Maintainers decide whether to merge based on compatibility, risk, and benefit.
1. Accepted changes enter the changelog and release process.

### Merge Principles

- Breaking changes require documented migration guidance.
- New configuration options must be reflected in `.env.example` and the documentation.
- Code changes must include a minimal, reproducible verification method.

## Support

- Report problems through GitHub Issues and include the branch, commit, runtime mode, redacted environment variables, reproduction steps, and logs.
- Issue templates are available in `.github/ISSUE_TEMPLATE/`.
- The open-source version is community-supported and has no SLA.
- Report security issues privately as described in [`SECURITY.md`](SECURITY.md).

## Release Process

This project follows [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`.

### Pre-release Checklist

1. Confirm the target branch and milestone.
1. Update the root README, relevant submodule READMEs, and API documentation.
1. Update both [`CHANGELOG.md`](CHANGELOG.md) and [`CHANGELOG_zh.md`](CHANGELOG_zh.md).
1. Run the quality checks:

```bash
just format && just lint
cd apps/gradio-demo && uv run python -m py_compile main.py
cd ../miroflow-agent && uv run pytest
cd ../../libs/miroflow-tools && uv run pytest
```

5. Create and push the `v0.x.y` tag only after the release is approved.

### Version Upgrade Guidelines

- `PATCH`: documentation corrections, non-behavioral changes, and low-risk fixes
- `MINOR`: backward-compatible features
- `MAJOR`: breaking changes

### Rollback

- For a critical post-release failure, prefer rolling back to the latest stable tag.
- After rollback, document the root-cause analysis and remediation plan.
