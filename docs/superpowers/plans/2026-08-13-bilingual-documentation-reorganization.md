# 中英文文档梳理实施计划（第一阶段：核心文档）

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）跟踪进度。本计划不执行 Git commit；仓库约定要求用户明确授权后才能提交。

**目标：** 依据 `v0.2.11` 当前代码、配置与测试，先完成根 README、核心 `docs/` 文档和 `apps/api-server/README*` 的中英文重组，并消除这些文档中的过期版本、接口和鉴权说明。其余模块 README 与 Skill 文档留到后续阶段继续处理。

**架构：** 保留现有英文路径作为稳定入口，为第一阶段范围内的中文文档增加 `_zh.md` 配对；根 README、核心文档和 API Server README 分层维护。API、部署、版本历史和未来规划分别以 API Spec、Deploy、Changelog 和 Roadmap 为单一事实入口，其他文档只保留摘要和链接。模块 README 与 Skill references 的完整配对作为后续阶段延续同一规则。

**技术栈：** Markdown、Mermaid、mdformat 0.7.17、ripgrep、Git 差异检查。

## 当前阶段边界

第一阶段收口以下内容：

- 根 `README.md`、`README_zh.md`。
- `docs/` 下 README、API、架构、部署、Roadmap、安全、贡献、行为准则、抓取计划和 Changelog 的中英文配对。
- `apps/api-server/README.md` 与 `apps/api-server/README_zh.md`。

以下内容属于后续阶段，不作为本轮验收前置条件：

- `apps/collect-trace/`、`apps/gradio-demo/`、`apps/lobehub-compatibility/`、`apps/miroflow-agent/`、`apps/visualize-trace/`、`deploy/`、`libs/miroflow-tools/` 的中文 README。
- `skills/*/SKILL.md` 与 `skills/openclaw-mirosearch/references/` 的双语梳理。

在后续中文 README 尚未创建前，中文文档索引直接链接现有英文 README，避免产生失效链接。

---

## 文件结构

### 核心入口

- 修改：`README.md`、`README_zh.md`——对称的项目概览、快速开始和文档索引。
- 修改：`docs/README.md`——英文文档索引。
- 创建：`docs/README_zh.md`——中文文档索引。

### 核心技术文档

- 修改/创建：`docs/API_SPEC.md`、`docs/API_SPEC_zh.md`——FastAPI 主契约与 Gradio 兼容契约。
- 修改/创建：`docs/ARCHITECTURE.md`、`docs/ARCHITECTURE_zh.md`——架构、数据流和部署拓扑。
- 修改/创建：`docs/DEPLOY.md`、`docs/DEPLOY_zh.md`——部署、鉴权、网络策略与排错。
- 修改/创建：`docs/ROADMAP.md`、`docs/ROADMAP_zh.md`——当前基线与未来里程碑。

### 治理、专项与历史

- 修改/创建：`docs/SECURITY.md`、`docs/SECURITY_zh.md`。
- 修改/创建：`docs/CONTRIBUTING.md`、`docs/CONTRIBUTING_zh.md`。
- 修改/创建：`docs/CODE_OF_CONDUCT.md`、`docs/CODE_OF_CONDUCT_zh.md`。
- 修改/创建：`docs/SCRAPING_ITERATION_PLAN.md`、`docs/SCRAPING_ITERATION_PLAN_zh.md`。
- 修改/创建：`docs/CHANGELOG.md`、`docs/CHANGELOG_zh.md`。

### 模块 README

- 修改/创建：`apps/api-server/README.md`、`apps/api-server/README_zh.md`。
- 后续阶段：
- 修改/创建：`apps/collect-trace/README.md`、`apps/collect-trace/README_zh.md`。
- 修改/创建：`apps/gradio-demo/README.md`、`apps/gradio-demo/README_zh.md`。
- 修改/创建：`apps/lobehub-compatibility/README.md`、`apps/lobehub-compatibility/README_zh.md`。
- 修改/创建：`apps/miroflow-agent/README.md`、`apps/miroflow-agent/README_zh.md`。
- 修改/创建：`apps/visualize-trace/README.md`、`apps/visualize-trace/README_zh.md`。
- 修改/创建：`deploy/README.md`、`deploy/README_zh.md`。
- 修改/创建：`libs/miroflow-tools/README.md`、`libs/miroflow-tools/README_zh.md`。

### Skill 文档

- 后续阶段：
- 修改：`skills/openclaw-mirosearch/SKILL.md`——固定发现入口，更新到 `v0.2.11`。
- 修改/创建：`skills/openclaw-mirosearch/references/*.md` 及对应 `_zh.md`。
- 修改：`skills/searxng/SKILL.md`——紧凑双语入口和选择边界。

---

### 任务 1：锁定当前事实基线

**文件：**

