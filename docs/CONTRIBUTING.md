# 贡献指南

感谢你参与 OpenClaw-MiroSearch 的开发与改进。

## 开发环境

1. 安装 Python 3.10+ 与 `uv`
1. 克隆仓库后安装依赖：

```bash
cd apps/gradio-demo && uv sync
cd ../miroflow-agent && uv sync
cd ../../libs/miroflow-tools && uv sync
```

## 本地验证

在提交前建议执行：

```bash
# 仓库根目录
just format
just lint

# Demo 可启动性
cd apps/gradio-demo && uv run python -m py_compile main.py

# Agent 侧
cd ../miroflow-agent && uv run pytest
```

## 分支与提交规范

- 建议基于 `dev` 分支提交 PR
- 提交信息遵循 Conventional Commits，建议带 scope
- 示例：
  - `feat(search): 增加并发检索与置信补检策略`
  - `docs(readme): 重构部署与调用文档`

## 配置与安全

- 不要提交真实 API Key、密钥、内网地址
- 使用 `.env.example` 作为配置模板
- 新增配置项时，务必同步更新对应 `.env.example` 与文档

## 文档要求

- 新增能力必须补充文档：
  - 根 `README.md`（对外概览）
  - 子模块 README（使用细节）
  - 必要时新增 `docs/` 下专题文档

## Pull Request 要求

PR 描述至少包含：

- 变更目标与背景
- 影响范围（模块/接口/配置）
- 验证方式（命令 + 结果）
- 如涉及 UI，请附截图
