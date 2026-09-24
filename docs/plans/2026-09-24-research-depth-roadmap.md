# 谛听研究深度与证据可信度优化方案（评审稿）

- **日期：** 2026-09-24
- **状态：** 评审中，未动工
- **分支：** `feat/diting-research-quality-ui`
- **背景：** 团队零星讨论提出五个方向（图像证据、图谱丰富、RAG 式引用、递归顺藤摸瓜、交叉验真），本文档对照代码现状核查后给出取舍判断与分层路线图，供评审。

---

## 1. 项目定位与取舍判据

**定位（本次讨论已与产品负责人确认）：** 谛听是「质量/验真优先」的可信研究 agent——可信域名表、证据一致性门禁（fail-closed）、总结失败时的诚实降级报告，近期投入全部围绕「结论可信」。同时确认两个定位问题：

1. **api-server 算产品面** —— 报告口径必须与 gradio-demo 一致，不能只在 demo 补丁层做引用要求。
2. **谛听未来会往「全源情报分析」方向走** —— 图像/影像证据因此从「不该做」转为**规划项**纳入路线图（仍非本期实施，见 §4.3）。

**取舍判据（三条）：**

- 是否强化「来源可溯、结论可信、过程透明」；
- 能否按部署方的实际配置**自动分层运行**：配置参差是常态（有人全配、有人只配部分），同一套代码须在低配档位真跑起来、在高配档位自动升档（配置分层原则，见 §5）；
- 是修完已有半成品，还是开新能力战线。

## 2. 现状核查（已验证的代码事实）

### 2.1 研究循环：平铺，不是递归

- 主循环是扁平轮次循环：`apps/miroflow-agent/src/core/orchestrator.py:1802`（`max_turns` 如 `conf/agent/demo.yaml:16` = 20）。
- 线索追踪已有雏形：`src/core/lead_tracker.py`，用正则从助手文本抽取线索（`extract_leads_from_text` :186-288），每轮注入 1 条（`_inject_lead_followup`，orchestrator.py:958-1005），deep 模式 follow-up 上限 2（`deep_efficiency.py:17`）。**无层级、无递归。**
- 早停门禁：数值条件 + 无工具 LLM 一致性裁决（`_should_early_stop_clue_chase`，orchestrator.py:690-701；`generate_agreement_check`，answer_generator.py:703-742），conflict/unknown fail-closed，最多裁决 3 次。
- 子代理循环（orchestrator.py:1338）与 `task_planner` 工具存在但**未被任何 demo 配置启用**。

### 2.2 证据模型：纯文本，零图像

- 搜索命中结构 `SearchResult{position,title,link,snippet,source,extra}`（`libs/miroflow-tools/.../providers/base.py:10-32`）；Tavily `include_images: False`。
- `scrape_url` 只接受 html/text/pdf/json/xml（`search_and_scrape_webpage.py:1051-1062`），**og:image、缩略图、任何图像/影像均不采集、不存储、不引用**（已核实）。
- 现成但未启用的资产：`libs/miroflow-tools/src/miroflow_tools/mcp_servers/vision_mcp_server.py`（图 → VLM 文字描述），不在任何 demo 配置中。

### 2.3 引用：demo 层有内联 [N]，References 丢信息

- demo 的 prompt patch 要求内联 `[N]` 引用 + 文末 References（`apps/gradio-demo/prompt_patch.py:60-79`）。
- **core 基准 summarize prompt 只要求 `\boxed{}` 短答，无引用要求**（`prompt_utils.py:238-300`）——api-server 与 demo 报告口径不一致。
- References 汇总只保留 `{url, title}`，**organic 结果中的 snippet 被丢弃**（`apps/gradio-demo/main.py:3070-3073`，已亲验）；实时搜索卡片也在事件过滤器中剥掉 snippet（main.py:1366-1378）。
- `[N]` 引用在报告渲染时解析为可点击 chip（`_linkify_reference_citations`，main.py:2482-2548）。

### 2.4 图谱：只有一张未渲染的 mermaid 图

- 全库**无实体抽取、无关系三元组、无图导出**。
- 唯一「图」是从 ≤4 条冲突要点生成的 mermaid 流程图（`report_presentation.py:168-234`），有 CSS 卡片包裹（main.py:2949-2954），但**前端没有 mermaid 渲染器，实际显示为代码块**（`static/js/` 全部 8 个文件已核实，无渲染器）。
- `apps/visualize-trace` 是 Flask 线性 trace 查看器，不是图。

### 2.5 交叉验真：域名级已有，来源/结论级缺失

