# 谛听研究深度与证据可信度优化方案（评审回填版）

- **日期：** 2026-09-24（v2，回填外部评审意见）
- **状态：** §0 已提交（0334af2），失败/空抓取不计证据与中性撤销提示已合入；阶段 A 基线与契约已冻结；M1 已完成离线验收；M2 + Q2/Q3 代码接线已接通；阶段 B 验收清单 fixture 已补齐（见 §阶段 B）；M3/Q4/Q1/M4/M5 的**代码、测试与主流程接线均已落地并逐项核验**（核验记录见 §9.3）。**真实 LLM 验收的欠费阻塞已解除**（§9.2，改用免费模型 `glm-4.7-flash`），M3 端到端与「一份可查看的真实报告」已达成；**但免费档容量有限**——§7 三件套复跑中 light 用例忠实通过，deep / hotspot 用例被 `429 code 1305` 限流打穿（§9.2.1），故全量 16 个用例仅 A、H5B 通过；另有主动暂缓的 P1/D1–D3。实现层无欠账。
- **分支：** `feat/diting-research-quality-ui`
- **v2 变更说明：** 按评审修正两处过期事实（provider 覆盖门槛、API 报告模式）；新增本 PR 合并门槛（§0）、数据契约（阶段 A）、统一完成标准（§7）；Q2/Q3 移出快赢批次改为与 M2 同批交付；移除未实测的工期估计；M3 计数口径、M5 范围按评审裁决收窄。
- **关联文档：** [2026-09-24-jev-search-borrowing.md](./2026-09-24-jev-search-borrowing.md)（对标评审，已同步更正两处事实错误）；[2026-09-24-phase-a-baseline-and-source-contract.md](./2026-09-24-phase-a-baseline-and-source-contract.md)（阶段 A 设计稿：A-1 基线协议 + A-2~A-5 数据契约，代码事实已核实）

---

## 0. 本 PR 合并门槛：新证据使旧 AGREE 失效

先于一切路线图事项。**问题：** 第 3 轮取得 AGREE 后，第 4 轮新搜索或新抓取带来相反证据时，旧裁决及其支撑的早停倒计时必须失效，否则模型在证据已被推翻的情况下照旧收敛。

**行为契约：**

- 新证据进入主历史后，对当前证据版本重裁决；
- CONFLICT / UNKNOWN / 裁决调用失败 / 次数耗尽，一律不得强制总结（fail-closed，继续研究）；
- 冲突解除并重新获得 AGREE 时，倒计时从当前回合重新起算。
- 失败抓取与空正文不算新证据（不递增 `evidence_revision`）；撤销收敛提示须中性表述，不得预设“新证据相冲突”。

**回归测试覆盖：** 新搜索带来反证、仅抓取带来反证、冲突解除后重新 AGREE、新证据继续支持原结论可再 AGREE、裁决调用失败、并行批次、失败/空抓取不计证据；每批最多裁决一次，整次运行维持 3 次裁决上限。

**范围纪律：** M1–M5 不混入本修复。

**实现现状（2026-09-24 核实）：** 版本绑定与撤销机制已提交于 `0334af2`（`evidence_revision` 绑定 `_should_early_stop_clue_chase`；`_bump_evidence_revision`；`_revoke_stale_early_stop_countdown`；配套 `test_deep_early_stop_exit.py` / `test_parallel_scrape_budget.py`）。失败/空抓取不计证据：`_classify_scrape_result` 解析工具内层 JSON（`success=false` 计失败、`success=true` 且正文为空计空抓取、仅成功且正文非空才递增证据版本并计抓取配额）；撤销提示为中性核查措辞。`ruff` 干净、`apps/miroflow-agent` 全量 227 项测试通过。

## 1. 项目定位与取舍判据

**定位（已与产品负责人确认）：** 谛听是「质量/验真优先」的可信研究 agent——可信域名表、证据一致性门禁（fail-closed）、总结失败时的诚实降级报告，近期投入全部围绕「结论可信」。两个定位裁决：

1. **api-server 算产品面** —— 报告口径必须与 gradio-demo 一致，不能只在 demo 补丁层做引用要求。
2. **谛听未来会往「全源情报分析」方向走** —— 图像/影像证据因此纳入路线图（仍非本期实施，见规划项 P1）。

**取舍判据（三条）：**

- 是否强化「来源可溯、结论可信、过程透明」；
- 能否按部署方实际配置**自动分层运行**：配置参差是常态（有人全配、有人只配部分），同一套代码须在低配档位真跑起来、在高配档位自动升档（见 §5）；
- 是修完已有半成品，还是开新能力战线。

