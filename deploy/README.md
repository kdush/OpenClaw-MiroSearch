# Deploy Resources / 部署资源

This directory contains reusable, platform-agnostic deployment resources for the open-source repository.

本目录保留适合开源仓库长期维护的通用部署辅助资源。

## Contents / 当前内容

| File / 文件 | Purpose / 用途 |
|------|------|
| `searxng/settings.yml` | SearXNG configuration template / SearXNG 配置模板 |

## Deployment Checklist / 部署检查清单

### Pre-deployment / 部署前

- [ ] Docker installed / 已安装 Docker
- [ ] Docker Compose installed (if using Compose) / 如使用 Compose，已安装 Docker Compose
- [ ] Sufficient CPU, memory, and disk resources / 具备满足当前模型与服务的资源
- [ ] Network access to external model and search services / 具备访问外部模型服务与搜索服务所需的网络权限
- [ ] Root `compose.yaml` exists / 根目录 `compose.yaml` 存在
- [ ] `.env.compose` copied from `.env.compose.example` and configured / `.env.compose` 已由 `.env.compose.example` 复制并完成配置
- [ ] `deploy/searxng/settings.yml` correctly mounted / `deploy/searxng/settings.yml` 可被正确挂载
- [ ] `BASE_URL` and `API_KEY` configured / `BASE_URL` 和 `API_KEY` 已配置
- [ ] Port configuration does not conflict with existing services / 端口配置未与宿主机已有服务冲突

### Deployment / 部署执行

- [ ] Deployment method chosen (Docker Compose or custom) / 已选择合适的部署方式
- [ ] Images built or pulled successfully / 镜像已成功构建或拉取
- [ ] Containers started successfully / 容器已成功启动

### Post-deployment / 部署后

- [ ] Web UI accessible / Web 界面服务可访问
- [ ] API health check passing / API 服务健康检查正常
- [ ] Search service health check passing / 搜索服务健康检查正常
- [ ] Can submit a research request and see streaming output / 能正常发起一次研究请求并看到流式输出
- [ ] No persistent errors in logs / 日志中无明显持续性错误
- [ ] Rollback plan prepared / 已准备回滚方案

## Principles / 使用原则

- Platform-specific scripts, host addresses, and private deployment details should be maintained in private docs / 私有服务器、局域网主机、NAS 平台和个人运维流程不在开源仓库中保留
- Only reusable, reviewable, infrastructure-agnostic resources belong here / 开源仓库仅保留可复用、可审阅、与具体基础设施解耦的部署说明

## Recommended Entry Points / 推荐入口

- Root `compose.yaml` + `.env.compose.example`
- [`docs/DEPLOY.md`](../docs/DEPLOY.md) — full deployment guide / 完整部署指南
- [`apps/gradio-demo/README.md`](../apps/gradio-demo/README.md)
- [`apps/api-server/README.md`](../apps/api-server/README.md)
