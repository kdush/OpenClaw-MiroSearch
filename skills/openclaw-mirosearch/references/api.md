# API 调用参考

v0.2.0 起提供两套 API，**AI Agent 推荐使用 FastAPI API**。当前 skill 对齐版本：v0.2.2。

> v0.2.2 修复说明：早期版本（v0.2.0 / v0.2.1）的 FastAPI worker 会丢弃请求里的
> `mode` / `search_profile` / `search_result_num` / `verification_min_search_rounds` / `output_detail_level`
> 五个字段并强行回退硬编码轻量预设。**升级到 v0.2.2 后这五个字段会全链路生效，请显式传入需要的值。**

---

## FastAPI API（推荐，v0.2.0+）

默认地址：`http://127.0.0.1:8090`

采用异步任务队列架构，支持并发多任务、SSE 流式事件推送、任务状态持久化。

### 认证

设置 `API_TOKENS` 环境变量启用 Bearer Token 认证（逗号分隔支持多 Token），留空跳过。

### 1) 提交任务

`POST /v1/research`

```json
{
  "query": "量子计算最新进展",
  "mode": "balanced",
  "search_profile": "parallel-trusted",
  "search_result_num": 20,
  "verification_min_search_rounds": 3,
  "output_detail_level": "balanced",
  "caller_id": "my-agent-001"
}
```

请求参数：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `query` | string | 是 | — | 研究问题 |
| `mode` | string | 否 | `balanced` | 研究模式 |
| `search_profile` | string | 否 | `parallel-trusted` | 检索路由 |
| `search_result_num` | int | 否 | `20` | 每轮检索结果数 |
| `verification_min_search_rounds` | int | 否 | `3` | 最少检索轮次（verified 模式） |
| `output_detail_level` | string | 否 | `detailed` | 输出篇幅 |
| `caller_id` | string | 否 | — | 调用方标识 |

建议：

- 调用方始终显式传入 `mode`、`search_profile`、`search_result_num`、`verification_min_search_rounds`、`output_detail_level`
- 需要批量取消或会话隔离时显式传入 `caller_id`

响应（异步入队）：

```json
{"task_id": "d255d142-...", "status": "accepted"}
```

缓存命中时（同步返回）：

```json
{"task_id": "xxx", "status": "cached", "result": "...markdown..."}
```

### 2) 查询任务状态

`GET /v1/research/{task_id}`

```json
{
  "task_id": "d255d142-...",
  "status": "running",
  "meta": {"query": "...", "mode": "balanced", "current_stage": "tool:unknown"},
  "result": null,
  "event_count": 8
}
```

`status` 枚举：`queued` → `running` → `completed` / `failed` / `cancelled` / `cached`

### 3) SSE 流式监听

`GET /v1/research/{task_id}/stream`

返回 SSE 事件流，已完成任务返回历史事件，运行中任务实时推送。常见事件包括：

```
event: start_of_workflow
data: {"workflow_id": "...", "input": [...]}

event: stage_heartbeat
data: {"phase": "检索", "turn": 1, "detail": "执行工具 google_search", ...}

event: tool_call
data: {"tool_name": "google_search", "tool_input": {...}}

event: final_output
data: {"markdown": "..."}

event: done
data: {"status": "completed"}
```

说明：

- `done.data.status` 可能为 `completed`、`failed`、`cancelled` 或 `cached`
- 进度类事件名称以服务端实际产出为准，常见为 `stage_heartbeat`

### 4) 取消任务

`POST /v1/research/{task_id}/cancel`

### 4.1) 按 caller_id 批量取消

`POST /v1/research/cancel?caller_id=<caller_id>`

不传 `caller_id` 时，取消所有运行中任务。

响应：

```json
{
  "cancelled": 2,
  "task_ids": ["task-a", "task-b"]
}
```

### 5) 健康检查

`GET /health`

### 6) 最近运行指标

`GET /v1/metrics/last`

用于获取最近一次任务的结构化运行指标；若尚无数据，返回：

