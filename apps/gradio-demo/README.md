# OpenClaw-MiroSearch Demo（Gradio）

本目录提供 Web Demo 与对外 API 入口。

- Web 界面：用于交互式提问与调参
- API：用于其他智能体/脚本程序化调用

## 1. 安装

```bash
cd apps/gradio-demo
uv sync
```

## 2. 配置

```bash
cp .env.example .env
```

最小建议配置：

```bash
# LLM 网关（OpenAI 兼容）
BASE_URL="https://api.longcat.chat/openai"
API_KEY="<your_longcat_key>"

# 搜索源（至少一个）
SEARXNG_BASE_URL="http://127.0.0.1:27080"
SERPAPI_API_KEY="<your_serpapi_key>"
SERPER_API_KEY="<your_serper_key>"
```

可选：

```bash
# 默认下拉选项
DEFAULT_RESEARCH_MODE="balanced"
DEFAULT_SEARCH_PROFILE="parallel-trusted"
```

## 3. 启动

```bash
uv run main.py
```

默认监听：`http://127.0.0.1:8080`

### Docker Compose 一键部署

在仓库根目录执行：

```bash
cp .env.compose.example .env.compose
docker compose --env-file .env.compose up -d --build
```

详细说明见：[`docs/DEPLOY_DOCKER_COMPOSE.md`](../../docs/DEPLOY_DOCKER_COMPOSE.md)

## 4. 页面可切换配置

### 检索模式（`mode`）

- `production-web`
- `verified`
- `research`
- `balanced`
- `quota`
- `thinking`

### 检索源策略（`search_profile`）

- `searxng-first`
- `serp-first`
- `multi-route`
- `parallel`
- `parallel-trusted`
- `searxng-only`

推荐：

- 默认：`balanced + parallel-trusted`
- 强校验：`verified + parallel-trusted`
- 省额度：`quota + searxng-only`

## 5. API 调用

### 5.1 单次研究（最终 Markdown）

1. 申请任务：

```bash
curl -sS -H 'Content-Type: application/json' \
  -d '{"data":["中国大陆有哪些厂商推出了 OpenClaw 变体？","balanced","parallel-trusted"]}' \
  'http://127.0.0.1:8080/gradio_api/call/run_research_once'
```

响应中拿到 `event_id`。

2. 拉取结果：

```bash
curl -sS "http://127.0.0.1:8080/gradio_api/call/run_research_once/<event_id>"
```

### 5.2 停止当前任务

```bash
curl -sS -H 'Content-Type: application/json' \
  -d '{"data":[]}' \
  'http://127.0.0.1:8080/gradio_api/run/stop_current'
```

### 5.3 查看 API 元信息

```bash
curl -sS 'http://127.0.0.1:8080/gradio_api/info'
```

## 6. 与生产模式的关系

- Demo 是“可视化交互层”，核心能力仍由 `apps/miroflow-agent` 与 `libs/miroflow-tools` 提供。
- 若转生产调用，通常直接保留 `run_research_once` 接口并固定参数，不需要前端页面。

## 7. 常见问题

- 页面长时间 `Waiting to start research...`：
  - 先调用 `stop_current` 清理挂起任务
  - 检查 `.env` 中的 LLM 与搜索引擎配置
- 结果噪声大：
  - 使用 `verified` 或 `balanced`
  - `search_profile` 选择 `parallel-trusted`
