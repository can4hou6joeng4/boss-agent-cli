# Troubleshooting

> When something misbehaves, always start with `boss doctor` and `boss status` — most
> failures carry an actionable `error.recovery_action` in the JSON envelope.
> 中文版见 [troubleshooting.md](troubleshooting.md)。
> For Cookie, CDP, patchright, real-account, request-rate, or platform-drift issues,
> read [Platform Risk Boundaries](platform-risk.en.md) first.

```bash
boss doctor
boss status
# Optional: run an explicit low-frequency read-only live probe
boss status --live
boss doctor --live-probe
```

## Doctor checks

| Check | What it means |
|-------|---------------|
| `python` | Python ≥ 3.10 installed |
| `patchright_chromium` | The Chromium and headless shell revisions required by patchright are installed; Windows also checks `%LOCALAPPDATA%\ms-playwright` |
| `windows_uv_tool_path` | Whether the global `uv tool` command directory is on PATH on Windows |
| `cookie_extract` | Local browser cookies accessible |
| `credential_file` | Encrypted credential file exists and is readable |
| `auth_session` | Encrypted session file readable |
| `cookie_presence` / `wt2_presence` | Cookies and the primary auth cookie are present |
| `stoken_presence` / `stoken_freshness` | `__zp_stoken__` exists and is likely fresh |
| `auth_token_quality` | Core tokens (wt2 / stoken) present |
| `cookie_completeness` | Auxiliary tokens (wbg / zp_at) |
| `cdp` | Chrome DevTools Protocol reachable |
| `bridge_daemon` | Local Browser Bridge daemon is reachable |
| `bridge_extension` | Chrome extension is connected to the daemon |
| `bridge_protocol` | CLI and extension version/protocol are compatible |
| `bridge_workspace` | Current Bridge workspace/tab is usable |
| `bridge_exec` / `bridge_fetch` / `bridge_navigate` | Basic extension execution, browser fetch, and navigation capabilities |
| `browser_channel` | CDP/Bridge summary; not a risk-control bypass path |
| `candidate_search_health` / `candidate_detail_health` | Candidate read-only prerequisites |
| `recruiter_read_health` | Recruiter read-only prerequisites; Zhaopin recruiter mode is explicitly marked unsupported |
| `network` | zhipin.com reachable |

## Login issues

### Cookie extraction fails

```bash
# Force re-login via QR scan
boss logout && boss login
```

### BOSS detects automation (code 36 / `ACCOUNT_RISK`)

Stop automated access and return to the official BOSS Zhipin website. Do not retry the blocked action through CDP, patchright, or Browser Bridge.

### Browser Bridge is not connected

```bash
python -m boss_agent_cli.bridge.daemon --serve
# Then load and enable extension/ from chrome://extensions, and run:
boss doctor
```

`bridge_daemon`, `bridge_extension`, `bridge_protocol`, `bridge_workspace`,
`bridge_exec`, `bridge_fetch`, and `bridge_navigate` show the local daemon,
extension, tab, and basic browser-command health. Bridge is only for local diagnostics,
user-triggered login compatibility, and read-only assistance. Do not use it to
retry platform risk-control blocks.

### Token expired mid-session

```bash
# stoken (core session token) expires after ~24h
# Re-login or use Chrome CDP hydration; auth_token_quality will report the issue
boss logout && boss login
```

## Browser / patchright issues

### `patchright install chromium` fails

```bash
# macOS / Linux: ensure write access to ~/Library/Caches (macOS) or ~/.cache (Linux)
# Windows: run as admin once
pip install --upgrade patchright
patchright install chromium --with-deps
# If the global tool environment reports a missing headless shell:
patchright install chromium-headless-shell
```

### `AUTH_REQUIRED` before live tests

`AUTH_REQUIRED` means the selected data directory has no usable login session. It is
not a CLI failure. Validate local commands, schema, MCP, and doctor first; run
`boss login` before live `search`, `detail`, or `status --live` checks.

### Windows PATH and UTF-8

If `uv tool update-shell` times out, temporarily expose global tools in PowerShell:

```powershell
$env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
```

For Chinese Windows test runs, force UTF-8:

