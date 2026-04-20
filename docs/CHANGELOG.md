# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- 规划：结构化冲突检测报告与专项评测集

## [0.2.0] - 2026-04-20

### Added

- **异步任务队列**：基于 arq + Valkey 实现异步研究任务调度
  - `services/task_store.py`：Valkey 持久化任务元数据、事件流、结果与取消标志
  - `services/task_queue.py`：arq 任务入队封装，连接池管理
  - `services/task_event_sink.py`：Pipeline 事件 → 持久化事件流适配器
  - `services/pipeline_runtime.py`：Hydra 配置工厂，管理 Pipeline 组件生命周期
  - `workers/research_worker.py`：arq Worker 消费任务，执行完整 Pipeline
  - `worker.py`：Worker 入口脚本，含 LLM 配置诊断日志
  - `settings.py`：Pydantic 统一配置管理（Valkey / 队列 / Worker / API）
- **SSE 流式端点重构**：切换到 `sse_starlette`，修复 `ServerSentEvent` 序列化 bug
- **任务状态查询**：`GET /v1/research/{task_id}` 返回完整任务元数据与事件计数
- **请求参数扩展**：`search_result_num`、`verification_min_search_rounds` 可由调用方指定
- **Docker 构建优化**：BuildKit 缓存挂载、PyPI 镜像源加速
- **Compose 编排**：新增 worker / valkey 服务定义，修复 working_dir 路径
- **测试覆盖 24 条新增**：
  - `test_research_queue_api`（7 条）：任务入队、缓存命中、状态查询、取消
  - `test_research_worker`（3 条）：Worker 成功/取消/失败场景
  - `test_sse_stream`（6 条）：SSE 完整生命周期、404、增量读取
  - `test_task_store`（8 条）：TaskStore 集成测试

### Changed

- `POST /v1/research` 响应从同步返回结果改为异步入队（返回 `task_id` + `status: accepted`）
- 研究端点从单进程阻塞改为 Worker 异步执行，支持并发多任务
- `deps.py` 精简：移除内存任务管理逻辑，改用 TaskStore/TaskQueue 服务层注入
- `.env.compose.example` 新增 Valkey、任务队列、Worker 配置项

### Removed

- 删除冗余英文文档副本（ARCHITECTURE_en / CONTRIBUTING_en / SECURITY_en / CODE_OF_CONDUCT_en）
- 删除过期文档：GOVERNANCE / RELEASE / QA / SUPPORT / LOCAL-TOOL-DEPLOYMENT / AI_AGENT_INTEGRATION
- 删除已完成计划文档

## [0.1.14] - 2026-04-05

### Changed

- api-server pipeline 预加载逻辑重写：对齐 gradio-demo 的 `load_miroflow_config` 模式，正确处理 Hydra 全局初始化状态
- api-server 添加到 `compose.yaml`：`api` 服务监听 8090 端口，与 `app`（Gradio）并行运行
- `_build_config_overrides` 从环境变量读取 LLM 配置，支持 `DEFAULT_LLM_PROVIDER` / `DEFAULT_MODEL_NAME` / `BASE_URL` / `API_KEY`
- 子代理工具定义暴露：`_ensure_pipeline_loaded` 自动调用 `expose_sub_agents_as_tools`

### Fixed

- api-server 安全审查修复 7 项问题：
  - 修复任务管理内存泄漏：`cleanup_stale_tasks` 定期清理已完成任务，`finish_task` 记录 `finished_at` 时间戳
  - 修复异常信息泄漏：pipeline 异常返回通用错误消息，不暴露内部堆栈
  - 替换废弃 `asyncio.get_event_loop()` 为 `asyncio.get_running_loop()`
  - 替换废弃 `app.on_event` 为 FastAPI `lifespan` 上下文管理器，集成周期性清理任务
  - `ResearchRequest` 添加 `mode`、`search_profile`、`output_detail_level` 枚举校验
  - 限流中间件 429 响应隐藏内部配置（`RATE_LIMIT_RPM`），导出 `cleanup_rate_limit_buckets` 供定期清理
- Dockerfile（gradio-demo / api-server）CMD 添加 `--frozen`，修复容器无外网时 `uv run` 尝试下载依赖导致启动失败

## [0.1.13] - 2026-04-05

### Added

