# 部署指南

[English](./DEPLOY.md)

本文档说明如何用 Docker Compose 部署 `v0.2.11` 运行时。默认拓扑运行 `app`、
`api`、`worker`、`valkey` 和 `searxng`。

## 前置条件

- Docker Engine 24 或更高版本
- Docker Compose v2
- OpenAI 兼容的 LLM 端点及 API Key
- 可选的搜索提供商 Key，用于扩大检索覆盖

## 准备配置

```bash
cp .env.compose.example .env.compose
```

至少需要替换以下占位值：

```dotenv
BASE_URL=https://your-llm-gateway.example/v1
API_KEY=replace_with_your_llm_key
```

启动前检查复制出的文件。尤其需要确保每个鉴权键（`API_TOKENS`、
`API_BEARER_TOKEN` 和 `AUTH_DISABLED`）只有一个实际生效的定义，并显式选择一种
模式。如果某个模板版本含有重复定义，dotenv 最后一次赋值可能覆盖前面的值，部署时
很容易误读。

### 本机开发鉴权

仅在所有发布端口都保持回环绑定时使用：

```dotenv
BIND_HOST=127.0.0.1
AUTH_DISABLED=1
API_TOKENS=
API_BEARER_TOKEN=
```

### 共享或生产鉴权

在仓库外生成随机强 Token，再为 FastAPI 服务端和默认 Gradio API 客户端设置相同
Token：

```dotenv
BIND_HOST=127.0.0.1
AUTH_DISABLED=0
API_TOKENS=replace_with_a_random_strong_token
API_BEARER_TOKEN=replace_with_the_same_token
```

`API_TOKENS` 可以用逗号分隔多个服务端 Token，`API_BEARER_TOKEN` 必须与其中一个
一致。不要提交 `.env.compose`。

FastAPI 服务默认 fail-closed：未配置 Token 且没有设置 `AUTH_DISABLED=1` 时，
受保护端点返回 `503`；请求缺少有效凭据时返回 `401`。

## 选择检索默认值

所有值都可以在 `.env.compose` 中配置。海外提供商可能不稳定的网络环境，可先使用
以下保守配置：

```dotenv
DEFAULT_RESEARCH_MODE=balanced
DEFAULT_SEARCH_PROFILE=searxng-first
DEFAULT_SEARCH_RESULT_NUM=20
DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS=3
```

如果网络可以稳定访问多个已配置提供商，可使用
`DEFAULT_SEARCH_PROFILE=parallel-trusted`。仅为实际使用的提供商填写
`SERPAPI_API_KEY`、`SERPER_API_KEY` 或 `TAVILY_API_KEY`。

## 启动服务栈

```bash
docker compose --env-file .env.compose up -d --build
```

Compose 顶层项目名固定为 `openclaw-mirosearch`（`compose.yaml` / `compose.host-network.yaml` 的 `name:`）。
镜像标签可以是 `diting:latest`，但**不要**把项目名改成 `diting`：否则 `docker compose up` 会新建一组容器与
`diting_valkey-data` 卷，与旧栈争用端口，且读不到原任务/缓存。

若曾误用 `name: diting` 起过栈：先 `docker compose -p diting down`（确认无用后再删卷），再在本仓库用默认项目名启动；
Valkey 数据不会自动迁移，需要停机后自行拷贝卷或接受缓存清空。

默认发布地址如下：

| 服务 | 地址 |
| --- | --- |
| Gradio app | `http://127.0.0.1:8080` |
| FastAPI | `http://127.0.0.1:8090` |
| SearXNG | `http://127.0.0.1:27080` |

`worker` 和 `valkey` 是不发布主机端口的内部服务。端口可通过 `APP_PORT`、
`API_PORT` 和 `SEARXNG_HOST_PORT` 修改。

## 验证部署

```bash
docker compose ps
docker compose logs --tail=100 api worker
curl http://127.0.0.1:8090/health
curl http://127.0.0.1:8080/gradio_api/info
```

健康检查响应报告 `API_VERSION`。其代码默认值为 `0.2.0`，与项目发布版本
`v0.2.11` 相互独立。

发送带鉴权的请求：

```bash
curl -X POST http://127.0.0.1:8090/v1/research \
  -H "Authorization: Bearer ${API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"query":"总结最新的检索研究进展。"}'
```

轮询与 SSE 示例参见 [API 规格说明](./API_SPEC_zh.md)。

## 网络暴露

Compose 默认将 `app`、`api` 和 `searxng` 绑定到 `127.0.0.1`。建议通过可信的
TLS 反向代理提供服务，并保留回环绑定。如果必须直接暴露主机端口，应先启用 Token
并配置防火墙，再设置 `BIND_HOST=0.0.0.0`。

只有在可信代理会覆盖 `X-Forwarded-For` 时才能设置 `TRUST_PROXY=1`，否则限流
可能信任伪造的客户端地址。FastAPI 请求限流默认为 `RATE_LIMIT_RPM=30`。

无法使用 Docker 桥接网络时，仓库还提供 `compose.host-network.yaml`：

```bash
docker compose -f compose.host-network.yaml \
  --env-file .env.compose up -d --build
```

Host 网络会移除常规端口隔离。使用前需要检查本机端口冲突和防火墙规则。

## 日常运维

读取日志：

```bash
docker compose logs -f app api worker
```

重启应用服务且不删除数据：

```bash
docker compose restart app api worker
```

停止服务栈并保留命名卷：

```bash
docker compose down
```

Valkey 任务数据和 SearXNG 缓存位于命名卷中。删除卷也会删除持久化任务、事件、
结果和缓存；执行任何卷删除操作前，应先备份所需数据。

## 故障排查

| 现象 | 检查项 |
| --- | --- |
| 受保护 API 返回 `503` | 配置 `API_TOKENS`，或仅为本机开发显式使用 `AUTH_DISABLED=1` |
| 受保护 API 返回 `401` | 检查 Bearer Token，确认 `API_BEARER_TOKEN` 与服务端 Token 一致 |
| 任务长期停留在 `queued` | 检查 `worker` 健康状态及其与 `valkey` 的连接 |
| Gradio 无法提交任务 | 检查 `BACKEND_MODE=api`、`API_BASE_URL` 和客户端 Token 配置 |
| 搜索结果很少 | 检查 SearXNG 健康状态、提供商 Key 和检索路由 |
| 请求返回 `429` | 降低请求速率，或调整部署中的有界限流策略 |
