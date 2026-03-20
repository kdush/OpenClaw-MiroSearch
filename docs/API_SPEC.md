# API 规格说明

本文档定义 OpenClaw-MiroSearch 对外 API 的调用约定（统一标准接口）。

> 接口约束：研究接口仅保留 `run_research_once`，历史双接口已收敛为统一标准。

## 基础地址

- 本地默认：`http://127.0.0.1:8080`

## 端点一览

1. `POST /gradio_api/call/run_research_once`
1. `GET /gradio_api/call/run_research_once/{event_id}`
1. `POST /gradio_api/call/stop_current`
1. `POST /gradio_api/call/stop_current_by_caller`（v0.1.9+）
1. `GET /gradio_api/info`

## 1) run_research_once（统一标准接口）

### 请求

`POST /gradio_api/call/run_research_once`

```json
{
  "data": ["<query>", "<mode>", "<search_profile>", 20, 3, "<output_detail_level>"]
}
```

字段说明：

- `query`：研究问题（字符串，必填）
- `mode`：研究模式（可选，默认 `balanced`）
- `search_profile`：检索路由（可选，默认 `searxng-first`）
- `search_result_num`：单轮检索条数（可选，`10/20/30`）
- `verification_min_search_rounds`：最少检索轮次（可选，仅 `verified` 生效）
- `output_detail_level`：输出篇幅档位（可选，`compact/balanced/detailed`）
- `render_mode`：渲染模式（可选，通常无需手动指定）
- `caller_id`：调用方标识（可选，v0.1.9+；用于会话级任务隔离，配合 `stop_current` 定向取消）

### 响应

```json
{
  "event_id": "xxxx"
}
```

参数生效约束：

- `verification_min_search_rounds` 仅在 `mode=verified` 时生效；其它模式按服务默认门槛处理

## 2) 轮询结果

`GET /gradio_api/call/run_research_once/{event_id}`

返回 SSE 文本，读取 `event: complete` 的 `data`。

`data` 为 JSON 数组，第一项为最终 Markdown 输出。

终态约定：

- 任务完成以 `event: complete` 为准
- 若 `complete` 内容为 `No \boxed{} content found in the final answer.`，表示本轮未收敛，建议按降级策略重试

心跳约定：

- 期间会持续发送 `event: heartbeat`
- `heartbeat.data.stage` 包含 `phase/turn/search_round/detail/agent_name`，用于展示“当前处于哪一阶段”

## 3) stop_current

`POST /gradio_api/call/stop_current`

```json
{
  "data": []
}
```

作用：请求终止所有活跃任务（向后兼容，0 参数）。

### 3.1) stop_current_by_caller（v0.1.9+）

`POST /gradio_api/call/stop_current_by_caller`

```json
{
  "data": ["<caller_id>"]
}
```

作用：按 `caller_id` 定向取消。仅终止该调用方发起的任务，不影响其他并发任务。

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

## 输出渲染约定

- API 默认渲染模式跟随 `output_detail_level`：
  - `compact` -> `summary_only`
  - `balanced` -> `summary_with_details`
  - `detailed` -> `full`
- `detailed` 档会启用报告式总结策略，目标是更完整的长篇输出。

## 输出篇幅枚举（`output_detail_level`）

- `compact`：精简（当前短篇幅，聚焦核心结论）
- `balanced`：适中（核心优先 + 必要非核心信息）
- `detailed`：详细（超长报告，信息密集）

## 错误与重试建议

- `422`：参数格式错误（通常是 `data` 数组长度或字段类型不匹配）
- `429`：上游限流，建议按 `retry_after` 或指数退避重试
- 超时：建议先调用 `stop_current` 清理挂起任务后再重试
- 任务日志中的陈旧 `running` 状态会被后台巡检自动收敛为 `failed`，避免长期假运行

## 面向 AI Agent 的接入说明

### 最小调用闭环

1. `GET /gradio_api/info` 确认服务在线与参数签名
2. `POST /gradio_api/call/run_research_once` 发起任务，保存 `event_id`
3. `GET /gradio_api/call/run_research_once/{event_id}` 轮询 SSE，直到 `event: complete`
4. 仅将 `complete` 的第一项 Markdown 作为最终结论输入下游推理

### 参数建议（按任务意图）

- 快速问答：`mode=balanced` + `search_profile=parallel-trusted` + `output_detail_level=compact`
- 普通研究：`mode=balanced` + `search_profile=parallel-trusted` + `output_detail_level=balanced`
- 高核查/长文：`mode=verified` + `search_profile=parallel-trusted` + `search_result_num=30` + `verification_min_search_rounds=4` + `output_detail_level=detailed`

### 失败处理建议

- 若 SSE 未出现 `complete`：先调 `stop_current`，再重试
- 若返回 `No \\boxed{} content found in the final answer.`：视为“未收敛”，不是服务不可用
- 若出现 429：指数退避并降级 `mode`（`thinking -> balanced -> quota`）

## 可观测性说明（google_search）

在工具链路中，`google_search` 的结果包含下列元信息（用于判断是否真实走了多路并发与补检）：

- `searchParameters.provider_mode`
- `searchParameters.providers_with_results`
- `confidence`
- `route_trace`
- `provider_fallback`
