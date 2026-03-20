# Security Policy

> 📄 中文版：[SECURITY.md](./SECURITY.md)

## Supported Versions

Currently, the latest versions on the `main` and `dev` branches are the primary maintenance targets.

## Reporting Vulnerabilities

If you discover a security issue, **do not** disclose details in a public Issue. Please report privately and include:

- Affected module and file path
- Steps to reproduce
- Impact assessment
- Suggested fix (optional)

**Contact:** Please use [GitHub Security Advisories](../../security/advisories/new) to report vulnerabilities privately.

## Security Baseline

- Never commit real keys, tokens, or passwords to the repository
- Use `.env` for sensitive configuration; use placeholders when committing
- Evaluate the license and security risk of new external dependencies
- All external inputs must be validated with proper boundary and error handling

## Key Leak Response

If you suspect a key has been leaked:

1. Revoke the old key immediately in the provider's console
2. Generate a new key and update `.env`
3. Check logs and API call records for abnormal requests