```powershell
$env:PYTHONUTF8='1'
uv run python scripts/quality_baseline.py
```

### Chromium launches but stays blank

- Check `auth_session` via `boss doctor` — if "corrupted", delete `~/.boss-agent/auth/` and re-login
- Check `network` — some regions need a proxy: `HTTPS_PROXY=http://...:port boss login`

### CDP connection refused

```bash
# Verify CDP is actually listening
curl http://localhost:9222/json/version

# If empty, Chrome wasn't started with --remote-debugging-port
# macOS users: make sure Chrome is fully quit first (⌘Q, not just close window)
```

### CDP launch examples

macOS:

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/boss-chrome
```

Linux:

```bash
google-chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/boss-chrome
```

Then log in via CDP from another terminal:

```bash
boss --cdp-url http://localhost:9222 login --cdp
```

## Search / API errors

### `code 36` / `ACCOUNT_RISK`

Risk control detected automation. Stop the automated flow and use the official website manually.

### `RATE_LIMITED`

Too many requests in a window. Increase delay:

```bash
boss --delay 3-7 search "python"
# Or set globally
boss config set request_delay "[3.0, 7.0]"
```

### `JOB_NOT_FOUND`

- Check if job was taken down on BOSS website manually
- Pass `--job-id` directly if you have `encrypt_job_id`, skips broken detail cache

### Empty search results despite valid query

- Always check `boss doctor` first — often an auth problem surfacing as zero results
- Add `--log-level debug` to see the actual request going out on stderr

## Error codes & agent-friendly recovery

Every error response contains `code`, `recoverable`, and `recovery_action`, so agents can react programmatically.

| Error Code | Meaning | Agent Recovery |
|------------|---------|----------------|
| `AUTH_REQUIRED` | Not logged in | `boss login` |
| `AUTH_EXPIRED` | Session expired | `boss login` |
| `RATE_LIMITED` | Too many requests | Wait and retry |
| `TOKEN_REFRESH_FAILED` | stoken refresh failed | `boss login` |
| `ACCOUNT_RISK` | Risk-control block (code 36) | Stop automated access; use the official website manually |
| `COMPLIANCE_BLOCKED` | Low-risk mode blocked a sensitive command | Use read-only/local tools or complete the action manually on the official website |
| `JOB_NOT_FOUND` | Job removed or invalid | Skip |
| `ALREADY_GREETED` | Already messaged recruiter | Skip |
| `ALREADY_APPLIED` | Already applied | Skip |
| `GREET_LIMIT` | Daily greet quota hit | Pause until tomorrow |
| `NETWORK_ERROR` | Connection failed | Retry with backoff |
| `INVALID_PARAM` | Bad argument | Fix parameter |
| `AI_NOT_CONFIGURED` | AI service not set up | `boss ai config` |
| `AI_API_ERROR` | AI provider call failed | Retry / check key |
| `AI_PARSE_ERROR` | AI response not JSON | Retry |
| `BROWSER_KERNEL_MISSING` | patchright browser kernel missing or mismatched | `patchright install chromium`; if the headless shell is missing, run `patchright install chromium-headless-shell` |

## Windows smoke checklist

```powershell
boss --version
boss doctor
boss status
boss login
boss status --live
boss search "Python" --page 1
boss detail <security_id>
```

Before login, `boss status` returning `AUTH_REQUIRED` is expected and should not be counted as a live-platform failure.

## Glossary (Chinese terms kept in code)

| Term | Meaning |
|------|---------|
| `stoken` | Session token — core auth credential for BOSS API |
| `wt2` | Long-lived bearer token, paired with stoken |
| `wbg` / `zp_at` | Auxiliary cookies used by wapi endpoints |
| `security_id` | Per-job opaque ID returned by search; used by detail and local organization commands |
| `encrypt_job_id` | Alternative job ID for the httpx fast path (skips browser) |
| `CDP` | Chrome DevTools Protocol — compatibility login mechanism, not a risk-control bypass |
| `wapi` | BOSS Zhipin internal JSON API (behind `www.zhipin.com/wapi/...`) |

These terms appear in JSON responses and error messages as-is — we deliberately don't translate them to keep parity with BOSS's own naming.
