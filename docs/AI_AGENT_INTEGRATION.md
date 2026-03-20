# AI Agent 接入指引

本文档面向调用 OpenClaw-MiroSearch 的上层智能体（如 OpenClaw、工作流 Agent、企业编排器）。

## 目标

- 以统一接口完成检索与研究，不再维护多版本调用分支
- 让上层 Agent 可控输出篇幅（精简/适中/详细）
- 在超时、限流、未收敛时具备稳定降级路径

## 接口原则

- 仅使用 `POST /gradio_api/call/run_research_once`
- 通过 `GET /gradio_api/call/run_research_once/{event_id}` 获取终态结果
- `event: complete` 才是唯一完成信号
- `verification_min_search_rounds` 仅在 `mode=verified` 生效
- 中间 `heartbeat` 可用于显示阶段进度，不可替代终态判断

## 请求参数

```json
{
  "data": ["<query>", "<mode>", "<search_profile>", 20, 3, "<output_detail_level>"]
}
```

- `query`：问题文本
- `mode`：研究模式
- `search_profile`：检索路由
- `search_result_num`：单轮条数（10/20/30）
- `verification_min_search_rounds`：最少检索轮次（仅 verified 生效）
- `output_detail_level`：`compact` / `balanced` / `detailed`

## 推荐模板

### 模板 A：普通研究（默认）

- `mode=balanced`
- `search_profile=parallel-trusted`
- `search_result_num=20`
- `verification_min_search_rounds=3`
- `output_detail_level=balanced`

### 模板 B：高可靠核查

- `mode=verified`
- `search_profile=parallel-trusted`
- `search_result_num=30`
- `verification_min_search_rounds=4`
- `output_detail_level=detailed`

### 模板 C：低成本快速响应

- `mode=quota`
- `search_profile=searxng-only`
- `search_result_num=10`
- `verification_min_search_rounds=1`
- `output_detail_level=compact`

## 按网络环境选择检索策略

上层 Agent 应将“网络环境”作为路由决策条件，而不是固定单一模板：

- 中国大陆（无代理或出海链路波动）：
  - 优先 `search_profile=searxng-first`
  - 检索源顺序建议：`searxng,serpapi,serper`
  - 失败策略：保持 `fallback`，不要直接并发所有海外源
- 海外或有稳定代理：
  - 优先 `search_profile=parallel-trusted`
  - 检索源顺序建议：`serpapi,searxng,serper`
  - 可启用并发聚合与置信补检
- 未知网络：
  - 首轮用 `searxng-first` 探测可达性
  - 连续 1-2 轮稳定后再提升到 `parallel-trusted`

建议 Agent 在启动阶段做一次轻量连通性采样（如 `bing/google/duckduckgo`），用结果决定初始模板，避免全量超时。

## 稳定性与重试

- 轮询超时：调用 `stop_current` 后再发起新任务（v0.1.9+ 支持传入 `caller_id` 定向取消，不影响其他并发任务）
- 返回 `No \\boxed{} content found in the final answer.`：按“未收敛”处理并重试
- 限流 `429`：服务端已支持多 Key 自动轮转（v0.1.9+），单 Key 限流时自动切换；调用方仍建议指数退避，必要时降级到 `mode=quota`
- 若看到长期 `running` 但无推进：检查最新 `heartbeat.data.stage`；系统会自动回收陈旧 `running` 为 `failed`

## 输出消费建议

- 只消费 `complete` 事件首项 Markdown
- 若需要机器二次处理，先保留原文，再做结构化抽取
- 对时效问题，优先保留“时间锚点 + 关键数字 + 来源”三要素
- 进度展示建议读取 `heartbeat.data.stage.phase`（检索/推理/校验/总结）与 `search_round`
