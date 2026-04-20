# Contributing Guide / 贡献指南

Thank you for contributing to OpenClaw-MiroSearch.

感谢你参与 OpenClaw-MiroSearch 的开发与改进。

## Development Environment / 开发环境

1. Install Python 3.10+ and `uv` / 安装 Python 3.10+ 与 `uv`
2. Clone the repository and install dependencies / 克隆仓库后安装依赖：

```bash
cd apps/gradio-demo && uv sync
cd ../miroflow-agent && uv sync
cd ../../libs/miroflow-tools && uv sync
```

## Local Validation / 本地验证

Before submitting, run / 在提交前建议执行：

```bash
# Repository root / 仓库根目录
just format
just lint

# Demo compilation check / Demo 可启动性
cd apps/gradio-demo && uv run python -m py_compile main.py

# Agent tests / Agent 侧
cd ../miroflow-agent && uv run pytest
```

## Branch & Commit Conventions / 分支与提交规范

- Submit PRs based on the `dev` branch / 建议基于 `dev` 分支提交 PR
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/), with scope recommended / 提交信息遵循 Conventional Commits，建议带 scope
- Examples / 示例：
  - `feat(search): add concurrent retrieval / 增加并发检索与置信补检策略`
  - `docs(readme): restructure deployment docs / 重构部署与调用文档`

## Configuration & Security / 配置与安全

- Never commit real API keys, secrets, or internal network addresses / 不要提交真实 API Key、密钥、内网地址
- Use `.env.example` as the configuration template / 使用 `.env.example` 作为配置模板
- When adding new config options, always update the corresponding `.env.example` and documentation / 新增配置项时，务必同步更新对应 `.env.example` 与文档

## Documentation Requirements / 文档要求

- New features must include documentation updates / 新增能力必须补充文档：
  - Root `README.md` (public overview) / 对外概览
  - Sub-module README (usage details) / 使用细节
  - Topic-specific docs under `docs/` when necessary / 必要时新增 `docs/` 下专题文档

## Pull Request Requirements / Pull Request 要求

PR descriptions must include at minimum / PR 描述至少包含：

- Change objective and background / 变更目标与背景
- Impact scope (modules / interfaces / configuration) / 影响范围（模块/接口/配置）
- Verification method (commands + results) / 验证方式（命令 + 结果）
- Screenshots if UI changes are involved / 如涉及 UI，请附截图

## Governance / 治理说明

### Roles / 角色

- **Maintainers**: version release, PR merge, roadmap progress / 维护者：版本发布、PR 合并、路线图推进
- **Contributors**: submit improvements via Issue / PR / 贡献者：通过 Issue / PR 提交改进

### Decision Process / 决策流程

1. Requirements or issues recorded via Issue / 需求或问题通过 Issue 记录
2. Solutions discussed and reviewed in Issue / PR / 方案在 Issue / PR 中讨论并评审
3. Maintainers make merge decisions based on compatibility, risk, and benefit / 维护者基于兼容性、风险与收益做合并决策
4. Changes enter CHANGELOG and release process / 变更进入 CHANGELOG 与版本发布流程

### Merge Principles / 合并原则

- Breaking changes require migration path documentation / 破坏性改动需提前说明迁移路径
- New config options must sync `.env.example` and docs / 新增配置项必须同步 `.env.example` 与文档
- Code changes must include minimal reproducible verification / 代码变更必须附最小可复现验证

## Support / 支持说明

- Submit issues via GitHub Issue, including: branch, commit, runtime mode, env vars (redacted), reproduction steps, logs / 请通过 GitHub Issue 提交问题，并附带：分支与提交号、运行方式、环境变量（脱敏）、复现步骤、报错日志
- Issue templates: `.github/ISSUE_TEMPLATE/` / Issue 模板
- No SLA guarantee for the open-source version / 开源版本以社区协作为主，不提供 SLA 承诺
- Security issues: report privately per [`SECURITY.md`](SECURITY.md) / 安全问题请按 [`SECURITY.md`](SECURITY.md) 私下报告

## Release Process / 发布流程

This project follows [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH` / 本项目采用语义化版本（SemVer）

### Pre-release Checklist / 发布前检查清单

1. Confirm target branch and milestone / 确认目标分支与里程碑一致
2. Update docs (README / sub-module README / API spec) / 确认文档已更新
3. Update [`CHANGELOG.md`](CHANGELOG.md) / 更新变更记录
4. Run quality checks / 质量校验：

```bash
just format && just lint
cd apps/gradio-demo && uv run python -m py_compile main.py
cd ../miroflow-agent && uv run pytest
```

5. Tag and push: `v0.x.y` / 打标签并推送

### Version Upgrade Guidelines / 版本升级建议

- `PATCH`: doc fixes, non-behavioral changes, low-risk fixes / 文档修复、非行为变更、低风险修复
- `MINOR`: backward-compatible new features / 向后兼容的新功能
- `MAJOR`: breaking changes / 破坏性变更

### Rollback / 回滚原则

- Roll back to the latest stable tag on critical failure / 若发布后出现关键故障，优先回滚到最近稳定标签
- Post-rollback: add RCA and fix plan / 回滚后补充问题根因与修复计划
