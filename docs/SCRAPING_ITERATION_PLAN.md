# 网页抓取能力迭代计划（v0.2.2 → v0.3.x）

更新时间：2026-04-26
当前版本：`v0.2.2`
计划范围：MCP 工具 `scrape_url`（位于 `libs/miroflow-tools/src/miroflow_tools/dev_mcp_servers/search_and_scrape_webpage.py`）的能力扩展，配合 LLM 在 `google_search` snippet 不足时主动"打开正文"。

---

## 1. 背景

`google_search` 系列工具只返回标题/摘要/URL，对法规原文、官方公告全文、统计公报、长篇报告类问题无法直接给出可信结论。
v0.2.2 已上线最小可用 `scrape_url`：基于 `httpx + BeautifulSoup`，仅支持 HTML/XHTML/纯文本，单次请求，硬切字符截断，无重定向 SSRF 二次校验，无 PDF/JSON/RSS 支持。

实际跑"深圳公交吸烟事件"等任务时观察到的瓶颈：

- 命中的 PDF 公告 / RSS 摘要直接被 `content-type` 白名单拒绝。
- 部分政府站点 `Content-Type: text/html; charset=GBK`，bs4 解码后中文乱码。
- 长正文按字符硬切，常截在句子中间，LLM 引用时上下文断裂。
- 多 URL 串行抓取耗时叠加（5 个站点 × 25s 超时 = 最坏 125s），影响整轮交付时延。
- 重定向 follow 由 httpx 内部完成，跳到内网/云元数据服务的风险未被显式拦截。

---

## 2. 短板分析（4 维度）

### 2.1 功能维度（A）

| ID | 现状 | 期望 |
|----|------|------|
| A1 | 仅支持 `text/html` / `application/xhtml+xml` / `text/plain` | 同时覆盖 PDF / JSON / RSS / Atom / XML，对 LLM 暴露统一 schema |
| A2 | PDF 直接被拒 | 解析 PDF 文本（pdfminer.six 或 pypdf），保留段落边界 |
| A3 | JSON / RSS 类响应一律拒绝 | 直通透传：JSON 原样保留 + 解析摘要、RSS/Atom 返回 entries 结构 |

### 2.2 质量维度（B）

| ID | 现状 | 期望 |
|----|------|------|
| B1 | bs4 + 选择器启发式抽正文，召回不稳 | 引入 `trafilatura` 主路径，bs4 仅作 fallback |
| B2 | 直接 `response.text` 解码，依赖 httpx 自动猜编码 | 增加 `charset_normalizer` + `<meta charset>` 兜底，专门解中文 GBK/GB18030 站点 |
| B3 | `<table>` 在 `get_text()` 后只剩纯文本，列对齐丢失 | 表格转 markdown，保留行/列对齐 |
| B4 | 超长正文按 `text[:cap_chars]` 硬切 | 按段落 / 句号 / 中文句点边界回溯截断，附 `truncated_reason` |

### 2.3 性能与吞吐维度（C）

| ID | 现状 | 期望 |
|----|------|------|
| C1 | 每次调用都新建 `httpx.AsyncClient` | 共享单例 client，复用连接池；单跳 / 解析 / 抽取耗时分别 metrics |
| C2 | 仅支持 `scrape_url(url)` 单条 | 新增 `scrape_urls(urls: list[str], ...)`，受信号量约束的并发抓取 |

### 2.4 安全与稳定性维度（D）

| ID | 现状 | 期望 |
|----|------|------|
| D1 | 入参做了 SSRF 与 scheme 校验 | 维持现状（已就绪） |
| D2 | httpx 默认 follow_redirects 自带跳转 | 改成 `follow_redirects=False`，手动循环最多 5 跳 |
| D3 | 重定向后未对最终目标二次 SSRF 校验 | 每一跳 location 都跑 `_is_private_or_loopback_host` |
| D4 | 响应体只有 `max_chars` 截断 | 在 streaming 阶段就限制 body ≤ 20MB（防内存型 DoS） |

### 2.5 LLM 协同维度（E）

| ID | 现状 | 期望 |
|----|------|------|
| E1 | LLM 只在 prompt 里见到 `scrape_url(url, max_chars)` | 在 system prompt / tool description 里点明"snippet 不够 → 立刻 scrape_url"，并强制对每个引用包含原文片段 |
| E2 | 工具说明仅英文 | 双语关键场景说明（监管公告、统计公报、新闻全文） |