- api-server 请求限流中间件：基于内存滑动窗口计数器（`SlidingWindowCounter`），按 IP 或 Bearer Token 限流
- 限流配置：`RATE_LIMIT_ENABLED`（默认开启）、`RATE_LIMIT_RPM`（默认 30 次/分钟）
- `/health`、`/docs` 等路径自动跳过限流
- api-server Dockerfile：与 gradio-demo 对齐的容器化配置，HEALTHCHECK 指向 `/health`
- 6 条限流中间件回归测试（配额内通过、超额 429、bypass 路径、禁用模式、独立 key 计数）
- `.env.example` 补充限流和缓存配置说明

## [0.1.12] - 2026-04-05

### Added

- 新增 `ResultCache` 类（`src/cache/result_cache.py`）：内存 LRU + TTL 结果缓存，相同 query+mode+profile+detail_level 命中缓存避免重复消耗搜索配额与 LLM tokens
- `gradio-demo` `run_research_once` 集成结果缓存：入口检查缓存，完成后写回缓存
- `api-server` `POST /v1/research` 集成结果缓存：命中时立即返回 `status=cached`
- 缓存配置通过环境变量 `RESULT_CACHE_MAX_SIZE`（默认 128）和 `RESULT_CACHE_TTL_SECONDS`（默认 3600）控制
- 11 条 ResultCache 回归测试（LRU 淘汰、TTL 过期、key 确定性、invalidate、clear）

## [0.1.11] - 2026-04-05

### Added

- 新增 `apps/api-server/`：基于 FastAPI 的独立 HTTP API 层，脱离 Gradio 依赖
- `POST /v1/research`：提交研究任务，返回 task_id
- `GET /v1/research/{task_id}/stream`：SSE 流式获取任务实时进度
- `POST /v1/research/{task_id}/cancel`：取消指定任务
- `POST /v1/research/cancel`：按 caller_id 批量取消
- `GET /v1/metrics/last`：复用 RunMetrics，返回最近任务运行指标
- `GET /health`：健康检查端点
- Bearer Token 认证中间件：`API_TOKENS` 环境变量配置，留空则跳过认证（开发模式）
- 9 条 api-server 回归测试（健康检查、认证、参数校验、404 路径）
- GitHub Actions `run-tests.yml` 新增 api-server job

## [0.1.10] - 2026-04-05

### Added

- 结构化运行 metrics：新增 `RunMetrics` dataclass，任务结束时聚合 429 次数、超时次数、Key 切换次数、模型路由命中、检索轮次等指标写入 `TaskLog`
- `OpenAIClient` 埋点：`_create_message` 中自动采集 rate_limit_429、timeout、key_switch、model_route 指标
- `Orchestrator` 埋点：搜索工具返回有效链接时递增 `search_rounds` 计数
- `pipeline.py` 任务结束时聚合 `total_duration_ms` 和 `stage_durations` 到 `run_metrics`，并通过 `stream_queue` 发送 `run_metrics` 事件
- Gradio Demo 新增 `GET /api/metrics_last` 端点，返回最近一次任务的结构化运行指标
- 模型级 failback（轻量版）：`OpenAIClient` 新增 `model_fallback_name` 配置和 `activate_fallback()` 方法，主模型连续失败时自动切换备用模型
- `Orchestrator` 主循环和子代理循环：连续 LLM 失败达阈值时优先尝试 failback，成功则重置计数器继续执行
- 最小回归门禁：新增 `test_output_detail_level_routing.py`（4 条）和 `test_model_failback.py`（4 条）pytest 回归测试
- GitHub Actions 新增 `run-tests.yml` workflow，PR 和 push 到 main 时自动运行 miroflow-agent 和 gradio-demo 测试

### Changed

- `.env.example`（gradio-demo）及 `.env.compose.example` 新增 `MODEL_FALLBACK_NAME` 配置说明

## [0.1.9] - 2026-03-21

### Added

- 新增 `KeyPool` 通用模块（`libs/miroflow-tools`）：线程安全的 API Key 轮转池，支持 round-robin 分配、429 限速标记与冷却、全部耗尽时返回最短剩余冷却时间
- LLM Key 池轮转：`openai_client.py` 支持 `OPENAI_API_KEYS=key1,key2,key3` 环境变量，429 时自动切换到下一 Key 重试
- 429 感知退避增强：识别 `openai.RateLimitError`，读取 `Retry-After` header；Key 全部耗尽才走指数退避兜底
- 搜索工具 Key 轮转：`search_and_scrape_webpage.py`、`serper_mcp_server.py`、`searching_google_mcp_server.py` 支持 `SERPER_API_KEYS` / `SERPAPI_API_KEYS` 多 Key 环境变量
- 会话级 API 任务隔离：`stop_current_api` 支持可选 `caller_id` 参数，按调用方定向取消；`run_research_once` 新增可选 `caller_id` 参数
- 活动任务表从 `Set[task_id]` 改为 `{task_id: caller_id}` 映射，不再全局广播取消

