# OpenClaw-MiroSearch

<p align="center">
  <img src="assets/mirologo.png" alt="OpenClaw-MiroSearch Logo" width="320" />
</p>

OpenClaw-MiroSearch 是一个面向智能体场景的开源联网检索工程，目标是提供可控成本、可配置路由与可编程调用接口。

## 项目目标

- 降低检索成本：支持本地 SearXNG 与可选商业搜索源
- 提升结果稳定性：支持并发检索、置信度评估与高信源补检
- 便于系统集成：提供统一 API，便于 OpenClaw 与其他智能体接入

## 上游归属与许可证

本项目基于 [MiroMindAI/MiroThinker](https://github.com/MiroMindAI/MiroThinker) 改造。

- 归属声明：[`docs/OPEN_SOURCE_ATTRIBUTION.md`](docs/OPEN_SOURCE_ATTRIBUTION.md)
- 许可证：[`LICENSE`](LICENSE)

## 已实现功能

### 研究模式（`mode`）

系统支持以下研究模式：

- `production-web`
- `verified`
- `research`
- `balanced`
- `quota`
- `thinking`

推荐默认：`balanced`

### 检索路由（`search_profile`）

系统支持以下检索路由策略：

- `searxng-first`
- `serp-first`
- `multi-route`
- `parallel`
- `parallel-trusted`
- `searxng-only`

关键策略说明：

- `parallel`：多路并发检索后聚合去重
- `parallel-trusted`：并发检索后执行置信度评估；若不足则按高信源顺序串行补检

### 搜索源兼容

- SearXNG
- SerpAPI
- Serper

### 对外接口

- `POST /gradio_api/call/run_research_once`
- `GET /gradio_api/call/run_research_once/{event_id}`
- `POST /gradio_api/call/stop_current`
- `GET /gradio_api/info`

接口标准说明：

- 研究调用统一为一个标准接口：`run_research_once`
- 不再维护历史双接口分支

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

# 搜索源（至少配置一个）
SEARXNG_BASE_URL="http://127.0.0.1:27080"
SERPAPI_API_KEY="<your_serpapi_key>"
SERPER_API_KEY="<your_serper_key>"

# 默认运行策略
DEFAULT_RESEARCH_MODE="balanced"
DEFAULT_SEARCH_PROFILE="parallel-trusted"
```

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

Skill 获取与安装：

- 仓库目录：`skills/openclaw-mirosearch/`
- 打包文件：`skills/openclaw-mirosearch.zip`
- 安装说明：[`skills/openclaw-mirosearch/references/skill-install.md`](skills/openclaw-mirosearch/references/skill-install.md)
- API 说明：[`skills/openclaw-mirosearch/references/api.md`](skills/openclaw-mirosearch/references/api.md)
- AI Agent 接入详解：[`docs/AI_AGENT_INTEGRATION.md`](docs/AI_AGENT_INTEGRATION.md)

## 路由参数说明

以下环境变量用于控制检索行为：

- `SEARCH_PROVIDER_ORDER`
- `SEARCH_PROVIDER_MODE`：`fallback | merge | parallel | parallel_conf_fallback`
- `SEARCH_PROVIDER_TRUSTED_ORDER`
- `SEARCH_PROVIDER_PARALLEL_MAX_WAIT_MS`
- `SEARCH_PROVIDER_PARALLEL_MIN_SUCCESS`
- `SEARCH_PROVIDER_FALLBACK_MAX_STEPS`
- `SEARCH_RESULT_NUM`
- `SEARCH_CONFIDENCE_ENABLED`
- `SEARCH_CONFIDENCE_SCORE_THRESHOLD`
- `SEARCH_CONFIDENCE_MIN_RESULTS`
- `SEARCH_CONFIDENCE_MIN_UNIQUE_DOMAINS`
- `SEARCH_CONFIDENCE_MIN_PROVIDER_COVERAGE`
- `SEARCH_CONFIDENCE_MIN_HIGH_CONF_HITS`
- `SEARCH_CONFIDENCE_HIGH_CONF_DOMAINS`

## 建议配置基线

- 默认生产基线：`mode=balanced` + `search_profile=parallel-trusted`
- 高风险事实核查：`mode=verified` + `search_profile=parallel-trusted`
- 额度优先场景：`mode=quota` + `search_profile=searxng-only`
- 核查深度建议：`search_result_num=30` + `verification_min_search_rounds=4`

## 文档索引

- 文档总览：[`docs/README.md`](docs/README.md)
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

当前规划分为四个阶段：

- 阶段 A（可发布基线）：接口定版、最小回归测试、发布 `v0.1.0`
- 阶段 B（生产化）：多 Key 轮转、模型 failback、观测指标、发布 `v0.2.0`
- 阶段 C（质量增强）：数字事实交叉校验与口径统一、发布 `v0.3.0`
- 阶段 D（生态分发）：OpenClaw 技能发布与一键化部署模板、发布 `v1.0.0`
