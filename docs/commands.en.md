# Command Reference

> The capability source of truth is `boss schema` — the machine-readable self-description
> covering commands, parameters, platform support, and error codes. This page is a
> human-friendly cheat sheet; when the two disagree, trust the live `boss schema` output.
> 中文版见 [commands.md](commands.md)。

```bash
boss schema                            # full capability JSON (agents call this first)
boss schema --format openai-tools      # export OpenAI Functions / Tools definitions
boss schema --format anthropic-tools   # export Claude Tool Use definitions
boss <cmd> --help                      # options for a single command
```

`boss schema` currently exposes 39 top-level commands, plus 11 first-level recruiter
subcommands under `hr`, grouped below by workflow stage.

Compatibility setting: `boss config set operating_mode assisted|research`. Both modes can call every implemented capability; schema still reports risk/data classifications, and missing platform implementations return `NOT_SUPPORTED`.

## Command or wizard

Top-level commands and `boss wizard` are two parallel capability surfaces. The split is declared in `conventions.command_vs_wizard` from `boss schema`:

- **Single-shot, stateless capability calls** → top-level commands (`boss search`, `boss detail`, `boss greet`, …)
- **Cross-step state, resumability, or handing guidance to a human mid-flow** → `boss wizard` (goals listed under `wizard_catalog`)

Envelope `hints` splits by audience the same way (`conventions.hints`):

| Field | Audience | Form |
|-------|----------|------|
| `hints.next_actions` | AI Agent | `boss xxx` commands the agent runs directly |
| `hints.operator_actions` | Human operator | Natural language, usually done away from the terminal (scan a QR code, adjust filters in the browser, clear a risk-control check) |

In a TTY only `operator_actions` is rendered, to stderr; `next_actions` stays an agent-only channel and is not rendered. An agent receiving `operator_actions` should relay it verbatim rather than inventing its own wording.

## Basics

| Command | Description |
|---------|-------------|
| `boss` / `boss wizard` | Start the TTY wizard; use `--input-json` for agent workflows and `--status/--resume/--stop <run_id>` for persisted runs |
| `boss schema` | Full tool self-description JSON (agents call this first) |
| `boss platforms` | Local platform registry and capability status (no network; `--platform` filter, `--capability` reverse lookup, includes `capability_status_legend`) |
| `boss login` | User-triggered login (Cookie / CDP / QR / browser fallback per platform) |
| `boss logout` | Log out |
| `boss status` | Check login state (local-only by default; `--live` runs a low-frequency read-only probe) |
| `boss doctor` | Diagnose environment, dependencies, credential integrity, and network; local-only by default, `--live-probe` opts into a read-only probe |
| `boss me` | My info (profile / resume / expectations / application records) |

### Import a browser cURL session

If browser Cookie extraction is unavailable, use **Copy as cURL (bash)** on your own authenticated `https://www.zhipin.com` request and save the raw text in a private file:

```bash
boss login --curl-file /path/to/private-request.txt
# macOS: read the clipboard through stdin instead of putting credentials in arguments
pbpaste | boss login --curl-file -
```

Supports common POSIX cURL syntax, not PowerShell/cmd or Markdown-escaped text. Cannot be combined with `--cdp` / `--cookie-source`. Extracts only inline Cookies, User-Agent and the stoken Cookie; never executes cURL, reads referenced files or replays the request body. One read-only check uses the existing user-info endpoint. Only successful verification **replaces the native encrypted BOSS session**, without merging another account's old Cookies. Verification failure preserves the existing session and does not launch a browser.

This is an alternative credential input, not a login/risk-control bypass or a copy of the browser fingerprint. Later refreshes still follow AuthManager and browser-source policies; browser-free operation is not guaranteed. The `zp_token` header is not persisted separately; recruiter requests derive it from the current `bst` Cookie. Source files and clipboard contents remain sensitive and are not removed by the CLI; do not commit them or paste them into chats.

## Discovery

| Command | Description |
|---------|-------------|
| `boss search <query>` | Search jobs (`--url` web filters, comma multi-select, `--welfare` filtering, `--sort score` local sorting, `--preset`) |
| `boss recommend` | Fetch personalized job recommendations |
| `boss detail <security_id>` | Job detail (`--job-id` uses the fast path) |
| `boss show <#>` | Re-view a numbered result from the last search |
| `boss cities` | 40 supported cities |

## Resumable bulk crawl

`crawl` is an explicitly triggered, bounded Chrome task. Install `uv sync --extra crawl` first; it starts only the isolated `<data-dir>/crawl/chrome-profile`, never attaches to a daily Chrome profile, checkpoints every stage, and applies fixed request/detail/time/retry budgets plus a stop control.

```powershell
boss crawl configure --max-requests 20 --max-details 50 --max-seconds 600 --max-retries 1
boss crawl run "AI" --city 杭州 --pages 3 --with-detail `
  --hook-profile screenshot-full --hook-dir E:\boss-agent-cli-local-hooks\AntiDebug_Breaker
