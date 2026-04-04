# MiroSearch API Server

独立于 Gradio Demo 的标准 HTTP API 层，基于 FastAPI 构建。

## 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/research` | 提交研究任务 |
| GET | `/v1/research/{task_id}/stream` | SSE 流式获取任务进度 |
| POST | `/v1/research/{task_id}/cancel` | 取消指定任务 |
| POST | `/v1/research/cancel` | 按 caller_id 批量取消 |
| GET | `/v1/metrics/last` | 最近任务运行指标 |
| GET | `/health` | 健康检查 |

## 快速启动

```bash
cp .env.example .env
# 编辑 .env 填入实际配置
uv sync
uv run python main.py
```

## 认证

设置 `API_TOKENS` 环境变量启用 Bearer Token 认证（逗号分隔支持多 Token）。  
留空则跳过认证（开发模式）。

```bash
# 请求示例
curl -X POST http://localhost:8090/v1/research \
  -H "Authorization: Bearer your-token" \
  -H "Content-Type: application/json" \
  -d '{"query": "量子计算最新进展"}'
```

## 测试

```bash
uv run pytest tests/ -v
```