- 读取：`apps/api-server/models.py`
- 读取：`apps/api-server/routers/research.py`
- 读取：`apps/api-server/routers/metrics.py`
- 读取：`apps/api-server/settings.py`
- 读取：`apps/api-server/services/profile_resolver.py`
- 读取：`compose.yaml`
- 读取：`apps/gradio-demo/main.py`
- 读取：`docs/CHANGELOG.md`

- [ ] **步骤 1：核对 FastAPI 路由和响应模型**

```bash
rg -n '@router\.(get|post)|class Research(Request|Response|TaskStatusResponse)|class ResultQuality' apps/api-server
```

预期：确认提交、状态、SSE、单任务取消、按 `caller_id` 取消、最近指标与健康检查端点。

- [ ] **步骤 2：核对参数枚举和有效默认值**

```bash
rg -n 'VALID_|DEFAULT_|resolve_effective_research_params|AUTH_DISABLED|API_TOKENS' apps/api-server apps/gradio-demo compose.yaml
```

预期：记录 6 种 `mode`、6 种 `search_profile`、3 种篇幅、10/20/30 检索条数、1-8 校验轮次及部署默认值。

- [ ] **步骤 3：核对部署拓扑和端口**

```bash
rg -n '^  (app|api|api-worker|valkey|searxng):|ports:|healthcheck:' compose.yaml
```

预期：确认 5 个服务及 8080、8090、27080 等宿主机默认端口。

- [ ] **步骤 4：建立过期表述扫描基线**

```bash
rg -n -g '*.md' 'v0\.2\.2|当前.*v0\.2\.5|留空跳过|No \\boxed|stdio \+ SSE|Prometheus.*已|RRF.*已' README*.md docs apps libs skills
```

预期：列出现有过期位置；Changelog 中明确属于历史记录的表述允许保留。

### 任务 2：整理根 README 与文档索引

**文件：**

- 修改：`README.md`
- 修改：`README_zh.md`
- 修改：`docs/README.md`
- 创建：`docs/README_zh.md`

- [ ] **步骤 1：重写英文根 README**

保留项目定位、已实现能力、5 服务快速启动、FastAPI 最小调用、文档索引、开发验证和 Roadmap 摘要；历史版本亮点统一链接到 Changelog。

- [ ] **步骤 2：按相同结构重写中文根 README**

确保标题顺序、命令、表格行和链接与英文版对应，中文使用自然表达和规范的中英混排空格。

- [ ] **步骤 3：建立双语文档索引**

按“开始使用、核心参考、运维治理、模块文档、历史设计”分类。

- [ ] **步骤 4：验证根入口结构**

```bash
rg -n '^#{1,3} ' README.md README_zh.md docs/README.md docs/README_zh.md
```

预期：每对文件标题顺序可对应，顶部存在语言切换链接。

### 任务 3：重构 API、架构、部署与 Roadmap

**文件：**

- 修改/创建：`docs/API_SPEC.md`、`docs/API_SPEC_zh.md`
- 修改/创建：`docs/ARCHITECTURE.md`、`docs/ARCHITECTURE_zh.md`
- 修改/创建：`docs/DEPLOY.md`、`docs/DEPLOY_zh.md`
- 修改/创建：`docs/ROADMAP.md`、`docs/ROADMAP_zh.md`

- [ ] **步骤 1：重写 API Spec 双语对**

以 FastAPI 为主，准确记录请求省略字段语义、提交响应、缓存状态、任务快照、`result_quality`、SSE、取消、指标、健康检查、限流和 fail-closed 鉴权；Gradio 放在兼容章节。

- [ ] **步骤 2：拆分架构文档双语对**

补入 FastAPI、arq Worker、Valkey、SSE、结果缓存和每任务 ToolManager 隔离的当前拓扑。

- [ ] **步骤 3：重写部署文档双语对**

统一 Compose 命令、服务名、端口、鉴权模式、网络策略、健康检查和排错；删除不存在的示例文件或过期服务名引用。

- [ ] **步骤 4：拆分 Roadmap 双语对**

共同保留 `v0.2.11` 当前基线、`v0.3.0` 抓取、`v0.4.0` 评测、`v0.5.0` MCP 和 `v1.0.0` 生态里程碑。

- [ ] **步骤 5：核对核心文档的端点与规划状态**

```bash
rg -n '/v1/research|/v1/metrics/last|/health|Streamable HTTP|scrape_urls|Prometheus' docs/API_SPEC*.md docs/ARCHITECTURE*.md docs/DEPLOY*.md docs/ROADMAP*.md
```

预期：已实现能力只出现在现状章节，未实现能力只出现在 Roadmap。

### 任务 4：配对治理、抓取计划与 Changelog

**文件：**

- 修改/创建：`docs/SECURITY.md`、`docs/SECURITY_zh.md`
- 修改/创建：`docs/CONTRIBUTING.md`、`docs/CONTRIBUTING_zh.md`
- 修改/创建：`docs/CODE_OF_CONDUCT.md`、`docs/CODE_OF_CONDUCT_zh.md`
- 修改/创建：`docs/SCRAPING_ITERATION_PLAN.md`、`docs/SCRAPING_ITERATION_PLAN_zh.md`
- 修改/创建：`docs/CHANGELOG.md`、`docs/CHANGELOG_zh.md`

