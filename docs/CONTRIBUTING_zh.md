[English](CONTRIBUTING.md) | [中文](CONTRIBUTING_zh.md)

# 贡献指南

感谢你参与谛听（Diting）的开发与改进。

## 开发环境

1. 安装 Python 3.12+ 与 `uv`。
1. 克隆仓库并安装依赖：

```bash
cd apps/gradio-demo && uv sync
cd ../miroflow-agent && uv sync
cd ../../libs/miroflow-tools && uv sync
```

## 本地验证

提交变更前，请运行与改动相关的检查：

```bash
# 仓库根目录
just format
just lint

# Demo 编译检查
cd apps/gradio-demo && uv run python -m py_compile main.py

# Agent 测试
cd ../miroflow-agent && uv run pytest

# 共享工具测试
cd ../../libs/miroflow-tools && uv run pytest
```

## 分支与提交规范

- 除非维护者另有要求，请基于 `dev` 分支提交 Pull Request。
- 提交信息遵循 [Conventional Commits](https://www.conventionalcommits.org/)，并包含 scope：`type(scope): description`。
- 同时涉及前后端时，请将前端与后端变更拆分为职责单一的提交。
- 示例：
  - `feat(search): 增加并发检索`
  - `docs(readme): 重构部署指引`

## 配置与安全

- 不得提交真实 API Key、秘密信息或内网地址。
- 使用 `.env.example` 作为配置模板。
- 新增配置项时，必须同步更新对应的 `.env.example` 与文档。
- API 默认采用 fail-closed 策略：`API_TOKENS` 为空且 `AUTH_DISABLED != 1` 时，受保护端点返回 `503`。
- `AUTH_DISABLED=1` 仅限本地开发使用。生产或共享部署必须配置高强度 `API_TOKENS`。

## 文档要求

新增功能必须同步更新相关文档：

- 根目录 `README.md`：对外概览
- 子模块 README：详细用法
- 必要时在 `docs/` 下新增专题文档
- 配对文档的英文版与中文版

## Pull Request 要求

Pull Request 描述至少包含：

- 变更目标与背景
- 受影响的模块、接口与配置
- 验证命令与结果
- 涉及用户界面变更时的截图

## 治理说明

### 角色

- \*\*维护者：\*\*管理版本发布、合并 Pull Request，并推进路线图。
- \*\*贡献者：\*\*通过 Issue 与 Pull Request 提交改进。

### 决策流程

1. 通过 Issue 记录需求或问题。
1. 在 Issue 或 Pull Request 中讨论并评审方案。
1. 维护者基于兼容性、风险与收益决定是否合并。
1. 已接受的变更进入 Changelog 与版本发布流程。

### 合并原则

- 破坏性改动必须提供迁移指引。
- 新增配置项必须同步到 `.env.example` 与文档。
- 代码变更必须附带最小可复现的验证方式。

## 支持说明

- 请通过 GitHub Issue 提交问题，并附带分支、提交号、运行模式、脱敏后的环境变量、复现步骤与日志。
- Issue 模板位于 `.github/ISSUE_TEMPLATE/`。
- 开源版本以社区协作为主，不提供 SLA 承诺。
- 安全问题请按 [`SECURITY_zh.md`](SECURITY_zh.md) 私下报告。

## 发布流程

本项目遵循[语义化版本](https://semver.org/lang/zh-CN/)：`MAJOR.MINOR.PATCH`。

### 发布前检查清单

1. 确认目标分支与里程碑。
1. 更新根目录 README、相关子模块 README 与 API 文档。
1. 同步更新 [`CHANGELOG.md`](CHANGELOG.md) 与 [`CHANGELOG_zh.md`](CHANGELOG_zh.md)。
1. 运行质量检查：

```bash
just format && just lint
cd apps/gradio-demo && uv run python -m py_compile main.py
cd ../miroflow-agent && uv run pytest
cd ../../libs/miroflow-tools && uv run pytest
```

5. 版本获批后再创建并推送 `v0.x.y` 标签。

### 版本升级指引

- `PATCH`：文档修正、非行为变更与低风险修复
- `MINOR`：向后兼容的新功能
- `MAJOR`：破坏性变更

### 回滚原则

- 发布后出现关键故障时，优先回滚到最近的稳定标签。
- 回滚后记录根因分析与修复计划。
