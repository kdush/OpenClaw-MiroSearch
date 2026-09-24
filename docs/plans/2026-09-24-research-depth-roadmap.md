# 谛听研究深度与证据可信度优化方案（评审回填版）

- **日期：** 2026-09-24（v2，回填外部评审意见）
- **状态：** 评审意见已合入本文档；路线图事项未动工。代码侧仅 §0 本 PR 阻塞项在工作区进行中（未提交）
- **分支：** `feat/diting-research-quality-ui`
- **v2 变更说明：** 按评审修正两处过期事实（provider 覆盖门槛、API 报告模式）；新增本 PR 合并门槛（§0）、数据契约（阶段 A）、统一完成标准（§7）；Q2/Q3 移出快赢批次改为与 M2 同批交付；移除未实测的工期估计；M3 计数口径、M5 范围按评审裁决收窄。
- **关联文档：** [2026-09-24-jev-search-borrowing.md](./2026-09-24-jev-search-borrowing.md)（对标评审，已同步更正两处事实错误）

---

## 0. 本 PR 合并门槛：新证据使旧 AGREE 失效

先于一切路线图事项。**问题：** 第 3 轮取得 AGREE 后，第 4 轮新搜索或新抓取带来相反证据时，旧裁决及其支撑的早停倒计时必须失效，否则模型在证据已被推翻的情况下照旧收敛。

**行为契约：**

- 新证据进入主历史后，对当前证据版本重裁决；
- CONFLICT / UNKNOWN / 裁决调用失败 / 次数耗尽，一律不得强制总结（fail-closed，继续研究）；
- 冲突解除并重新获得 AGREE 时，倒计时从当前回合重新起算。

**回归测试覆盖：** 新搜索带来反证、仅抓取带来反证、冲突解除后重新 AGREE、裁决调用失败、并行批次；每批最多裁决一次，整次运行维持 3 次裁决上限。

**范围纪律：** M1–M5 不混入本修复。

**实现现状（2026-09-24 核实）：** 版本绑定与撤销机制已在工作区实现（`evidence_revision` 绑定 `_should_early_stop_clue_chase`，orchestrator.py:697-712；`_bump_evidence_revision` :714-716；`_revoke_stale_early_stop_countdown` :718 起，配套 test_deep_early_stop_exit.py / test_parallel_scrape_budget.py 修改），未提交；合并前须按上述清单逐项核验。

## 1. 项目定位与取舍判据

**定位（已与产品负责人确认）：** 谛听是「质量/验真优先」的可信研究 agent——可信域名表、证据一致性门禁（fail-closed）、总结失败时的诚实降级报告，近期投入全部围绕「结论可信」。两个定位裁决：

1. **api-server 算产品面** —— 报告口径必须与 gradio-demo 一致，不能只在 demo 补丁层做引用要求。
2. **谛听未来会往「全源情报分析」方向走** —— 图像/影像证据因此纳入路线图（仍非本期实施，见规划项 P1）。

**取舍判据（三条）：**

- 是否强化「来源可溯、结论可信、过程透明」；
- 能否按部署方实际配置**自动分层运行**：配置参差是常态（有人全配、有人只配部分），同一套代码须在低配档位真跑起来、在高配档位自动升档（见 §5）；
- 是修完已有半成品，还是开新能力战线。

## 2. 现状核查（已验证的代码事实；v2 修正两处）

### 2.1 研究循环：平铺，不是递归

- 主循环是扁平轮次循环：`apps/miroflow-agent/src/core/orchestrator.py:1802`（`max_turns` 如 `conf/agent/demo.yaml:16` = 20）。
- 线索追踪已有雏形：`src/core/lead_tracker.py`，正则从助手文本抽线索（`extract_leads_from_text` :186-288），每轮注入 1 条（`_inject_lead_followup`，orchestrator.py:958-1005），deep 模式 follow-up 上限 2（`deep_efficiency.py:17`）。**无层级、无递归。**
- 早停门禁：数值条件 + 无工具 LLM 一致性裁决（`_should_early_stop_clue_chase`，orchestrator.py:697-712；`generate_agreement_check`，answer_generator.py:703-742），conflict/unknown fail-closed，最多裁决 3 次；裁决已绑定证据版本（§0）。
- 子代理循环（orchestrator.py:1338）与 `task_planner` 工具存在但**未被任何 demo 配置启用**。

### 2.2 证据模型：纯文本，零图像