## 2. 阶段 A 核查快照（M1/M2 实施前；v2 修正两处）

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
- **Q2 References 展示：** 标题 + 可读摘要 + 链接 + 「仅摘要 / 已抓取全文 / 抓取失败（仅摘要）」状态；抓取成功不代表事实核实。
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

**基线已冻结：** `docs/acceptance/baseline-2026-09-24/`（07871fd）。M1 本轮按用户要求仅做离线验收，未复跑真实报告，因此不声称耗时、token 或报告质量较基线改善。

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

## 9. 实施现状回填（2026-09-28）

本轮按上表顺序推进，逐项落地代码与验收测试；表中「接入主流程」列已全部为 ✅（M3/M5 的接线在后续提交中补齐），逐项核验记录见 §9.3。

| 项 | 代码 | 测试 | 接入主流程 |
|---|---|---|---|
| 阶段 B 验收清单 | —（补 fixture） | ✅ 重定向 / 同 URL 多 provider / 恶意摘要注入 / 压缩后引用 / API↔Demo 编号一致 | ✅ |
| M3 结论核验 | ✅ `src/core/claim_verification.py` | ✅ 6 项 fixture + fail-closed + 主张抽取 | ✅ `orchestrator.run_main_agent` 在 `prepare_user_facing_report` 前调用，由 `resolve_claim_verification_config` 控制（deep 默认开），fail-closed 不阻断出稿 |
| Q4 拓扑丰富 | ✅ `report_presentation.ensure_content_analysis_and_topology` | ✅ 支持/反驳/未知边、证据缺口、未登记来源不画 + 真实入口链路 | ✅ 随 M3 接线生效（`claim_map`/`source_registry` 由编排层传入） |
| Q1 渲染器 | ✅ `static/js/mermaid_render.js` | ✅ strict + 回退契约 | ✅ |
| M4 线索链 | ✅ `lead_tracker.snapshot()/get_trace()` + `lead_trace` 日志元数据 | ✅ 快照/reason/序列化 | ✅ |
| M5 分档 | ✅ `providers/tiering.py` | ✅ 单 Serper / 单 SearXNG / 多 provider / 无凭据 / 中途超时 + 门槛消费 | ✅ `search_and_scrape_webpage.perform_search` 解析档位并写入 `searchParameters.provider_tier`，`min_provider_coverage` 作为 confidence 门槛（串行回退路径同样消费） |

**阶段 B 交付物现状：** 字段契约文档 = `2026-09-24-phase-a-baseline-and-source-contract.md` §3.2（已与实现一致）；单元/集成测试 = 本轮补齐；"一份可查看的真实报告" 仍需凭据跑 `run_acceptance_live.py`（离线不可完成）。

**仍未开始：** P1 图像证据管线（前置条件未齐备）、D1–D3（维持暂缓）。

**§7 统一完成标准提醒：** 上述各项均未与阶段 A 基线做"无依据结论数 / 错误引用数 / p50-p95 耗时 / token / 检索抓取量"对比——该对比需真实凭据复测。**对比工具已就位**（§9.4），额度恢复后一条命令即可出表；M3 与 M5 接线后的净收益（额外一次无工具 LLM 调用 vs 无依据结论数下降）同样待实测确认，故 M3 默认只在 deep 档开启。

### 9.1 真实检索验收（M5，已完成）

`scripts/verify_search_provider_tier_live.py` 在真实检索服务上验证 M5，不依赖 LLM 凭据。
单 provider 部署（仅 Serper）实测结果：

- `provider_tier.tier = single-provider`，`min_provider_coverage = 1`
- `confidence.constraints.min_provider_coverage = 1`（门槛跟随档位，不再结构性不可达）
- `degraded_from = ""`（无 profile 概念时诚实留空，不写错误的 provider 名）
- 真实检索成功返回结果，`retrieval_quality` 显示 `providers=1/1`

### 9.2 端到端验收（2026-09-28 用免费模型打通）

**原阻塞根因有两个，均已解决：**

1. **LLM 付费额度耗尽**（`HTTP 429 code 1113 余额不足或无可用资源包`）。
   **已绕过**：同一把智谱 key 对**免费模型**照常放行，欠费只挡付费模型。
   改用标准端点 `https://open.bigmodel.cn/api/paas/v4` + `glm-4.7-flash`
   （永久免费、支持 tool calling）即可，**无需新账号**。
