# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.7] - 2026-05-02

### Added

- **新增 `searxng` 独立技能**：提供轻量级搜索入口，适配成本优先的调用场景，包含 `agents/openai.yaml`、`scripts/searxng.py`、SKILL.md
- **新增 `openclaw-search-skills-bundle.zip` 聚合技能包**：整合多个搜索相关技能
- **新增 `openclaw-mirosearch/agents/openai.yaml`**：适配 OpenClaw Agent 技能调用规范

### Changed

- **README 从 Gradio API 示例改为 FastAPI REST API 示例**：新增 `caller_id` 参数说明、任务取消方式、`/health` 健康检查、`/v1/research` 完整路径与 SSE 流式说明
- **`SKILL.md` 改为核心约定格式**：强调 FastAPI 闭环为推荐方式，简化 v0.2.2 旧变更说明

## [0.2.6] - 2026-05-02

### Fixed

- **禁止 failure summary 和总结阶段传入 tool_definitions**：failure summary 阶段无需工具调用；总结阶段禁止工具调用，避免 OpenRouter 等网关因模型不支持 tool use 返回 404
- **找不到 `\boxed{}` 时回退到完整答案文本**：兼容 qwen3.6 等不使用 `\boxed{}` 格式的模型，避免 `FORMAT_ERROR_MESSAGE` 触发不必要的重试或 "not converged" 警告

## [0.2.5] - 2026-04-27

> 本版本交付 [`docs/SCRAPING_ITERATION_PLAN.md`](./SCRAPING_ITERATION_PLAN.md) 的 T6 + T7 + T8：
> `trafilatura` 主路径 + `bs4` fallback、HTML 表格转 markdown、按句子 / 段落边界智能截断。

### Added

- **T6 [B1] `scrape_url` 引入 `trafilatura` 主路径**：HTML 正文抽取优先走 `trafilatura.extract(output_format="markdown", include_tables=True, include_comments=False, favor_recall=True)`，抽空或不可用时回退到现有 `bs4` 路径；新增 `SCRAPE_USE_TRAFILATURA` 开关可快速回滚
- **T7 [B3] HTML 表格转 Markdown**：`bs4` fallback 路径将 `<table>` 转为 Markdown 表格，保留行列结构，避免统计表、法规附表在纯文本抽取中丢列
- **T8 [B4] 句子 / 段落边界智能截断**：超长正文不再直接 `text[:cap_chars]` 硬切，优先回退到段落、换行、中文/英文句末标点边界；返回新增 `truncation` 元数据
- **新增 3 条 `scrape_url` 单元测试**：覆盖 `trafilatura` 主路径参数、fallback 表格 Markdown 保真、软边界截断元数据

### Changed

- **显式依赖 `trafilatura`**：`libs/miroflow-tools` 运行依赖新增 `trafilatura`，由 `uv sync` 传递到 api / worker / demo 运行环境
- **截断长度语义调整**：`content_length` 在截断时可能小于 `max_chars`，以保证返回内容落在自然边界

### Testing

- **本地回归通过**：
  - `libs/miroflow-tools`: `27 passed`
  - `apps/api-server`: `94 passed, 13 skipped`
  - `apps/gradio-demo`: `39 passed`
  - `apps/miroflow-agent`: `39 passed, 7 skipped`
- **本地 Docker 真实端到端验证通过**：使用 `COMPOSE_ENV_FILE=.env.compose.local-e2e` 重建 `app + api + worker` 后，真实任务 SSE `tool_call` 确认命中 `scrape_url`，并成功基于 `https://www.iana.org/about` 生成完整答案

## [0.2.4] - 2026-04-27

### Added

- **T3 [A1/A2] `scrape_url` 支持 PDF 抽取与响应体大小上限**：新增 `SCRAPE_MAX_BODY_BYTES`（默认 20MB）并改为流式读取响应体；`application/pdf` 现在可返回 `content_kind="pdf"`、`pages`、`bytes_read`、`text_quality`，使统计公报、监管 PDF 和公告附件可直接被抓取
- **T5 [A3] `scrape_url` 支持 JSON / RSS / Atom / XML 结构化直通**：新增 `application/json`、`text/json`、`application/rss+xml`、`application/atom+xml`、`application/xml`、`text/xml` 白名单；返回 `json_type` / `json_keys`、`feed_title` / `entries`、`xml_root` 等结构化字段，便于 LLM 直接消费 API / Feed 数据
- **XML 声明编码识别**：在 header / meta / `charset_normalizer` 之外，新增 XML declaration 编码探测，减少 XML / Feed 抓取乱码
- **显式依赖 `pdfminer-six`**：`libs/miroflow-tools` 及引用它的应用锁文件同步显式登记 `pdfminer-six`，避免未来依赖树变化造成 PDF 抽取运行时缺包
- **新增 4 条 `scrape_url` 单元测试**：覆盖 PDF 正文抽取、超大响应体拒绝、JSON 结构化返回、RSS 结构化返回；同时将旧的 PDF content-type 拒绝用例更新为“坏 PDF 解析失败”语义