- 搜索命中结构 `SearchResult{position,title,link,snippet,source,extra}`（`libs/miroflow-tools/.../providers/base.py:10-32`）；Tavily `include_images: False`。
- `scrape_url` 只接受 html/text/pdf/json/xml（`search_and_scrape_webpage.py:1051-1062`），**og:image、缩略图、任何图像/影像均不采集、不存储、不引用**（已核实）。
- 现成但未启用的资产：`libs/miroflow-tools/src/miroflow_tools/mcp_servers/vision_mcp_server.py`（图 → VLM 文字描述），不在任何 demo 配置中。

### 2.3 引用：demo 层有内联 [N]，契约未上移（v2 修正）

- demo 的 prompt patch 要求内联 `[N]` 引用 + 文末 References（`apps/gradio-demo/prompt_patch.py:60-79`）。
- core 基准 summarize prompt 只要求 `\boxed{}` 短答（`prompt_utils.py:238-300`）。
- **评审更正：API 侧已有 `research_report_mode` 开关**（`apps/api-server/services/profile_resolver.py:410/431/452`，以 hydra override 追加）——缺口不在「API 能不能出报告」，而在**内联引用契约与校验未上移**（M2 据此重述）。旧稿「api-server 与 demo 报告口径不一致」的笼统说法作废，准确表述是：报告模式两边都有，引用契约只有 demo 有。
- References 汇总只保留 `{url, title}`，**organic 结果中的 snippet 被丢弃**（`apps/gradio-demo/main.py:3070-3073`，已亲验）；实时搜索卡片也在事件过滤器中剥掉 snippet（main.py:1366-1378）。
- `[N]` 引用在报告渲染时解析为可点击 chip（`_linkify_reference_citations`，main.py:2482-2548）。

### 2.4 图谱：只有一张未渲染的 mermaid 图

- 全库**无实体抽取、无关系三元组、无图导出**。
- 唯一「图」是从 ≤4 条冲突要点生成的 mermaid 流程图（`report_presentation.py:168-234`），有 CSS 卡片包裹（main.py:2949-2954），但**前端没有 mermaid 渲染器，实际显示为代码块**（`static/js/` 全部 8 个文件已核实，无渲染器）。
- `apps/visualize-trace` 是 Flask 线性 trace 查看器，不是图。

### 2.5 交叉验真：域名级已有，来源/结论级缺失（v2 修正）

- 可信域名表两处（orchestrator.py:76-89；`conf/agent/demo_verified_search.yaml:21-39`）。
- 每次搜索有 0-1 置信分（`_evaluate_confidence`，search_and_scrape_webpage.py:296-385），喂给早停门禁；报告级有 high/mid/low 置信标签（report_presentation.py:334-350）。
- **评审更正：provider 覆盖门槛已按本路由可用 provider 数钳制**（search_and_scrape_webpage.py:318-331，"Cap coverage by providers allowed on *this route*"），单 provider 下门禁可达有测试（`test_search_confidence_policy.py:57`）。旧稿「单 Serper key 下 parallel-trusted 结构性不可达、触发无止境补搜」的说法**已过期作废**。
- **无按来源、按结论的置信分级**（差距仍在）。

### 2.6 演示层

- 无独立来源面板；证据折叠进 `<details>`（main.py:2939-2967）。
- 报告走 Gradio markdown 渲染，**无图像展示能力**（唯一 `<img>` 是 logo data-URI）。

## 3. 与五个讨论点的差距结论

| 讨论点 | 现状 | 差距 |
|---|---|---|
| 图像/影像证据引用 | 全链路零图像 | 全新能力（采集→描述→引用→渲染） |
| 图谱关系丰富 | 一张未渲染的 mermaid | 渲染缺失 + 内容单薄（仅冲突点） |
| RAG 式结论引用 | demo 有内联 [N]；References 只有链接 | snippet 简述丢弃；引用契约未上移 core/API |
| 递归顺藤摸瓜 | 平铺循环 + 每轮 1 条线索、上限 2 | 无层级递归 |
| 交叉验真 | 域名级 + 一致性门禁 | 来源级/结论级缺失；独立支持计数口径待 M3 定义 |

## 4. 阶段方案与依赖顺序（v2 按评审重建）

### 阶段 A：基线与数据契约（M1 的前置设计，先于一切 UI 改动）

**A-1 基线固定：** 用现有验收脚本（`apps/miroflow-agent/scripts/run_acceptance_live.py --out-dir <dir>`）现场生成并**冻结**至少三组样例：一组 SERP 摘要足够、一组需全文核查、一组相互矛盾来源。每组记录：报告、来源列表、搜索/抓取次数、模型调用与 token、耗时、失败路径。后续每阶段与同一基线对比（对比维度见 §7）。

**A-2 三个概念分离：** 「检索命中」≠「可引用来源」≠「支持某结论的独立证据」。搜索 provider/引擎只是发现渠道：同一 URL 被多个引擎命中**不增加**独立支持数；同一报道被转载**不能**简单按域名加一。