boss crawl resume <run_id>
boss crawl stop <run_id>
```

| Command | Description |
|---------|-------------|
| `boss crawl configure [--chrome-path PATH] [--port N] [--max-* N]` | Configure the crawl-only Chrome and request, detail, wall-clock, and retry budgets; the profile is fixed at `<data-dir>/crawl/chrome-profile` |
| `boss crawl run <query> --city <city-or-code> [--pages N] [--with-detail]` | Sequential capture; `--pages` defaults to `5` and must be positive; `--with-detail` serially completes job details |
| `boss crawl start <query> --city <city-or-code> [...]` | Create a background task and return `run_id` immediately; used by local task orchestration |
| `boss crawl status <run_id>` / `boss crawl results <run_id>` | Read the SQLite cursor, risk state, detail progress, and persisted jobs without opening Chrome |
| `boss crawl resume <run_id> [--pages N] [--with-detail] [--background]` | Resume from the page cursor, seen jobs, and pending details; `--background` returns immediately for polling; can raise a positive page cap and fill details without duplicate writes |
| `boss crawl stop <run_id>` | Request a running task to stop at its next safe point and retain its checkpoint |
| `boss crawl shortlist <run_id> (--all \| --selector <csel_...>)` | Import crawl results into the project's local shortlist without a platform request, retaining selectors and detail cache for `boss ai fit` |

The default Hook is `none`. `screenshot-full` is enabled only when the user explicitly selects `--hook-profile screenshot-full --hook-dir <directory>`; the directory must be authorized by its user and include the original seven scripts plus `SHA256SUMS`. This project no longer redistributes those third-party scripts; each source file is SHA-256 verified before injection and only its identifier and digest are recorded. Cookies, headers, and full request bodies are not recorded.

Candidate workflow: `boss agent crawl --run-id <run_id> --resume <resume-name>` runs “completed crawl → shortlist → ai fit → score ordering” without opening a browser. `boss agent crawl --query <query> --city <city> --resume <resume-name>` starts a new real Chrome crawl. On `risk_stopped` or `budget_stopped`, Agent returns the `run_id` and resume command instead of retrying indefinitely or recreating the session.

After every page, `<data-dir>/crawl/runs/<run_id>/jobs.json`, `jobs.csv`, and a filtered/frozen `jobs.xlsx` are updated. XLSX keeps the complete values but every data row is a fixed-height single line, so long content is visually clipped rather than expanding the row. JSON/CSV/XLSX and `crawl results` omit `security_id`, job IDs, selectors, recruiter names, and recruiter titles by default; those remain in restricted local SQLite state, and `boss clean --privacy` deletes crawl runs, budgets, and exports. Codes `37` / `38`, a security page, a missing job-list container, an exhausted budget, or a stop request checkpoint and stop immediately; stdout remains a JSON envelope containing the resume command.

## Candidate actions

| Command | Description |
|---------|-------------|
| `boss greet <sid> <jid>` | Greet a recruiter; duplicates return `ALREADY_GREETED` |
| `boss batch-greet <query>` | Search and greet up to an explicit `--limit`; supports `--dry-run` |
| `boss apply <sid> <jid>` | Apply or start a conversation; duplicates return `ALREADY_APPLIED` |
| `boss exchange <sid>` | Request a phone-number or WeChat exchange |

## Conversation track

| Command | Description |
|---------|-------------|
| `boss chat` | List conversations with pagination and source filters |
| `boss chatmsg <sid> [--raw]` | Read message history; `--raw` preserves structured body/link/card fields |
| `boss chat-summary <sid>` | Build a structured conversation summary |
| `boss mark <sid> --label X` | Add or remove a contact label |
| `boss interviews` | Interview invitations |
| `boss history` | Browsing history |

## Pipeline & organization

| Command | Description |
|---------|-------------|
| `boss pipeline` / `boss follow-up` / `boss digest` | Build progress, follow-up, and daily views from conversations/interviews |
| `boss watch add/list/remove/run` | Save, list, remove, or run incremental job watches |
| `boss shortlist add/list/annotate/compare/remove` | Local shortlist with tags, notes, and offline compare |
| `boss favorites list/sync` | Read BOSS job favorites with validity status; sync imports only explicitly active jobs (deduplicates jobs, refreshes dynamic access IDs, preserves first-saved time) |
| `boss preset add/list/remove` | Search presets |

## Recruiter mode

| Command | Description |
|---------|-------------|
| `boss hr jobs list/offline/online/detail` | Job listing, detail, and lifecycle management |
| `boss hr applications` / `hr resume` / `hr chat` / `hr chatmsg` / `hr last-messages` / `hr candidates` / `hr reply` / `hr request-resume` | Candidate applications, resumes, conversations, search, replies, and attached-resume requests |
| `boss hr recommendations --job-id <encJobId>` | Read rich recommended-candidate cards and first-contact parameters |
| `boss hr greet ... --message <text> --yes` | Create a conversation, send the first contact once, and clean up its unread state when needed |

Preview the candidate parameters and message with `--dry-run`. Replace it with `--yes` only after the operator explicitly approves that candidate and message. MCP omits `yes` by default; agents must not infer approval, and a preview is not human authorization.

`greet` atomically reserves the encrypted candidate/job pair locally and records a confirmed send. A lost response, process exit, or rate limit leaves the reservation in place and blocks automatic resending. Check `boss hr chat --job-id <id>` and use the official page if needed; do not delete the reservation to resend.

Cleanup remains part of `greet`, not a separate command. A known zero unread count returns `not_needed`; without MQTT permission, cleanup returns `deferred`. With permission, a missing count does not block a receipt when the exact conversation and latest message ID can be resolved. An unresolved target or message ID returns `unknown` without publishing. Unknown counts in `chat` / `last-messages` remain `null`, not zero.

An independent MQTT session may disconnect the active webpage. Add `--allow-mqtt-session` before sending (MCP: `allow_mqtt_session=true`) only after the operator separately approves that risk. `--yes` approves the candidate and message, not this permission. Random client IDs do not guarantee same-account coexistence; the exact cause of webpage logout remains undetermined and must not be automatically probed with live accounts.

MQTT parameters follow web v11308: a `ws-` prefix with 16 uppercase hexadecimal characters, MQTT 3.1, explicit `cleanSession=true`, and a 10-second connect timeout setting. The password and WebSocket subprotocol come from `get/wt`; handshake cookies use the native jar's domain/path selection for the server's `/chatws`, without overwriting `wt2`. Presence `uniqid` joins the second and first `__a` segments; `model` comes from `sid`, with missing values left empty. The UA uses native auth metadata or the CLI default when absent. Imported cookies without domain/path metadata cannot reproduce browser cookie selection exactly. Connection reuse is not implemented, and the heartbeat calculation is unchanged; parameter alignment does not establish webpage coexistence.

`--read-receipt-timeout` defaults to 25 seconds (1–60). A private short-lived subprocess reuses the native auth store; the shared budget covers startup, lookups, and publish. Timeout kills and reaps the worker to prevent continued background requests, but cannot undo operations already received by the platform. MQTT QoS 1 publish acknowledgement completes cleanup with `read_state.status=published` and `partial_success=false`, without an unread-state readback. This confirms receipt publication, not that the webpage unread count was verified as zero; `cleared` is no longer returned. Ordinary cleanup failures return `partial_success=true`; auth, account risk, and rate limits return an error envelope preserving `error.details.sent=true`, with guidance in `hints.operator_actions`. Never resend a greeting because cleanup failed or to add MQTT permission.

## Resume & AI

| Command | Description |
|---------|-------------|
| `boss resume init/list/show/edit/delete/export/import/clone/diff/link/applications` | Local resume management |
| `boss ai config` | Configure the AI provider |
| `boss ai local status` | Show local model config, recommendations, and imported registry |
| `boss ai local configure --runtime ollama --model qwen3:14b` | Configure a local Ollama OpenAI-compatible service |
| `boss ai local pull --model qwen3:14b --confirm-download` | Explicitly download local model weights |
| `boss ai local smoke` | Run one local model health check |
| `boss ai analyze-jd` / `ai polish` / `ai optimize` / `ai suggest` | JD analysis, resume polish, role-targeted optimization, suggestions |
| `boss ai reply` / `ai interview-prep` / `ai chat-coach` | Reply drafts, mock interviews, chat coaching |
| `boss ai cover-letter` | Draft a tailored cover letter / self-intro from local resume + JD (draft only, not sent) |

> Latest models such as Claude 4.7 / GPT-5 / DeepSeek-V3 / Qwen3 are supported — see [recommended models](integrations/ai-models.en.md).

## Utilities

| Command | Description |
|---------|-------------|
| `boss config list/set/reset` | Configuration management |
| `boss clean` | Clean caches |
| `boss stats` | Funnel stats from local state (greeted/applied/shortlist) |
| `boss export <query>` | Export results (CSV/JSON/HTML, supports `--url` web filters) |

## Search filter parameters

```bash
boss search "golang" \
  --city 广州 \
  --salary 20-50K \
  --experience 3-5年,5-10年 \
  --education 本科,硕士 \
  --scale 100-499人 \
  --industry 互联网 \
  --stage 已上市 \
  --welfare "双休,五险一金" \
  --sort score
```

Search and export can reuse filters selected manually on the BOSS web UI:

```bash
boss search --url 'https://www.zhipin.com/web/geek/jobs?query=Golang&city=101280100&experience=104,105'
boss export --url 'https://www.zhipin.com/web/geek/jobs?query=Golang&city=101280100' --count 50 -o jobs.csv
```

**How welfare filtering works**:

1. Check job welfare tags (`welfareList`) first
2. Fall back to full-text search of the job description when tags don't match
3. Auto-paginate (up to 5 pages)
4. Every result carries `welfare_match` explaining the match source and `match_score` for `--sort score` local sorting
