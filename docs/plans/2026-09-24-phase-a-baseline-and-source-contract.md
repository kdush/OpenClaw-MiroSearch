# 阶段 A 设计稿：基线固定与来源数据契约

- **日期：** 2026-09-24
- **状态：** 阶段 A 已完成并冻结基线（07871fd）；M1 已完成离线验收。M2 + Q2/Q3 引用与展示契约已接通，**阶段 B 验收清单 fixture 已补齐并通过**（重定向 / 同 URL 多 provider / 恶意摘要注入 / 压缩后引用 / API↔Demo 编号一致，见 `tests/test_source_registry_contract.py`、`tests/test_report_citations_contract.py`）。真实报告验收仍需凭据跑 `run_acceptance_live.py`。
- **分支：** `feat/diting-research-quality-ui`（含 §0 修复 0334af2）
- **关联文档：** [2026-09-24-research-depth-roadmap.md](./2026-09-24-research-depth-roadmap.md)（阶段 A 定义见其 §4，完成标准见其 §7）
- **阶段 A 盘点与行号保留冻结时快照；M1 实施见 §6，M2 当前契约见 §5，不将实施前缺口视作当前现状。**

---

## 1. A-1 基线固定协议

### 1.1 用例选择（零新增定义，复用 harness 内置 CASES）

| 场景 | 用例 | 查询 | 配置要点 |
|---|---|---|---|
| SERP 摘要足够 | A | What is the capital of France? | light/compact，max_turns=5，无 env 补丁 |
| 需全文核查 | B | What were the global CO2 emissions in 2023? | verified/deep，min_search_rounds=3，max_turns=10 |
| 相互矛盾来源 | H1 | ZCode 上传争议（上传内容/跨境/官方回应/冲突点） | verified/deep + research_report_mode + 效率旋钮全开，hotspot_detailed 门禁 |

- 三用例均为 `run_acceptance_live.py` 现有定义（CASES :145-686），不新增。
- **provider 现实**：本机仅 Serper 有 key（.env 实测 SERPAPI/TAVILY/SEARXNG 均为空值）。H1 声明 parallel-trusted，实际退化为单 Serper——覆盖门槛按路由钳制（search_and_scrape_webpage.py:318-331），基线如实反映单 provider 档行为，符合路线图 §5 档位原则。
- 基线必须在含 0334af2 的本分支生成；M1+ 每个 PR 用同用例同配置复测对比。

### 1.2 harness 现状盘点（apps/miroflow-agent/scripts/run_acceptance_live.py）

**已记录**（record :908-929）：status、duration_seconds、final_boxed_answer、报告文件+摘录、route_traces（provider/provider_mode/success/organic_count/error，:743-793）、lead_steps_count、gate_evaluation、run_metrics。

**run_metrics 已有字段**（task_logger.py:116-146）：search_rounds、search_attempts、search_provider_hits、scrape_count、follow_up_searches、retrieval_confidence_passed、early_stop、stage_durations、model_route_hits（模型调用次数按 requested→responded 计）、failback、effective_config。

**对照 A-1 要求「报告、来源列表、搜索/抓取次数、模型调用与 token、耗时、失败路径」的缺口**：

| 要求 | 现状 | 结论 |
|---|---|---|
| 报告 / 搜索抓取次数 / 耗时 / 失败路径 | 已覆盖 | 无缺口 |
| 模型调用 | model_route_hits（task_logger.py:127） | 无缺口 |
| token | client 已累计 total_input/output/cache（openai_client.py:314-336，base_client.py:84），经 `format_token_usage_summary` 进入输出日志（output_formatter.py:215-216/:323-324） | **harness 未抽取** |
| 来源列表 | 日志 step 含完整工具结果 | **harness 未抽取** |
| 凭据 | `--credentials` 只认 JSON，默认远端 VM 路径（:1020-1024），本机不存在 | **无法本机启动** |

### 1.3 两处 harness 小改动（不动 core）