```json
{
  "status": "no_data",
  "message": "尚无已完成的任务"
}
```

### cURL 示例

```bash
# 提交任务
TASK_ID=$(curl -sS -X POST http://127.0.0.1:8090/v1/research \
  -H "Content-Type: application/json" \
  -d '{"query": "量子计算最新进展", "mode": "balanced", "search_profile": "parallel-trusted"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["task_id"])')

# 轮询状态
curl -sS "http://127.0.0.1:8090/v1/research/$TASK_ID"

# SSE 流式监听
curl -sS -N "http://127.0.0.1:8090/v1/research/$TASK_ID/stream"

# 按 caller_id 批量取消
curl -sS -X POST "http://127.0.0.1:8090/v1/research/cancel?caller_id=my-agent-001"

# 查看最近一次运行指标
curl -sS "http://127.0.0.1:8090/v1/metrics/last"
```

### 脚本调用

```bash
python3 scripts/call_openclaw_mirosearch.py \
  --api-mode fastapi \
  --base-url "http://127.0.0.1:8090" \
  --query "量子计算最新进展" \
  --mode balanced \
  --search-profile parallel-trusted \
  --output-detail-level balanced
```

---

## Gradio API（兼容保留）

默认地址：`http://127.0.0.1:8080`

> 仍可使用，但不支持异步任务队列与并发。适合 Demo 体验和浏览器交互场景。

### 1) 发起任务

`POST /gradio_api/call/run_research_once`

```json
{"data": ["<query>", "<mode>", "<search_profile>", 20, 3, "<output_detail_level>", null, "<caller_id>"]}
```

返回：`{"event_id": "..."}`

### 2) 轮询结果

`GET /gradio_api/call/run_research_once/{event_id}`

终态以 SSE `event: complete` 为准。

### 3) 停止任务

- 取消所有：`POST /gradio_api/call/stop_current`，请求体 `{"data": []}`
- 按 caller_id：`POST /gradio_api/call/stop_current_by_caller`，请求体 `{"data": ["my-session-001"]}`

### cURL 示例

```bash
BASE_URL="http://127.0.0.1:8080"

EVENT_ID=$(curl -sS -H 'Content-Type: application/json' \
  -d '{"data":["量子计算最新进展","balanced","parallel-trusted",20,3,"balanced"]}' \
  "$BASE_URL/gradio_api/call/run_research_once" | python3 -c 'import sys,json;print(json.load(sys.stdin)["event_id"])')

curl -sS "$BASE_URL/gradio_api/call/run_research_once/$EVENT_ID"
```

### 脚本调用

```bash
python3 scripts/call_openclaw_mirosearch.py \
  --api-mode gradio \
  --base-url "http://127.0.0.1:8080" \
  --query "量子计算最新进展" \
  --mode balanced \
  --search-profile parallel-trusted
```

---

## 输出篇幅建议

- `compact`：快速短总结，适合告警、机器人回执、低 token 成本场景
- `balanced`：默认推荐，结论清晰且保留必要背景
- `detailed`：超长报告，适合研究归档与汇报材料

## 限流与降级建议

- 当出现 `429 rate_limit_exceeded` 时，先指数退避，再按以下顺序降级：
  1. 原参数重试 1 次
  2. `thinking -> balanced`
  3. `balanced -> quota`
  4. `search_profile` 切换为 `searxng-only`（省额度）或 `parallel-trusted`（提质量）

## 面向 AI Agent 的调用约定

- **优先使用 FastAPI API**，支持异步并发与任务持久化
- FastAPI 终态：轮询 `status=completed` 或 `status=cached`，取 `result` 字段
- Gradio 终态：等待 SSE `event: complete`
- `No \boxed{} content found in the final answer.` 代表未收敛，不代表服务故障
- 推荐将 `output_detail_level` 显式传入，避免依赖服务端默认值
- 推荐始终传入 `caller_id`，便于隔离并发任务和定向取消
- 可读取 SSE 的 `stage_heartbeat` 事件展示阶段进度（检索/推理/校验/总结）
