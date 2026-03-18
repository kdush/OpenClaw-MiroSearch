# OpenClaw-MiroSearch 路线图（版本化）

更新时间：2026-03-18  
说明：已完成能力前置到已发布版本，未完成能力后移到后续版本。

## 已发布

### `v0.1.0`（建议本次发布）

已纳入能力：

- 对外接口定版：`run_research_once` / `run_research_once_v2` / 轮询 / `stop_current` / `info`（见 `docs/API_SPEC.md`）。
- 模式体系可用：`production-web` / `verified` / `research` / `balanced` / `quota` / `thinking`。
- 检索路由可用：`searxng-first` / `serp-first` / `multi-route` / `parallel` / `parallel-trusted` / `searxng-only`。
- `parallel-trusted` 路由支持“并发聚合 + 置信不足串行高信源补检”。
- 交叉校验门槛已落地：最少检索轮次与高置信来源门槛（`verification_min_search_rounds`、`high_conf_domains`）。
- 稳定性保护已落地：连续 LLM 失败保护与任务终态守卫，降低 `running` 悬挂风险。
- 部署与集成可用：`compose.yaml`（`app + searxng + valkey`）与 OpenClaw 技能包（`skills/openclaw-mirosearch/`）。

版本定位：

- 可用基线版本（MVP），用于智能体联网检索与可配置多路路由。

## 后续版本

### `v0.2.0`（生产化补齐）

目标能力：

- 同服务多 Key 轮转（LLM 与搜索源）。
- 模型级 failback（主模型失败自动切换备用模型）。
- 结构化运行观测：429、超时、路由命中率、失败原因分布。
- 最小回归门禁：覆盖 `mode/search_profile/run_research_once_v2` 核心路径。

验收标准：

- 长时间运行稳定。
- 配额耗尽或单点异常时可自动降级。

### `v0.3.0`（质量增强）

目标能力：

- 结构化冲突检测报告（时间窗/统计对象/统计口径/参与方定义）。
- 数字事实专项评测集与自动回归评分。
- 高置信来源白名单分层（按领域可配置）。

验收标准：

- 数字类问答错误率下降。
- 输出可解释，证据链清晰。

### `v1.0.0`（生态与分发）

目标能力：

- 反向代理模板与 HTTPS 生产示例（Nginx/Caddy/Traefik 至少一种）。
- OpenClaw 技能包版本化发布与兼容矩阵。
- 里程碑验收清单与正式发布流程闭环。

验收标准：

- 新环境可按文档快速部署。
- 外部智能体可按技能文档直接调用。
