# 全局审查问题修复实现计划

> **面向 AI 代理的工作者：** 逐任务使用测试驱动开发执行；当前工作区已有用户修改，
> 项目规则禁止未经授权提交，因此计划中的每个任务只验证和保留工作区变更，不执行 commit。

**目标：** 修复总结、模式参数、运行时协议和部署契约中已复现的高风险问题。

**架构：** 通过显式内部消息类型、统一 Pipeline 映射结果和单次有效参数解析建立跨模块
契约。模式作为硬预算最后覆盖，输出篇幅只提供不突破模式上限的默认值。

**技术栈：** Python 3.12、pytest、pytest-asyncio、Pydantic、Hydra、FastAPI、Gradio。

---

### 任务 1：修复总结消息保留

**文件：**
- 创建：`apps/miroflow-agent/tests/test_summary_message_retention.py`
- 修改：`apps/miroflow-agent/src/llm/base_client.py`
- 修改：`apps/miroflow-agent/src/llm/providers/openai_client.py`
- 修改：`apps/miroflow-agent/src/llm/providers/anthropic_client.py`
- 修改：`apps/miroflow-agent/src/core/answer_generator.py`

- [ ] 编写测试：构造两条工具结果和最终总结指令，断言 compact 保留最新一条真实结果、
  balanced 保留最新两条、detailed 全部保留。
- [ ] 编写测试：`keep_tool_result=0` 只省略工具结果，最终总结指令保持原文。
- [ ] 运行：
  `cd apps/miroflow-agent && uv run pytest tests/test_summary_message_retention.py -q`
  ，确认旧实现因总结指令被误分类、最新结果被删除而失败。
- [ ] 在工具结果消息上添加内部类型标记；过滤副本只按标记裁剪并在 SDK 调用前移除标记。
- [ ] 删除最终总结前无条件丢弃最后 `user` 消息的逻辑。
- [ ] 重跑目标测试并确认通过。

### 任务 2：修复总结重试、质量与失败状态

**文件：**
- 修改：`apps/miroflow-agent/tests/test_output_formatter_quality.py`
- 创建：`apps/miroflow-agent/tests/test_answer_generator_outcome.py`
- 修改：`apps/miroflow-agent/src/io/output_formatter.py`
- 修改：`apps/miroflow-agent/src/core/answer_generator.py`
- 修改：`apps/miroflow-agent/src/core/orchestrator.py`
- 修改：`apps/miroflow-agent/src/core/pipeline.py`
- 修改：`apps/api-server/tests/test_research_worker.py`
- 修改：`apps/api-server/workers/research_worker.py`

- [ ] 编写测试：未闭合 `\boxed{` 的 payload 必须 `format_valid=false`。
- [ ] 编写测试：compact/balanced 的总结重试次数不受 `keep_tool_result` 影响。
- [ ] 编写测试：空总结且无中间 fallback 时 Pipeline/Worker 返回 failed；缺 boxed 但有正文时
  completed 且质量为 invalid/fallback。
- [ ] 逐个运行测试并确认旧实现按预期失败。
- [ ] 让总结重试使用独立配置并在无工具退出时执行总结上下文保护。
- [ ] 贯通 `result_quality`，区分“生成失败”和“格式降级”。
- [ ] 重跑 agent 与 worker 聚焦测试。

### 任务 3：统一 Pipeline 映射协议

**文件：**
- 创建：`apps/miroflow-agent/tests/test_pipeline_result_contract.py`
- 修改：`apps/miroflow-agent/main.py`
- 修改：`apps/miroflow-agent/benchmarks/common_benchmark.py`
- 修改：`apps/miroflow-agent/src/core/pipeline.py`

- [ ] 编写异步测试，用字典 Pipeline 结果调用 CLI `amain()`，断言不会位置解包。
- [ ] 编写 benchmark 结果读取测试或提取共享映射读取辅助函数。
- [ ] 运行目标测试，确认旧调用方出现 `too many values to unpack`。
- [ ] 所有调用方按键读取 `final_summary`、`final_boxed_answer`、日志与失败摘要。
- [ ] 重跑目标测试和 agent 全量测试。

### 任务 4：统一 API 参数契约与有效值

**文件：**
- 修改：`apps/api-server/models.py`
- 修改：`apps/api-server/routers/research.py`
- 修改：`apps/api-server/services/profile_resolver.py`
- 修改：`apps/api-server/tests/test_profile_resolver.py`
- 修改：`apps/api-server/tests/test_research_queue_api.py`
- 修改：`apps/api-server/tests/test_pipeline_runtime_overrides.py`

- [ ] 编写请求验证测试：25/100 条和 0/20 轮返回 422；10/20/30 与 1..8 可用。
- [ ] 编写省略字段测试：部署 `DEFAULT_*` 值成为任务元数据和队列 payload 的有效值。
- [ ] 编写状态测试：GET 回报值与 Worker override 一致。
- [ ] 运行测试并确认旧硬编码默认与静默归一化导致失败。
- [ ] 将可选请求解析为单一有效参数对象，并用于缓存、存储和入队。
- [ ] 重跑 API 参数与路由测试。

