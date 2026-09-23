# API 规格说明

[English](./API_SPEC.md)

本文档定义当前对外 API 契约。FastAPI 服务是主要集成接口；Gradio 端点仅作为
现有客户端的兼容 API。

项目发布版本为 `v0.2.11`。API 服务单独报告可配置的 `API_VERSION`，其代码默认值
为 `0.2.0`。

## FastAPI 基础地址

Docker Compose 默认地址为：

```text
http://127.0.0.1:8090
```

OpenAPI 文档位于 `/docs` 和 `/redoc`。

## 鉴权与限流

除非显式开启无鉴权开发模式，否则所有 `/v1/*` 端点都需要 Bearer Token。
`/health` 为公开端点。

```http
Authorization: Bearer <token>
```

鉴权策略为 fail-closed：

- `API_TOKENS` 为空且 `AUTH_DISABLED` 不为 `1` 时，受保护端点返回 `503`；
- 已配置 Token 但请求未携带有效 Token 时，受保护端点返回 `401`；
- `AUTH_DISABLED=1` 仅用于本机开发。

限流默认开启，每分钟 `30` 个请求。健康检查与 API 文档路径不参与限流。生产配置
参见[部署指南](./DEPLOY_zh.md)。

## 端点一览

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/v1/research` | 提交研究任务 |
| `GET` | `/v1/research/{task_id}` | 读取任务状态、元数据与结果 |
| `GET` | `/v1/research/{task_id}/stream` | 通过 SSE 流式读取任务事件 |
| `POST` | `/v1/research/{task_id}/cancel` | 取消单个任务 |
| `POST` | `/v1/research/cancel?caller_id=...` | 取消某个调用方的活动任务 |
| `GET` | `/v1/metrics/last` | 读取最近一次运行指标 |
| `GET` | `/health` | 读取公开服务健康状态 |

## 提交研究任务

`POST /v1/research`

```bash
curl -X POST http://127.0.0.1:8090/v1/research \
  -H "Authorization: Bearer ${API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "今年检索增强生成领域有哪些变化？",
    "mode": "balanced",
    "search_profile": "parallel-trusted",
    "search_result_num": 20,
    "verification_min_search_rounds": 3,
    "output_detail_level": "balanced",
    "caller_id": "client-session-42"
  }'
```

### 请求字段

| 字段 | 必填 | 允许值或含义 |
| --- | --- | --- |
| `query` | 是 | 非空研究问题 |
| `mode` | 否 | `balanced`、`verified`、`research`、`production-web`、`quota`、`thinking` |
| `search_profile` | 否 | `searxng-first`、`serp-first`、`multi-route`、`parallel`、`parallel-trusted`、`searxng-only` |
| `search_result_num` | 否 | `10`、`20` 或 `30` |
| `verification_min_search_rounds` | 否 | `1` 至 `8` 的整数；校验行为取决于模式 |
| `output_detail_level` | 否 | `compact`、`balanced` 或 `detailed` |
| `caller_id` | 否 | 用于定向取消的稳定调用方标识 |

省略策略字段或传入 `null` 时，使用部署环境的 `DEFAULT_*` 值。代码级安全回退值
依次为 `balanced`、`searxng-first`、`20`、`3` 和 `detailed`，但部署配置可以覆盖。

### 响应

新入队任务返回：

```json
{
  "task_id": "2d66d941-0ce8-4f35-b0fb-92102bdfd5ea",
  "status": "accepted"
}
```

缓存命中时创建新的任务记录，状态为 `cached`：

```json
{
  "task_id": "cached-9fba276d-2867-4985-96e2-99a957c615e8",
  "status": "cached"
}
```

提交响应不会内嵌报告正文。包括缓存命中在内，都需要通过任务状态端点或 SSE 流
取得正文。

## 读取任务状态与结果

`GET /v1/research/{task_id}`

```bash
curl -H "Authorization: Bearer ${API_TOKEN}" \
  http://127.0.0.1:8090/v1/research/${TASK_ID}
