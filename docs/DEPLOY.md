# Deployment Guide / 部署指南

---

## Docker Compose Deployment / Docker Compose 部署

Quick standalone deployment, starting by default / 快速独立部署，默认同时启动：

- `app`：Gradio Demo（port/端口 8080，可覆盖 via `APP_PORT`）
- `api`：FastAPI API Server（port/端口 8090，可覆盖 via `API_PORT`）
- `searxng`：本地搜索引擎（port/端口 27080）
- `valkey`：SearXNG 缓存与限流存储

### Prerequisites / 前置条件

- Docker Engine 24+
- Docker Compose v2（`docker compose version` 可用）

### 1. Prepare Environment Variables / 准备环境变量

```bash
cp .env.compose.example .env.compose
```

Required / 至少需要填写：

- `BASE_URL`
- `API_KEY`

Optional (improve search quality) / 可选填写（提升检索质量）：

- `SERPAPI_API_KEY`
- `SERPER_API_KEY`

Recommended (improve cross-validation) / 建议同时设置（提升交叉验证体感）：

- `DEFAULT_SEARCH_PROFILE=parallel-trusted`
- `DEFAULT_SEARCH_RESULT_NUM=20`（or/或 30）
- `DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS=3`（for fact-checking, set to / 核查型问题可提到 4）

### Choose Search Strategy by Network / 按网络环境选择引擎（重要）

- 中国大陆（无代理/出海链路不稳定）：
  - `DEFAULT_SEARCH_PROFILE=searxng-first`
  - `SEARCH_PROVIDER_ORDER=searxng,serpapi,serper`
  - `SEARCH_PROVIDER_MODE=fallback`
  - SearXNG 引擎建议：优先启用 `bing`、`baidu`、`sogou`、`yandex`；建议禁用 `google`、`duckduckgo`、`brave`、`startpage`、`wikipedia`
- 海外或有稳定代理：
  - `DEFAULT_SEARCH_PROFILE=parallel-trusted`
  - `SEARCH_PROVIDER_ORDER=serpapi,searxng,serper`
  - `SEARCH_PROVIDER_MODE=parallel_conf_fallback`
  - SearXNG 引擎建议：保留 `google`、`duckduckgo`、`brave`、`startpage`、`wikipedia` 与区域引擎混合
- 网络环境不确定：
  - 先用 `DEFAULT_SEARCH_PROFILE=searxng-first` 保守启动，再根据实测切换到 `parallel-trusted`

SearXNG 可覆盖配置：`deploy/searxng/settings.yml`（`compose.yaml` 已挂载到容器）

Connectivity self-check / 连通性自检（在目标机器执行）：

```bash
curl -sS -m 8 -o /dev/null -w 'bing: %{http_code} %{time_total}\n' https://www.bing.com
curl -sS -m 8 -o /dev/null -w 'baidu: %{http_code} %{time_total}\n' https://www.baidu.com
curl -sS -m 8 -o /dev/null -w 'google: %{http_code} %{time_total}\n' https://www.google.com
curl -sS -m 8 -o /dev/null -w 'duckduckgo: %{http_code} %{time_total}\n' https://duckduckgo.com
```

### 2. Start Services / 启动服务

```bash
docker compose --env-file .env.compose up -d --build
```

### 3. Check Status / 检查状态

```bash
docker compose ps
docker compose logs -f app
```

Port mapping / 端口映射配置：在项目根目录创建 `.env` 文件（注意：这不是 `.env.compose`）：

```bash
# .env — Docker Compose 变量替换用
APP_PORT=28080    # Gradio Demo 宿主机端口（默认 8080）
API_PORT=28090    # API Server 宿主机端口（默认 8090）
```

> `.env.compose` 用于容器内环境变量，`.env` 用于 `compose.yaml` 中的端口映射等变量替换。

### 4. Verify Endpoints / 验证接口

```bash
curl -sS 'http://127.0.0.1:8080/gradio_api/info'
curl -sS 'http://127.0.0.1:8090/health'
curl -sS 'http://127.0.0.1:27080/healthz'
```

Access URLs / 访问地址：

- Gradio Demo：`http://127.0.0.1:8080`（或自定义 `APP_PORT`）
- API Server：`http://127.0.0.1:8090`（或自定义 `API_PORT`）
- SearXNG：`http://127.0.0.1:27080`

### 5. Stop & Clean Up / 停止与清理

```bash
docker compose down
```

To also remove volumes (clears SearXNG cache) / 如需同时删除卷（会清空 SearXNG 缓存）：

```bash
docker compose down -v
```

### Common Scenarios / 常见场景

#### Using External SearXNG / 使用外部 SearXNG