### Changed

- **手动重定向链路改为流式响应**：`_fetch_with_manual_redirects` 采用 `stream=True`，并在中间 30x hop 上及时 `aclose()`，降低连接泄漏风险
- **正文归一化路径统一**：HTML / text / PDF / Feed 统一复用文本归一化逻辑，减少换行噪音和抽取结果抖动

### Testing

- **本地回归通过**：
  - `libs/miroflow-tools`: `24 passed`
  - `apps/api-server`: `18 passed`
  - `apps/gradio-demo`: `20 passed`
  - `apps/miroflow-agent`: `16 passed`
- **本地 Docker 真实端到端验证通过**：在 `app + api + worker + searxng + valkey` 全部 `healthy` 条件下，使用 IANA 官方站点职责总结样例完成真实任务执行；最终状态 `completed`，`search_rounds=1`，最近一次任务总耗时约 `49.1s`，且 `timeout_count=0`、`rate_limit_429_count=0`

## [0.2.3] - 2026-04-27

### Added

- **T1 [C1] scrape_url 共享 `httpx.AsyncClient` + 分阶段耗时 metrics**：模块级 `_SCRAPE_CLIENT` 懒初始化，`atexit` 关闭；返回 JSON 新增 `metrics: {t_request_ms, t_parse_ms, t_extract_ms, redirect_hops}`，让 LLM 一轮研究中连续抓取多个 URL 时复用 TCP/TLS，大幅降低尾延迟
- **T2 [D2/D3] 重定向手动循环 + 每跳 SSRF 校验 + 上限 5 跳**：默认 `follow_redirects=False`，自实现 30x 跟随；每一跳重新校验 scheme + `_is_private_or_loopback_host`，跳到内网 / 跳数超 `SCRAPE_MAX_REDIRECT_HOPS`（默认 5）立刻返回 `error="redirect_blocked: ..."`，并保留 `redirect_chain` 字段
- **T4 [B2] 中文编码兜底（header → meta → charset_normalizer）**：bytes 路径解码，Content-Type charset → `<meta charset>` / `<meta http-equiv>` → `charset_normalizer.from_bytes(...).best()` → utf-8(replace) 四级回退；返回字段新增 `encoding`，专治 GBK / GB18030 政府站点中文乱码
- **代理/TUN fake-ip DNS 兼容开关**：新增 `SCRAPE_PROXY_FAKE_IP_CIDRS`，用于显式允许域名解析到受信任的 fake-ip 网段（如 `198.18.0.0/15`）；该开关只对域名解析结果生效，不允许 IP 字面量绕过 SSRF 拦截
- 新增 11 条单元测试覆盖：metrics 字段 / redirect 链跟随 / redirect 私网拒绝 / 超 5 跳拒绝 / GBK header 解码 / meta charset 兜底 / charset_normalizer 兜底 / 共享 client 复用 / fake-ip DNS 默认拒绝 / fake-ip 显式允许 / IP 字面量仍拒绝

## [0.2.2] - 2026-04-26

### Added

- **MCP 工具 `scrape_url` 雏形**（libs/miroflow-tools）：基于 `httpx + BeautifulSoup`，让 LLM 在 `google_search` snippet 不足时主动"打开页面看正文"
  - 仅支持 http(s) 绝对 URL，content-type 白名单 `text/html` / `application/xhtml+xml` / `text/plain`
  - SSRF 防护：拒绝 loopback / 私网 / link-local / multicast / reserved 主机
  - 超时（默认 25s）、`max_chars` 截断（默认 10000，硬上限 30000）、可配 User-Agent
  - 正文优先选择 `article` / `main` / `[role=main]` / `#content` 等容器，fallback 全文 text
  - 5 条单元测试覆盖：非 http scheme、空 url、私网 SSRF、HTML 正文抽取、非 HTML content-type 拒绝
  - 同步在 `apps/miroflow-agent/src/utils/parsing_utils.py` 的 `TARGET_TOOLS` 集合中登记，LLM 错写 server_name 时自动修正
  - 详细后续迭代规划见 [`docs/SCRAPING_ITERATION_PLAN.md`](./SCRAPING_ITERATION_PLAN.md)（T1-T9）
