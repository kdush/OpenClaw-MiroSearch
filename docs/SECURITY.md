[English](SECURITY.md) | [中文](SECURITY_zh.md)

# Security Policy

## Supported Versions

The latest versions on the `main` and `dev` branches are the primary maintenance targets.

## Reporting Vulnerabilities

If you discover a security issue, **do not** disclose details in a public issue. Report it privately and include:

- The affected module and file path
- Steps to reproduce
- An impact assessment
- A suggested fix, if available

**Preferred reporting channel:** [GitHub Security Advisories](../../security/advisories/new)

## Secure Defaults

- Never commit real keys, tokens, passwords, or other secrets.
- Store sensitive configuration in `.env` files and commit placeholders only.
- Assess the license and security risks of every new external dependency.
- Validate all external input and handle boundary conditions and errors explicitly.
- The API is fail-closed by default. If `API_TOKENS` is not configured and `AUTH_DISABLED != 1`, protected endpoints return `503`.
- `AUTH_DISABLED=1` is an explicit authentication bypass for local development only. Production and shared deployments must configure strong `API_TOKENS` and keep authentication enabled.

## Responding to a Key Leak

If you suspect that a key has leaked:

1. Revoke the old key immediately in the provider's console.
1. Generate a replacement and update the relevant `.env` file.
1. Review logs and API request records for suspicious activity.
1. Rotate any downstream secrets that may have been exposed through the compromised key.
