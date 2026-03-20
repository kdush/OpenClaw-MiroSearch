# OpenClaw-MiroSearch

<p align="center">
  <img src="assets/mirologo.png" alt="OpenClaw-MiroSearch Logo" width="320" />
</p>

OpenClaw-MiroSearch 是一个面向智能体场景的开源联网检索工程，目标是提供可控成本、可配置路由与可编程调用接口。

> 📄 English version: [README.md](./README.md)

## 项目目标

- 降低检索成本：支持本地 SearXNG 与可选商业搜索源
- 提升结果稳定性：支持并发检索、置信度评估与高信源补检
- 便于系统集成：提供统一 API，便于 OpenClaw 与其他智能体接入

## 上游归属与许可证

本项目基于 [MiroMindAI/MiroThinker](https://github.com/MiroMindAI/MiroThinker) 改造。

- 归属声明：[`docs/OPEN_SOURCE_ATTRIBUTION.md`](docs/OPEN_SOURCE_ATTRIBUTION.md)
- 许可证：[`LICENSE`](LICENSE)

## 已实现功能

- **6 种研究模式**：`production-web` / `verified` / `research` / `balanced`（默认） / `quota` / `thinking`
- **6 种检索路由**：`searxng-first` / `serp-first` / `multi-route` / `parallel` / `parallel-trusted` / `searxng-only`
- **多源检索**：SearXNG、SerpAPI、Serper —— 支持并发聚合与置信度补检
- **统一 API**：`run_research_once` 提供 6 参数全维控制（模式、路由、深度、输出篇幅）
- **运行态可观测**：阶段心跳（检索/推理/校验/总结）、陈旧任务自动收敛

> 完整 API 规格与参数说明请参见 [`docs/API_SPEC.md`](docs/API_SPEC.md)
>
> 架构概览与数据流图请参见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)（[English](docs/ARCHITECTURE_en.md)）

<p align="center"><img src="assets/demo-screenshot.png" alt="Demo Screenshot" width="900" /></p>

## 代码结构

- `apps/gradio-demo/`：Web 入口与 API 服务
- `apps/miroflow-agent/`：Agent 运行与配置
- `libs/miroflow-tools/`：MCP 工具与检索路由实现
- `assets/`：品牌与静态资源
- `skills/openclaw-mirosearch/`：面向 OpenClaw 的调用技能包

## 快速部署

### 1. 安装依赖

```bash
cd apps/gradio-demo
uv sync
```

### 2. 初始化配置

```bash
cp .env.example .env
```

`.env` 最小示例：

```bash
# OpenAI 兼容 LLM 网关
BASE_URL="https://api.longcat.chat/openai"
API_KEY="<your_longcat_key>"
DEFAULT_LLM_PROVIDER="openai" # openai / anthropic / qwen
DEFAULT_MODEL_NAME="gpt-4o-mini"
MODEL_TOOL_NAME="gpt-4o-mini"
MODEL_FAST_NAME="gpt-4o-mini"
MODEL_THINKING_NAME="gpt-4o-mini"
MODEL_SUMMARY_NAME="gpt-4o-mini"

# 搜索源（至少配置一个）
SEARXNG_BASE_URL="http://127.0.0.1:27080"
SERPAPI_API_KEY="<your_serpapi_key>"
SERPER_API_KEY="<your_serper_key>"

# 默认运行策略
DEFAULT_RESEARCH_MODE="balanced"
DEFAULT_SEARCH_PROFILE="parallel-trusted"
```

模型配置说明：

- `DEFAULT_LLM_PROVIDER` 控制 provider 路由（`openai` / `anthropic` / `qwen`）。
- `DEFAULT_MODEL_NAME` 是默认主模型。
- 分角色模型：
  - `MODEL_TOOL_NAME`：工具调用阶段
  - `MODEL_FAST_NAME`：快速阶段
  - `MODEL_THINKING_NAME`：深度思考阶段
  - `MODEL_SUMMARY_NAME`：总结阶段
- 回退规则：
  - 未设置 `MODEL_TOOL_NAME` / `MODEL_FAST_NAME` / `MODEL_THINKING_NAME` 时，回退到 `DEFAULT_MODEL_NAME`
  - 未设置 `MODEL_SUMMARY_NAME` 时，回退到 `MODEL_FAST_NAME`

### 3. 启动服务

```bash
uv run main.py
```

默认地址：`http://127.0.0.1:8080`

### 4. 健康检查

```bash
curl -sS 'http://127.0.0.1:8080/gradio_api/info'
```

## API 调用示例

统一接口（6 参数）：

```bash
BASE_URL="http://127.0.0.1:8080"
QUERY="中国大陆有哪些厂商推出了 OpenClaw 变体？"
MODE="verified"
PROFILE="parallel-trusted"
RESULT_NUM=30
MIN_ROUNDS=4
DETAIL_LEVEL="balanced" # compact / balanced / detailed

EVENT_ID=$(curl -sS -H 'Content-Type: application/json' \
  -d "{\"data\":[\"$QUERY\",\"$MODE\",\"$PROFILE\",$RESULT_NUM,$MIN_ROUNDS,\"$DETAIL_LEVEL\"]}" \
  "$BASE_URL/gradio_api/call/run_research_once" | python3 -c 'import sys,json;print(json.load(sys.stdin)["event_id"])')

curl -sS "$BASE_URL/gradio_api/call/run_research_once/$EVENT_ID"
```

