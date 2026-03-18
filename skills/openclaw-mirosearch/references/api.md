# API 调用参考

默认地址：`http://127.0.0.1:8080`

## 1. 单次研究接口

### 1) 发起任务（兼容接口）

`POST /gradio_api/call/run_research_once`

请求体：

```json
{"data": ["<query>", "<mode>", "<search_profile>"]}
```

扩展接口（推荐）：

`POST /gradio_api/call/run_research_once_v2`

```json
{"data": ["<query>", "<mode>", "<search_profile>", 30, 4]}
```

其中：

- 第 4 项：`search_result_num`（10/20/30）
- 第 5 项：`verification_min_search_rounds`（verified 模式生效）

返回：

```json
{"event_id": "..."}
```

### 2) 轮询结果

`GET /gradio_api/call/run_research_once/{event_id}`

扩展接口轮询：

`GET /gradio_api/call/run_research_once_v2/{event_id}`

返回 SSE 文本；读取 `event: complete` 对应 `data`，第一项即最终 Markdown。

终态约定：

- 以 `event: complete` 作为结束信号。
- 若 `complete` 正文为 `No \boxed{} content found in the final answer.`，表示本轮未收敛，建议执行降级重试，而不是判定服务宕机。

## 2. 停止当前任务

`POST /gradio_api/run/stop_current`

请求体：

```json
{"data": []}
```

## 3. 查询接口信息

`GET /gradio_api/info`

## 4. cURL 示例

```bash
BASE_URL="http://127.0.0.1:8080"
QUERY="中国大陆有哪些厂商推出了 OpenClaw 变体？"
MODE="balanced"
PROFILE="parallel-trusted"

EVENT_ID=$(curl -sS -H 'Content-Type: application/json' \
  -d "{\"data\":[\"$QUERY\",\"$MODE\",\"$PROFILE\",30,4]}" \
  "$BASE_URL/gradio_api/call/run_research_once_v2" | python3 -c 'import sys,json;print(json.load(sys.stdin)["event_id"])')

curl -sS "$BASE_URL/gradio_api/call/run_research_once_v2/$EVENT_ID"
```

## 5. 脚本调用

使用：`scripts/call_openclaw_mirosearch.py`

```bash
python3 scripts/call_openclaw_mirosearch.py \
  --base-url "http://127.0.0.1:8080" \
  --query "中国大陆有哪些厂商推出了 OpenClaw 变体？" \
  --mode balanced \
  --search-profile parallel-trusted \
  --search-result-num 30 \
  --verification-min-search-rounds 4 \
  --api-name run_research_once_v2
```

## 6. 可观测性检查（是否真的多路）

如果结果仍像单路，可在最终 Markdown 中重点检查是否出现：

- `provider_mode`
- `providers_with_results`
- `route_trace`
- `confidence`

若 `providers_with_results` 只有 1，说明当前有效搜索源仍只有一路。

## 7. 限流与降级建议

- 当出现 `429 rate_limit_exceeded` 时，先指数退避，再按以下顺序降级：
  1. 原参数重试 1 次；
  2. `thinking -> balanced`；
  3. `balanced -> quota`；
  4. `search_profile` 切换为 `searxng-only`（省额度）或 `parallel-trusted`（提质量）。
