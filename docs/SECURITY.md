# Security Policy / 安全策略

## Supported Versions / 支持范围

Currently, the latest versions on the `main` and `dev` branches are the primary maintenance targets.

当前以 `main` 与 `dev` 分支的最新版本为主要维护对象。

## Reporting Vulnerabilities / 漏洞报告方式

If you discover a security issue, **do not** disclose details in a public Issue. Please report privately and include:

若发现安全问题，请不要在公开 Issue 直接披露细节。建议通过私下渠道报告，并包含以下信息：

- Affected module and file path / 影响模块与文件路径
- Steps to reproduce / 复现步骤
- Impact assessment / 影响范围评估
- Suggested fix (optional) / 建议修复方案（可选）

**Report channel:** [GitHub Security Advisories](../../security/advisories/new)（推荐 / Recommended）

## Security Baseline / 安全基线要求

- Never commit real keys, tokens, or passwords to the repository / 不在仓库提交真实密钥、令牌、账号密码
- Use `.env` for sensitive configuration; use placeholders when committing / 默认使用 `.env` 管理敏感配置，提交时使用占位符
- Evaluate the license and security risk of new external dependencies / 新增外部依赖时，评估其许可证与安全风险
- All external inputs must be validated with proper boundary and error handling / 外部输入必须做边界与错误处理

## Key Leak Response / 密钥泄露处置

If you suspect a key has been leaked, immediately:

若怀疑密钥已泄露，请立即：

1. Revoke the old key immediately in the provider's console / 在提供方控制台吊销旧密钥
2. Generate a new key and update `.env` / 生成新密钥并更新 `.env`
3. Check logs and API call records for abnormal requests / 检查日志与调用记录，确认是否存在异常请求