> 注：E 类改造与 prompt 工程绑定，不在本计划单独列任务，而是随 T1-T9 迭代时同步打 prompt 补丁。

---

## 3. 任务清单（T1-T9）

> 顺序：先把"拿得到"的形态打齐（T3/T5），再补"拿得稳"的安全位（T2），随后冲质量（T4/T6/T7/T8），最后做吞吐（T1/T9）。
> T1 与 T2 可并入同一 commit（共享 client + 重定向手动循环天然一体）。

### T1 [C1] scrape_url 共享 httpx AsyncClient + 分阶段耗时 metrics

- 目标：连接池复用、降首字节耗时；区分 dns/connect/tls/transfer/parse 耗时
- 设计：
  - 模块级 `_SCRAPE_CLIENT: httpx.AsyncClient | None`，首次调用 lazy 初始化
  - 进程退出时 `atexit` 关闭；fastmcp shutdown 钩子注册兜底
  - 每次调用记录 `t_request_ms / t_parse_ms / t_extract_ms`，挂在返回 JSON `metrics` 字段
- 测试：
  - 连续 N 次抓同站点，第二次 RTT 显著低于第一次（连接复用）
  - 进程结束后 client 已 close（pytest fixture 检测）
- 回滚：去掉 metrics 字段不影响 LLM 解析；client 单例改回 per-call new 即恢复原状
- 风险：单例 client 配置若被 module reload 打断会泄漏连接，需配合容器优雅 shutdown

### T2 [D2/D3] 重定向手动循环，每跳 SSRF 校验 + 上限 5 跳

- 目标：阻止跳到 169.254.169.254 / 内网 / loopback
- 设计：
  - `follow_redirects=False`
  - while 循环，最多 5 次 30x，每次 `_is_private_or_loopback_host(redirect_target)` 校验
  - 任意一跳判私网立刻 fail，返回 `error="redirect_blocked"`
- 测试：
  - mock 服务器返回 302 → 169.254.169.254：scrape_url 拒绝
  - mock 服务器返回 6 次 302：到达上限拒绝
- 回滚：改回 `follow_redirects=True` 即可
- 风险：少量站点依赖第三方 CDN 跳转，需要把白名单扩展放在配置里

### T3 [A2] PDF 内容抽取（pdfminer）+ 响应大小 20MB 上限

- 目标：政府公告、白皮书 PDF 也能转 text
- 设计：
  - content-type 白名单加 `application/pdf`
  - 流式读取，超过 `SCRAPE_MAX_BODY_BYTES`（默认 20MB）立即中断
  - 优先 `pdfminer.six`（性能稳），fallback `pypdf`
  - 文本归一：去除 `\f`、连字符断行、页眉页脚启发式去重
  - 返回字段加 `content_kind="pdf"`、`pages`、`bytes_read`
- 测试：
  - 真实 PDF（深圳市统计局公报样本）抽取后能命中关键统计口径
  - 巨型 PDF（>20MB）应被拒绝
- 回滚：去掉 `application/pdf` 白名单回到 HTML-only
- 风险：pdfminer 对扫描件无效（无 OCR），需要在返回里标 `text_quality="empty"` 让 LLM 提示用户

### T4 [B2] 中文编码兜底（charset_normalizer / meta charset 估计）

- 目标：解决 `text/html; charset=GBK` 站点在 httpx 默认 utf-8 下乱码
- 设计：
  - 拿 `response.content`（bytes），优先 header 里的 charset
  - 没有就读 HTML head 里的 `<meta charset=...>` / `<meta http-equiv="Content-Type">`
  - 都没有就 `charset_normalizer.from_bytes(...).best()`
  - 对 `application/json` / RSS 仍走 utf-8 / xml decl
- 测试：
  - GBK 编码网页 → 正文中文不乱码
  - meta 标签 charset 与 header 不一致时以 meta 为准（实际遇到不少政府站点这么搞）
- 回滚：直接 `response.text` 即可
- 风险：极少数二进制 HTML（带 BOM）需要特判

### T5 [A3] JSON / RSS / Atom / XML 直通抓取与结构化返回

