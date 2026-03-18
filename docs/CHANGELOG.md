# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## \[Unreleased\]

### Added

- 规划：同服务多 Key 轮转（LLM 与搜索源）
- 规划：模型级 failback（主模型失败自动切换备用模型）
- 规划：结构化冲突检测报告与专项评测集

### Changed

- 路线图改为“已完成前置、未完成后移”的版本化结构

## \[0.1.0\] - 2026-03-18

### Added

- 发布 OpenClaw-MiroSearch 首个可用基线版本（MVP）
- 新增 `run_research_once_v2` 扩展接口（支持 `search_result_num`、`verification_min_search_rounds`）
- 新增研究模式：`production-web`、`verified`、`research`、`balanced`、`quota`、`thinking`
- 新增检索路由：`searxng-first`、`serp-first`、`multi-route`、`parallel`、`parallel-trusted`、`searxng-only`
- 新增并发聚合与置信不足高信源补检能力（`parallel-trusted`）
- 新增 OpenClaw 技能包：`skills/openclaw-mirosearch/`
- 新增独立部署 `compose.yaml`（`app + searxng + valkey`）

### Changed

- 根 `README` 重构为开源项目导向文档，并补齐文档索引
- `docs/API_SPEC.md` 与 Demo 文档对齐当前 API 与参数行为
- Demo 页面支持模式与检索源策略选择，并暴露关键检索参数

### Fixed

- 修复长时间执行下任务可能停留在 `running` 的终态问题（任务终态守卫）
- 增加连续 LLM 失败保护，避免空响应重试导致卡住
