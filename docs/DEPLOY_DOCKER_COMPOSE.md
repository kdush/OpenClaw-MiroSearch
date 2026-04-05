# Docker Compose 独立部署

本文档用于快速独立部署 OpenClaw-MiroSearch，默认同时启动：

- `app`：Gradio Demo（默认端口 8080，可通过 `APP_PORT` 覆盖）
- `api`：FastAPI API Server（默认端口 8090，可通过 `API_PORT` 覆盖）
- `searxng`：本地搜索引擎（默认端口 27080）
- `valkey`：SearXNG 缓存与限流存储

## 前置条件

- Docker Engine 24+
- Docker Compose v2（`docker compose version` 可用）

## 1. 准备环境变量

```bash
cp .env.compose.example .env.compose
```

至少需要填写：

- `BASE_URL`
- `API_KEY`

可选填写（提升检索质量）：

- `SERPAPI_API_KEY`
- `SERPER_API_KEY`

建议同时设置（提升交叉验证体感）：

- `DEFAULT_SEARCH_PROFILE=parallel-trusted`
- `DEFAULT_SEARCH_RESULT_NUM=20`（或 30）
- `DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS=3`（核查型问题可提到 4）

### 按网络环境选择引擎（重要）

在不同网络环境下，推荐使用不同的检索源策略：

- 中国大陆（无代理/出海链路不稳定）：
  - 推荐：
    - `DEFAULT_SEARCH_PROFILE=searxng-first`
    - `SEARCH_PROVIDER_ORDER=searxng,serpapi,serper`
    - `SEARCH_PROVIDER_MODE=fallback`
  - SearXNG 引擎建议：
    - 优先启用：`bing`、`baidu`、`sogou`、`yandex`
    - 建议禁用：`google`、`duckduckgo`、`brave`、`startpage`、`wikipedia`
- 海外或有稳定代理：
  - 推荐：
    - `DEFAULT_SEARCH_PROFILE=parallel-trusted`
    - `SEARCH_PROVIDER_ORDER=serpapi,searxng,serper`
    - `SEARCH_PROVIDER_MODE=parallel_conf_fallback`
  - SearXNG 引擎建议：
    - 保留 `google`、`duckduckgo`、`brave`、`startpage`、`wikipedia` 与区域引擎混合
- 网络环境不确定：
  - 先用 `DEFAULT_SEARCH_PROFILE=searxng-first` 保守启动，再根据实测切换到 `parallel-trusted`

仓库已内置 SearXNG 可覆盖配置：

- `deploy/searxng/settings.yml`

可通过编辑该文件控制启用/禁用的引擎，`compose.yaml` 已挂载到容器。

建议先做连通性自检（在目标机器执行）：

```bash
curl -sS -m 8 -o /dev/null -w 'bing: %{http_code} %{time_total}\n' https://www.bing.com
curl -sS -m 8 -o /dev/null -w 'baidu: %{http_code} %{time_total}\n' https://www.baidu.com
curl -sS -m 8 -o /dev/null -w 'google: %{http_code} %{time_total}\n' https://www.google.com
curl -sS -m 8 -o /dev/null -w 'duckduckgo: %{http_code} %{time_total}\n' https://duckduckgo.com
```

## 2. 启动服务

```bash
docker compose --env-file .env.compose up -d --build
```

## 3. 检查状态

```bash
docker compose ps
docker compose logs -f app
```

### 端口映射配置

如需自定义宿主机端口，在项目根目录创建 `.env` 文件（注意：这不是 `.env.compose`）：

```bash
# .env — Docker Compose 变量替换用
APP_PORT=28080    # Gradio Demo 宿主机端口（默认 8080）
API_PORT=28090    # API Server 宿主机端口（默认 8090）
```

> `.env.compose` 用于容器内环境变量，`.env` 用于 `compose.yaml` 中的端口映射等变量替换。

## 4. 验证接口

```bash
curl -sS 'http://127.0.0.1:8080/gradio_api/info'
curl -sS 'http://127.0.0.1:8090/health'
curl -sS 'http://127.0.0.1:27080/healthz'
```

访问地址：

- Gradio Demo：`http://127.0.0.1:8080`（或自定义 `APP_PORT`）
- API Server：`http://127.0.0.1:8090`（或自定义 `API_PORT`）
- SearXNG：`http://127.0.0.1:27080`

## 5. 停止与清理

```bash
docker compose down
```

如需同时删除卷（会清空 SearXNG 缓存）：

```bash
docker compose down -v
```

## 6. 常见场景

### 使用外部 SearXNG

在 `.env.compose` 设置：

```bash
SEARXNG_BASE_URL=http://<external_searxng_host>:<port>
```

然后仍可直接执行：

```bash
docker compose --env-file .env.compose up -d --build
```

### 升级镜像与重建

```bash
docker compose pull
docker compose --env-file .env.compose up -d --build
```

### 容器无法访问外网（Unraid / NAS 常见）

**现象**：LLM 调用全部返回 `Connection error`，SearXNG 预检失败；但宿主机本身可正常访问外网。

**原因**：部分 NAS 系统（如 Unraid）的 Docker bridge 网络 NAT 转发异常，导致容器内部无法建立到外网的 TCP 连接。

**解决方案**：将 `app` 服务切换为 host 网络模式。

1. 编辑 `compose.yaml`，在 `app` 服务中取消 `network_mode: host` 的注释并注释掉 `ports` 段：

```yaml
services:
  app:
    # ...
    network_mode: host
    # ports:
    #   - "${APP_PORT:-8080}:8080"
```

2. 使用 host 模式后，`app` 容器内通过 `localhost` 访问 SearXNG，需在 `.env.compose` 中设置：

```bash
SEARXNG_BASE_URL=http://127.0.0.1:${SEARXNG_HOST_PORT:-27080}
```

3. 重建容器：

```bash
docker compose --env-file .env.compose up -d --build
```

**验证**：

```bash
# 检查容器内外网连通性
docker exec <container_name> python3 -c "import urllib.request; print(urllib.request.urlopen('https://httpbin.org/ip', timeout=10).read())"
```
