# Skill 使用方式（仅使用）

> 本文档只描述“如何使用 skill 完成检索任务”。
> 安装请看：`references/skill-install.md` 与 `references/install.md`。

默认服务地址：`http://127.0.0.1:8080`

## 1. 先选对 skill

- 简单搜索（快速找网页、单事实查询、成本优先）：
  - 推荐 `searxng` skill：`https://clawhub.ai/abk234/searxng`
- 深度检索或高质量检索（多来源交叉验证、核查、研究报告）：
  - 使用 `openclaw-mirosearch`（本 skill）

## 2. OpenClaw-MiroSearch 调用闭环

1. 探活：`GET /gradio_api/info`
1. 发起：`POST /gradio_api/call/run_research_once`
1. 轮询：`GET /gradio_api/call/run_research_once/{event_id}`
1. 终态：只以 SSE `event: complete` 为准
1. 失败或卡住：`POST /gradio_api/call/stop_current` 后重试（v0.1.9+ 可传 `caller_id` 定向取消，不影响其他并发任务）

## 3. 参数推荐模板

### 3.0 先按网络环境选路由

- 中国大陆（无代理/出海链路不稳定）：
  - 推荐 `search_profile=searxng-first`
  - 推荐搜索源顺序：`searxng,serpapi,serper`
  - 建议 SearXNG 引擎：`bing`、`baidu`、`sogou`、`yandex`
- 海外或有稳定代理：
  - 推荐 `search_profile=parallel-trusted`
  - 推荐搜索源顺序：`serpapi,searxng,serper`
  - 可启用 `google`、`duckduckgo`、`brave`、`startpage`、`wikipedia`
- 网络不确定：
  - 先用 `searxng-first` 起步，稳定后再切 `parallel-trusted`

- 常规检索（默认）：
  - `mode=balanced`
  - `search_profile=parallel-trusted`
  - `search_result_num=20`
  - `verification_min_search_rounds=3`
  - `output_detail_level=balanced`
- 高质量或事实核查：
  - `mode=verified`
  - `search_profile=parallel-trusted`
  - `search_result_num=30`
  - `verification_min_search_rounds=4`
  - `output_detail_level=balanced` 或 `detailed`
- 额度优先：
  - `mode=quota`
  - `search_profile=searxng-only`
  - `search_result_num=10` 或 `20`
  - `output_detail_level=compact`

## 4. 可直接复制的调用命令

```bash
python3 scripts/call_openclaw_mirosearch.py \
  --base-url "http://127.0.0.1:8080" \
  --query "中国大陆有哪些厂商推出了 OpenClaw 变体？" \
  --mode balanced \
  --search-profile parallel-trusted \
  --search-result-num 20 \
  --verification-min-search-rounds 3 \
  --output-detail-level balanced
```

## 5. 失败与降级

- 若结果出现 `No \boxed{} content found in the final answer.`：
  - 代表“本轮未收敛，可重试”，不代表服务离线。
- 若 SearXNG 页面出现“大量引擎超时”：
  - 优先判断是否网络可达性问题（不是服务崩溃）
  - 中国大陆无代理建议禁用 `google/duckduckgo/brave/startpage/wikipedia` 等高超时源
- 推荐降级顺序：
  1. 原参数重试 1 次
  1. `thinking -> balanced`
  1. `balanced -> quota`
  1. `search_profile` 切换为 `parallel-trusted`（质量优先）或 `searxng-only`（额度优先）