- 目标：让 LLM 看到结构化条目（标题/链接/发布时间）
- 设计：
  - content-type 白名单加 `application/json`、`application/rss+xml`、`application/atom+xml`、`application/xml`、`text/xml`、`text/json`
  - JSON：保留前 N KB 原始 JSON 字符串 + 用 `json.loads` 后做 schema sniffing（top-level array / object）
  - RSS / Atom：用 `feedparser`，返回 `feed_title`、`entries: [{title,link,published,summary}]`，最多前 50 条
  - 输出字段加 `content_kind in {"html","pdf","json","rss","atom","xml","text"}`
- 测试：
  - GitHub releases.atom → entries 至少 5 条
  - 政府站点 RSS → 命中关键字段
  - JSON 端点（NWS / 数据开放平台）→ 原文与解析摘要并存
- 回滚：去掉新加 mime 走老路径
- 风险：feedparser 对脏 XML 容错强，但对编码识别弱，需要先按 T4 解码再喂

### T6 [B1] 引入 trafilatura 主路径 + bs4 fallback

- 目标：明显提升正文召回（trafilatura 在中文新闻 / 政府站点多源测试上比 bs4 选择器召回高一截）
- 设计：
  - `from trafilatura import extract`，参数 `output_format="markdown"`、`include_tables=True`、`include_comments=False`、`favor_recall=True`
  - trafilatura 抽空时 fallback 现有 `_extract_main_text`
  - 镜像装包：`apps/api-server/Dockerfile` 与 `apps/gradio-demo/Dockerfile` 显式安装 `trafilatura`，避免 worker 容器没有依赖
  - `libs/miroflow-tools/pyproject.toml` 增 optional extra `scrape`
- 测试：
  - 用本地 fixture（`tests/fixtures/news_*.html`）对比两路径召回长度，trafilatura 不应明显劣于 bs4
- 回滚：feature flag `SCRAPE_USE_TRAFILATURA=false` 退回原路径
- 风险：trafilatura 体积大（含 lxml），镜像层会增 10MB 左右，需要写在镜像优化条目

### T7 [B3] HTML 表格转换为 markdown，保留不丢列

- 目标：法规附表、统计表、对比表保留列对齐
- 设计：
  - bs4 fallback 路径：遇到 `<table>` 时调用 `_table_to_markdown(table)`，把每行 `<td>` 映射成 markdown 列
  - 多行表头合并取最浅一层；rowspan/colspan 用 `<br>` 占位避免错位
  - trafilatura 路径：开启 `include_tables=True`，自带 markdown 表格输出
- 测试：
  - 包含 3 列 5 行的表 → markdown 输出列数列名一致
  - 嵌套表 → 不崩，按外层平铺
- 回滚：跳过表格转换，仅输出纯文本
- 风险：复杂跨页表表现欠佳，标注 `tables_lossy=true` 让 LLM 判断

### T8 [B4] 按句句号 / 段落边界截断代替硬切字符

- 目标：让 LLM 引用上下文不被截断
- 设计：
  - 截到 `cap_chars` 时回溯到最近的 `\n\n`、`。`、`.`、`!`、`?`、`！`、`？`，3 选 1 取最远那个边界
  - 截断时附加 `truncation: {strategy: "soft_paragraph", original_chars: N, returned_chars: M}` 元数据
- 测试：
  - 长正文截断后末尾必须落在标点或段落边界
  - 截断不会增加正文（只能更短）
- 回滚：恢复 `text[:cap_chars]`
- 风险：极长无标点正文（爬下来的纯英文 wall-of-text）会回退到原硬切，需要 fallback 路径

### T9 [C2] 新增 scrape_urls(urls,...) 批量并发工具

- 目标：LLM 一轮可以让多个 URL 并发抓取，把"3 个候选源"的 RTT 从串行 75s 压到并行 25s
- 设计：
  - 新工具 `scrape_urls(urls: list[str], max_chars=8000, concurrency=4)`，最多 8 条
  - 内部 `asyncio.Semaphore(concurrency)` 控制并发，复用 T1 的共享 client
  - 单条失败不阻塞整体，返回 `[{url, success, ...}, ...]`
  - 总体超时 `SCRAPE_BATCH_TIMEOUT_SECONDS`（默认 60s）
- 测试：
  - 4 条 URL 中 1 条 5xx，剩余 3 条仍能拿到正文
  - 8+ 条 URL 时返回前 8 条