**A-3 来源注册表：** 在工具返回与 Orchestrator/报告层之间建立**不依赖 LLM message_history** 的注册表，字段至少含：稳定 `source_id`、原始/规范化 URL、原始发布方或域名、发现 provider、获取时间、模态、状态（仅摘要 / 已抓正文 / 抓取失败）、摘要或正文定位。历史压缩或 `summary_keep_tool_result` 丢掉旧工具 JSON 后，来源与引用映射仍须可用。

**A-4 URL 规范化（保守规则）：** 只处理 scheme/host 大小写、fragment、明确的跟踪参数；**不照搬 jev-search 对整个路径转小写**；不删可能区分内容的查询参数。fixture 覆盖：重定向、路径大小写差异、同文多 URL。

**A-5 引用准入：** 报告只允许引用注册表内、确实返回过的来源；搜索摘要可被引用，但必须显式标「仅摘要」，不得伪装成读过全文。

### 阶段 B：M1 → M2（同批交付 Q2/Q3，来源与引用闭环）

- **M1 来源注册表落地：** 来源收集/去重/持久映射先行。
- **M2 引用契约上移：** 把 Gradio 的 `[N]` 规则上移到核心报告生成与 API 输出；API 与 Demo 共用同一 `source_id → [N] → URL` 契约。聚焦内联引用契约与校验（评审更正后的口径，非「API 只能短答」）。
- **Q2 References 展示：** 标题 + 可读摘要 + 链接 + 「仅摘要 / 已核查全文」状态。
- **Q3 实时摘要展示：** 搜索过程展示实时返回摘要——**外部不可信内容，渲染必须转义**；未进入注册表的临时命中不可被报告引用。
- **校验升级：** 不止数 `[N]`——每个 `[N]` 必须存在、指向实际返回过的来源、链接与状态一致；无法解析的引用删除或降级标注，**不造 URL**。

**阶段 B 验收清单：** 仅搜索命中、抓取成功/失败、同 URL 多 provider 命中、重定向、无有效来源、历史压缩后引用旧来源；API 与 Demo 对同一运行展示相同编号与目标链接；恶意摘要不能注入 HTML。交付物：字段契约文档、单元/集成测试、一份可查看的真实报告。

### 阶段 C：M3 → Q4（结论核验与拓扑）

- **M3 结论核验：** 先产出结构化映射「结论/关键主张 → source_id → 支持/反驳/未知 → 依据片段」，**再**计算独立支持数。计数单位 = 能明确支持该主张的**独立原始来源**——provider 数、检索命中数、域名数都不能直接代替。转载/同一原文合并计数；互相矛盾的可信来源在报告中显式保留。证据不足或独立性无法判定时写「未核实/来源不足」，**不让 prompt 猜一个精确 N**。`report_structure.py` 承担可机械验证的结构检查；语义支持判断需明确的裁决结果与失败回退，**不把结构门称作事实核验**。
- **M3 fixture：** 同 URL 两引擎命中、跨域转载、两来源支持同一主张、两个可信来源互相反驳、摘要与全文冲突、无依据结论。
- **Q4 拓扑丰富：** 只画**已验证的**结论—来源关系；未知/反驳边用不同标记；不把「可点击」误呈现为「已证实」。节点从「议题 + ≤4 冲突点」扩展到结论/证据缺口（`report_presentation.py:ensure_content_analysis_and_topology`，prompt/模板层工作）。

### 并行小轨道（各自独立小 PR，按依赖排期）

- **Q1 渲染器：** Mermaid 安全渲染 + 纯文本回退。它只是展示，**不赋予图中关系真实性**。
- **M4 线索链：** 记录搜索、追问、跳转与结果的 trace/event，展示「为什么继续查」；与「什么证实了结论」（M3）职责分离，不混用。数据基础：`lead_tracker.py` 的 question/source/turn/priority/followed。
- **M5 基础配置自适应（范围收窄）：** 本期只覆盖检索 provider 与 profile 选择。显式严格路由（如 searxng-only）保持严格；自动模式按**实际可用且健康的** provider 选档，记录生效档位、原因与降级路径；明确超时/缺凭据的回退规则。测试矩阵：单 Serper、单 SearXNG、多 provider、无凭据、运行中一路超时；各档置信门槛可达且带搜索次数上限。LLM 网关、视觉能力、复杂多源排序**留待后续独立设计**。立项依据：按实际可用/健康 provider、查询质量与降级行为论证（覆盖门槛钳制已存在，不再以「结构性不可达」为由）。

### 规划项 P1（本期不实施）