### 任务 5：明确模式预算优先级

**文件：**
- 修改：`apps/api-server/services/profile_resolver.py`
- 修改：`apps/api-server/tests/test_profile_resolver.py`
- 修改：`apps/gradio-demo/main.py`
- 修改：`apps/gradio-demo/tests/test_output_detail_level_routing.py`

- [ ] 编写矩阵测试：`quota+detailed` 的 turns/token 不得超过 quota；research 的模式预算同样
  最后生效，同时 `output_detail_level=detailed` 保留。
- [ ] 运行 API 与 Gradio 目标测试，确认旧追加顺序失败。
- [ ] 两个入口均先添加篇幅 override，再添加模式 override。
- [ ] 重跑矩阵测试。

### 任务 6：修复 Gradio 缓存、后端与调用方隔离

**文件：**
- 修改：`apps/gradio-demo/main.py`
- 修改：`apps/gradio-demo/api_client.py`
- 修改：`apps/gradio-demo/tests/test_stop_current_api.py`
- 修改：`apps/gradio-demo/tests/test_api_client.py`
- 创建：`apps/gradio-demo/tests/test_run_research_once_routing.py`

- [ ] 编写缓存测试：不同检索条数/校验轮次必须调用不同执行路径。
- [ ] 编写 API 模式测试：`run_research_once` 只调用 api-client，不调用本地 Pipeline。
- [ ] 编写 API 绑定测试：`caller_id` 被传入 run 入口，调用方 A 不能取消 B。
- [ ] 运行测试并确认旧实现失败。
- [ ] 补齐缓存键参数、API 分流和 Gradio 组件绑定。
- [ ] 对非法 `BACKEND_MODE` 启动配置抛出清晰错误。
- [ ] 重跑 Gradio 聚焦测试。

### 任务 7：避免本地 ToolManager 跨任务共享状态

**文件：**
- 修改：`apps/gradio-demo/main.py`
- 修改：`apps/gradio-demo/tests/test_compose_config.py`
- 修改：`apps/miroflow-agent/src/core/pipeline.py`

- [ ] 编写测试：同一 profile 连续创建两次任务运行时，ToolManager 实例不同而配置缓存命中。
- [ ] 运行测试并确认旧缓存返回同一 manager。
- [ ] 缓存不可变 cfg/定义；每个任务按 API runtime 的参考实现新建 manager。
- [ ] 重跑 Gradio 和 agent 聚焦测试。

### 任务 8：对齐 Anthropic 分阶段路由

**文件：**
- 创建：`apps/miroflow-agent/tests/test_anthropic_stage_routing.py`
- 修改：`apps/miroflow-agent/src/llm/providers/anthropic_client.py`

- [ ] 查询当前 Anthropic SDK 文档，确认 `model` 与 `max_tokens` 调用参数。
- [ ] 编写测试：`final_summary`、`verification`、`thinking` 使用各自模型与 token 上限。
- [ ] 运行测试并确认旧实现始终发送主模型参数。
- [ ] 复用 OpenAI 的阶段解析语义实现 Anthropic 路由。
- [ ] 重跑 Provider 测试。

### 任务 9：完成鉴权迁移

**文件：**
- 创建：`apps/api-server/tests/conftest.py`
- 修改：`apps/api-server/tests/test_api_server.py`
- 修改：`.env.compose.example`
- 修改：`docs/DEPLOY.md`
- 修改：`apps/api-server/README.md`

- [ ] 运行 API 全量测试，确认未设置鉴权环境时受保护测试返回 503。
- [ ] 添加测试公共 fixture，默认显式使用 `AUTH_DISABLED=1`，鉴权专项测试可覆盖。
- [ ] 将 Compose 示例明确设为本地开发模式，并写明生产 Token 配对方式。
- [ ] 更新 API README，删除“留空自动跳过认证”的旧说明。
- [ ] 在默认环境和 `AUTH_DISABLED=1` 下重跑 API 全量测试。

### 任务 10：全量验证与差异复审

**文件：**
- 检查：本计划涉及的全部文件

- [ ] 运行 `cd apps/miroflow-agent && uv run pytest -q`。
- [ ] 运行 `cd apps/api-server && AUTH_DISABLED=1 uv run pytest -q`。
- [ ] 运行 `cd apps/gradio-demo && uv run pytest -q`。
- [ ] 运行 `cd libs/miroflow-tools && uv run pytest -q`。
- [ ] 运行项目适用的 Ruff/格式检查，记录任何既存失败。
- [ ] 检查 `git diff --check`、`git diff --stat` 和完整差异，确认未覆盖用户既有修改。
- [ ] 对照设计逐项核验，未通过的任务不得标记完成。