- 可信域名表两处（orchestrator.py:76-89；`conf/agent/demo_verified_search.yaml:21-39`）。
- 每次搜索有 0-1 置信分（`_evaluate_confidence`，search_and_scrape_webpage.py:296-385），喂给早停门禁；报告级有 high/mid/low 置信标签（report_presentation.py:334-350）。
- **无按来源、按结论的置信分级。**
- 多 provider 交叉档位 `parallel-trusted` 在本地环境**结构性不可达**（单 Serper key，覆盖度门槛永远不满足）。

### 2.6 演示层

- 无独立来源面板；证据折叠进 `<details>`（main.py:2939-2967）。
- 报告走 Gradio markdown 渲染，**无图像展示能力**（唯一 `<img>` 是 logo data-URI）。

## 3. 与五个讨论点的差距结论

| 讨论点 | 现状 | 差距 |
|---|---|---|
| 图像/影像证据引用 | 全链路零图像 | 全新能力（采集→描述→引用→渲染） |
| 图谱关系丰富 | 一张未渲染的 mermaid | 渲染缺失 + 内容单薄（仅冲突点） |
| RAG 式结论引用 | demo 有内联 [N]；References 只有链接 | snippet 简述丢弃；core 无引用要求 |
| 递归顺藤摸瓜 | 平铺循环 + 每轮 1 条线索、上限 2 | 无层级递归 |
| 交叉验真 | 域名级 + 一致性门禁 | 来源级/结论级缺失；多 provider 本地不可达 |

## 4. 分层方案与取舍

### 4.1 快赢批次（修半成品，半天量级，不动研究循环）

| # | 事项 | 依据 | 涉及位置 |
|---|---|---|---|
| Q1 | 引入 mermaid.js 渲染器，让关系拓扑图真正渲染 | 图已生成、卡片已包，显示为代码块是可见缺陷 | `apps/gradio-demo/static/js/`、`ui_layout.py` |
| Q2 | References 升级为「标题 + snippet 简述 + 链接」 | snippet 已在 organic 结果中，仅被丢弃；兑现「引用源简述+链接」 | `main.py:_collect_report_sources`（:3024） |
| Q3 | 实时搜索卡片恢复 snippet 展示 | 同上，事件过滤器别剥掉 | `main.py:1366-1378` |
| Q4 | **关系拓扑内容丰富**：节点从「议题 + ≤4 冲突点」扩展到结论/实体/证据缺口，让每个结论在图上可溯源 | 讨论点②「图谱丰富」的落地。现状 auto-append 只消费冲突 bullets（`report_presentation.py:168-234`），但结论、内联引用、lead 数据报告里都有——是 prompt/模板层工作，不动循环 | `report_presentation.py:ensure_content_analysis_and_topology`、summarize prompt |

### 4.2 中件批次（把可信做实）

| # | 事项 | 依据 | 涉及位置 |
|---|---|---|---|
| M1 | **References 溯源校验**：末尾引用与全程实际抓取 URL 注册表对账，防编造来源 | 直接兑现验真品牌承诺；为 2026-09-21 质量评审明确搁置、约定「report-sourcing 再提就捡起来」的事项 | 需打通 工具→orchestrator→报告；`answer_generator.py:_collect_evidence_sources` 已有解析逻辑可复用 |
| M2 | **内联引用要求从 demo patch 上提到 core summarize prompt** | 定位裁决①：api-server 是产品面，报告口径必须一致 | `prompt_utils.py:238-300`；demo patch 转为薄层 |
| M3 | **结论级交叉计数**：每条结论标注「N 个独立域名支持」 | 单 provider 环境下唯一可跑的交叉验真实现；与内联 [N] 复用同一引用体系 | summarize prompt + 报告结构校验（`report_structure.py`） |
| M4 | **线索链可视化**：把 lead_tracker 的 question/source/turn/priority/followed 渲染为「探索路径」 | 数据现成；过程透明是演示卖点（与进度文案、耗时计时同方向） | `lead_tracker.py` 数据透出 + demo UI |
| M5 | **检索能力按配置自动分层**：检测已配置的 provider/工具，自动选择或降级检索 profile 与置信门槛（单 key → serp-first + 域名级验真；多 key → parallel-trusted 交叉验真），任何档位缺配置时安全降级 | 用户配置必然参差（全配/部分配）。现状只有 provider 级可用性过滤（`ProviderRegistry.resolve_order`，`providers/registry.py:34`），profile 与置信门槛**不随配置自适应**——`parallel-trusted` 要求 `MIN_PROVIDER_COVERAGE=2`，单 key 部署永远满足不了，触发无止境补搜的已知故障，目前靠运维手动改三个环境变量规避 | `apps/api-server/services/profile_resolver.py:220-263`；`search_and_scrape_webpage.py:_evaluate_confidence` |