### Changed

- `.env.example`（miroflow-agent / gradio-demo）及 `.env.compose.example` 新增多 Key 配置说明
- Skill 文档（SKILL.md / api.md / usage.md）同步更新 `caller_id` 定向取消说明
- 调用脚本 `call_openclaw_mirosearch.py` 新增 `--caller-id` 参数
- Skill 包 `openclaw-mirosearch.zip` 重新打包

## [0.1.8] - 2026-03-20

### Fixed

- 修复 demo 模式"研究总结"区域仅显示 `\boxed{}` 一句话的渲染错误：`prompt_patch.py` 不再用 boxed 内容覆盖完整报告文本，改为保留全文并移除标记

### Changed

- `detailed` 档位 token 上限全面提升：`summary_max_tokens` / `max_tokens` 8192→16384，`verification_max_tokens` 6144→12288，`tool_result_max_chars` 12000→20000，`max_turns` 16→20
- `detailed` 档位研究报告模式提示词重写：新增"全量保留、去重整合、禁止压缩"核心原则，要求每轮检索信息全部体现、多轮重复信息去重合并、绝对禁止为控制篇幅省略信息
- `detailed` 档位正文字数目标 6000→12000 字符，最小章节数 10→12
- `detailed` 档位总结过短重试阈值 1800→5000 字符，`balanced` 档位重试阈值 900→1500 字符
- 总结提示词基础版本（`prompt_patch.py`）移除简洁优先倾向，改为"全量保留优于简洁"原则
- 扩写提示词强化：要求逐轮检查信息覆盖，最终报告须比任何单轮检索输出更长更完整

## [0.1.7] - 2025-03-20

### Added

- 新增按网络环境分流的部署与调用指引：区分“中国大陆无代理”与“海外/有代理”两类推荐检索策略
- 新增 SearXNG 可覆盖配置文件 `deploy/searxng/settings.yml`，支持按环境启停搜索引擎
- Skill 文档新增网络环境路由章节，安装后可直接按地域与链路稳定性选 `search_profile`

### Changed

- Docker Compose 部署文档补充“引擎可达性自检”命令与参数建议，降低“全部超时”误判
- AI Agent 接入文档补充网络感知路由建议：未知网络先 `searxng-first`，稳定后再升 `parallel-trusted`
- OpenClaw Skill（`usage/install/skill-install/SKILL.md`）同步更新为网络环境优先决策

### Fixed

- 修复受限网络场景下 SearXNG 默认引擎集合导致的大面积超时问题（通过可达引擎集避免全量超时）
- 修复自定义 SearXNG 配置缺失 `server.secret_key` 导致的容器重启循环问题

## [0.1.6] - 2025-03-20

### Added

- 新增 `docs/ARCHITECTURE.md` 与 `docs/ARCHITECTURE_en.md`（含 Mermaid 系统架构图、数据流时序图、部署拓扑图）
- 新增核心文档英文版：`CONTRIBUTING_en.md`、`SECURITY_en.md`、`CODE_OF_CONDUCT_en.md`
- 中英文文档互相引用，`docs/README.md` 索引同步更新
- 新增 `.github/CODEOWNERS` 代码所有者配置
- 根 README（中/英）添加架构文档链接与 Demo 截图占位

### Changed

- 根 README 已实现功能从冗长枚举精简为摘要列表，详细参数引用 `docs/API_SPEC.md`
- 根 README 路由参数列表精简，引用 Agent README 与 API_SPEC
- `libs/miroflow-tools/README.md` 从 933 行精简至约 365 行，移除重复代码示例
- `docs/QA.md` 重写：标注历史文档性质、统一中文、移除重复内容
- `docs/SECURITY.md` 补充 GitHub Security Advisories 作为首选漏洞报告渠道

### Fixed

- 修复 `.github/ISSUE_TEMPLATE/` 中 bug_report.md 和 feature_request.md 的 YAML front matter 格式
- 修正 CHANGELOG 5 处与 ROADMAP 1 处日期错误（2026 → 2025）

