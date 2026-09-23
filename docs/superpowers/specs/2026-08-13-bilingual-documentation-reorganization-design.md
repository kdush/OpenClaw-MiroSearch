# 中英文文档梳理设计（分阶段实施）

## 1. 背景

当前仓库的正式文档同时存在以下组织方式：

- 根目录使用 `README.md` 与 `README_zh.md` 分离中英文。
- `docs/ARCHITECTURE.md`、`docs/DEPLOY.md` 等文件在同一文档中混排中英文。
- `docs/API_SPEC.md`、`docs/ROADMAP.md` 等文件只有中文。
- 应用、库与 Skill 文档的主要语言不一致。
- 部分文档仍引用 `v0.2.2`、`v0.2.5` 等过期现状，或把规划能力描述为已完成。
- FastAPI、Gradio、Skill 和部署文档重复维护同一接口说明，内容已经出现差异。

本次整理以当前代码、配置、自动化测试和 `v0.2.11` 发布记录为事实依据，分阶段将文档重组为可持续维护的中英文配对体系。第一阶段优先收口根 README、核心 `docs/` 文档和 API Server README；其余模块 README 与 Skill 文档后续继续处理。

## 2. 目标

1. 所有核心对外文档均提供完整英文版和中文版。
2. 英文与中文文档保持相同的信息架构、接口契约和示例语义。
3. 文档只描述当前已经实现的能力；未实现能力统一进入 Roadmap。
4. FastAPI 是正式 Agent API，Gradio API 明确标注为兼容接口。
5. 降低版本信息、API 参数、默认值和部署拓扑在多处重复导致的漂移。
6. 保持现有英文文档路径稳定，尽量避免破坏外部链接。

## 3. 非目标

- 不修改任何运行时代码、配置或 API 行为。
- 不重写 `docs/plans/` 与 `docs/superpowers/` 中的历史计划和设计记录。
- 不整理 `.windsurf/` 中的内部技能文档。
- 不删除 Changelog 中的历史版本内容。
- 不在本次工作中实现 Roadmap 中的 MCP Server、批量抓取或可观测性能力。
- 不执行 Git commit、push 或创建 PR。
- 第一阶段不要求补齐除 `apps/api-server/` 之外的模块中文 README，也不要求完成 Skill 文档重构。

## 3.1 分阶段范围

第一阶段包含：

- `README.md`、`README_zh.md`。
- 第 4.1 节列出的全部核心文档。
- `apps/api-server/README.md`、`apps/api-server/README_zh.md`。

后续阶段包含：

- 第 4.2 节中除 `apps/api-server/` 外的 7 组模块 README 配对。
- 第 4.3 节中的 Skill 与 references 整理。

后续阶段文件尚未创建时，中文索引允许链接现有英文 README，前提是链接目标真实存在。

## 4. 文档范围

### 4.1 核心文档

以下文档建立一一对应的英文版和中文版：

| 英文入口 | 中文入口 | 主要职责 |
|---|---|---|
| `README.md` | `README_zh.md` | 项目概览、快速开始、文档入口 |
| `docs/README.md` | `docs/README_zh.md` | 文档导航 |
| `docs/ARCHITECTURE.md` | `docs/ARCHITECTURE_zh.md` | 架构、模块职责、数据流、部署拓扑 |
| `docs/API_SPEC.md` | `docs/API_SPEC_zh.md` | FastAPI 主契约、Gradio 兼容契约 |
| `docs/DEPLOY.md` | `docs/DEPLOY_zh.md` | 部署、鉴权、网络策略、验证与排错 |
| `docs/ROADMAP.md` | `docs/ROADMAP_zh.md` | 已发布基线与未来里程碑 |
| `docs/SECURITY.md` | `docs/SECURITY_zh.md` | 支持范围、安全基线与漏洞报告 |
| `docs/CONTRIBUTING.md` | `docs/CONTRIBUTING_zh.md` | 开发、测试、提交、发布与治理 |
| `docs/CODE_OF_CONDUCT.md` | `docs/CODE_OF_CONDUCT_zh.md` | 社区行为准则 |
| `docs/SCRAPING_ITERATION_PLAN.md` | `docs/SCRAPING_ITERATION_PLAN_zh.md` | 抓取专项状态和 T9 计划 |
| `docs/CHANGELOG.md` | `docs/CHANGELOG_zh.md` | 完整版本历史 |