1. **凭据 .env 回退**：`_load_credentials`（:689-719）在 JSON 文件不存在时改为解析 `apps/gradio-demo/.env`（KEY=VALUE 行），沿用**现有 mapping 键表**（:692-709，只含 LLM 凭据与搜索 key）——不引入 .env 中的检索路由/置信度变量，保持 A/B 用例与历史轮次同构；符合「secrets 只进 .env」红线。
2. **record 补两字段**：
   - `token_usage`：从运行日志的 token 汇总行抽取数值（落盘路径为 output_formatter.py:215-216；实现时核对多模型路由下 `format_token_usage_summary` 覆盖哪些 client，需聚合则聚合）；
   - `source_urls`：沿 demo `_collect_report_sources` 口径（main.py:3024-3080）从日志抽取——搜索类工具 organic 的 link/url（工具集 {google_search, sogou_search}，main.py:2993）+ 抓取类工具入参 url（:2994-3000），按首见顺序去重；本期以 scrape 结果 `success`+`url/final_url`（search_and_scrape_webpage.py:1771-1778）对齐出「仅摘要/已抓取」状态。

### 1.4 产物与冻结

- out-dir：`docs/acceptance/baseline-2026-09-24/`。
- `logs/` 已被全局 .gitignore:215 忽略——完整日志留本地；**提交**：`case_*.json`、`case_*_summary.md`、`acceptance_results.json`、`BASELINE.md`。
- BASELINE.md 必记环境快照：分支+HEAD、实际生效 provider 集（本机=仅 Serper）、模型（`_compose_cfg` :738 强制 glm-5.3-flash）、执行日期、对比维度。
- 不预设工期与费用估计（路线图裁决 8）；费用按 provider 控制台实际读数人工回填，不在 harness 内估算。

### 1.5 对比维度（沿用路线图 §7）

无依据结论数、错误引用数、冲突处理、p50/p95 耗时、模型调用与 token、检索/抓取量、实际供应商费用。首次基线即本节产物，后续每 PR 逐一对照。

---

## 2. A-2 三个概念分离

| 概念 | 定义 | 现状映射 |
|---|---|---|
| 检索命中 hit | 单次工具调用返回的一条 organic 条目（`SearchResult{position,title,link,snippet,source,extra}`，dev_mcp_servers/providers/base.py:10-32） | 命中数只是检索排序信号，不代表独立支持 |
| 可引用来源 source | 进入注册表（§3）、按归一化 URL 去重后的条目 | 同 URL 多引擎命中 → 同一 source，discovery 记录发现渠道 |
| 独立证据 independent evidence | 能明确支持某结论的**独立原始来源**（M3 定义并计数；注册表只存事实数据，不做语义判定） | 现按域名聚合（orchestrator.py:535-541 `independent_source_domains`）——域名≠独立来源（跨域转载、同域多篇），M3 修正 |

orchestrator 已在 `_record_search_evidence`（:520-546）做检索后处理：provider 指标、confidence 门控、域名聚合、`_bump_evidence_revision`（:533）。注册表按 URL 级存原始事实，域名聚合保留不动（早停门禁继续用域名口径，M3 才换计数口径）。

---

## 3. A-3 来源注册表

### 3.1 存储位置

`SourceRegistry` 实例挂 **task_log**（与 run_metrics 同级，task_logger.py:292）——随日志持久化、随 pipeline 暴露，**不依赖 LLM message_history**：`summary_keep_tool_result`（answer_generator.py:447）或历史压缩丢掉旧工具 JSON 后，来源与引用映射仍完整（路线图 A-3 硬性要求）。

### 3.2 字段契约（v1）

| 字段 | 类型 | 来源 | 说明 |
|---|---|---|---|
| source_id | int | 自增 | 首见顺序编号；M2 的 `[N]` 直接映射 |
| raw_url | str | organic.link / scrape 入参 | 原样保留 |
| normalized_url | str | §4 规则 | **去重键** |
| domain | str | 复用 `_normalize_domain` | 与早停门禁同口径 |
| title | str | organic.title 原样 | 剥高亮标签属展示逻辑（main.py:3014-3021），不入库 |
| snippet | str | organic.snippet 原样 | Q2 展示与 M3 依据片段用；外部不可信内容，渲染必须转义（Q3 红线） |
| status | enum | §3.3 状态机 | snippet_only / fetched / fetch_failed |
| modality | str | 常量 "text" | P1 图像管线预留 |
| discoveries | list | {provider, turn, position} | 首见+重复命中全记录（M3：跨引擎共识只是排序信号） |
| first_seen_turn / last_seen_turn | int | 注册/再命中时 | |
| content_ref | str \| None | scrape 成功时 | 日志 step / tool_call 定位；正文不复制进注册表 |
| aliases | list[str] | redirect_chain / final_url | 重定向别名，指向同一 source_id |

### 3.3 状态机