- **阶段心跳镜像到 stderr**：`Orchestrator._emit_stage_heartbeat` 与 `AnswerGenerator._emit_stage_heartbeat` 增加同名事件去重 + `logger.info` 落 stderr，方便 `docker logs` 直接观察长任务进度

### Fixed

- **API 模式严重回归：worker 完全忽略 demo 投递的检索参数**（apps/api-server）
  - 现象：用户在 demo 选 `verified + parallel-trusted + 20 results + detailed`，切到 `BACKEND_MODE=api` 后实际跑的却是硬编码的 `agent=demo_search_only`，导致原本能跑出研究总结的 query 全部回退成"未收敛"兜底文案
  - 根因：`apps/api-server/services/pipeline_runtime.py` 内 `build_config_overrides` 仅依据 `DEFAULT_LLM_PROVIDER` / `AGENT_CONFIG` 等 process env 决定 cfg，把 `RequestLike` 的 `mode` / `search_profile` / `search_result_num` / `verification_min_search_rounds` / `output_detail_level` 五个字段全部丢弃
  - 修复：新增 `apps/api-server/services/profile_resolver.py`，与 gradio-demo 的 `_ensure_preloaded` 对齐策略
    - 复用同一套 `SEARCH_PROFILE_ENV_MAP`（searxng-first / serp-first / multi-route / parallel / parallel-trusted / searxng-only）
    - 复用同一套 mode → hydra overrides 映射（production-web / verified / research / balanced / quota / thinking），所有可调常量改为运行时 `os.getenv` 读取
    - 复用同一套 `output_detail_level` → max_turns / keep_tool_result / max_tokens 映射
  - `pipeline_runtime` 同步改造：
    - `build_config_overrides` 返回 `(search_env, hydra_overrides)` 二元组
    - `create_runtime_components` 在 `_temporary_env_vars` 上下文中创建组件，让检索 MCP 子进程从进程 env 继承到正确的 `SEARCH_PROVIDER_*` 配置
    - 新增 `asyncio.Lock` 串行化组件创建流程，避免 worker 多 task 并发覆盖进程级 env
- **新增 55 条单元测试**：
  - 47 条 `test_profile_resolver.py`：normalize_* / build_search_env / build_mode_overrides / build_full_overrides 全分支覆盖
  - 8 条 `test_pipeline_runtime_overrides.py`：验证 `RequestLike` 五字段被正确传递，base llm overrides 与 mode_overrides 顺序正确
- **Worker cancel 链路鲁棒性修复**（apps/api-server/workers/research_worker.py）
  - `check_cancel` 协程加启动 INFO 日志（确认 watcher 被正确启动）；redis 单次读取异常仅打 warning 后继续轮询，不再静默退出导致 cancel 信号永远收不到
  - `pending` 任务清理路径改为 `asyncio.wait_for(timeout=10s)`：当下游代码吞掉 CancelledError 时（如 `pipeline.py` 的 except 分支）worker 不再 hang，最多 10s 后强制 abandon 并继续返回 `cancelled` 状态
  - 新增 2 条测试覆盖：`test_cancel_watcher_survives_redis_errors`（redis 抖动 watcher 不死）、`test_cancel_path_with_unresponsive_pipeline`（不响应 cancel 的 pipeline 在超时窗口后被 abandon）
- **Dockerfile 默认走国内 apt 镜像源**（apps/api-server, apps/gradio-demo）
  - 新增 `APT_MIRROR` build-arg，默认 `mirrors.tuna.tsinghua.edu.cn`，sed 替换 `/etc/apt/sources.list*` 内 `deb.debian.org` 与 `security.debian.org`
  - 兼容 deb822（trixie 起 `/etc/apt/sources.list.d/debian.sources`）与老 `sources.list` 两种格式
  - 需要恢复官方源时：`docker compose build --build-arg APT_MIRROR= api worker`
- **compose 文件 build 段加 `network: host`**（compose.yaml, compose.host-network.yaml）
  - app/api/worker 三个服务的 `build.network: host`，让 build 阶段直接走宿主机网络
  - 解决 tower 等环境下 docker0 桥接网无法访问外部 apt/pip 仓库（宿主能连但 build 容器连不上）的问题
