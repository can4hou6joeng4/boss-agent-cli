# Platform Risk Boundaries

boss-agent-cli automates access to recruitment platforms, but it does not control platform rules, account risk control, API drift, or third-party browser environments.

## 1. Platform APIs can drift

The project depends on web pages, APIs, cookies, login state, and response shapes from platforms such as BOSS Zhipin and Zhaopin. These platforms can change fields, risk-control behavior, endpoint paths, or page flows at any time.

Treat these symptoms as possible platform drift:

- Previously working read-only commands suddenly return `NETWORK_ERROR`, `AUTH_EXPIRED`, `TOKEN_REFRESH_FAILED`, or malformed structures.
- `search` returns results but `detail` fails.
- `boss schema --format native` works, but live commands fail.
- Mock tests pass while real-account commands fail.

## 2. Login and Cookie boundaries

Login can use Cookie extraction, CDP, QR httpx, or patchright browser automation. The project reads and stores login state locally. Users should never submit cookies, tokens, phone numbers, WeChat IDs, names, company-private information, or real `security_id` values to the repository.

Redact issue payloads:

```json
{
	"security_id": "<redacted>",
	"cookie": "<redacted>",
	"token": "<redacted>"
}
```

## 3. Request rate and account responsibility

The default request interval is controlled by `--delay`. Do not use this tool for high-frequency scraping, spam, bypassing platform limits, or violating platform terms. For write actions such as `greet`, `apply`, `exchange`, and `hr reply`, confirm the target and context with read-only commands first.

## 4. Browser automation boundaries

patchright, CDP, local Chrome profiles, system keychains, browser extensions, and platform risk-control systems all affect login and access stability. A working browser session does not guarantee the httpx path works; a working httpx path does not guarantee the patchright login path works.

## 5. Smoke-test boundaries

Real-flow smoke tests must be explicitly configured and should not access real accounts from regular CI:

```bash
BOSS_SMOKE_DRY_RUN=1 uv run python scripts/smoke_p0.py
BOSS_SMOKE_PLATFORM=zhipin BOSS_SMOKE_QUERY=Golang BOSS_SMOKE_SECURITY_ID=<redacted> uv run python scripts/smoke_p0.py
```

`BOSS_SMOKE_DRY_RUN=1` verifies the plan only. It does not prove live platform availability.

## 6. Reporting security issues

If a report involves cookies, tokens, accounts, contact details, private resumes, company-private information, or exploitable automation bypasses, do not open a public issue. Use the private disclosure channels in [SECURITY.md](../SECURITY.md).
