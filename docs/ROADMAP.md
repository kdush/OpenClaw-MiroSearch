# OpenClaw-MiroSearch 路线图（版本化）

更新时间：2026-04-05  
当前版本：`v0.1.11`  
说明：已完成能力前置到已发布版本，未完成能力后移到后续版本。

## 已发布

### `v0.1.1`（已完成归档）

已纳入能力：

- 模式体系可用：`production-web` / `verified` / `research` / `balanced` / `quota` / `thinking`
- 检索路由可用：`searxng-first` / `serp-first` / `multi-route` / `parallel` / `parallel-trusted` / `searxng-only`
- `parallel-trusted` 路由支持“并发聚合 + 置信不足串行高信源补检”
- 交叉校验门槛落地：最少检索轮次与高置信来源门槛
- 稳定性保护：连续 LLM 失败保护与任务终态守卫
- Demo 搜索历史本地持久化（localStorage）与可删除交互
- 交叉校验提示词污染修复与回归测试
- 模型路由观测日志与关键环节模型路由优化

### `v0.1.2`（已完成归档）

已纳入能力：

- 对外研究接口统一：`run_research_once`（历史双端点已收敛）
- 统一单次研究请求参数：`query/mode/search_profile/search_result_num/verification_min_search_rounds`
- UI 输出默认“综合结果优先 + 过程折叠”，降低多轮中间稿干扰
- API 输出支持按篇幅档位控制渲染粒度，更适合程序化消费
- OpenClaw 技能包文档与调用脚本统一到单接口规范

版本定位：

- 可用增强版本，强调接口一致性、可调用性与输出可读性

### `v0.1.7`（已完成归档）

已纳入能力：

- 统一接口 `run_research_once` 增加 `output_detail_level` 参数（`compact/balanced/detailed`）
- 输出篇幅分层落地：`精简`（短）、`适中`（中）、`详细`（超长）
- 研究报告模式启用：总结阶段按档位执行结构化篇幅控制
- 详细档 token 上限提升，减少“详细仍过短”的截断问题
- API/Skill/脚本文档同步到 6 参数统一调用规范
- 阶段日志心跳：前端可见“检索/推理/校验/总结 + 回合/检索轮次”
- 陈旧 `running` 自动收敛为 `failed`，降低长时间假运行
- 搜索历史结果详情持久化增强：增加可见结果区兜底采集与存储分级压缩
- 网络环境适配指引：区分“中国大陆无代理 / 海外或有代理”两类默认检索策略
- 新增 SearXNG 自定义引擎配置模板（`deploy/searxng/settings.yml`），可按地域启停引擎
- Agent/Skill 文档同步支持“先探测网络再选路由”策略，降低全超时概率

版本定位：

- 质量增强小版本，强调“可控篇幅 + 长文输出完整性 + 网络环境鲁棒性”

### `v0.1.8`（当前）

已纳入能力：

- 修复 demo 模式"研究总结"区域仅显示 `\boxed{}` 一句话的渲染错误（`prompt_patch.py` 保留完整报告，移除标记而非截断）
- `detailed` 档位 token 上限全面提升：`summary_max_tokens` / `max_tokens` 8192→16384，`verification_max_tokens` 6144→12288，`tool_result_max_chars` 12000→20000，`max_turns` 16→20
- `detailed` 档位研究报告模式提示词重写：核心原则"全量保留、去重整合、禁止压缩"，每轮检索信息全部体现，多轮重复信息去重合并
- `detailed` 档位正文字数目标 6000→12000 字符，最小章节数 10→12
- `detailed` 档位总结过短重试阈值 1800→5000 字符，`balanced` 档位重试阈值 900→1500 字符
- 总结提示词基础版本移除简洁优先倾向，改为"全量保留优于简洁"
- 扩写提示词强化：要求逐轮检查覆盖，最终报告须比任何单轮检索输出更长更完整

版本定位：

- 输出质量修复版本，强调"全量信息保留 + 总结区域正确渲染 + detailed 档位真正超长输出"

### `v0.1.9`（配额韧性 · Key 层）

已纳入能力：

