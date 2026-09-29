# 研究质量基线（阶段 A-1 冻结）

- **执行日期：** 2026-09-24
- **分支/代码：** `feat/diting-research-quality-ui`，HEAD `8aa2b9c` + 本提交的 harness 改动
- **复测命令：** `cd apps/miroflow-agent && uv run python scripts/run_acceptance_live.py --out-dir <dir> --cases A,B,H1`
- **产物：** `case_*.json`（记录）、`case_*_summary.md`（报告全文）、`acceptance_results.json`（汇总）；完整日志在 `logs/`（gitignore，仅本地）
- **后续每个路线图 PR 交付时，须按下表维度与本基线对比**（对比维度见路线图 §7）

## 环境快照

| 项 | 值 |
|---|---|
| LLM | Zhipu BigModel 网关，`glm-5.3-flash`（reasoning 模型，thinking token 计入 max_tokens），全部调用经单 client 路由（model_route_hits 无旁路） |
| 检索 provider | **仅 serper 可用**（SERPAPI/TAVILY key 为空、SEARXNG_BASE_URL 为空）——parallel-trusted 声明下实际退化为单 provider 档；覆盖门槛已按路由钳制 |
| 凭据来源 | `apps/gradio-demo/.env`（gitignore；harness .env 回退路径，值不打印、不入产物） |

## 三组用例结果

| 用例 | 场景 | status | 耗时 | 搜索轮/次 | 抓取 | LLM 调用 | token in/out | 抽得来源 | 门禁 |
|---|---|---|---|---|---|---|---|---|---|
| A | SERP 摘要应足够（法国首都） | completed | 34.8s | 0/0 | 0 | 2 | 8008/34 | 0 | — |
| B | 需全文核查（2023 全球 CO2 排放） | completed | 339.6s | 5/6 | 1 | 11 | 103764/14091 | 39（全 snippet_only） | — |
| H1 | 相互矛盾来源（ZCode 上传争议） | completed | 427.8s | 7/18 | 8 | 13 | 165184/18026 | 61（4 fetched / 57 snippet_only） | hotspot_detailed 全过 |

补充：B 在第 5 轮触发早停（`early_stop_triggered=True, turn=5`，域名聚合口径）；H1 线索追踪 2 条全部 follow-up；B/H1 的 `retrieval_confidence_passed` 均为 False（工具侧 confidence 门控未通过：B 因高置信域名命中 0/2——CO2 议题命中的科学期刊不在可信域名表内；未引发无止境补搜，轮次上限生效）。

## 诚实发现（基线记录，勿在后续 PR 中粉饰）

1. **用例 A 未真正走到检索**：light 模式下模型对「法国首都」直接作答，零搜索。「SERP 摘要足够」场景在本次采样中未触发检索路径，A 的基线价值是记录「简单事实题跳过检索」这一行为本身。
2. **日志派生的来源清单天然不完整**：LLM 侧 sanitizer 将工具结果截断至约 4000 字符（`...(truncated)`），故每轮搜索只能恢复前 ~8 条 organic（B：5 轮×20 条 → 仅 39 个唯一 URL；H1：7 轮×20 条 → 61 个）；成功抓取状态仅在 scrape JSON 未超截断限时可识别（H1 8 次成功抓取仅识别出 4 次 fetched；B 的唯一一次抓取未能标记）。**这正是 A-3「注册表不得依赖 message_history」的量化证据**——M1 的 SourceRegistry 是结构性修复，落地后本清单口径将被注册表取代。
3. **抽取器曾空转**：原 `_extract_route_traces_from_log` 期望日志中不存在的 `step["tool_calls"]` 键，从未产出过数据（死代码）；本次已替换为可工作的 token/来源抽取器。

## 对比维度（路线图 §7）

后续每个 PR 复测同用例同配置，逐项对比：**无依据结论数、错误引用数、冲突处理、p50/p95 耗时、模型调用与 token、检索/抓取量、实际供应商费用**（费用按 provider 控制台实际读数人工回填，不在 harness 内估算）。
