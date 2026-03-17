---
name: openclaw-mirosearch
description: 用于 OpenClaw 或其他智能体调用 OpenClaw-MiroSearch。覆盖安装部署（uv + .env + 启动）、API 调用（run_research_once/stop_current/info）、模式与检索路由选择（mode + search_profile）、常见卡住与超时排障。
---

# OpenClaw-MiroSearch

## 何时使用

当用户需要以下任一能力时使用本技能：

- 为 OpenClaw 接入联网检索
- 部署或安装 OpenClaw-MiroSearch
- 通过 API 调用研究服务
- 选择 `mode` 与 `search_profile`
- 处理卡住、超时、无结果、噪声结果

## 执行顺序

1. 先确认服务是否在线：`GET /gradio_api/info`
1. 不在线则按安装文档部署：`references/install.md`
1. 使用 `run_research_once` 发起研究并轮询结果：`references/api.md`
1. 根据问题类型选择模式：`references/modes.md`
1. 若卡住，先调用 `stop_current` 再重试

## 默认策略

- 默认组合：`mode=balanced` + `search_profile=parallel-trusted`
- 强校验问题：`mode=verified` + `search_profile=parallel-trusted`
- 额度优先：`mode=quota` + `search_profile=searxng-only`

## 资源文件

- Skill 安装：`references/skill-install.md`
- 安装部署：`references/install.md`
- API 参考：`references/api.md`
- 模式选择：`references/modes.md`
- 调用脚本：`scripts/call_openclaw_mirosearch.py`

## 交付要求

- 给出可直接执行的命令
- 明确 `mode` 与 `search_profile` 的推荐值
- 若失败，返回“失败点 + 下一步排查命令”
