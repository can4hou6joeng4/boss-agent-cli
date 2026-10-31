# Platform Risk Boundaries

boss-agent-cli retains `assisted|research` configuration compatibility, and both modes can call every implemented capability. The project does not control platform rules, account risk control, API drift, or third-party browser environments; open access does not remove budgets, checkpoints, stop controls, redaction, or data-handling duties.

## 0. Open capabilities and runtime controls

Candidate writes, bulk outreach, recruiter candidate data, and browser crawling all enter their owning business layers. Callers must:

- set explicit input, volume, timeout, retry, and stop conditions;
- preserve and reuse workflow/crawl `run_id` values instead of selecting the latest task implicitly;
- redact cookies, tokens, `security_id`, contact details, resumes, and chat data;
- checkpoint and stop on `ACCOUNT_RISK`, security pages, or exhausted budgets without switching transports in a loop.

Missing platform implementations return `NOT_SUPPORTED`; workflows must not fake success. `COMPLIANCE_BLOCKED` remains only as a historical error code and is not emitted by current execution paths.

## 0.1 Compatibility modes

```bash
boss config set operating_mode research
boss schema --format native
```

Both modes share these runtime constraints:

- Pages, retries, and runtime must have finite defaults; zero must not silently mean unlimited.
- Checkpoint before resuming after a risk signal; continuation requires a new user action.
- **Dedicated-profile channels** (crawl, research, and a new login through `boss login --cdp`) must use an isolated profile and must not close or alter the user's daily profile or tabs.
- **Existing-browser read channels** (an already-running CDP browser explicitly selected with `--browser-source existing-browser`) may reuse only an already-open target-site page. They must not navigate, close, write, read or inject stored credentials, create contexts/pages, or launch a browser. If all allowed channels are unavailable, return `BROWSER_SESSION_NOT_FOUND` rather than falling back to headless, a temporary profile, or a login flow. Reusing a session does not authorize sending messages, greeting recruiters, or applying.
- Both channel categories remain subject to every other rule on this page. Relaxing profile isolation does not exempt visible browsers, RPA tools, or fingerprint browsers from section 4.
- `--browser-source httpx-only` is a third category: it opens no browser, routes every read through httpx, and returns `BROWSER_DISABLED` for page-only capabilities. Its live stability is unproven; see 4.2.
- Credentials and identifiers must remain redacted; vendored hooks need auditable repository/commit/license/hash metadata; outputs need local retention and cleanup controls.

## 1. Platform APIs can drift

The project depends on web pages, APIs, cookies, login state, and response shapes from recruiting platforms such as BOSS Zhipin. These platforms can change fields, risk-control behavior, endpoint paths, or page flows at any time.

Treat these symptoms as possible platform drift:

- Previously working read-only commands suddenly return `NETWORK_ERROR`, `INTERNAL_ERROR`, `AUTH_EXPIRED`, `TOKEN_REFRESH_FAILED`, or malformed structures.
- `search` returns results but `detail` fails.
- `boss schema --format native` works, but live commands fail.
- Mock tests pass while real-account commands fail.

## 2. Login and Cookie boundaries

Login can use Cookie extraction, CDP, or a browser QR fallback. The project reads and stores login state locally. Users should never submit cookies, tokens, phone numbers, WeChat IDs, names, company-private information, or real `security_id` values to the repository. No adapter may turn login compatibility into a risk-control retry channel; all adapters follow section 0.1.

With the default `auto` source, `chat` and `chatmsg` retain the httpx path; missing stored credentials return `AUTH_REQUIRED` plus `boss login`. Only explicitly selecting `--browser-source existing-browser` reuses an existing target-site tab through CDP without reading or exporting CLI-stored cookies. A reachable CDP endpoint does not prove that the target page is logged in, so platform unauthenticated responses remain platform errors. An unavailable CDP session or existing target page returns `BROWSER_SESSION_NOT_FOUND` plus `boss doctor`, without starting headless or a login flow. `stored-cookie` connects only to an explicit `--cdp-url`, with no auto-probing or fallback. The browser path does not inherit httpx stoken-refresh or rate-limit retries.

The Browser Bridge daemon and extension were removed in v3.0.0. Upgrading the CLI does not stop an old daemon or uninstall an old extension; users must clean them up manually. Keep CDP debugging ports within a trusted local environment, never exposed publicly, and log in manually on the official site. Migrating to CDP must not become a risk-control bypass path.

`boss status` checks local encrypted credentials and layered health by default without contacting the platform. Use `boss status --live` or `boss doctor --live-probe` only when you explicitly want a read-only online probe. A `wt2` cookie without `__zp_stoken__` is a partial login state; the stoken is generated by real page JavaScript, so users may run `boss login --cdp` with a Chrome CDP debugging port they started intentionally. CDP must not be used as a risk-control bypass channel.

Redact issue payloads:

