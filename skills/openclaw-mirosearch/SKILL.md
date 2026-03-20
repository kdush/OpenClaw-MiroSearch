---
name: openclaw-mirosearch
description: 用于 OpenClaw 或其他智能体执行深度检索与高质量联网研究。安装与使用流程分离：安装看 skill-install/install，使用看 usage/api/modes。
---

# OpenClaw-MiroSearch（深度检索 Skill）

## 何时使用（先分流）

优先按任务复杂度选择 skill：

- 简单搜索（快速找网页、查一个事实、低成本优先）：
  - 推荐使用 `searxng` skill：`https://clawhub.ai/abk234/searxng`
- 深度检索或高质量检索（多来源交叉、核查、结构化报告）：
  - 使用本 skill（`openclaw-mirosearch`）

接口约束：

- 仅使用 `run_research_once` 单一接口，不再走历史双接口分支逻辑

## 文档分工（安装与使用分离）

- 只看安装：`references/skill-install.md`、`references/install.md`
- 只看使用：`references/usage.md`、`references/api.md`、`references/modes.md`

## 使用阶段执行顺序（仅运行时）

1. 先确认服务是否在线：`GET /gradio_api/info`
1. 使用统一接口 `run_research_once` 发起研究并轮询结果：`references/api.md`
1. 根据问题类型选择模式：`references/modes.md`
1. 根据交付需求选择篇幅：`detailed`（超长）/`balanced`（适中）/`compact`（精简）
1. 若卡住，先调用 `stop_current` 再重试

## 默认策略

- 默认组合：`mode=balanced` + `search_profile=parallel-trusted` + `output_detail_level=balanced`
- 强校验问题：`mode=verified` + `search_profile=parallel-trusted`
- 额度优先：`mode=quota` + `search_profile=searxng-only`
- 核查深度：`search_result_num=30` + `verification_min_search_rounds=4`
- 超长报告：`output_detail_level=detailed`

## 终态与降级重试

- 任务完成信号以 SSE `event: complete` 为准。
- 若返回 `No \boxed{} content found in the final answer.`，视为“本轮失败可重试”。
- 建议降级顺序：
  1. 原参数重试 1 次；
  1. `thinking -> balanced`；
  1. `balanced -> quota`；
  1. 检索策略改为 `parallel-trusted`（质量优先）或 `searxng-only`（额度优先）。
- 近期已修复“终态误报 running 导致前端一直生成中”问题；若再遇长时间等待，优先检查是否 429 限流。

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
- 若失败，返回“失败点 + 下一步排查命令”

## 面向 AI Agent 的执行要点

- 明确告知调用方：终态只认 SSE `event: complete`
- 返回内容中若出现 `No \boxed{} content found in the final answer.`，应走“重试/降级”，而不是判定服务离线
- 在推荐参数时，优先给出可直接复制的完整 6 参数模板
- 中间进度可读取 `heartbeat.data.stage`（阶段、回合、检索轮次），但不可作为终态信号
- 若任务日志出现陈旧 `running`，服务端会自动收敛为 `failed`，避免长期假运行