**图像/影像证据管线：** 因「全源情报分析」方向纳入路线图。建议路径：启用 `vision_mcp_server` → 关键图生成「URL + 一句说明 + VLM 描述」作为**佐证引用**（不作直接证据）。**前置条件：图像搜索 key、VLM 成本验证、出处校验能力**——三者齐备前图像只能是弱证据（图像最易伪造，验真工具链缺失是已明示的遗留风险）。

### 暂缓项（建议暂缓，维持评审裁决）

| # | 事项 | 暂缓理由 |
|---|---|---|
| D1 | 递归子代理顺藤摸瓜 | 必须先与简单的线索预算方案**对比质量、延迟与 token**，确认增益再上。glm-5.3-flash 为 reasoning 模型，thinking token 计费，深度扇出成本不可控（现配置 deep follow-up 上限即只有 2）；时延：demo 每事件并发 2。 |
| D2 | 实体三元组/知识图谱画像 | 作为过程控制是科研项目；作为事后输出只是一次 LLM 调用的装饰。「画像」诉求由 Q4 拓扑丰富化覆盖，不单独立项。 |
| D3 | 多 provider 交叉验真特性调优 | 调优需多 key 环境实测，维持暂缓；架构由 M5 自动分层承接。 |

## 5. 环境约束与配置分层原则

**本地当前档位（最低配）：** 仅配置 Serper key（`SEARCH_PROVIDER_ORDER=serper`、`DEFAULT_SEARCH_PROFILE=serp-first`）；无 SearXNG、无 SerpAPI/Tavily。部署给 agent 的工具只有 `google_search` 与 `scrape_url`。LLM 网关为 Zhipu BigModel，`glm-5.3-flash` 是 reasoning 模型，thinking token 计在 `max_tokens` 内——任何增加 LLM 调用次数的方案都要算这笔账。

**配置分层设计原则：** 部署方配置必然参差——有人全配、有人只配部分。能力必须按**实际配置**自动分层：

- 单 provider 档：serp-first + 域名级验真（agreement 门禁），阶段 B/C 各项都必须在此档位可跑；
- 多 provider 档：自动升级为 multi-route/parallel-trusted 交叉验真（M5 负责升档逻辑）；
- 未来图像搜索档：P1 的前置条件（图像搜索 key）本质也是一次升档；
- 任何档位的硬性要求（如高置信域名数、覆盖门槛）必须随配置自适应，**禁止出现「所选 profile 的要求在当前配置下结构性不可达」的故障形态**（覆盖门槛已按路由钳制，v2 核实；其余门槛照此原则逐一校准可达性）。

**验收材料需现场生成：** `docs/acceptance/` 已删除，跑 `run_acceptance_live.py --out-dir <dir>` 重新产出（阶段 A 基线即由此固定）。

## 6. 评审已裁决事项（2026-09-24，替代 v1 评审要点）

1. **M3 计数口径** = 独立原始来源（provider 数/命中数/域名数不代替）；v1 的「provider × 域名双维」提议作废。跨引擎共识计数只是检索排序信号。
2. **M2 聚焦**内联引用契约与校验；API 已有 `research_report_mode`，非「API 只能短答」。
3. **M5 本期范围**只做检索 provider + profile 自适应；LLM 网关、视觉、复杂多源排序后续独立设计。
4. **Q2/Q3 依赖来源契约**，与 M2 同批交付，移出快赢批次；Q1 不受影响可先行。
5. **URL 规范化**采保守规则；不学 jev-search 整路径转小写、不删可能区分内容的查询参数。
6. **猜测性请求**（意图未出先搜索）：低配环境消耗配额，先测收益再开。
7. **预算熔断**是我们自行设计的候选功能，jev-search 并未提供（对标文档已更正，见关联文档）。
8. **工期不预设**：移除一切未经实测的估计（如 v1 的「半天量级」）。
9. **P1 证据等级**：只作佐证引用、不作直接证据；前置条件加出处校验。

## 7. 统一完成标准（每个 PR 逐一对照）

- 写清**输入/输出契约、失败回退、对应 fixture、可观察指标**；
- 跑相关自动测试；有凭据时 `run_acceptance_live.py --out-dir` 复测；
- 与阶段 A 基线对比：**无依据结论数、错误引用数、冲突处理、p50/p95 耗时、模型调用与 token、检索/抓取量、实际供应商费用**；
- 先证明质量提升与成本边界，再确定默认开启与优化目标；不在文档中预设未经实测的工期。

## 8. 建议实施顺序

**本 PR 阻塞项（§0）与文档事实修正（已完成）→ 阶段 A（基线/数据契约）→ M1 → M2 + Q2/Q3 → M3 → Q4；Q1、M4、M5 按依赖并行做独立小 PR。** P1 待前置条件齐备后另行立项；D1–D3 维持暂缓。
