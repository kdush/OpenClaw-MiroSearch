# MiroSearch API 服务

[English](README.md) | [中文](README_zh.md)

OpenClaw-MiroSearch 的独立 FastAPI 接入层。它将研究任务提交给 `arq`
Worker，并在 Valkey 中保存任务元数据、事件、取消标记和结果。

## 架构

- **API 进程**：校验请求、检查共享结果缓存、任务入队、返回任务快照，
  并通过 SSE 流式传输进度。
- **Worker 进程**：消费队列并运行 `execute_task_pipeline()`。
- **Valkey**：提供队列、任务存储、事件流和结果缓存。

研究任务需要 API 与 Worker 两个进程同时运行才能完成。

## 端点

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/v1/research` | 提交研究任务 |
| `GET` | `/v1/research/{task_id}` | 获取任务快照 |
| `GET` | `/v1/research/{task_id}/stream` | 通过 SSE 获取任务事件 |
| `POST` | `/v1/research/{task_id}/cancel` | 取消单个任务 |
| `POST` | `/v1/research/cancel` | 按必填的 `caller_id` 取消任务 |
| `GET` | `/v1/metrics/last` | 获取最近一次已完成任务的指标 |
| `GET` | `/health` | 检查 API 健康状态 |

请求与响应契约见 [API 规范](../../docs/API_SPEC_zh.md)。

## 本地开发

启动进程前需确保 Valkey 可访问。

```bash
cd apps/api-server
cp .env.example .env
uv sync

# 终端 1：API
uv run python main.py

# 终端 2：Worker
uv run python worker.py
```

代码默认监听 `127.0.0.1:8090`，OpenAPI 页面位于 `/docs` 和 `/redoc`。

## 认证

认证采用 fail-closed 策略：

- 使用 `API_TOKENS` 配置一个或多个以逗号分隔的 Bearer Token，以保护端点。
- `API_TOKENS` 为空且 `AUTH_DISABLED` 不是 `1` 时，受保护端点返回 `503`。
- `AUTH_DISABLED=1` 只用于本地开发。
- `/health` 保持公开。

```bash
curl -X POST http://127.0.0.1:8090/v1/research \
  -H "Authorization: Bearer replace_with_a_random_token" \
  -H "Content-Type: application/json" \
  -d '{"query":"量子计算的最新进展"}'
```

Gradio 使用 `BACKEND_MODE=api` 时，其 `API_BEARER_TOKEN` 必须与服务端
`API_TOKENS` 中的一个 Token 一致。

## Docker Compose

在仓库根目录执行：

```bash
cp .env.compose.example .env.compose
docker compose --env-file .env.compose up -d --build
```

服务拓扑和生产注意事项见[部署指南](../../docs/DEPLOY_zh.md)。

## 关键配置

| 范围 | 变量 |
| --- | --- |
| API | `API_HOST`、`API_PORT`、`API_VERSION` |
| 认证 | `API_TOKENS`、`AUTH_DISABLED` |
| Valkey | `VALKEY_HOST`、`VALKEY_PORT`、`VALKEY_PASSWORD` |
| 队列与存储 | `TASK_QUEUE_REDIS_DB`、`TASK_STORE_REDIS_DB`、`TASK_QUEUE_NAME` |
| 保留时间 | `TASK_RESULT_TTL_SECONDS`、`TASK_METADATA_TTL_SECONDS`、`RESULT_CACHE_TTL_SECONDS` |
| Worker | `ARQ_JOB_TIMEOUT_SECONDS`、`ARQ_WORKER_MAX_JOBS`、`ARQ_WORKER_MAX_TRIES`、`ARQ_RETRY_DEFER_SECONDS` |

默认值定义在 [`settings.py`](settings.py)，部署相关值应写入环境配置文件。

## 测试

```bash
cd apps/api-server
uv run pytest
```
