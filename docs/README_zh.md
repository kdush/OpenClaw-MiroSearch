# 文档索引

[English](README.md) | [中文](README_zh.md)

## 从这里开始

- [项目 README](../README_zh.md)：项目概览和最短可运行路径。
- [部署指南](DEPLOY_zh.md)：Docker Compose、源码安装、鉴权、网络与排错。
- [API 规格](API_SPEC_zh.md)：FastAPI 正式契约与 Gradio 兼容接口。

## 核心参考

- [架构说明](ARCHITECTURE_zh.md)：组件、数据流、持久化和隔离边界。
- [路线图](ROADMAP_zh.md)：当前能力基线与未来里程碑。
- [抓取迭代计划](SCRAPING_ITERATION_PLAN_zh.md)：已发布的 T1-T8 与待实现的 T9 批量抓取。
- [变更记录](CHANGELOG_zh.md)：按版本记录已发布变更。

## 运维与治理

- [安全策略](SECURITY_zh.md)
- [贡献指南](CONTRIBUTING_zh.md)
- [行为准则](CODE_OF_CONDUCT_zh.md)

## 模块文档

- [FastAPI 服务](../apps/api-server/README_zh.md)
- [Gradio Demo](../apps/gradio-demo/README.md)
- [Agent 核心](../apps/miroflow-agent/README.md)
- [MiroFlow 工具库](../libs/miroflow-tools/README.md)
- [部署资产](../deploy/README.md)
- [Trace 采集](../apps/collect-trace/README.md)
- [Trace 可视化](../apps/visualize-trace/README.md)
- [LobeHub 兼容](../apps/lobehub-compatibility/README.md)

## 集成包

- [Diting Skill](../skills/diting/SKILL.md)
- [SearXNG Skill](../skills/searxng/SKILL.md)

## 历史设计记录

`plans/` 与 `superpowers/` 下的文件记录特定时间点的实施决策，不属于当前产品文档。若其内容与代码或上述文档冲突，以当前代码和 API 规格为准。
