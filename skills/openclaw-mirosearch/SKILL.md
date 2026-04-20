---
name: openclaw-mirosearch
description: 用于 OpenClaw 或其他智能体执行深度检索与高质量联网研究。v0.2.0 起推荐使用 FastAPI 异步 API。安装看 skill-install/install，使用看 usage/api/modes。
---

# OpenClaw-MiroSearch（深度检索 Skill）

## 何时使用（先分流）

优先按任务复杂度选择 skill：

- 简单搜索（快速找网页、查一个事实、低成本优先）：
  - 推荐使用 `searxng` skill：`https://clawhub.ai/abk234/searxng`
- 深度检索或高质量检索（多来源交叉、核查、结构化报告）：
  - 使用本 skill（`openclaw-mirosearch`）

## 两套 API（v0.2.0）

| API | 地址 | 适用场景 | 状态 |
|-----|------|----------|------|
| **FastAPI（推荐）** | `http://127.0.0.1:8090` | AI Agent 接入、生产环境 | ✅ 推荐 |
| Gradio API | `http://127.0.0.1:8080` | Demo 体验、浏览器交互 | 兼容保留 |

> v0.2.0 起，FastAPI API Server 采用异步任务队列（arq + Valkey），支持并发多任务、SSE 流式事件推送和任务状态持久化。**AI Agent 应优先使用 FastAPI API。**

## 文档分工（安装与使用分离）

- 只看安装：`references/skill-install.md`、`references/install.md`
- 只看使用：`references/usage.md`、`references/api.md`、`references/modes.md`

## 使用阶段执行顺序（FastAPI，推荐）

1. 确认服务在线：`GET /health`
2. 提交任务：`POST /v1/research` → 返回 `task_id`
3. 轮询状态或流式监听：
   - 轮询：`GET /v1/research/{task_id}`（`status` 为 `completed` 时取 `result`）
   - 流式：`GET /v1/research/{task_id}/stream`（SSE 事件流）
4. 根据问题类型选择模式：`references/modes.md`
5. 根据交付需求选择篇幅：`detailed`（超长）/`balanced`（适中）/`compact`（精简）
6. 若需取消：`POST /v1/research/{task_id}/cancel`

## 使用阶段执行顺序（Gradio，兼容）

1. 确认服务在线：`GET /gradio_api/info`
2. 发起研究：`POST /gradio_api/call/run_research_once`
3. 轮询结果：`GET /gradio_api/call/run_research_once/{event_id}`
4. 终态以 SSE `event: complete` 为准
5. 若卡住：`POST /gradio_api/call/stop_current` 后重试

## 默认策略

- 默认组合：`mode=balanced` + `search_profile=parallel-trusted` + `output_detail_level=balanced`
- 强校验问题：`mode=verified` + `search_profile=parallel-trusted`
- 额度优先：`mode=quota` + `search_profile=searxng-only`
- 核查深度：`search_result_num=30` + `verification_min_search_rounds=4`
- 超长报告：`output_detail_level=detailed`（研究总结区域将展示完整多章节报告，字数目标 ≥12000 字符，全量保留所有检索轮次信息）
- 网络分流：先看 `references/usage.md` 的"先按网络环境选路由"（中国大陆无代理优先 `searxng-first`，海外/有代理优先 `parallel-trusted`）

## 终态与降级重试

- **FastAPI**：任务状态为 `completed` 时，`result` 字段为最终 Markdown
- **Gradio**：以 SSE `event: complete` 作为结束信号
- 若返回 `No \boxed{} content found in the final answer.`，视为"本轮失败可重试"
- 建议降级顺序：
  1. 原参数重试 1 次
  2. `thinking -> balanced`
  3. `balanced -> quota`
  4. 检索策略改为 `parallel-trusted`（质量优先）或 `searxng-only`（额度优先）

## 资源文件

- Skill 安装：`references/skill-install.md`
- 安装部署：`references/install.md`
- Skill 使用：`references/usage.md`
- API 参考：`references/api.md`
- 模式选择：`references/modes.md`
- 调用脚本：`scripts/call_openclaw_mirosearch.py`

## 交付要求

- 给出可直接执行的命令
- 明确 `mode` 与 `search_profile` 的推荐值
- 对核查类问题给出 `search_result_num` 与 `verification_min_search_rounds`，并给出 `output_detail_level` 推荐值
- 若失败，返回"失败点 + 下一步排查命令"

## 面向 AI Agent 的执行要点

- **优先使用 FastAPI API**（`/v1/research`），支持异步任务队列与并发
- FastAPI 终态判定：轮询 `GET /v1/research/{task_id}`，`status=completed` 即为完成
- SSE 流式监听：`GET /v1/research/{task_id}/stream`，可实时获取 `stage_heartbeat` 进度事件
- Gradio 终态判定：以 `event: complete` 为准，`heartbeat` 仅做进度展示
- `No \boxed{} content found in the final answer.` 代表未收敛，应走重试/降级
- 推荐参数时，优先给出可直接复制的完整参数模板
- 调用脚本支持 `--api-mode fastapi`（默认）或 `--api-mode gradio`
