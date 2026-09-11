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

Stop the current workflow and retain its run ID or checkpoint. Resolve the login or security-page state before an explicit resume; do not retry through multiple transports.

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

`login --cdp` first scans every browser context in the CDP Chrome: if a context already
holds a BOSS login session (`wt2`), it reuses that context and its existing zhipin tab
directly — no login-page navigation, no polling. It only opens the login page for QR
scanning when no existing login is found. Tab cleanup applies only to pages created by
this call; tabs you already have open are never closed.

When reusing a session, the terminal prints the chosen context index and an "account
fingerprint" (an irreversible hash prefix of the login cookie value — the cookie itself
is never shown), e.g. `context 2/2, account fingerprint c26a7f01`. With multiple browser
windows / incognito pages, or several profiles each logged into a different BOSS account,
the reused context is the first one holding a login session. If the fingerprint is not the
account you want, close the extra windows or keep only the target account logged in, then retry.

Reuse only checks that the login cookie **exists**; it does not verify the session server-side.
If every command returns `AUTH_REQUIRED` / `TOKEN_REFRESH_FAILED` after reuse while
`boss login --cdp` still reports "reusing existing session", the browser session has expired
server-side: run `boss login --cdp --force`. It scans and reuses nothing, clears the target
platform's cookies **in the current context only** (other sites and other contexts are untouched,
but the platform account in that Chrome is signed out), then opens the login page for a fresh QR
scan. Use it to switch accounts as well.

## Locking the browser channel: `--browser-source`

`--browser-source stored-cookie --cdp-url <addr>` is a fail-closed strict mode: it locks the browser channel to the exact CDP endpoint you specify and forbids falling back to Bridge or headless, returning `CDP_UNAVAILABLE` immediately when unavailable. The endpoint can be your daily Chrome's debug port or a long-lived dedicated debug profile — **it only guarantees "locked channel", not reuse of your daily browser's login session**. For the latter, use `--browser-source existing-browser`.

> Note: the `recovery_action` for `CDP_UNAVAILABLE` depends on context; the envelope value is authoritative, and the value declared in `boss schema` is the default suggestion.

Comparison of the three sources:

| Source | Channels | Reads stored credentials | Auto-probes :9222 | Launches browser | Failure code |
|---|---|---|---|---|---|
| `auto` (default) | Bridge→CDP→headless | yes | yes | allowed | NETWORK_ERROR |
| `existing-browser` | Bridge/CDP | no | yes | forbidden | BROWSER_SESSION_NOT_FOUND |
| `stored-cookie` | specified CDP only | yes | no | forbidden | CDP_UNAVAILABLE |

## Search / API errors

### `code 36` / `ACCOUNT_RISK`

Risk control stopped the workflow. Keep its run ID/checkpoint, resolve the account or security-page state, and resume explicitly instead of looping retries.

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

> **Breaking change (code 37 classified by context):** previously every code 37 emitted `TOKEN_REFRESH_FAILED`
> (`recoverable=true`, recovery `boss login`). Now only code 37 whose message explicitly indicates token/stoken expiry keeps
> that behavior; environment-risk wording and ambiguous code 37 responses emit `ENVIRONMENT_RISK` (`recoverable=false`) and are
> never refreshed, retried, or answered with a re-login hint. Agents that branch on error codes should add a terminal
> `ENVIRONMENT_RISK` branch: never auto-login, refresh, or retry it — stop automation, keep the current dedicated profile, and
> let the user verify on the official site before resuming manually.

| Error Code | Meaning | Agent Recovery |
|------------|---------|----------------|
| `AUTH_REQUIRED` | Not logged in | `boss login` |
| `AUTH_EXPIRED` | Session expired | `boss login` |
| `BROWSER_SESSION_NOT_FOUND` | An existing-browser source was selected, but its Bridge/CDP session or target page is unavailable | Run `boss doctor`; open and log in to BOSS Zhipin in the daily browser, connect Bridge, then retry |
| `RATE_LIMITED` | Too many requests | Wait and retry |
| `TOKEN_REFRESH_FAILED` | stoken refresh failed | `boss login` |
| `ENVIRONMENT_RISK` | The access environment was rejected | Stop automation; keep the current dedicated profile, verify it on the official site, and lower the request frequency |
| `ACCOUNT_RISK` | Risk-control block (code 36) | Stop the workflow, retain its run ID/checkpoint, and resume only after resolving the account or security page |
| `COMPLIANCE_BLOCKED` | Historical mode-policy block | Upgrade to the current version and retry; current execution paths do not emit it |
| `WIZARD_INPUT_REQUIRED` | A headless workflow lacks role/platform/goal/inputs | Complete `--input-json` from the `boss schema` catalog |
| `WORKFLOW_TIMEOUT` | The workflow exceeded its explicit timeout | Retain `run_id`, adjust timeout, and run `boss wizard --resume <run_id>` |
| `WORKFLOW_PLAN_MISMATCH` | An existing `run_id` is bound to a different plan | Resume with the original plan or omit `run_id` to create a new run |
| `JOB_NOT_FOUND` | Job removed or invalid | Skip |
| `ALREADY_GREETED` | Already messaged recruiter | Skip |
| `CONFIRMATION_REQUIRED` | Operation target and content not approved | Preview with `hr greet --dry-run` or `hr accept-resume <friend_id> --message-id <mid> --dry-run`; set `--yes` only after explicit operator approval. Agents must not infer approval. |
| `RESUME_ACCEPT_RESULT_UNKNOWN` | Resume acceptance result is unconfirmed | Keep `accepted=null` and check the request on the official page; never automatically retry acceptance. |
| `GREET_RESULT_UNKNOWN` | Send reserved, outcome unconfirmed | Check `boss hr chat --job-id <id>`; never automatically resend. Keep the local reservation and use the official page if needed. |
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