### 4.3 规划项（定位裁决②后纳入路线图，本期不实施）

| # | 事项 | 说明 |
|---|---|---|
| P1 | **图像/影像证据管线** | 因「全源情报分析」方向纳入路线图。建议路径：启用 `vision_mcp_server` → 关键图生成「URL + 一句说明 + VLM 描述」作为**佐证引用**（不作直接证据）；后续再评估图像搜索 provider 与 UI 图像展示。**遗留风险需评审时明确承认：图像本身最易伪造，图像验真（时间/地点/OOC 检测）工具链目前完全缺失**，在补齐验真能力前图像只能作为弱证据。前置条件：图像搜索 key、VLM 成本验证。 |

### 4.4 暂缓项及理由（建议暂缓，待评审确认）

| # | 事项 | 暂缓理由 |
|---|---|---|
| D1 | 递归子代理顺藤摸瓜 | 成本：glm-5.3-flash 为 reasoning 模型，thinking token 计费，深度扇出成本不可控（现配置 deep follow-up 上限即只有 2）；时延：demo 每事件并发 2；**质量增益无证据**。便宜的替代先行：调 lead 预算/优先级并测量，有数据再谈递归。 |
| D2 | 实体三元组/知识图谱画像 | 作为过程控制是科研项目；作为事后输出只是一次 LLM 调用的装饰。「画像」诉求由 Q4 拓扑图丰富化覆盖（prompt/模板层），不单独立项。 |
| D3 | 多 provider 交叉验真特性调优 | 端到端验证需要多 key 环境，本地不可达，维持暂缓；但架构前提改为 M5 的自动分层——**不是「不写」，而是「配了就自动升档、没配就安全降级」**。 |

## 5. 环境约束与配置分层原则（实施前必读）

**本地当前档位（最低配）：** 仅配置 Serper key（`SEARCH_PROVIDER_ORDER=serper`、`DEFAULT_SEARCH_PROFILE=serp-first`）；无 SearXNG、无 SerpAPI/Tavily。部署给 agent 的工具只有 `google_search` 与 `scrape_url`。LLM 网关为 Zhipu BigModel，`glm-5.3-flash` 是 reasoning 模型，thinking token 计在 `max_tokens` 内——任何增加 LLM 调用次数的方案都要算这笔账。

**配置分层设计原则（评审新增）：** 部署方配置必然参差——有人全配、有人只配部分。能力必须按**实际配置**自动分层，而不是按某一套固定环境设计：

- 单 provider 档：serp-first + 域名级验真（agreement 门禁），本期所有中件（M1-M5）都必须在此档位可跑；
- 多 provider 档：自动升级为 multi-route/parallel-trusted 交叉验真（M5 负责升档逻辑本身）；
- 未来图像搜索档：P1 的前置条件（图像搜索 key）本质也是一次升档；
- 任何档位的硬性要求（如置信门槛的 provider 覆盖数）必须随配置自适应，**禁止出现「所选 profile 的要求在当前配置下结构性不可达」的故障形态**（现状反例：单 key + parallel-trusted → 无止境补搜）。

**验收材料需现场生成：** `docs/acceptance/` 已删除，跑 `apps/miroflow-agent/scripts/run_acceptance_live.py --out-dir <dir>` 重新产出。

## 6. 评审要点

1. **4.2-M1 的 plumbing 范围**：溯源注册表需要工具层把每次抓取的 URL 透出到编排层再进报告，涉及三个模块的接口，请评审是否接受这个改动面。
2. **4.2-M3 的判定口径**：「独立域名支持数」按域名去重计数即可，还是要求不同 provider？单 provider 下只能做到前者。
3. **4.3-P1 的证据等级**：是否认可「图像只作佐证引用、不作直接证据」的边界？这决定了后续图像验真投入的优先级。
4. **4.4-D1 的触发条件**：递归顺藤摸瓜是否以「lead 预算调优后的测量数据」作为重新立项门槛？
5. **M5 的分层维度**：分层只按检索源（provider 数量/种类），还是 LLM 网关、工具集（未来的图像搜索）也纳入升档维度？档位对照表需在 M5 设计时明确。

## 7. 建议实施顺序

快赢 Q1–Q4（一个批次）→ M2（口径统一，改动小）→ M3 → M1 → M4。M5 与报告质量主线相互独立，可并行安排。P1 待图像搜索 key 与 VLM 成本验证后另行立项；D1–D3 维持暂缓。