- **新增构建脚本** `scripts/deploy/build_images.sh`
  - 直接调 `docker build --network=host -f ... -t ...`，绕过 `docker compose build` 在 BuildKit 下需要交互式授权 `network.host` entitlement 的问题（ssh 非 TTY 场景无法通过）
  - 支持 `APT_MIRROR` / `PIP_INDEX_URL` / `IMAGE_TAG_API` / `IMAGE_TAG_DEMO` 环境变量覆盖
  - 用法：`scripts/deploy/build_images.sh [api|demo|all]`

### Added

- **Demo 断电重连（gradio-demo）**：研究任务可在浏览器刷新或网络中断后通过 URL `?task_id=xxx` 自动续看完整进度，不再丢失中间结果
  - 新增 `BACKEND_MODE=api` 后端模式，启用后 demo 不再在自己进程内执行 pipeline，而是把每次检索作为一个任务投递到 `api-server`
  - 新增 `apps/gradio-demo/api_client.py`：基于 `aiohttp` 的轻量 HTTP/SSE 客户端，封装 `create_task` / `get_task` / `cancel_task` / `stream_task_events` 四个端点；手撕 SSE 解析器避免新增依赖
  - 任务创建后服务端 `task_id` 通过 CSS 隐藏的 `<textarea id="gr-task-id-bridge">` + `MutationObserver` 同步写入 `?task_id=xxx`（`history.replaceState`，无页面跳转）
  - `demo.load(reconnect_or_init)` 自动从 URL `query_params["task_id"]` 接管：所有非空任务状态都通过 SSE 重建 UI，由 `api-server` 从 Redis Stream 头部回放历史事件 + 阻塞等待新事件，刷新后渲染与实时观察体验完全一致
  - "停止"按钮在 API 模式下额外调用 `POST /v1/research/{task_id}/cancel`，触发 worker 协作式中止
  - 保留 `BACKEND_MODE=local` 默认值与原有进程内执行路径，向后兼容
- **未收敛任务文案中文化**：探测 pipeline 兜底文案（`No \boxed{} content found in the final answer.` / `Task incomplete - reached maximum turns ...`），在研究总结区域重写为"本轮检索未能在限定回合内收敛出可信结论 ... 建议降级 mode 或重试"，提升可读性，避免用户误以为 demo 故障
- **新增 21 条单元测试**：
  - 16 条 `test_api_client.py`：BACKEND_MODE 切换 / SSE 单块解析（默认事件名、多行 data、注释、id/retry、非 JSON 回退）/ 4 端点 + SSE 流端到端（基于本地 aiohttp 测试服务）
  - 4 条 `test_render_markdown.py`：`_humanize_pipeline_fallback` 与 `_build_summary_section` 的兜底文案重写覆盖
  - 1 条 `test_render_markdown.py`：正常总结块不被误改

### Changed

- `.env.compose.example`、`apps/gradio-demo/.env.example` 新增 `BACKEND_MODE` / `API_BASE_URL` / `API_BEARER_TOKEN` 三个配置项及中文注释

### Notes

- Gradio 5 中 `visible=False` 的组件不会进入 DOM，因此 `task_id_box` 必须 `visible=True` + CSS 移到屏幕外，才能被 JS 桥找到。已加入相关注释与 CSS 规则。

## [0.2.1] - 2026-04-23

### Added

- **研究报告引用可点击**（gradio-demo）：研究总结正文中形如 `[2]`、`[5]`、`[9]` 的数字引用自动转换为指向报告末尾"References / 参考文献"章节对应真实 URL 的 HTML 锚点，点击在新标签页打开原始信源
  - 自动识别多种参考文献章节标题（`**References**` / `## 参考文献` / `## References` / `## 引用` / `## Sources` 等）
  - 解析形如 `[N] 标题. URL` 的条目构建 id → url 映射
  - 跳过围栏代码块与行内代码内的 `[N]`，避免误伤
  - URL 尾部自动修剪 ASCII 与常见中文标点
  - 参考文献章节本身保持原样，内部 `[N]` 不嵌套链接
  - 新增 3 条单元测试覆盖正常替换、无参考文献段落、代码块保护

### Fixed

- **Compose Worker 启动命令**：`compose.yaml` 中 `api-worker` 服务启动命令改为 `.venv/bin/python worker.py`，避免容器 `PATH` 未指向 uv 托管解释器时拉起错误的 Python，确保 arq Worker 稳定启动

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