### Removed

- 删除冗余的根目录 `README_en.md`（内容已合并至 `README.md`）

## [0.1.5] - 2025-03-20

### Added

- 根 README 默认切换为英文入口，并新增 `README_zh.md` 中文切换文档
- 根 README 新增模型配置说明，补充 `DEFAULT_LLM_PROVIDER` 与分角色模型变量
- OpenClaw 技能文档拆分为安装 / 使用两部分，并补充简单搜索与深度检索分流建议
- 根 README 新增 changelog 摘要区，并链接完整变更记录

### Changed

- 统一整理文档入口，默认面向英文读者，中文文档作为单独切换页
- 技能调用说明从安装文档中剥离，降低安装与使用混淆

## [0.1.4] - 2025-03-20

### Added

- 统一接口 `run_research_once` 新增第 6 个参数：`output_detail_level`（`compact/balanced/detailed`）
- 总结阶段新增“研究报告模式”提示词覆盖策略，按档位约束输出长度与结构密度
- OpenClaw 技能文档与调用脚本支持 `output_detail_level` 参数
- 新增“阶段日志心跳”透传与前端展示：阶段（检索/推理/校验/总结）、回合、检索轮次
- 新增陈旧任务巡检线程：长时间未更新的 `running` 自动收敛为 `failed`

### Changed

- 三档输出语义明确化：`compact=精简` / `balanced=适中` / `detailed=超长报告`
- `detailed` 档默认上调总结与校验 token 上限，提升长文承载能力
- `run_research_once` 的默认渲染策略改为跟随 `output_detail_level`
- Demo 默认检索模式为 `balanced`
- UI 中“最少检索轮次（verified 生效）”默认隐藏，且仅在 `verified` 模式显示并生效
- 文档全面收敛为统一接口描述，移除 demo 文档中的 `run_research_once_v2` 残留

### Fixed

- 修复“详细档仍偏短”的上限约束问题（`summary_max_tokens` 被 `max_tokens` 隐式限制）
- 修复研究类场景被短答案模板约束压缩输出的问题
- 修复浏览器本地搜索历史偶发仅保存标题、未保存结果详情的问题（增加可见结果区兜底采集与分级压缩持久化）

### Security

- Demo 技能包下载入口增加 URL/路径安全约束，限制协议与越权路径访问风险

## [0.1.2] - 2025-03-19

### Changed

- 对外研究接口统一为单端点：`run_research_once`（历史双端点已收敛）
- `run_research_once` 统一采用五参数输入：`query/mode/search_profile/search_result_num/verification_min_search_rounds`
- UI 默认渲染为“综合结果优先 + 过程折叠”，API 默认渲染为“仅综合结果”
- OpenClaw 技能文档与调用脚本更新为统一单接口规范

### Fixed

- 减少 `verified` 多轮检索时中间稿重复暴露导致的多段报告体验问题
- 修正文档中的 `stop_current` 路径为 `/gradio_api/call/stop_current`

## [0.1.1] - 2025-03-19

### Added

- Demo 输入区新增浏览器本地搜索历史（localStorage），支持回填、单条删除、清空
- 新增提示词安全回归测试，防止交叉校验模板出现场景化硬编码污染

### Changed

- 交叉校验与跟进提示词改为通用口径描述，移除特定领域示例词污染
- 主流程与总结/校验模型路由优化，增强高级模型在关键判断环节的介入
- 新增模型路由观测日志（`requested`/`responded`）用于核验真实命中模型

## [0.1.0] - 2025-03-18

### Added

- 发布 OpenClaw-MiroSearch 首个可用基线版本（MVP）
- 新增研究模式：`production-web`、`verified`、`research`、`balanced`、`quota`、`thinking`
- 新增检索路由：`searxng-first`、`serp-first`、`multi-route`、`parallel`、`parallel-trusted`、`searxng-only`
- 新增并发聚合与置信不足高信源补检能力（`parallel-trusted`）
- 新增 OpenClaw 技能包：`skills/openclaw-mirosearch/`
- 新增独立部署 `compose.yaml`（`app + searxng + valkey`）

### Changed

- 根 `README` 重构为开源项目导向文档，并补齐文档索引
- Demo 页面支持模式与检索源策略选择，并暴露关键检索参数

### Fixed

- 修复长时间执行下任务可能停留在 `running` 的终态问题（任务终态守卫）
- 增加连续 LLM 失败保护，避免空响应重试导致卡住
