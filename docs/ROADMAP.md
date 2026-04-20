# OpenClaw-MiroSearch 路线图（版本化）

更新时间：2026-04-20  
当前版本：`v0.2.0`  
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

### `v0.1.8`

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

### `v0.1.12`（结果缓存）

已纳入能力：

- **ResultCache**：内存 LRU + TTL 结果缓存（`src/cache/result_cache.py`），相同 query+mode+profile+detail_level 命中缓存避免重复消耗搜索配额与 LLM tokens
- **双入口集成**：`gradio-demo` `run_research_once` 和 `api-server` `POST /v1/research` 均集成缓存命中逻辑
- **环境变量配置**：`RESULT_CACHE_MAX_SIZE`（默认 128）、`RESULT_CACHE_TTL_SECONDS`（默认 3600）
- **回归测试**：11 条 ResultCache 测试覆盖 LRU 淘汰、TTL 过期、key 确定性

版本定位：

- 成本优化版本，强调"重复查询零消耗 + 可配置缓存策略"

### `v0.1.13`（请求限流）

已纳入能力：

- **请求限流中间件**：基于内存滑动窗口计数器（`SlidingWindowCounter`），按 IP 或 Bearer Token 限流
- **限流配置**：`RATE_LIMIT_ENABLED`（默认开启）、`RATE_LIMIT_RPM`（默认 30 次/分钟）
- **路径白名单**：`/health`、`/docs` 等路径自动跳过限流
- **容器化**：api-server Dockerfile 与 gradio-demo 对齐，HEALTHCHECK 指向 `/health`
- **回归测试**：6 条限流中间件测试（配额内通过、超额 429、bypass 路径、禁用模式、独立 key 计数）

版本定位：

- 安全增强版本，强调"请求级限流 + 容器化部署就绪"

### `v0.1.14`（当前 · pipeline 对齐 + 安全审查）

已纳入能力：

- **api-server pipeline 预加载重写**：对齐 gradio-demo 的 `load_miroflow_config` 模式，正确处理 Hydra 全局初始化状态
- **compose.yaml 集成**：`api` 服务监听 8090 端口，与 `app`（Gradio）并行运行
- **安全审查修复 7 项**：
  - 任务管理内存泄漏修复（`cleanup_stale_tasks` 定期清理）
  - 异常信息泄漏修复（pipeline 异常返回通用错误消息）
  - 废弃 API 替换（`asyncio.get_running_loop()`、FastAPI `lifespan`）
  - 输入校验增强（`ResearchRequest` 枚举约束）
  - 限流响应隐藏内部配置 + 定期清理桶
- **Dockerfile `--frozen` 修复**：容器无外网时 `uv run` 不再尝试下载依赖

版本定位：

- 安全加固与部署修复版本，强调"生产环境安全 + 容器离线可用"

### `v0.2.0`（生产化 · 当前版本）

核心主题：持久化、异步架构、搜索协议化

已纳入能力：

- ~~**SearchProvider 协议化**~~（✅ 已完成）：`SearchProvider` Protocol + `ProviderRegistry` 注册中心 + Serper/SerpAPI/SearXNG 三个 Provider 独立实现，主路由通过协议调用，新增搜索源只需实现 Protocol 并注册
- ~~**异步任务队列**~~（✅ 已完成）：基于 arq + Valkey 的异步研究任务调度，支持并发多任务、超时自动取消、状态持久化、SSE 流式事件推送
- ~~**持久化缓存**~~（✅ 已完成）：ResultCache 升级为 Valkey 后端，任务结果、元数据、事件流均持久化存储

版本定位：

- 生产化架构版本，强调"异步任务队列 + Valkey 持久化 + SSE 流式输出 + Docker Compose 多服务编排"

## 后续版本

### `v0.2.5`（质量增强 + 可观测性）

核心主题：检索质量、评测体系、Prometheus 可观测性

目标能力：

- **Prometheus 可观测性**：暴露 `/metrics` 端点（请求量、延迟 P50/P99、搜索源命中率、429 频次、LLM token 用量、失败原因分布），附带 Grafana dashboard JSON
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

### MCP Server（待定 · 按需启动）

> 降级说明：项目已通过 HTTP API + Skills 文档包实现完整的 Agent 接入能力。MCP 协议的增量价值有限，维护成本较高。仅在社区有明确需求时启动。

目标能力：

- 将 `run_research_once` 暴露为标准 MCP tool（stdio + SSE transport）
- 完整 tool schema（参数类型、枚举值、默认值）
- MCP 与 HTTP API 共享同一任务引擎

## 优先级说明

如果资源有限，建议优先投入以下三项：

1. ~~**SearchProvider 协议化**（v0.2.0）~~ — ✅ 已完成
2. ~~**异步任务队列**（v0.2.0）~~ — ✅ 已完成
3. **Eval Pipeline CI 化**（v0.2.5）— 质量提升的度量基础，没有评测就没有可量化的改进
4. **Prometheus 可观测性**（v0.2.5）— 生产运行必备，发现瓶颈和异常的基础设施