```
snippet_only --scrape 成功--> fetched   （记 content_ref；final_url 归一后入 aliases）
snippet_only / fetch_failed --scrape 失败--> fetch_failed
fetched 为终态（重复抓取不降级）；fetch_failed 可经成功抓取转 fetched
```

### 3.4 注册 API 与挂载点（M1 落点，本期只定义）

- `register_search_hits(parsed, turn)`：挂 `_record_search_evidence`（orchestrator.py:520-546）——该处已有 parsed payload、轮次与 provider 指标；凡走此路径的检索类工具（google_search/sogou_search 等）一律注册。
- `mark_fetched(url, final_url, content_ref)` / `mark_fetch_failed(url)`：挂抓取槽位提交处——成功 `_commit_scrape_slot`（:627，定义 :598）、失败 `_release_scrape_slot`（:623/:671，定义 :590）；scrape_url 返回含 `{success, url, final_url, http_status, content, truncated, redirect_chain, error}`（search_and_scrape_webpage.py:1755-1778）。
- **暴露**：照 run_metrics 事件模式（pipeline.py:461-467：stream 事件 + 结果 payload + log 落盘），一次接入全部消费面。

**失败回退**：注册表写入是旁路记账——URL 解析/注册异常一律 log 后继续，**不得阻断研究主循环**；引用准入（§5）才是严格侧，两者方向相反，不可混淆。

### 3.5 消费方清单

| 消费方 | 现状 | M2+ 目标 |
|---|---|---|
| gradio-demo 报告 | `_collect_report_sources` 遍历 demo state（main.py:3024-3080），仅 {url,title}，snippet 丢弃（:3070-3073） | 改读注册表：source_id→[N]→URL 同一契约 |
| gradio-demo 渲染 | `_linkify_reference_citations`（main.py:2482-2548）只渲染不校验 | Q2 状态标注 + 校验升级 |
| gradio-demo 实时卡片 | 事件过滤器剥 snippet（main.py:1366-1378） | Q3 转义展示 |
| api-server | `research_report_mode` 已有（profile_resolver.py:410/431/452） | 同一契约输出 |
| 验收 harness | route_traces（provider 级，:743-793） | §1.3 的 source_urls 抽取 |

编号兼容：demo 现按首见顺序去重（main.py:3025-3033）、上限 30（:3002）——注册表编号即首见顺序，行为兼容；上限改为渲染层选择，注册表本身不设上限。

---

## 4. A-4 URL 归一化（保守规则）

**现状**：demo `_normalize_source_url` 仅 strip + http(s) 检查（main.py:3005-3011）；core 无任何归一化。

**规则（按序应用）**：

1. strip 空白；非 http(s) 丢弃（沿用 demo 现行为）；
2. scheme、host 转小写（DNS 大小写不敏感）；
3. 去默认端口 http:80 / https:443；
4. 去 fragment（`#` 及以后）；
5. 去跟踪参数**固定白名单**：`utm_*`、`gclid`、`fbclid`、`msclkid`、`igshid`、`mc_cid`、`mc_eid`——白名单外参数一律保留；
6. 路径原样（含大小写）；查询参数名值与顺序原样。

**明确不做**：整路径转小写（jev-search 教训，评审裁决 5）、删除白名单外参数、猜测 canonical、http 与 https 合并。

**别名**：重定向 `final_url` ≠ 请求 URL 时（redirect_chain，scrape 返回 :1776），final_url 归一后命中已有条目则互为 alias，不新增 source。

**fixture 清单（M1 交付）**：重定向（单跳/多跳）、路径大小写差异不合并、白名单跟踪参数差异合并、白名单外参数差异不合并、同文多 URL、非 http(s) 丢弃、默认端口、fragment 差异合并。

---

## 5. A-5 引用准入

1. 报告内联 `[N]` 必须解析到注册表内存在的 source_id；References 条目必须来自注册表。
2. `status=snippet_only` 的来源可被引用，但必须显式带「仅摘要」标注（Q2 渲染；M2 报告文本层约定），不得伪装已读全文。
3. 无法解析的 `[N]`：删除该引用标记或降级标注；**不构造 URL、不猜目标**（路线图阶段 B「校验升级」）。
4. 校验归属：机械可验证部分进 `report_structure.py`（M2 实施）；语义支持判断属 M3，需明确裁决结果与失败回退，不把结构门称作事实核验。

**M2 实施契约（代码接线，尚未运行验收）：**

