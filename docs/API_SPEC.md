# API 规格说明

本文档定义 OpenClaw-MiroSearch 对外 API 的调用约定。

## 基础地址

- 本地默认：`http://127.0.0.1:8080`

## 端点一览

1. `POST /gradio_api/call/run_research_once`
1. `POST /gradio_api/call/run_research_once_v2`
1. `GET /gradio_api/call/run_research_once/{event_id}`
1. `GET /gradio_api/call/run_research_once_v2/{event_id}`
1. `POST /gradio_api/run/stop_current`
1. `GET /gradio_api/info`

## 1) run_research_once

### 请求

`POST /gradio_api/call/run_research_once`

```json
{
  "data": ["<query>", "<mode>", "<search_profile>"]
}
```

字段说明：

- `query`：研究问题（字符串，必填）
- `mode`：研究模式（可选，默认 `balanced`）
- `search_profile`：检索路由（可选，默认 `searxng-first`）

### 响应

```json
{
  "event_id": "xxxx"
}
```

## 2) run_research_once_v2（推荐）

### 请求

`POST /gradio_api/call/run_research_once_v2`

```json
{
  "data": ["<query>", "<mode>", "<search_profile>", 30, 4]
}
```

字段说明：

- `query`：研究问题（字符串，必填）
- `mode`：研究模式（可选，默认 `balanced`）
- `search_profile`：检索路由（可选，默认 `searxng-first`）
- `search_result_num`：单轮检索条数（可选，10/20/30）
- `verification_min_search_rounds`：最少检索轮次（可选，仅 `verified` 生效）

### 响应

```json
{
  "event_id": "xxxx"
}
```

## 3) 轮询结果

支持：

- `GET /gradio_api/call/run_research_once/{event_id}`
- `GET /gradio_api/call/run_research_once_v2/{event_id}`

返回 SSE 文本，读取 `event: complete` 的 `data`。

`data` 为 JSON 数组，第一项为最终 Markdown 输出。

## 4) stop_current

`POST /gradio_api/run/stop_current`

```json
{
  "data": []
}
```

作用：请求终止当前任务。

## 5) info

`GET /gradio_api/info`

作用：返回接口与参数元信息。

## 模式枚举（`mode`）

- `production-web`
- `verified`
- `research`
- `balanced`
- `quota`
- `thinking`

## 检索路由枚举（`search_profile`）

- `searxng-first`
- `serp-first`
- `multi-route`
- `parallel`
- `parallel-trusted`
- `searxng-only`

## 错误与重试建议

- `422`：参数格式错误或接口版本不匹配
- `429`：上游限流，建议按 `retry_after` 或指数退避重试
- 超时：建议先调用 `stop_current` 清理挂起任务后再重试

## 兼容性说明

- 推荐优先使用五参数：`query + mode + search_profile + search_result_num + verification_min_search_rounds`
- 兼容接口三参数：`query + mode + search_profile`
- 更老版本兼容双参数：`query + mode`

## 可观测性说明（google_search）

在工具链路中，`google_search` 的结果包含下列元信息（用于判断是否真实走了多路并发与补检）：

- `searchParameters.provider_mode`
- `searchParameters.providers_with_results`
- `confidence`
- `route_trace`
- `provider_fallback`
