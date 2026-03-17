# 安装与部署（OpenClaw-MiroSearch）

## 1. 前置条件

- Python 3.10+
- `uv` 已安装
- 至少一个搜索源可用：SearXNG / SerpAPI / Serper
- 一个 OpenAI 兼容 LLM 网关（例如 LongCat）

## 2. Docker Compose 快速部署（推荐）

在仓库根目录执行：

```bash
cp .env.compose.example .env.compose
docker compose --env-file .env.compose up -d --build
```

验证：

```bash
curl -sS 'http://127.0.0.1:8080/gradio_api/info'
curl -sS 'http://127.0.0.1:27080/healthz'
```

停止：

```bash
docker compose down
```

## 3. uv 本地安装（开发场景）

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
DEFAULT_SEARCH_RESULT_NUM=20
DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS=3
```

启动：

```bash
uv run main.py
```

访问：`http://127.0.0.1:8080`

## 4. 健康检查

```bash
curl -sS 'http://127.0.0.1:8080/gradio_api/info'
```

若返回接口元数据，说明服务可用。

## 5. 常见部署形态

- 单机 Demo（容器）：使用 `docker compose` 统一启动
- 单机 Demo（源码）：直接运行 `apps/gradio-demo/main.py`
- 生产 API：固定 `mode/search_profile`，通过反向代理暴露 `gradio_api`

## 6. 失败排查

### 服务起不来

容器部署优先检查：

```bash
docker compose ps
docker compose logs -f app
```

源码部署可检查：

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
- 提高深度：`search_result_num=30` + `verification_min_search_rounds=4`
- 检查搜索源密钥是否有效