- 最终总结统一注入注册表中的稳定 `source_id`、URL、标题、摘要、状态；历史正文省略后仅使用仍提供的摘要，不声称读过全文。
- `ReportStructureValidator.enforce_citations` 处理引用准入，`build_source_references` 从注册表生成 References；展示层不重新编号，也不为裸域名补造 HTTPS 链接。代码块与行内代码不当作引用处理。
- 报告可引用已抓取正文的来源或实际返回过的搜索命中；仅有失败抓取记录且没有搜索 discovery 的 URL 不可引用。未知编号、未登记链接、编号与链接不匹配分别降级标注；正文及 References 中的链接定义均参与核对，短答案沿用正文的准入结果。降级草稿的旧编号必须有可核对的链接目标，否则不沿用；无可引用来源时明确说明未经来源核实。
- References 包含稳定编号、标题、链接、原始返回摘要与状态。状态文案为「仅摘要」「已抓取全文（不代表事实核实）」「抓取失败（仅摘要）」；无摘要如实标记，不生成伪摘要。
- 搜索/抓取后及 `final_output` 前发送 `source_registry` 快照；pipeline 结束仍发送最终快照。事件、pipeline 返回、任务日志、API GET 快照与结果缓存均沿用 `{"entries": [...]}`，缓存缺少此字段视为未命中。
- 标题、摘要和 URL 均按其输出位置转义，链接仅允许 HTTP(S)。Q3 展示临时搜索命中不赋予引用资格；主张支持、独立证据计数与语义核验仍属 M3。

阶段 B 验收清单已补齐为可执行测试（`apps/miroflow-agent/tests/test_source_registry_contract.py`、`test_report_citations_contract.py`，以及 `apps/gradio-demo/tests/test_render_markdown.py` 的转义与 API↔Demo 编号一致性用例），`ruff` 干净、相关包全量测试通过。**仍未完成：** 有凭据条件下的真实报告验收（`run_acceptance_live.py --out-dir`）与 §7 的基线指标对比。

---

## 6. M1 实施要点（文件级清单）

- 新增 `apps/miroflow-agent/src/core/source_registry.py`：SourceRegistry + URL 归一化（纯函数，可单测）；
- `task_logger.py`：挂实例 + to_dict 落盘；
- `orchestrator.py`：`_record_search_evidence` 注册 + 抓取槽位处状态迁移（改动集中，不触碰 §0 的 evidence_revision 机制）；
- `pipeline.py`：成功、失败、取消路径均返回 `source_registry`，并发送同名事件；事件、结果、任务日志统一使用 `{"entries": [...]}`。
- `search_and_scrape_webpage.py`：并行/合并去重结果保留逐条 `discoveries[{provider, position}]`，不改变命中排序、数量上限或 provider 调用顺序；注册时补 `turn`。外部结果未给出可确定的归属时保留未知，不从出版商 `source` 字段猜引擎。
- `content_ref`：使用任务日志内 JSON Pointer（`/step_logs/{index}/metadata/result`），指向历史压缩前保存的抓取载荷；正文不复制到注册表或结果 payload。
- 已登记的重定向两端保留原编号、互为别名并同步成功状态；未登记的请求 URL 若重定向至已有来源，只补别名，不新增编号。中间跳转也入别名。
- 测试：仅更新现有测试；§4 边界场景用离线合成数据检查。按用户要求未跑本轮真机，不作基线性能或报告质量改善结论。
- demo/API 的消费切换属 M2+Q2/Q3，**不进 M1**。

---

## 7. 完成标准对照（路线图 §7 逐条）

| §7 条目 | 本阶段对应 |
|---|---|
| 输入/输出契约 | §3.2 schema、§3.4 挂载与暴露、§4 规则 |
| 失败回退 | §3.4 末段：注册旁路 log-继续；引用准入严格侧见 §5 |
| 对应 fixture | §4 fixture 清单（M1 交付） |
| 可观察指标 | §1.5 对比维度 |
| 自动测试 + 复测 | M1 交付时跑相关套件；A-1 复测命令固定为 §1.1 三用例 |
| 基线对比 | 首次基线即 §1 产物 |

---

## 8. 执行顺序

1. 本设计稿评审；
2. harness 两处小改动（§1.3）；
3. 跑 A/B/H1 → 冻结产物 + BASELINE.md；
4. M1 立项实施（以本文 §3/§4/§5 为验收依据）。
