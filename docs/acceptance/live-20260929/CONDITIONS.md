# 真实验收运行条件（2026-09-29）

本文件固定本次「真实验收 + 基线对比」运行的**全部可复现条件**。任何结论都只在
这些条件下成立；条件不同的运行**不可直接与本次结果归因比较**。

## 1. 代码版本

| 项 | 值 |
|---|---|
| 仓库 | `rainwalkerhu/OpenClaw-MiroSearch`（fork）→ `kdush/OpenClaw-MiroSearch`（上游） |
| 提交 | `8f91db9637f6e504d33f48a72c43e73ff287f7eb`（上游 main 的 #2 合并提交） |
| 分支 | `feat/acceptance-live-and-baseline-compare`（自上述提交新建，工作树干净） |
| 对比基线 | `docs/acceptance/baseline-2026-09-24/`（阶段 A-1 冻结，HEAD `8aa2b9c`） |

本次运行期间**未修改任何产品代码**，因此「候选」= 「#2 合并后的 main」。
若后续发现缺陷并另作修复，修复提交会单独列出，不与本次数据混算。

## 2. LLM 与端点

| 项 | 值 |
|---|---|
| 网关 | 智谱 BigModel（OpenAI 兼容） |
| 端点 | `https://open.bigmodel.cn/api/paas/v4` |
| 模型 | `glm-4.5-flash`（免费档） |
| 路由 | `DEFAULT_MODEL_NAME` 与 5 个 `MODEL_*_NAME`（tool/fast/thinking/summary）**全部**指向同一模型，无旁路 |
| `LLM_MAX_TOKENS` | 16384 |
| 凭据来源 | `apps/gradio-demo/.env`（gitignore）；**密钥只经环境变量注入，不进入提交、不写入产物、不打印** |

### 2.1 为什么不是基线同款模型（归因前提，必读）

基线用的是 `glm-5.3-flash`（付费）。本账号**付费额度已耗尽**，付费模型一律返回
`HTTP 429 code 1113 余额不足或无可用资源包`，因此**本次候选运行与基线不是同一模型**。

**这直接决定了 §7 对比的解读方式**：本次对比表只用于**记录现状与发现异常**，
**不足以支撑「代码改动带来提升」的结论**。要归因到代码，需让基线与候选在同一模型、
同一检索配置、相近容量下复跑（见 `RESULTS.md` 的「可归因性」一节）。

## 3. 容量前置检查（先证明容量稳定，再跑用例）

用户要求「先确保深度用例所需的 LLM 容量稳定」。为此在开跑前做了定点探测
（脚本：连续 12 次带 tool schema 的 chat completion，模拟 deep 用例的 10+ 次调用负载）：

| 模型 | 成功率 | tool_calls 是否真的发生 | 单次延迟 p50 / p95 | 判定 |
|---|---|---|---|---|
| `glm-4.7-flash` | **0/12** | — | — | ❌ 全量 `429 code 1305`（该模型当前访问量过大） |
| `glm-4.5-flash` | **12/12** | ✅ 每次都发起工具调用 | 4.4s / 11.6s | ✅ **选用** |
| `glm-4-flash` | 12/12 | ❌ **0 次**（无视 tool schema，直接作答） | 4.5s / 7.7s | ❌ 不支持工具调用，agent 循环不可用 |

结论：`glm-4.7-flash`（此前记录的首选免费模型）在本时段**完全不可用**；
`glm-4.5-flash` 是当前唯一「有容量 + 支持 tool calling」的免费选项，故选用。
`glm-4-flash` 虽快但不会调工具，用它跑出的「通过」会掩盖检索/抓取路径未被触达的事实，
**不可用于验收**。

## 4. 检索配置

| 项 | 值 |
|---|---|
| `SEARCH_PROVIDER_ORDER` | `serper` |
| `SEARCH_PROVIDER_MODE` | `fallback` |
| `SEARCH_PROVIDER_TRUSTED_ORDER` | `serper` |
| `SEARCH_RESULT_NUM` | 20 |
| `SEARXNG_BASE_URL` | 空（本机无 docker，SearXNG 不可达，故不探测） |
| `SERPAPI_API_KEY` / `TAVILY_API_KEY` | 空 |
| `SEARCH_CONFIDENCE_ENABLED` | 1 |
| `SEARCH_CONFIDENCE_SCORE_THRESHOLD` | 0.62 |
| `SEARCH_CONFIDENCE_MIN_RESULTS` | 8 |
| `SEARCH_CONFIDENCE_MIN_UNIQUE_DOMAINS` | 5 |
| `SEARCH_CONFIDENCE_MIN_PROVIDER_COVERAGE` | 1 |
| `SEARCH_CONFIDENCE_MIN_HIGH_CONF_HITS` | 2 |

**只有 serper 一路可用**——与基线环境快照一致（基线同样是单 provider 退化）。
注意：用例 D1 自带 `env` 覆写要求 `SEARXNG_BASE_URL=http://127.0.0.1:27080` 且
`SEARCH_PROVIDER_ORDER_STRICT=1`，本机无 SearXNG，故 D1 预期检索不可达；
用例 D2 自带 `parallel_conf_fallback` 四路配置，实际同样只有 serper 可达。
这两例的「配置与可达性不一致」是**环境事实**，不是代码缺陷，报告中如实标注。

## 5. 用例与时间窗

- 用例集：路线图全量 16 个（`A, B, C, D1, D2, E1, E2, H1, H2, H3, H4, H5A, H5B, L1, L2, L3`）。
- 执行顺序：**先 B（deep）与 H1（hotspot）**（此前被限流打穿的两例），再其余 14 个。
- 时间窗：2026-09-29 16:05–（CST，UTC+8）。
- 复测命令（在 `apps/miroflow-agent` 下）：

```bash
.venv/Scripts/python.exe scripts/run_acceptance_live.py \
    --out-dir ../../docs/acceptance/artifacts/live-20260929 \
    --cases B,H1,C,E1,E2,H2,H3,H4,H5A,H5B,L1,L2,L3,D1,D2,A
```

- 原始产物（**gitignore**，仅本地）：`docs/acceptance/artifacts/live-20260929/`
  （`acceptance_results.json`、`case_*.json`、`case_*_summary.md`、`logs/`）。
- 本目录内的文件是**脱敏摘要**，可入库、可审阅。

## 6. 额度条件与「环境容量阻塞」的判定口径

- 账号付费额度耗尽（付费模型 `429 code 1113`）；免费档受**共享限流**影响，
  高峰期返回 `429 code 1305 该模型当前访问量过大`。
- **判定口径（严格）**：用例若因 `429` 未跑完，一律标为「**环境容量阻塞**」，
  保留原始错误与调用计数，**不计入质量通过，也不计为代码回归**；
  择低峰时段或足额资源重跑。
- 只有「无 429、且 pipeline 正常跑完」的用例，才进入质量与门禁判定。
