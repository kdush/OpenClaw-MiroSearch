# 安装与部署（OpenClaw-MiroSearch）

## 1. 前置条件

- Python 3.10+
- `uv` 已安装
- 至少一个搜索源可用：SearXNG / SerpAPI / Serper
- 一个 OpenAI 兼容 LLM 网关（例如 LongCat）

## 2. Demo 快速安装

在仓库根目录执行：

```bash
cd apps/gradio-demo
uv sync
cp .env.example .env
```

编辑 `.env` 最小配置：

```bash
BASE_URL="https://api.longcat.chat/openai"
API_KEY="<your_longcat_key>"

SEARXNG_BASE_URL="http://127.0.0.1:27080"
SERPAPI_API_KEY="<your_serpapi_key>"
SERPER_API_KEY="<your_serper_key>"

DEFAULT_RESEARCH_MODE="balanced"
DEFAULT_SEARCH_PROFILE="parallel-trusted"
```

启动：

```bash
uv run main.py
```

访问：`http://127.0.0.1:8080`

## 3. 健康检查

```bash
curl -sS 'http://127.0.0.1:8080/gradio_api/info'
```

若返回接口元数据，说明服务可用。

## 4. 常见部署形态

- 单机 Demo：直接运行 `apps/gradio-demo/main.py`
- 生产 API：固定 `mode/search_profile`，通过反向代理暴露 `gradio_api`

## 5. 失败排查

### 服务起不来

```bash
cd apps/gradio-demo
uv run main.py
```

观察启动日志是否有 `.env` 缺失字段或端口占用。

### 页面卡在 waiting

先清理挂起任务：

```bash
curl -sS -H 'Content-Type: application/json' \
  -d '{"data":[]}' \
  'http://127.0.0.1:8080/gradio_api/run/stop_current'
```

### 无结果或质量差

- 先改为：`mode=verified` + `search_profile=parallel-trusted`
- 检查搜索源密钥是否有效
