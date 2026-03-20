# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- 规划：同服务多 Key 轮转（LLM 与搜索源）
- 规划：模型级 failback（主模型失败自动切换备用模型）
- 规划：结构化冲突检测报告与专项评测集

## [0.1.5] - 2026-03-20

### Added

- 根 README 默认切换为英文入口，并新增 `README_zh.md` 中文切换文档
- 根 README 新增模型配置说明，补充 `DEFAULT_LLM_PROVIDER` 与分角色模型变量
- OpenClaw 技能文档拆分为安装 / 使用两部分，并补充简单搜索与深度检索分流建议
- 根 README 新增 changelog 摘要区，并链接完整变更记录

### Changed

- 统一整理文档入口，默认面向英文读者，中文文档作为单独切换页
- 技能调用说明从安装文档中剥离，降低安装与使用混淆

## [0.1.4] - 2026-03-20

### Added

- 统一接口 `run_research_once` 新增第 6 个参数：`output_detail_level`（`compact/balanced/detailed`）
- 总结阶段新增“研究报告模式”提示词覆盖策略，按档位约束输出长度与结构密度
- OpenClaw 技能文档与调用脚本支持 `output_detail_level` 参数
- 新增“阶段日志心跳”透传与前端展示：阶段（检索/推理/校验/总结）、回合、检索轮次
- 新增陈旧任务巡检线程：长时间未更新的 `running` 自动收敛为 `failed`

### Changed

- 三档输出语义明确化：`compact=精简` / `balanced=适中` / `detailed=超长报告`
- `detailed` 档默认上调总结与校验 token 上限，提升长文承载能力
- `run_research_once` 的默认渲染策略改为跟随 `output_detail_level`
- Demo 默认检索模式为 `balanced`
- UI 中“最少检索轮次（verified 生效）”默认隐藏，且仅在 `verified` 模式显示并生效
- 文档全面收敛为统一接口描述，移除 demo 文档中的 `run_research_once_v2` 残留

### Fixed

- 修复“详细档仍偏短”的上限约束问题（`summary_max_tokens` 被 `max_tokens` 隐式限制）
- 修复研究类场景被短答案模板约束压缩输出的问题
- 修复浏览器本地搜索历史偶发仅保存标题、未保存结果详情的问题（增加可见结果区兜底采集与分级压缩持久化）

### Security

- Demo 技能包下载入口增加 URL/路径安全约束，限制协议与越权路径访问风险

## [0.1.2] - 2026-03-19

### Changed

- 对外研究接口统一为单端点：`run_research_once`（历史双端点已收敛）
- `run_research_once` 统一采用五参数输入：`query/mode/search_profile/search_result_num/verification_min_search_rounds`
- UI 默认渲染为“综合结果优先 + 过程折叠”，API 默认渲染为“仅综合结果”
- OpenClaw 技能文档与调用脚本更新为统一单接口规范

### Fixed

- 减少 `verified` 多轮检索时中间稿重复暴露导致的多段报告体验问题
- 修正文档中的 `stop_current` 路径为 `/gradio_api/call/stop_current`

## [0.1.1] - 2026-03-19

### Added

- Demo 输入区新增浏览器本地搜索历史（localStorage），支持回填、单条删除、清空
- 新增提示词安全回归测试，防止交叉校验模板出现场景化硬编码污染

### Changed

- 交叉校验与跟进提示词改为通用口径描述，移除特定领域示例词污染
- 主流程与总结/校验模型路由优化，增强高级模型在关键判断环节的介入
- 新增模型路由观测日志（`requested`/`responded`）用于核验真实命中模型

## [0.1.0] - 2026-03-18

### Added

- 发布 OpenClaw-MiroSearch 首个可用基线版本（MVP）
- 新增研究模式：`production-web`、`verified`、`research`、`balanced`、`quota`、`thinking`
- 新增检索路由：`searxng-first`、`serp-first`、`multi-route`、`parallel`、`parallel-trusted`、`searxng-only`
- 新增并发聚合与置信不足高信源补检能力（`parallel-trusted`）
- 新增 OpenClaw 技能包：`skills/openclaw-mirosearch/`
- 新增独立部署 `compose.yaml`（`app + searxng + valkey`）

### Changed

- 根 `README` 重构为开源项目导向文档，并补齐文档索引
- Demo 页面支持模式与检索源策略选择，并暴露关键检索参数

### Fixed

- 修复长时间执行下任务可能停留在 `running` 的终态问题（任务终态守卫）
- 增加连续 LLM 失败保护，避免空响应重试导致卡住
