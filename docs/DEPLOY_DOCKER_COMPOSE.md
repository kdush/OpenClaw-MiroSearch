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