英文文件沿用现有无后缀路径，中文文件统一使用 `_zh.md`。每对文件开头添加 `English | 中文` 切换链接。

Changelog 也保持完整语言配对，不以摘要替代历史内容。后续发布必须在同一次变更中更新两个文件；`docs/CHANGELOG.md` 保留 Keep a Changelog 的英文结构，`docs/CHANGELOG_zh.md` 保留同样的版本、日期、条目数量和链接目标。

### 4.2 应用与库文档

第一阶段建立以下配对：

- `apps/api-server/`

后续阶段再为以下目录建立 `README.md` 与 `README_zh.md` 配对：

- `apps/collect-trace/`
- `apps/gradio-demo/`
- `apps/lobehub-compatibility/`
- `apps/miroflow-agent/`
- `apps/visualize-trace/`
- `deploy/`
- `libs/miroflow-tools/`

各 README 在两种语言中完整覆盖本模块职责、安装、运行、配置、测试和相关上层文档，但不复制完整 API 或部署手册。

### 4.3 Skill 文档

Skill 文档在后续阶段处理。`skills/*/SKILL.md` 是工具发现入口，保留固定文件名，不拆分为 `SKILL_zh.md`。其处理规则为：

- `SKILL.md` 保留紧凑、可执行的双语约定，避免破坏 Skill 发现机制。
- `skills/openclaw-mirosearch/references/*.md` 以英文为默认文件，新增 `_zh.md` 中文配对。
- 修正 `v0.2.2` 对齐版本、鉴权说明、缓存响应和失败判断等过期内容。
- Skill 的详细 API 说明链接到 `docs/API_SPEC*.md`，references 只保留调用工作流和 Agent 特有建议。

## 5. 信息架构

### 5.1 根 README

根 README 只保留：

1. 项目定位与已实现能力摘要。
2. 当前稳定版本。
3. Docker Compose 最短启动路径。
4. FastAPI 最小调用示例。
5. 文档索引。
6. 开发验证入口。
7. Roadmap 摘要。

版本亮点的详细历史从根 README 移除，统一链接到 Changelog，避免每次发布同时维护多份版本清单。

### 5.2 API 文档

API 文档按以下顺序组织：

1. 当前版本与接口定位。
2. FastAPI 基础地址与认证。
3. 请求模型和有效默认值。
4. 提交任务、查询状态、SSE、取消、指标、健康检查。
5. 状态机、事件与 `result_quality`。
6. 错误码、限流、重试和安全注意事项。
7. Gradio 兼容接口。
8. Agent 调用建议。

FastAPI 参数和默认值直接以 `apps/api-server/models.py`、`settings.py` 与 `profile_resolver.py` 为准。Gradio 参数顺序以实际 `api_name` 绑定和测试为准。

### 5.3 部署文档

部署文档以当前 Compose 拓扑为主：

- `app`：Gradio UI，默认端口 8080。
- `api`：FastAPI，默认端口 8090。
- `api-worker`：arq Worker。
- `valkey`：任务、事件与缓存存储。
- `searxng`：搜索服务，默认宿主机端口 27080。

文档明确区分：

- 本地开发：`AUTH_DISABLED=1`。
- 生产或共享环境：`AUTH_DISABLED=0` 且配置 `API_TOKENS`。
- `API_TOKENS` 留空不再表述为自动跳过认证。
- `.env.compose.example` 与 `.env.example` 中的占位值不能当作生产默认值。

### 5.4 Roadmap 与 Changelog

- Changelog 只记录已经发布的事实。
- Roadmap 的“已发布”部分只保留当前能力基线与关键里程碑，不重复完整 Changelog。
- 未实现能力只出现在后续里程碑中。
- README 只展示 Roadmap 的一行式摘要。

## 6. 事实源与冲突处理

