# API 规格说明

本文档定义 OpenClaw-MiroSearch 对外 API 的调用约定。

## 基础地址

- 本地默认：`http://127.0.0.1:8080`

## 端点一览

1. `POST /gradio_api/call/run_research_once`
1. `GET /gradio_api/call/run_research_once/{event_id}`
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

## 2) 轮询结果

`GET /gradio_api/call/run_research_once/{event_id}`

返回 SSE 文本，读取 `event: complete` 的 `data`。

`data` 为 JSON 数组，第一项为最终 Markdown 输出。

## 3) stop_current

`POST /gradio_api/run/stop_current`

```json
{
  "data": []
}
```

作用：请求终止当前任务。

## 4) info

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

- 新版优先使用三参数：`query + mode + search_profile`
- 兼容旧版仅双参数接口：`query + mode`