- **LLM Key 池轮转**：支持 `OPENAI_API_KEYS=key1,key2,key3` 环境变量，round-robin 分配；429 时自动切换到下一个 Key 重试，不再等待固定 `retry_wait_seconds`
- **429 感知退避增强**：在 `openai_client.py` 中识别 `openai.RateLimitError`，读取 `Retry-After` response header 作为等待时长；Key 全部耗尽才走指数退避兜底
- **搜索工具 Key 轮转**：`libs/miroflow-tools` 中 `search_and_scrape_webpage.py`、`serper_mcp_server.py`、`searching_google_mcp_server.py` 支持 `SERPER_API_KEYS` / `SERPAPI_API_KEYS` 多 Key 列表轮转
- **会话级 API 任务隔离**：`stop_current_api` 支持可选 `caller_id` 参数，按调用方定向取消；活动任务表从 `Set[task_id]` 改为 `{task_id: caller_id}` 映射，不再全局广播取消
- **KeyPool 通用模块**：`libs/miroflow-tools` 新增 `KeyPool` 类，线程安全的 API Key 轮转池，支持 round-robin、429 限速标记与冷却、全部耗尽时返回最短剩余冷却时间

版本定位：

- 配额韧性增强版本，强调"多 Key 自动轮转 + 429 感知退避 + 会话级定向取消"

### `v0.1.10`（可观测性基础 + 回归门禁）

已纳入能力：

- **结构化运行 metrics**：新增 `RunMetrics` dataclass，任务结束时聚合 429 次数、超时次数、Key 切换次数、模型路由命中、检索轮次、总耗时分段写入 `TaskLog`；暴露为 `GET /api/metrics_last` 端点
- **最小回归门禁**：新增 `test_output_detail_level_routing.py`（4 条）和 `test_model_failback.py`（4 条）pytest 测试；GitHub Actions `run-tests.yml` 在 PR/push 时自动运行
- **模型级 failback（轻量版）**：`OpenAIClient` 新增 `model_fallback_name` 配置和 `activate_fallback()` 方法；主模型连续失败达阈值时自动切换备用模型继续执行

版本定位：

- 可观测性与质量门禁版本，强调"结构化 metrics 采集 + 模型容灾 failback + CI 自动化回归"

### `v0.1.11`（API 层独立雏形）

已纳入能力：

- **FastAPI API Server**：新增 `apps/api-server/`，独立于 Gradio Demo 的标准 HTTP API 层
- **标准端点**：`POST /v1/research`（提交任务）、`GET /v1/research/{task_id}/stream`（SSE 流式进度）、`POST /v1/research/{task_id}/cancel`（取消）、`POST /v1/research/cancel`（按 caller_id 批量取消）、`GET /v1/metrics/last`（运行指标）、`GET /health`（健康检查）
- **Bearer Token 认证**：`API_TOKENS` 环境变量配置，留空跳过认证（开发模式）
- **回归测试**：9 条 api-server 测试（健康检查、认证、参数校验、404 路径），CI 同步覆盖

版本定位：

- API 层独立前置版本，为 v0.2.0 生产化奠基，Gradio 与 FastAPI 双入口并行

## 后续版本

### `v0.2.0`（生产化）

核心主题：API 层独立、运行时韧性、可观测性

目标能力：

- **API 层独立**：引入 FastAPI/Starlette 原生 API 层，提供标准 OpenAPI schema 与 SSE 流式输出；Gradio 仅作 Demo UI 保留，不再承担对外 API 职责
- **认证与限流**：API 层支持 Bearer Token 鉴权；基于 Valkey 实现请求级限流，防止外部调用方滥用
- **结果缓存**：相同 `query + mode + profile` 请求在可配置时间窗内命中 Valkey 缓存，避免重复消耗搜索配额与 LLM tokens
- **SearchProvider 协议化**：抽象 `SearchProvider` 协议接口，SearXNG / SerpAPI / Serper / Bing / 搜狗各实现一个 Provider，通过配置注册；新增搜索源不再需要改核心代码
- **同服务多 Key 轮转**：LLM 与搜索源支持多 API Key 池轮转，单 Key 耗尽或 429 时自动切换
- **模型级 failback**：主模型失败自动切换备用模型，按 `primary → secondary → fallback` 链路降级
- **结构化运行观测**：暴露 Prometheus `/metrics` 端点（请求量、延迟 P50/P99、搜索源命中率、429 频次、LLM token 用量、失败原因分布），附带 Grafana dashboard JSON
- **异步任务队列**：引入轻量任务队列（如 `arq`），支持并发多任务、任务优先级、超时自动取消与状态持久化
- **会话级任务隔离**：活动任务表、取消接口与停止动作按会话/调用方定向，不再全局广播取消
- **最小回归门禁**：CI 覆盖 `mode/search_profile/run_research_once` 核心路径的集成测试

