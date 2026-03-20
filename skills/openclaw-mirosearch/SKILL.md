---
name: openclaw-mirosearch
description: 用于 OpenClaw 或其他智能体调用 OpenClaw-MiroSearch。覆盖安装部署（Docker Compose 或 uv）、统一 API 调用（run_research_once）、模式与检索路由选择（mode + search_profile + 检索深度参数 + 输出篇幅档位）、以及卡住/超时/限流的降级重试排障。
---

# OpenClaw-MiroSearch

## 何时使用

当用户需要以下任一能力时使用本技能：

- 为 OpenClaw 接入联网检索
- 部署或安装 OpenClaw-MiroSearch
- 通过 API 调用研究服务
- 选择 `mode` 与 `search_profile`
- 控制检索深度：`search_result_num` 与 `verification_min_search_rounds`
- 控制总结篇幅：`output_detail_level=compact/balanced/detailed`
- 处理卡住、超时、无结果、噪声结果

接口约束：

- 仅使用 `run_research_once` 单一接口，不再走历史双接口分支逻辑

## 执行顺序

1. 先确认服务是否在线：`GET /gradio_api/info`
1. 不在线则按安装文档部署：`references/install.md`（优先 Docker Compose）
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
