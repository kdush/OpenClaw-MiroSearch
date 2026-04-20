# API 调用参考

v0.2.0 起提供两套 API，**AI Agent 推荐使用 FastAPI API**。

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
| `search_profile` | string | 否 | `searxng-first` | 检索路由 |
| `search_result_num` | int | 否 | `20` | 每轮检索结果数 |
| `verification_min_search_rounds` | int | 否 | `3` | 最少检索轮次（verified 模式） |
| `output_detail_level` | string | 否 | `detailed` | 输出篇幅 |
| `caller_id` | string | 否 | — | 调用方标识 |

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

`status` 枚举：`queued` → `running` → `completed` / `failed` / `cancelled`

### 3) SSE 流式监听

`GET /v1/research/{task_id}/stream`

返回 SSE 事件流，已完成任务返回历史事件，运行中任务实时推送：

```
event: start_of_workflow
data: {"workflow_id": "...", "input": [...]}

event: stage_heartbeat
data: {"phase": "检索", "turn": 1, "detail": "执行工具 google_search", ...}

event: tool_call
data: {"tool_name": "google_search", "tool_input": {...}}

event: done
data: {}
```

### 4) 取消任务

`POST /v1/research/{task_id}/cancel`

### 5) 健康检查

`GET /health`

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
- FastAPI 终态：轮询 `status=completed`，取 `result` 字段
- Gradio 终态：等待 SSE `event: complete`
- `No \boxed{} content found in the final answer.` 代表未收敛，不代表服务故障
- 推荐将 `output_detail_level` 显式传入，避免依赖服务端默认值
- 可读取 SSE 的 `stage_heartbeat` 事件展示阶段进度（检索/推理/校验/总结）
