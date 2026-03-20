# Docker Compose 独立部署

本文档用于快速独立部署 OpenClaw-MiroSearch，默认同时启动：

- `app`：Gradio Demo 与 API
- `searxng`：本地搜索引擎
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

## 4. 验证接口

```bash
curl -sS 'http://127.0.0.1:8080/gradio_api/info'
curl -sS 'http://127.0.0.1:27080/healthz'
```

访问地址：

- Demo/API：`http://127.0.0.1:8080`
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
