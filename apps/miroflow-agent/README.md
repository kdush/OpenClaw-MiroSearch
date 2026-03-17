# OpenClaw-MiroSearch Agent Core

本目录是核心 Agent 运行层。

如果你主要用 Web 页面与 API，请优先看：

- [apps/gradio-demo/README.md](../gradio-demo/README.md)

## 1. 安装

```bash
cd apps/miroflow-agent
uv sync
```

## 2. 环境变量

```bash
cp .env.example .env
```

至少保证：

- LLM 网关可用（`BASE_URL` / `API_KEY`）
- 至少一个搜索源可用（`SEARXNG_BASE_URL` / `SERPAPI_API_KEY` / `SERPER_API_KEY`）

## 3. 直接运行（命令行）

```bash
# 通用检索（推荐）
uv run python main.py llm=qwen-3 agent=demo_search_only llm.base_url=http://localhost:61002/v1

# 强校验检索
uv run python main.py llm=qwen-3 agent=demo_verified_search llm.base_url=http://localhost:61002/v1

# 纯思考（不走工具）
uv run python main.py llm=qwen-3 agent=demo_no_tools llm.base_url=http://localhost:61002/v1
```

说明：

- `demo_search_only` / `demo_verified_search` / `demo_no_tools` 对应 Demo 的 `mode` 预设。
- 若通过 Gradio 启动，通常不需要手工执行这些命令。

## 4. 检索路由配置

`search_and_scrape_webpage` 支持如下环境变量（本目录 `.env` 生效）：

- `SEARCH_PROVIDER_ORDER`
- `SEARCH_PROVIDER_MODE`：`fallback | merge | parallel | parallel_conf_fallback`
- `SEARCH_PROVIDER_TRUSTED_ORDER`
- `SEARCH_PROVIDER_PARALLEL_MAX_WAIT_MS`
- `SEARCH_PROVIDER_PARALLEL_MIN_SUCCESS`
- `SEARCH_PROVIDER_FALLBACK_MAX_STEPS`
- `SEARCH_CONFIDENCE_ENABLED`
- `SEARCH_CONFIDENCE_SCORE_THRESHOLD`
- `SEARCH_CONFIDENCE_MIN_RESULTS`
- `SEARCH_CONFIDENCE_MIN_UNIQUE_DOMAINS`
- `SEARCH_CONFIDENCE_MIN_PROVIDER_COVERAGE`
- `SEARCH_CONFIDENCE_MIN_HIGH_CONF_HITS`
- `SEARCH_CONFIDENCE_HIGH_CONF_DOMAINS`

## 5. 测试

```bash
cd apps/miroflow-agent
uv run pytest
```

## 6. 相关目录

- Agent 配置：`apps/miroflow-agent/conf/agent/`
- 核心配置加载：`apps/miroflow-agent/src/config/settings.py`
- 检索实现：`libs/miroflow-tools/src/miroflow_tools/dev_mcp_servers/search_and_scrape_webpage.py`