- 回滚：仅注释 `@mcp.tool()` 装饰器即可下线
- 风险：批量抓取会显著放大被站点封禁的风险，必须配 robots.txt 校验或退避（v0.3 再做）

---

## 4. 迭代节奏与版本映射

| 版本 | 任务 | 目标 |
|------|------|------|
| **v0.2.2**（已发布） | scrape_url 最小可用 + SSRF 防护 + 单元测试 | 让 LLM 至少能"打开 HTML 正文" |
| **v0.2.3** | T2 + T1 + T4 | 安全闭环 + 编码鲁棒 + 共享 client |
| **v0.2.4** | T3 + T5 | 把 PDF / JSON / RSS / Atom / XML 入口接上 |
| **v0.2.5** | T6 + T7 + T8 | 提升正文质量与表格保真，对接 trafilatura |
| **v0.3.0** | T9 + 配额限流 + robots.txt 校验 | 批量抓取与外部站点友好性 |

> 上述节奏与 `docs/ROADMAP.md` 中 "v0.2.5（质量增强 + 可观测性）" 互不冲突；评测体系与可观测性属于 ROADMAP 主线，本计划专注抓取工具能力。

---

## 5. 验收路径

每次任务交付都需跑下述基线：

1. **单元测试**：`uv run pytest libs/miroflow-tools/src/test/test_search_and_scrape_webpage_guards.py -v`
2. **集成验证**：在 demo / api-server 重跑下列 query，要求最终 Markdown 必须包含从 `scrape_url` 抓回的 **原文片段** 而不是仅 `google_search` snippet：
   - 监管类："深圳公交吸烟事件中应用了哪些条例？请给出条例原文"
   - 统计类："2024 年深圳市常住人口数据出处与具体数字"
   - 公告类："国家市场监管总局 2026 年 1 月发布的合规处罚案例"
3. **回归门禁**：在 CI `run-tests.yml` 中追加 `scrape_url` mock 测试用例，禁止重定向 SSRF 与超大 body 通过。
4. **观测性**：每个版本灰度上线后查看 `stage_heartbeat` 中是否出现 `scrape_url`，命中率 / 平均耗时通过 worker 日志聚合。

---

## 6. 回滚预案

| 风险点 | 回滚策略 |
|--------|----------|
| 单例 client 泄漏连接 | 环境变量 `SCRAPE_REUSE_CLIENT=false` 退回 per-call client |
| trafilatura 抽取退化 | 环境变量 `SCRAPE_USE_TRAFILATURA=false` 退回 bs4 路径 |
| pdfminer 解析超时 / 内存爆炸 | 环境变量 `SCRAPE_ENABLE_PDF=false` 关闭 PDF 通道 |
| 批量抓取被封 IP | 环境变量 `SCRAPE_BATCH_ENABLED=false` 下线 `scrape_urls` 工具 |

---

## 7. 验收 Demo Query（对照表）

| Query | 期望工具调用顺序 | 期望最终产物特征 |
|-------|------------------|-------------------|
| "深圳公交吸烟事件中应用了哪些条例？" | `google_search` → `scrape_url(法规原文 URL)` | 报告含 `《XX 条例》第 N 条 ...` 完整条文，不止于关键词 |
| "2024 年深圳市常住人口数据" | `google_search` → `scrape_url(统计公报 PDF)` | 报告含具体数字与公报标题，并标 PDF 来源 |
| "GitHub kubernetes/kubernetes 最新 release notes" | `google_search` → `scrape_url(.../releases.atom)` | 报告以 entries 形式列前 5 个 release |
| "OpenAI 2026 Q1 财报" | `google_search` → `scrape_urls([官网,SEC,新闻])` | 报告交叉引用 ≥3 源，用 `[N]` 引用真实 URL |

---

## 8. 落地注意事项

- 所有改动严格走 dev_mcp_servers，不污染生产 MCP 协议字段。
- prompt 侧（`apps/miroflow-agent/src/prompts/`）需要补：在 google_search 命中长正文需求场景下，强制要求至少调用一次 `scrape_url`，否则视为未完整披露。
- 镜像层装包优先用 `--no-cache-dir` 与 BuildKit cache mount，避免镜像膨胀。
- 任何对外 API（FastAPI、Gradio、Skill 包）都不需要直接暴露 `scrape_url`：它是 LLM agent 内部工具，对外契约保持稳定。