- [ ] **步骤 1：拆分治理文档**

保持支持范围、漏洞报告、安全默认值、开发命令、提交规范、发布流程和行为规则语义一致。

- [ ] **步骤 2：拆分抓取专项计划**

共同标记 T1-T8 已完成、T9 Pending；区分抓取专项已交付版本与当前仓库版本。

- [ ] **步骤 3：生成完整双语 Changelog**

保留相同版本顺序、发布日期、章节类别、条目数量和链接；历史事实不改写为当前行为。

- [ ] **步骤 4：验证版本对齐**

```bash
diff <(rg '^## \\[' docs/CHANGELOG.md | sed 's/ - .*//') <(rg '^## \\[' docs/CHANGELOG_zh.md | sed 's/ - .*//')
```

预期：无输出。

### 后续阶段任务 5：梳理应用、部署与库 README

**文件：** 第一阶段已完成 API Server README；其余 7 对文件在后续阶段执行。

- [ ] **步骤 1：整理 API、Demo 与 Agent README**

每对文件保留模块定位、目录边界、本地运行、环境变量入口、测试命令和核心文档链接；详细 API 指向 `docs/API_SPEC*.md`。

- [ ] **步骤 2：整理 Trace 与 LobeHub README**

将现有纯英文说明配成中文版本；核对实际脚本名、依赖安装命令和图片路径。

- [ ] **步骤 3：整理部署与工具库 README**

部署 README 聚焦镜像构建和 Compose 入口；工具库 README 保留 MCP Server 清单、ToolManager 用法和测试入口。

- [ ] **步骤 4：验证模块配对**

```bash
for file in apps/*/README.md deploy/README.md libs/*/README.md; do test -f "${file%.md}_zh.md" || exit 1; done
```

预期：退出码 0。

### 后续阶段任务 6：更新 Skill 与 references

**文件：**

- 修改：`skills/openclaw-mirosearch/SKILL.md`
- 修改/创建：`skills/openclaw-mirosearch/references/*.md` 及对应 `_zh.md`
- 修改：`skills/searxng/SKILL.md`

- [ ] **步骤 1：更新 Skill 发现入口**

服务对齐版本更新为 `v0.2.11`；删除 `No \\boxed{}` 必然失败和 `API_TOKENS` 留空跳过鉴权等失效约定。

- [ ] **步骤 2：压缩并配对 references**

API 字段表链接核心 API Spec，references 聚焦 Agent 调用闭环、模式选择、安装与降级策略。

- [ ] **步骤 3：更新 SearXNG Skill 入口**

保留发现需要的 frontmatter 与命令，补充中英文简要说明和与深度研究 Skill 的选择边界。

- [ ] **步骤 4：扫描 Skill 过期表述**

```bash
rg -n 'v0\.2\.2|留空跳过|No \\boxed|result.*cached.*提交响应' skills
```

预期：无当前约定命中；仅允许明确标注为历史背景的内容。

### 任务 7：执行第一阶段文档验证

**文件：** 第一阶段创建或修改的 Markdown 文件。

- [ ] **步骤 1：运行 Markdown 格式检查**

```bash
uv tool run mdformat@0.7.17 --check README.md README_zh.md docs/*.md apps/api-server/README*.md
```

预期：退出码 0。

- [ ] **步骤 2：运行 Git 差异检查**

```bash
git diff --check
```

预期：退出码 0。

- [ ] **步骤 3：验证语言配对**

```bash
for file in docs/{README,API_SPEC,ARCHITECTURE,DEPLOY,ROADMAP,SECURITY,CONTRIBUTING,CODE_OF_CONDUCT,SCRAPING_ITERATION_PLAN,CHANGELOG}.md; do test -f "${file%.md}_zh.md" || exit 1; done
```

预期：退出码 0。

- [ ] **步骤 4：验证相对链接**

扫描本次范围 Markdown 的相对链接，忽略 HTTP(S)、锚点、data URL 与历史计划目录；每个相对目标必须存在。

预期：0 个失效目标。

- [ ] **步骤 5：扫描版本和过期事实**

```bash
rg -n -g '*.md' '当前.*v0\.2\.[0-9]|current.*v0\.2\.[0-9]|留空跳过认证|stdio \+ SSE transport' README*.md docs apps libs skills
```

预期：当前版本只允许 `v0.2.11`；不存在已知过期鉴权和 MCP transport 表述。

- [ ] **步骤 6：复核最终差异与仓库状态**

```bash
git diff --stat
git status --short
```

预期：只包含第一阶段文档和本轮规格/计划文件；不包含运行时代码、配置或自动提交。模块 README 与 Skill 的后续阶段改动不作为本轮完成条件。