文档内容发生冲突时，按以下优先级裁决：

1. 当前代码、配置 Schema 和路由注册。
2. 覆盖该行为的自动化测试。
3. 当前 Compose 文件和 Dockerfile。
4. `v0.2.11` Changelog。
5. Roadmap。
6. 历史 README、Skill references 和实现计划。

若代码存在多个默认值层级，文档必须区分“请求省略时的部署有效默认值”和“模块内部后备默认值”，不得只抄写 Pydantic 字段展示值。

## 7. 中英文同步规则

1. 每对文档的一级至三级标题顺序一致。
2. 表格行、参数、枚举、默认值、命令和代码块保持语义一致。
3. 文件路径、环境变量、端点、JSON 字段和状态枚举不翻译。
4. 中文版遵循中英文之间留空格、中文全角标点和技术术语保留规则。
5. 英文版使用自然英文，不逐句机械直译。
6. 修改核心文档时，PR 检查必须同时包含对应语言文件。
7. 文档顶部语言切换链接必须指向真实存在的配对文件。
8. Changelog 两种语言的版本标题、日期和每个版本下的条目数量必须一致。

## 8. 去重规则

- API 的完整请求和响应模型只在 API Spec 中维护。
- Docker Compose 的完整部署流程只在 Deploy 中维护。
- 模块 README 使用链接引用上层文档，不复制长篇说明。
- Skill references 可保留面向 Agent 的最小可复制命令，但不复制完整字段表。
- 发布历史只在 Changelog 中维护。
- 未来功能只在 Roadmap 和专项计划中维护。

## 9. 迁移顺序

第一阶段：

1. 生成文档配对清单和事实审计清单。
2. 更新根 README，并建立文档索引配对。
3. 重构 API、架构、部署和 Roadmap 四组核心文档。
4. 配对安全、贡献、行为准则、抓取计划和 Changelog。
5. 完成 API Server README 配对，并更新第一阶段范围内的内部链接。
6. 执行格式、链接、配对和内容一致性检查。

后续阶段：

1. 梳理其余应用、部署与库 README。
2. 更新 Skill 入口与 references。
3. 补齐后续新增中文入口并重新执行全量验证。

迁移期间不删除现有英文路径；原先的中英混排文件直接收敛为英文版，并新增中文配对文件。

## 10. 验证方案

### 10.1 自动检查

- `mdformat --check` 验证第一阶段涉及的 Markdown 文件。
- `git diff --check` 验证空白和冲突标记。
- 相对链接扫描验证目标文件及锚点存在。
- 配对扫描验证核心英文文件和 API Server README 均存在对应 `_zh.md`。
- 标题扫描验证每对文件的结构可对应。
- 版本扫描禁止将低于 `v0.2.11` 的版本标为当前版本。
- 端点扫描核对 FastAPI 路由与文档端点一致。
- 过期表述扫描，包括：
  - `API_TOKENS` 留空自动跳过认证。
  - FastAPI 缓存命中直接在提交响应返回 `result`。
  - `No \\boxed{}` 一定代表失败。
  - Prometheus、RRF、Eval CI 或 MCP Server 已实现。

### 10.2 人工核对

- 从中英文 README 各自完成一次“启动 → 健康检查 → 提交任务 → 查询结果”阅读演练。
- 从文档索引进入每个核心文档，确认语言切换和返回路径清晰。
- 抽查 API、部署、Roadmap 和 Skill 四组文档的中英文表格与命令一致。

## 11. 验收标准

1. 核心文档清单全部存在英文和中文配对。
2. API Server README 存在语言配对；其他应用、部署与库 README 的语言配对列入后续阶段。
3. `docs/` 的正式文档不存在同一正文重复两种语言的情况。
4. FastAPI 端点、请求约束、状态、鉴权和缓存语义与代码一致。
5. 根 README 不再维护重复的历史版本亮点。
6. Skill 的版本和错误判断清理列入后续阶段，不作为第一阶段验收条件。
7. 所有新增和修改文档通过自动检查。
8. 不修改历史计划，不执行 Git commit。