2. **`thinking` 参数不兼容**（已修复）：summary/fast 阶段无条件下发
   `thinking={"type":"disabled"}`，而部分模型「始终思考」，直接返回 `400 code 1210`。
   因 `SUMMARY_AGENT_TYPES` 的 `max_retries=1` 不重试，`final_summary` 必然失败——
   **即使余额充足，报告也不会生成，M3 永远没有执行机会**。已改为**提供商中立的能力
   自适应**：先尝试下发，被 400 拒绝就剥离该可选参数重试一次（**不占 `max_retries`**）。
   判定只看状态码，不匹配任何模型名或提供商文案——新增模型无需改代码。

**实测结果（`glm-4.7-flash`，零成本）：**

| 用例 | status | 门禁 | 检索/抓取 | LLM 调用 | 耗时 | M3 证据 |
|---|---|---|---|---|---|---|
| **H5B** | **completed** | **`gates_pass=True`** | 3 轮 / 2 次 | 9 | 448.8s | **`claim_verification_ran=True`、`has_claim_topology=True`、注册表 28 条** |

**结论：M3 端到端证据已补齐**（此前仅有离线契约测试）。轻量用例 A 的表现与
限流下的失败样例见 §9.2.1——**免费档并非总能自愈**，需要 10+ 次调用的
deep/hotspot 用例会被限流打穿。

### 9.2.1 §7 基线对比实测（2026-09-28，免费模型）——**深用例被限流打穿**

同配置复跑基线的 A/B/H1 三件套（`overrides` / `effective_config` 与基线**逐字一致**，
唯一变量是模型 `glm-5.3-flash` → `glm-4.7-flash`）。产物见
`docs/acceptance/artifacts/free-model-20260928/`（含 `COMPARE-vs-baseline.md`）。

| 用例 | 基线 | 免费模型复跑 | 判定 |
|---|---|---|---|
| A（light） | completed, 34.8s, 2 次调用 | **completed, 52.0s, 2 次调用** | ✅ 行为忠实复现（同样跳过检索） |
| B（deep） | completed, 339.6s, 11 次调用 | **failed, 663.2s, 8 次调用, 429×8** | ❌ `Final summary produced no usable answer` |
| H1（hotspot_detailed） | completed, 427.8s, 13 次调用, 门禁 True | **failed, 167.8s, 0 次调用, 429×13** | ❌ 门禁 False，8 项缺失 |

**诚实结论：免费档只能支撑轻量用例。** 需要 10+ 次 LLM 调用的 deep / hotspot 用例
在高频限流下必然失败——**这是环境容量问题，不是代码问题**（同代码在 H5B 上
13:54–14:01 时段跑通，14:06–14:20 时段即被限流打穿，同一模型、同一代码）。
§7 的正式对比（代码改动净收益）**仍需付费额度或低峰时段**才能完成；
`compare_acceptance_runs.py` 已正确报出 8 项方向性回归并以退出码 1 卡口。

**尚未跑到的用例**：全量验收清单共 16 个用例（A–E、H1–H5B、L1–L3），
仅跑过 A、H5B（通过）与 B、H1（限流失败）；其余 12 个未跑。

### 9.3 逐项目标核验（2026-09-28，离线可核部分全部通过）

对文档列出的每一项「目标」按「落地位置 + 可执行证据」逐一核对，结论：**实现层无欠账**。
下表只列可离线复现的证据（测试名可 `pytest -k` 直达）。

