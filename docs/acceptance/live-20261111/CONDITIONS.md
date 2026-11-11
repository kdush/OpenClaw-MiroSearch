# 真实验收运行条件（2026-11-11）

本文件固定本次「真实验收 + 基线对比」运行的**全部可复现条件**。任何结论都只在
这些条件下成立；条件不同的运行**不可直接与本次结果归因比较**。

## 1. 代码版本

| 项 | 值 |
|---|---|
| 仓库 | 当前工作区 fork / 上游 `OpenClaw-MiroSearch` |
| 提交 | `fb2e99f04195c426db8c5f646a08d0e7d31e7556`（本轮开跑时 HEAD；含验收工具修复与上一轮产物） |
| 分支 | `cursor/acceptance-rivo-glm53flash-6366`（自 `feat/acceptance-live-and-baseline-compare` 拉出） |
| 对比基线 | `docs/acceptance/baseline-2026-09-24/`（阶段 A-1 冻结） |

本次运行期间**未修改任何产品代码**，只换了 LLM 网关与检索凭据并跑验收脚本。

## 2. LLM 与端点

| 项 | 值 |
|---|---|
| 网关 | RivoAPI（OpenAI 兼容） |
| 端点 | `https://api.rivoapi.com/v1` |
| 模型 | `glm-5.3-flash`（在本账号可访问模型中按定价比例选的便宜档，且通过工具调用探测） |
| 路由 | `DEFAULT_MODEL_NAME` 与 `MODEL_TOOL/FAST/THINKING/SUMMARY_NAME` **全部**指向同一模型，无旁路 |
| `LLM_MAX_TOKENS` | 16384 |
| 凭据来源 | `apps/gradio-demo/.env`（gitignore）；**密钥只经环境变量注入，不进入提交、不写入产物、不打印** |

### 2.1 与基线模型关系（归因前提）

基线同样记录为 `glm-5.3-flash`，但走的是智谱 BigModel 直连；本轮走 **RivoAPI 转发**。
模型名一致、网关不同，因此 §7 数字**比上一轮（候选 `glm-4.5-flash`）更接近可比**，
仍不能把「网关 / 配额 / 调度差异」与「代码改动」完全拆开。供应商费用本轮未回填。

## 3. 容量前置检查

开跑前对 `glm-5.3-flash` 做 12 次带 tool schema 的 chat completion：

| 模型 | 成功率 | tool_calls 是否发生 | 单次延迟 p50 / p95 | 判定 |
|---|---|---|---|---|
| `glm-5.3-flash` | **12/12** | ✅ 12/12 | 12.0s / 42.6s | ✅ **选用** |

同账号下更便宜的若干型号（如部分 luna/haiku/qwen flash）返回 `access_denied`，未选用。

## 4. 检索配置

| 项 | 值 |
|---|---|
| `SEARCH_PROVIDER_ORDER` | `serper` |
| `SEARCH_PROVIDER_MODE` | `fallback` |
| `SEARCH_PROVIDER_TRUSTED_ORDER` | `serper` |
| `SEARCH_RESULT_NUM` | 20 |
| `SEARXNG_BASE_URL` | 空（本环境无 docker / SearXNG 未起） |
| `SERPAPI_API_KEY` / `TAVILY_API_KEY` | 空 |
| `SEARCH_CONFIDENCE_ENABLED` | 1 |
| `SEARCH_CONFIDENCE_MIN_PROVIDER_COVERAGE` | 1 |

**主路径只有 Serper 一路。** 用例 D1 / H5A 自带 SearXNG 严格或对比配置，本机不可达，
预期检索退化或失败——这是环境事实，不是代码回归。

## 5. 用例与时间窗

- 用例集：全量 16 个（`A, B, C, D1, D2, E1, E2, H1, H2, H3, H4, H5A, H5B, L1, L2, L3`）。
- 执行：先 A 冒烟，再 `B,H1,C,E1,E2,H2,H3,H4,H5A,H5B,L1,L2,L3,D1,D2`；harness 与已落盘 `case_*.json` 合并。
- 时间窗：2026-11-11 08:58–10:20（北京时间，UTC+8）。
- 原始产物（gitignore）：`docs/acceptance/artifacts/live-20261111/`
- 可入库产物：本目录。

```bash
cd apps/miroflow-agent
uv run python scripts/run_acceptance_live.py \
    --out-dir ../../docs/acceptance/artifacts/live-20261111 \
    --cases B,H1,C,E1,E2,H2,H3,H4,H5A,H5B,L1,L2,L3,D1,D2,A
```