终止当前任务：

```bash
curl -sS -H 'Content-Type: application/json' \
  -d '{"data":[]}' \
  "$BASE_URL/gradio_api/call/stop_current"
```

## 面向 OpenClaw / AI Agent

这个项目的定位：

- 提供可被上层智能体调用的联网研究能力
- 支持模式、路由、检索深度与输出篇幅四维可控
- 通过 SSE 终态事件，保证智能体编排时可判断任务完成

推荐给 AI Agent 的调用闭环：

1. 先调 `GET /gradio_api/info` 探活
1. 发起 `run_research_once`
1. 轮询 `event: complete`
1. 只消费 `complete` 的最终 Markdown

Skill 使用建议（先分流）：

- 简单搜索（快速网页检索、单事实查询）：优先使用 `searxng` skill；链接：<https://clawhub.ai/abk234/searxng>
- 深度检索或高质量检索（多来源交叉、核查、研究报告）：使用 `openclaw-mirosearch` skill

Skill 安装：

- 仓库目录：`skills/openclaw-mirosearch/`
- 打包文件：`skills/openclaw-mirosearch.zip`
- 安装说明：[`skills/openclaw-mirosearch/references/skill-install.md`](skills/openclaw-mirosearch/references/skill-install.md)

Skill 使用：

- 使用说明：[`skills/openclaw-mirosearch/references/usage.md`](skills/openclaw-mirosearch/references/usage.md)
- API 说明：[`skills/openclaw-mirosearch/references/api.md`](skills/openclaw-mirosearch/references/api.md)
- AI Agent 接入详解：[`docs/AI_AGENT_INTEGRATION.md`](docs/AI_AGENT_INTEGRATION.md)

## 建议配置基线

- **默认生产**：`mode=balanced` + `search_profile=parallel-trusted`
- **高风险事实核查**：`mode=verified` + `search_profile=parallel-trusted`
- **额度优先**：`mode=quota` + `search_profile=searxng-only`
- **核查深度**：`search_result_num=30` + `verification_min_search_rounds=4`

> 完整路由环境变量说明请参见 [`apps/miroflow-agent/README.md`](apps/miroflow-agent/README.md) 和 [`docs/API_SPEC.md`](docs/API_SPEC.md)

## 文档索引

- 文档总览：[`docs/README.md`](docs/README.md)
- 架构概览：[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- Docker Compose 独立部署：[`docs/DEPLOY_DOCKER_COMPOSE.md`](docs/DEPLOY_DOCKER_COMPOSE.md)
- Demo 说明：[`apps/gradio-demo/README.md`](apps/gradio-demo/README.md)
- Agent 说明：[`apps/miroflow-agent/README.md`](apps/miroflow-agent/README.md)
- 工具层说明：[`libs/miroflow-tools/README.md`](libs/miroflow-tools/README.md)
- 本地工具部署：[`docs/LOCAL-TOOL-DEPLOYMENT.md`](docs/LOCAL-TOOL-DEPLOYMENT.md)
- OpenClaw 技能包：[`skills/openclaw-mirosearch/SKILL.md`](skills/openclaw-mirosearch/SKILL.md)
- 路线图：[`docs/ROADMAP.md`](docs/ROADMAP.md)
- API 规格：[`docs/API_SPEC.md`](docs/API_SPEC.md)

## 开源协作文档

- 贡献指南：[`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md)
- 安全策略：[`docs/SECURITY.md`](docs/SECURITY.md)
- 行为准则：[`docs/CODE_OF_CONDUCT.md`](docs/CODE_OF_CONDUCT.md)
- 变更记录：[`docs/CHANGELOG.md`](docs/CHANGELOG.md)
- 支持说明：[`docs/SUPPORT.md`](docs/SUPPORT.md)
- 治理说明：[`docs/GOVERNANCE.md`](docs/GOVERNANCE.md)
- 发布流程：[`docs/RELEASE.md`](docs/RELEASE.md)

## 开发校验

```bash
# 仓库根目录
just format
just lint

# Demo 启动
cd apps/gradio-demo && uv sync && uv run main.py

# Agent 侧测试
cd apps/miroflow-agent && uv run pytest
```

## 路线图

路线图详见：[`docs/ROADMAP.md`](docs/ROADMAP.md)

当前规划分为五个阶段：

- `v0.2.0`（生产化）：API 层独立（脱离 Gradio）、认证限流、结果缓存、SearchProvider 协议化、多 Key 轮转、模型 failback、Prometheus 可观测性、异步任务队列
- `v0.2.5`（MCP 标准暴露）：将 `run_research_once` 暴露为标准 MCP tool（stdio + SSE transport），支持 AI IDE 原生接入
- `v0.3.0`（质量增强）：Eval Pipeline CI 化、多源 RRF 融合排序、多语言检索优化、研究结果持久化、结构化冲突检测
- `v1.0.0`（生态分发）：Helm Chart / 一键云部署、技能包版本化发布、兼容矩阵自动验证