```json
{
	"security_id": "<redacted>",
	"cookie": "<redacted>",
	"token": "<redacted>"
}
```

## 3. Request rate and account responsibility

The default request interval is controlled by `--delay`. Collection, writes, and bulk outreach require bounded budgets, explicit inputs, and stop conditions; mode configuration does not remove account, data, or runtime responsibility.

### 3.1 Measured request-rate boundary

A controlled run against a real account on 2026-10-29 (one variable, long cooldowns,
at most three calls per condition) produced:

| Interval | Result |
|---|---|
| 15 s | the third consecutive `search` hit code 37 ("your environment looks abnormal") |
| 30 s | 6/6 across two runs |
| 60 s / 90 s | 3/3 each |

The default is therefore `--delay 30-60` (`config.DEFAULT_REQUEST_DELAY`). A single
read-only command issues one request and the throttle does not wait on first use —
`boss status` still runs in about 0.3 s. What slows down is pagination, bulk detail
fan-out, and crawl.

`boss doctor` warns when the lower bound is below 30 s. That bound comes from the run
above and is **not a published platform threshold**: each condition was a single
three-call sequence, and the real limit may vary with account, egress, and time of day.
Full data: [can read-only work without CDP](research/zhipin-cdp-free-read-path-2026-10-29.md).

### 3.2 Recovering from code 37

Do not switch channels to retry, and do not refresh the token or log in again to get
around it. Stop, then recover in order:

1. reload the target page in Chrome so the page script regenerates `__zp_stoken__`;
2. `boss clean --risk-lock` (clears the local lock only, not the server-side limit);
3. `boss login --cdp` to read the new token;
4. confirm with a single read-only command.

Step 1 is not optional: without it the re-login reads the already-invalidated token and
the browser channel keeps returning 37.

## 4. Browser automation boundaries


patchright, CDP, local Chrome profiles, system keychains, browser extensions, and platform risk-control systems all affect login and access stability. Every mode stops when the platform flags automation. Declared adapters must not switch channels or retry without bounds.

Windows app clients, visible browsers, CloakBrowser, RPA tools, and fingerprint browsers all follow the same budget, redaction, checkpoint, and stop policy. Regular CI must never run live risk-control research automatically.

Third-party implementations using stealth, response interception, auto-scroll collection, bulk extraction, or simulated real-user fingerprints may enter a declared adapter only after repository, license, commit, hash, data-path, and stop-behavior review. They must not enter real-account CI. Every new platform must first pass the [platform adapter research template](research/platforms/README.md) gate.

### 4.1 Both channels must look like one client

The httpx channel and the browser channel must present the same identity, otherwise a
single cookie jar alternates between two implementations within seconds. A capture of
the real pages on 2026-10-29 aligned User-Agent, `sec-ch-ua*`, Accept-Language,
Accept-Encoding, `priority`, and the page-owned `token` / `zp_token` / `traceid`.
Raw captures stay local in `.capture/` (gitignored); the per-header conclusions are in
[request identity consistency](research/zhipin-request-identity-2026-10-29.md).

Three supporting constraints:

- **One egress**: `resolve_proxy()` reads the environment proxy and every Chromium
  launch plus httpx share it; Chromium starts with non-proxied UDP disabled so WebRTC
  cannot leak the real address.
- **Same account**: when a CDP browser is auto-discovered, its `wt2` is compared with
  the stored credential. A different account means the session is not reused; the CLI
  falls back to its own browser with local credentials. Using account A's login state
  for account B's request was measured to return 37 immediately.
- **Read-first**: low-risk reads go through httpx; writes and page-only capabilities go
  through the browser.

### 4.2 `--browser-source httpx-only`

A source that never opens a browser: its channel allowlist is empty, so `search`,
`recommend`, `detail`, `friend_list`, and `chat_history` all use httpx, while page-only
capabilities return `BROWSER_DISABLED`.

Its **live stability is unproven**: the same day produced both successes and code 37,
and the cause is not established. It is therefore an explicit choice, never a default
or an automatic fallback — an automatic fallback is exactly what produces a
half-mixed state. On failure, `hints.operator_actions` tells the caller to fall back
to `--browser-source auto`.

## 5. Smoke-test boundaries

Real-flow smoke tests must be explicitly configured and should not access real accounts from regular CI:

```bash
BOSS_SMOKE_DRY_RUN=1 uv run python scripts/smoke_p0.py
BOSS_SMOKE_PLATFORM=zhipin BOSS_SMOKE_QUERY=Golang BOSS_SMOKE_SECURITY_ID=<redacted> uv run python scripts/smoke_p0.py
```

`BOSS_SMOKE_DRY_RUN=1` verifies the plan only. It does not prove live platform availability.

## 6. Reporting security issues

If a report involves cookies, tokens, accounts, contact details, private resumes, company-private information, or exploitable automation bypasses, do not open a public issue. Use the private disclosure channels in [SECURITY.md](../SECURITY.md).
