# 发布流程

本项目采用语义化版本（SemVer）：`MAJOR.MINOR.PATCH`。

## 发布前检查清单

1. 分支与变更确认

- 确认目标分支与里程碑一致
- 确认文档已更新（README / 子模块 README / API 规格）
- 确认 `docs/ROADMAP.md` 与 `docs/CHANGELOG.md` 已对应目标版本

1. 质量校验

```bash
just format
just lint
cd apps/gradio-demo && uv run python -m py_compile main.py
cd ../miroflow-agent && uv run pytest
```

1. 变更记录

- 更新 [`CHANGELOG.md`](CHANGELOG.md) 的 `Unreleased` 或新增版本节
- 标注新增能力、行为变更、兼容性影响

1. 发布动作（示例）

- 打标签：`v0.x.y`
- 推送标签并创建 GitHub Release

## `v0.1.0` 建议发布口径

- 版本类型：`MINOR`（首个可用基线）
- 发布范围：
  - API 与调用文档定版
  - 多模式与多路路由可用
  - `parallel-trusted` 交叉校验链路可用
  - Compose 独立部署可用
- 已知不纳入：
  - 多 Key 轮转
  - 模型级 failback
  - 完整观测面板

## 版本升级建议

- `PATCH`：文档修复、非行为变更、低风险修复
- `MINOR`：向后兼容的新功能（新模式/新配置）
- `MAJOR`：破坏性变更（接口、默认行为、配置格式变更）

## 回滚原则

- 若发布后出现关键故障，优先回滚到最近稳定标签
- 回滚后补充 RCA（问题根因）与修复计划