```bash
# In .env.compose
SEARXNG_BASE_URL=http://<external_searxng_host>:<port>
```

#### Upgrade & Rebuild / 升级镜像与重建

```bash
docker compose pull
docker compose --env-file .env.compose up -d --build
```

#### Container Cannot Access External Network / 容器无法访问外网

**Symptom**: LLM calls return `Connection error`, SearXNG pre-check fails; but the host itself can access the external network normally.

**Cause**: Docker network mode, DNS, NAT forwarding, or egress policy restrictions prevent the container from establishing TCP connections to the external network.

**Solution**: Switch the `app` service to host network mode.

1. Edit `compose.yaml`, uncomment `network_mode: host` and comment out `ports`:

```yaml
services:
  app:
    # ...
    network_mode: host
    # ports:
    #   - "${APP_PORT:-8080}:8080"
```

2. In host mode, `app` accesses SearXNG via `localhost`, set in `.env.compose`:

```bash
SEARXNG_BASE_URL=http://127.0.0.1:${SEARXNG_HOST_PORT:-27080}
```

3. Rebuild:

```bash
docker compose --env-file .env.compose up -d --build
```

**Verify**:

```bash
docker exec <container_name> python3 -c "import urllib.request; print(urllib.request.urlopen('https://httpbin.org/ip', timeout=10).read())"
```

---

## Optional Local Tool Deployment / 可选本地工具部署

Deploy optional local tool services to reduce commercial API dependency and enable operation in intranet/local environments.

部署可选的本地工具服务，降低商业 API 依赖，在内网/本地环境可持续运行。

### Available Tools / 可选工具

| Tool | Model | Prerequisites |
|------|-------|---------------|
| `tool-transcribe-os` (Audio transcription / 音频转写) | `openai/whisper-large-v3-turbo` | NVIDIA GPU, CUDA |
| `tool-vqa-os` (Visual Q&A / 视觉问答) | `Qwen/Qwen2.5-VL-72B-Instruct` | NVIDIA GPU, CUDA |
| `tool-reasoning-os` (Reasoning / 推理) | `Qwen/Qwen3-235B-A22B-Thinking-2507` | NVIDIA GPU, CUDA |

These tools are optional, not required for minimal Demo startup / 这些工具均为可选，不是 Demo 最小启动必需项。

### Audio Transcription / 音频转写

```bash
pip install vllm==0.10.0
pip install 'vllm[audio]'

vllm serve openai/whisper-large-v3-turbo \
  --served-model-name whisper-large-v3-turbo \
  --task transcription \
  --host 0.0.0.0 \
  --port 8000
```

`.env`:

```bash
WHISPER_MODEL_NAME="openai/whisper-large-v3-turbo"
WHISPER_BASE_URL="http://127.0.0.1:8000/v1"
WHISPER_API_KEY="<optional_key>"
```

### Visual Q&A / 视觉问答

```bash
pip install 'sglang[all]'

python3 -m sglang.launch_server \
  --model-path Qwen/Qwen2.5-VL-72B-Instruct \
  --tp 8 \
  --host 0.0.0.0 \
  --port 8001 \
  --trust-remote-code
```

`.env`:

```bash
VISION_MODEL_NAME="Qwen/Qwen2.5-VL-72B-Instruct"
VISION_BASE_URL="http://127.0.0.1:8001/v1/chat/completions"
VISION_API_KEY="<optional_key>"
```

### Reasoning Service / 推理服务

```bash
pip install 'sglang[all]'

python3 -m sglang.launch_server \
  --model-path Qwen/Qwen3-235B-A22B-Thinking-2507 \
  --tp 8 \
  --host 0.0.0.0 \
  --port 8002 \
  --trust-remote-code \
  --context-length 131072
```

`.env`:

```bash
REASONING_MODEL_NAME="Qwen/Qwen3-235B-A22B-Thinking-2507"
REASONING_BASE_URL="http://127.0.0.1:8002/v1/chat/completions"
REASONING_API_KEY="<optional_key>"
```

### Integration / 接入方式

Enable in `apps/miroflow-agent/conf/agent/*.yaml`:

```yaml
main_agent:
  tools:
    - search_and_scrape_webpage
    - jina_scrape_llm_summary
    - tool-transcribe-os
    - tool-vqa-os
    - tool-reasoning-os
```

Ensure `apps/miroflow-agent/.env` has the corresponding addresses and keys / 确保 `apps/miroflow-agent/.env` 填好对应地址与密钥。

> If you don't deploy local versions, you can continue using the default commercial tool versions (without `-os` suffix) / 如果不部署本地版本，可继续使用默认商业工具版本（不带 `-os` 后缀）。
