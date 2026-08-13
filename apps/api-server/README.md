# MiroSearch API Server

独立于 Gradio Demo 的标准 HTTP API 层，基于 FastAPI 构建。

## 架构

v0.2.0 重构后采用异步任务队列架构：

- **API 层**：参数校验、缓存命中短路、任务入队、状态查询、SSE 流式读取
- **Worker 层**：通过 `arq` 从队列消费任务并执行 `execute_task_pipeline()`
- **存储层**：任务元数据、取消标记、结果和事件流统一存储在 Valkey 中

## 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/research` | 提交研究任务 |
| GET | `/v1/research/{task_id}` | 获取任务状态 |
| GET | `/v1/research/{task_id}/stream` | SSE 流式获取任务进度 |
| POST | `/v1/research/{task_id}/cancel` | 取消指定任务 |
| POST | `/v1/research/cancel` | 按必填 caller_id 批量取消 |
| GET | `/v1/metrics/last` | 最近任务运行指标 |
| GET | `/health` | 健康检查 |

## 快速启动

### 本地开发

```bash
cd apps/api-server
cp .env.example .env
# 编辑 .env 填入实际配置，并明确选择一种认证模式：
# - 本地开发：AUTH_DISABLED=1
# - 生产/共享环境：AUTH_DISABLED=0 且 API_TOKENS=<随机强 Token>
uv sync

# 启动 API 服务
uv run python main.py

# 启动 Worker（另一个终端）
uv run python worker.py
```

### Docker Compose

```bash
cp .env.compose.example .env.compose
# 编辑 .env.compose 填入实际配置
docker compose --env-file .env.compose up -d --build
```

## 环境变量

### Valkey/Redis 配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `VALKEY_HOST` | `localhost` | Valkey 主机 |
| `VALKEY_PORT` | `6379` | Valkey 端口 |
| `VALKEY_PASSWORD` | (空) | Valkey 密码 |
| `TASK_QUEUE_REDIS_DB` | `1` | 队列 Redis DB |
| `TASK_STORE_REDIS_DB` | `2` | 任务存储 Redis DB |
| `TASK_QUEUE_NAME` | `miro:research:queue` | 队列名称 |
| `TASK_EVENT_STREAM_MAXLEN` | `1000` | 事件流最大长度 |
| `TASK_RESULT_TTL_SECONDS` | `3600` | 结果 TTL |
| `RESULT_CACHE_TTL_SECONDS` | `3600` | API/Worker 共享结果缓存 TTL；`0` 表示永久 |
| `TASK_METADATA_TTL_SECONDS` | `7200` | 元数据 TTL |
| `ARQ_JOB_TIMEOUT_SECONDS` | `1800` | 任务超时 |
| `ARQ_WORKER_MAX_JOBS` | `1` | Worker 最大并发 |
| `ARQ_WORKER_MAX_TRIES` | `5` | 单任务最大执行次数（含首次执行） |
| `ARQ_RETRY_DEFER_SECONDS` | `5` | 初始化失败后的重试延迟（秒） |
| `TASK_CANCEL_POLL_INTERVAL_SECONDS` | `0.5` | 取消轮询间隔 |

## 认证

认证默认采用 fail-closed 策略：

- `API_TOKENS` 配置一个或多个 Bearer Token（逗号分隔）后，受保护端点只接受其中的 Token。
- `API_TOKENS` 为空且未显式设置 `AUTH_DISABLED=1` 时，受保护端点返回 `503`，避免漏配认证后直接暴露。
- `AUTH_DISABLED=1` 仅用于本机开发；生产或共享环境应保持 `AUTH_DISABLED=0`。
- API 默认只监听 `127.0.0.1`。若要监听 `0.0.0.0`，应同时配置 Token，并通过防火墙或可信反向代理限制访问。

```bash
# 服务端 .env
API_TOKENS=replace_with_a_random_token
AUTH_DISABLED=0

# 请求示例
curl -X POST http://localhost:8090/v1/research \
  -H "Authorization: Bearer replace_with_a_random_token" \
  -H "Content-Type: application/json" \
  -d '{"query": "量子计算最新进展"}'
```

使用 Docker Compose 的 API 后端模式时，Gradio 侧的
`API_BEARER_TOKEN` 必须与 `API_TOKENS` 中的一个 Token 完全一致。

## 测试

```bash
cd apps/api-server
uv run pytest tests/ -v
```