```

```json
{
  "task_id": "2d66d941-0ce8-4f35-b0fb-92102bdfd5ea",
  "status": "completed",
  "meta": {
    "task_id": "2d66d941-0ce8-4f35-b0fb-92102bdfd5ea",
    "status": "completed",
    "caller_id": "client-session-42",
    "query": "今年检索增强生成领域有哪些变化？",
    "mode": "balanced",
    "search_profile": "parallel-trusted",
    "search_result_num": 20,
    "verification_min_search_rounds": 3,
    "output_detail_level": "balanced",
    "created_at": 0,
    "started_at": 0,
    "finished_at": 0,
    "current_stage": "",
    "error": null
  },
  "result": "# 研究报告……",
  "event_count": 12,
  "result_quality": {
    "format_valid": true,
    "fallback_used": false,
    "issues": [],
    "answer_available": true
  }
}
```

任务状态包括 `queued`、`running`、`completed`、`failed`、`cancelled` 和
`cached`。客户端应将 `completed`、`failed`、`cancelled` 与 `cached` 视为终态。

## 流式读取任务事件

`GET /v1/research/{task_id}/stream`

```bash
curl -N -H "Authorization: Bearer ${API_TOKEN}" \
  http://127.0.0.1:8090/v1/research/${TASK_ID}/stream
```

响应使用 Server-Sent Events。已持久化的 Pipeline 事件会按顺序回放，空闲期间
发送心跳，事件流最后以 `done` 事件结束：

```text
event: done
data: {"status":"completed"}
```

任务为 `completed` 或 `cached` 时，可消费已持久化的 `final_output` 事件，或读取
状态端点的 `result`。任务为 `failed` 或 `cancelled` 时，应检查终态事件和 `done`
负载。

## 取消任务

取消单个排队中或运行中的任务：

```bash
curl -X POST -H "Authorization: Bearer ${API_TOKEN}" \
  http://127.0.0.1:8090/v1/research/${TASK_ID}/cancel
```

取消某个调用方所有排队中或运行中的任务：

```bash
curl -X POST -H "Authorization: Bearer ${API_TOKEN}" \
  "http://127.0.0.1:8090/v1/research/cancel?caller_id=client-session-42"
```

`caller_id` 必填且不能为空。该端点绝不会退化为全局取消。

```json
{
  "cancelled": 1,
  "task_ids": ["2d66d941-0ce8-4f35-b0fb-92102bdfd5ea"]
}
```

## 指标与健康检查

`GET /v1/metrics/last` 为受保护端点，返回最近一次已持久化的运行指标；尚无已完成
任务时返回 `no_data`。

`GET /health` 为公开端点：

```json
{
  "status": "ok",
  "version": "0.2.0"
}
```

以上版本是 `API_VERSION` 的代码默认值，不是项目发布版本号。

## 错误处理

| 状态码 | 含义 |
| --- | --- |
| `400` | 任务当前状态不可取消 |
| `401` | Bearer Token 缺失或无效 |
| `404` | 任务不存在或已过期 |
| `422` | 请求校验失败 |
| `429` | 超出请求限流 |
| `503` | 鉴权未配置，或任务队列不可用 |

客户端可对暂时性的 `429` 和 `503` 使用有界退避重试。校验或鉴权失败时，应先
修改请求或配置，不应原样重试。

## Gradio 兼容 API

Gradio 服务默认地址为 `http://127.0.0.1:8080`。这些端点为 UI 和旧集成保留；
新的服务集成应使用 FastAPI。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/gradio_api/call/run_research_once` | 提交七项数组格式的兼容请求 |
| `GET` | `/gradio_api/call/run_research_once/{event_id}` | 轮询 Gradio 事件流 |
| `POST` | `/gradio_api/call/stop_current` | 按 `caller_id` 取消 |
| `POST` | `/gradio_api/call/stop_current_by_caller` | 按 `caller_id` 取消 |
| `GET` | `/gradio_api/info` | 读取 Gradio 端点元信息 |

```json
{
  "data": [
    "<query>",
    "<mode>",
    "<search_profile>",
    20,
    3,
    "<output_detail_level>",
    "<caller_id>"
  ]
}
```

数组第七项是 `caller_id`。空取消标识会被拒绝，不会被解释为全局停止请求。