| 目标 | 落地位置 | 核验证据 |
|---|---|---|
| §0 证据版本绑定与旧 AGREE 撤销 | `orchestrator._bump_evidence_revision` / `_revoke_stale_early_stop_countdown` / `_classify_scrape_result` | `test_deep_early_stop_exit.py`（26 例）：新搜索反证、仅抓取反证、冲突解除重新起算、支持性新证据可再 AGREE、裁决失败 fail-closed、无新证据不重裁、3 次上限；`test_parallel_scrape_budget.py`（2 例） |
| A-1 基线冻结 | `docs/acceptance/baseline-2026-09-24/`（A/B/H1 三组） | `BASELINE.md`；§9.4 工具控制组可复现其全部数字 |
| A-3 / M1 来源注册表 | `src/core/source_registry.py` | `test_source_registry_contract.py`（16 例）：同 URL 多 provider 合并、重定向别名、抓取失败不覆盖成功、序列化往返（= 历史压缩后引用仍可用） |
| A-4 URL 规范化（保守） | 同上 | 同文件：scheme/host 小写、去 fragment 与跟踪参数、**路径大小写保留**、保留可能区分内容的查询参数 |
| A-5 / M2 / Q2 / Q3 引用与展示契约 | `report_structure.enforce_citations`、`build_source_references` | `test_report_citations_contract.py`（13 例）：仅摘要可引用、未登记引用降级、无来源不造 URL、恶意摘要不注入 HTML；`apps/gradio-demo/tests/test_render_markdown.py::test_references_numbering_matches_core_contract`（API↔Demo 同编号同链接） |
| M3 结论核验 | `src/core/claim_verification.py`，`orchestrator._adjudicate_report_claims` 接线 | `test_claim_verification.py`（25 例）：6 项 fixture（同 URL 两引擎、跨域转载、两来源支持、两可信来源互驳、仅摘要不确定、无依据）+ 抽取上限 + 解析回退；`test_claim_verification_wiring.py`（7 例）钉住编排层调用与 fail-closed |
| Q4 拓扑丰富 | `report_presentation.ensure_content_analysis_and_topology` | `test_report_presentation.py`（20 例）：支持/反驳/未知边、证据缺口、未登记来源不画、无 claim_map 时不加边、抽取主张端到端入图 |
| Q1 渲染器 | `apps/gradio-demo/static/js/mermaid_render.js` | `test_static_assets.py::test_mermaid_renderer_is_strict_and_keeps_plaintext_fallback`（strict + 纯文本回退） |
| M4 线索链 | `lead_tracker.snapshot()` / `get_trace()`；`orchestrator` 日志元数据 `lead_trace` | `test_lead_tracker.py`（11 例）：快照/reason/序列化 |
| M5 分档 | `providers/tiering.py`，`search_and_scrape_webpage.perform_search` 消费档位与门槛 | `test_provider_tiering.py`（9 例）：单 Serper、单 SearXNG、多 provider 升档、无凭据、中途超时降档、严格路由不静默扩档、每档门槛可达；§9.1 真机证据 |

**本轮额外修复（提供商中立）：** summary/fast 阶段的「关闭思考」参数由**无条件下发**改为
**能力自适应**——先尝试下发，被 `400` 拒绝即剥离该可选参数重试一次且**不消耗
`max_retries`**；判定只看状态码，不匹配任何模型名或提供商文案。此前用模型名前缀黑名单
的做法已废弃（违背「系统不绑定特定 LLM 提供商」的架构约束）。

**测试与 lint 状态：** `apps/miroflow-agent` 338 passed / 7 skipped；
`libs/miroflow-tools` 129 passed；`ruff check apps libs` 全绿、`format --check` 干净。

**仍未完成（仅此两类）：**

1. **真实 LLM 验收的覆盖度**：欠费阻塞已解除（§9.2，改用免费模型 `glm-4.7-flash`），
   M3 端到端与「一份可查看的真实报告」**已达成**（H5B 实测）。但**免费档容量不足以
   支撑 deep/hotspot 用例**：§7 三件套复跑中 A 通过、B 与 H1 被限流打穿（§9.2.1）。
   全量 16 个用例中 4 个已跑（A、H5B 通过；B、H1 限流失败），其余 12 个未跑；
   §7 的正式对比仍需付费额度或低峰时段。
2. **主动暂缓**：P1 图像证据管线（前置条件未齐备）、D1–D3（维持评审裁决）。

### 9.4 §7 基线对比工具（本轮补齐）

§7 要求每个 PR 与阶段 A 基线逐项对比，但此前**没有任何工具能执行它**——这是一处
「标准已定、无法执行」的缺口。本轮补齐：

```bash
cd apps/miroflow-agent
# 控制组（基线自比，应全部为 0）
python scripts/compare_acceptance_runs.py \
    --baseline ../../docs/acceptance/baseline-2026-09-24 \
    --candidate ../../docs/acceptance/baseline-2026-09-24

# 真实对比（额度恢复后）
python scripts/compare_acceptance_runs.py \
    --baseline ../../docs/acceptance/baseline-2026-09-24 \
    --candidate ../../docs/acceptance/artifacts/<new-run> \
    --out ../../docs/acceptance/artifacts/<new-run>/COMPARISON.md
```

- **不调用 LLM、不需要凭据**，故额度受限时仍可用（控制组自比即验证工具正确）；
- 覆盖 §7 全部可计算维度：无依据结论数、错误引用数、冲突处理、p50/p95 耗时、
  模型调用与 token、检索/抓取量；「实际供应商费用」按约定人工回填，工具不估算；
- 逐用例 + 运行级汇总，并单列候选新增信号（注册表条目 / 结论核验已跑 / 结论—来源拓扑）
  作为 M1/M3/Q4 接线后的可观察证据；
- 出现**方向性回归**时退出码非零，可直接作 CI 卡口；
- 测试：`tests/test_compare_acceptance_runs.py`（24 例，含用真实冻结基线做控制组的集成用例）。