验收标准：

- 长时间运行稳定，配额耗尽或单点异常时可自动降级
- 外部 Agent 可通过标准 HTTP + Bearer Token 调用，无需依赖 Gradio 协议
- Prometheus 指标可被 Grafana 面板正常采集与展示

### `v0.2.5`（MCP 标准暴露）

核心主题：让 MiroSearch 成为可被 AI IDE 与智能体原生发现的 MCP Server

目标能力：

- **MCP Server 模式**：将 `run_research_once` 暴露为标准 MCP tool（支持 stdio 与 SSE transport），上层 Agent（Cursor、Windsurf、Claude Desktop 等）可原生 MCP 接入
- **MCP 工具描述规范化**：提供完整的 tool schema（参数类型、枚举值、默认值），让 Agent 无需查阅文档即可正确调用
- **MCP 与 HTTP API 共存**：两种接入方式共享同一任务引擎，行为一致

验收标准：

- 在 Cursor / Windsurf 等 AI IDE 中添加 MCP Server 配置后，可直接通过 tool call 发起检索
- MCP 调用与 HTTP API 调用结果一致

### `v0.3.0`（质量增强）

核心主题：检索质量、评测体系、多语言支持

目标能力：

- **Eval Pipeline CI 化**：搭建可重复运行的评测流水线（golden QA pairs → 自动调用 → 自动打分），集成到 CI，利用 `apps/miroflow-agent/benchmarks/` 目录
- **多源融合排序**：多源并发检索结果引入 URL 级去重 + Reciprocal Rank Fusion（RRF），提升 `parallel` 与 `parallel-trusted` 模式的结果质量
- **多语言检索优化**：自动 query 语言检测 → 选择对应搜索引擎集；中文查询自动启用搜狗搜索（`tool-sogou-search`）；跨语言查询扩展（中文 query 自动生成英文变体并发检索）
- **研究结果持久化**：完成的研究报告按 query hash 存入 Valkey 或 SQLite，提供 `/search_history` 端点供 Agent 查询历史研究
- **结构化冲突检测报告**：时间窗 / 统计对象 / 统计口径 / 参与方定义的冲突自动检测与报告
- **数字事实专项评测集与自动回归评分**
- **高置信来源白名单分层**：按领域可配置的分级信任机制

验收标准：

- 数字类问答错误率可量化下降
- 输出可解释，证据链清晰
- 中文检索质量不低于英文同等复杂度查询
- CI 每次合并自动运行评测集并输出分数报告

### `v1.0.0`（生态与分发）

核心主题：一键部署、生态接入、正式发布

目标能力：

- **反向代理模板与 HTTPS 生产示例**：Nginx / Caddy / Traefik 至少一种
- **Helm Chart / 一键云部署**：提供 Kubernetes Helm Chart，并支持 Railway / Render 等平台一键部署 template
- **OpenClaw 技能包版本化发布**：技能包语义化版本号，附带发布变更日志
- **兼容矩阵自动验证**：Skill 版本与服务版本的兼容矩阵通过 CI 自动验证，不仅靠文档声明
- **里程碑验收清单与正式发布流程闭环**

验收标准：

- 新环境可按文档在 15 分钟内完成部署
- 外部智能体可按技能文档直接调用，无需额外沟通
- 兼容矩阵在 CI 中自动检测并阻止不兼容版本发布

## 优先级说明

如果资源有限，建议优先投入以下三项：

1. **API 层独立**（v0.2.0）— 解除 Gradio 耦合是后续所有生产化能力的前置条件
2. **MCP Server 模式**（v0.2.5）— 项目定位"面向智能体"的差异化核心能力，让 MiroSearch 直接出现在各大 AI IDE 的工具列表中
3. **结果缓存**（v0.2.0）— 投入极小，成本收益比最高
