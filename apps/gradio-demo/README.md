# OpenClaw-MiroSearch Demo（Gradio）

本目录提供 Web Demo 与对外 API 入口。

- Web 界面：用于交互式提问与调参
- API：用于其他智能体/脚本程序化调用

> 📄 English version: [README_en.md](./README_en.md)

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

说明：

- Demo 页面默认检索模式为 `balanced`
- 搜索历史会在浏览器本地保存“问题 + 结果详情”，点击历史可回填并回显结果

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

### 新增控制项

- `search_result_num`：单轮检索条数（10/20/30）
- `verification_min_search_rounds`：最少检索轮次（仅 `verified` 模式显示且生效）
- `output_detail_level`：输出篇幅（`compact/balanced/detailed`）

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

统一接口为 6 参数：

```json
{"data":["<query>","<mode>","<search_profile>",20,3,"<output_detail_level>"]}
```

### 5.2 停止当前任务

```bash
curl -sS -H 'Content-Type: application/json' \
  -d '{"data":[]}' \
  'http://127.0.0.1:8080/gradio_api/call/stop_current'
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
- 页面显示“生成中”较久：
  - 观察“最近心跳”与阶段提示（检索/推理/校验/总结）判断是否在推进
  - 若任务异常中断，陈旧 `running` 会由后台巡检自动收敛为 `failed`
- 搜索历史里只有标题，没有结果详情：
  - 已支持从同步输出与可见结果区双通道采集结果，通常会自动写入详情
  - 若浏览器本地存储空间不足，会自动压缩历史内容并优先保留最新一条结果详情
- 结果噪声大：
  - 使用 `verified` 或 `balanced`
  - `search_profile` 选择 `parallel-trusted`
