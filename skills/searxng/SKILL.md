---
name: searxng
description: 使用本地或自托管的 SearXNG 实例执行简单网页检索、图片检索、新闻检索和低成本事实查询。用于 OpenClaw 或其他智能体在不需要深度研究编排时进行快速搜索，支持 category、language、time-range、json 输出，以及通过 SEARXNG_URL 和 SEARXNG_VERIFY_SSL 控制实例地址与 TLS 校验。
---

# SearXNG Search

使用你的本地或自托管 SearXNG 实例执行简单搜索。

这个仓库内分发的版本已按 `MiroThinker / OpenClaw-MiroSearch` 的默认部署做了适配：

- 优先兼容仓库已有的 `SEARXNG_BASE_URL`
- 默认指向本项目 Docker Compose 暴露的 `http://127.0.0.1:27080`
- 支持通过 `SEARXNG_VERIFY_SSL` 控制 TLS 校验

优先场景：

- 快速找网页、单事实查询、低成本优先
- 需要直接拿 SearXNG JSON 结果做后续程序处理
- 不需要 `openclaw-mirosearch` 那套多轮研究、核查和长报告能力

## Commands

### Web Search
```bash
uv run {baseDir}/scripts/searxng.py search "query"              # Top 10 results
uv run {baseDir}/scripts/searxng.py search "query" -n 20        # Top 20 results
uv run {baseDir}/scripts/searxng.py search "query" --format json # JSON output
```

### Category Search
```bash
uv run {baseDir}/scripts/searxng.py search "query" --category images
uv run {baseDir}/scripts/searxng.py search "query" --category news
uv run {baseDir}/scripts/searxng.py search "query" --category videos
```

### Advanced Options
```bash
uv run {baseDir}/scripts/searxng.py search "query" --language en
uv run {baseDir}/scripts/searxng.py search "query" --time-range day
```

## Configuration

配置环境变量：

```bash
export SEARXNG_BASE_URL=http://127.0.0.1:27080
export SEARXNG_VERIFY_SSL=true
```

也可以在运行时配置中设置：
```json
{
  "env": {
    "SEARXNG_BASE_URL": "http://127.0.0.1:27080",
    "SEARXNG_VERIFY_SSL": "false"
  }
}
```

默认值：

- `SEARXNG_BASE_URL` 或 `SEARXNG_URL` 未设置时，默认使用 `http://127.0.0.1:27080`
- `SEARXNG_VERIFY_SSL` 未显式设置时：
  - 本地 `http://` 或 `localhost/127.0.0.1` 默认关闭校验
  - 远程 `https://` 默认开启校验

若你使用自签名证书的远程实例，显式设置 `SEARXNG_VERIFY_SSL=false`。

## Features

- 🔒 Privacy-focused (uses your local instance)
- 🌐 Multi-engine aggregation
- 📰 Multiple search categories
- 🎨 Rich formatted output
- 🚀 Fast JSON mode for programmatic use

## API

Uses your local SearXNG JSON API endpoint (no authentication required by default).
