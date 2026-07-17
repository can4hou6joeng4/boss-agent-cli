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

`boss schema` currently exposes 37 top-level commands, plus 9 first-level recruiter
subcommands under `hr`, grouped below by workflow stage.

Operating mode: `boss config set operating_mode assisted|research`. The default is `assisted`; after switching, run `boss schema` again to inspect per-command mode, risk, and data classifications.

## Basics

| Command | Description |
|---------|-------------|
| `boss schema` | Full tool self-description JSON (agents call this first) |
| `boss platforms` | Local platform registry and capability status (no network; `--platform` filter, `--capability` reverse lookup, includes `capability_status_legend`) |
| `boss login` | User-triggered login (Cookie / CDP / QR / browser fallback per platform) |
| `boss logout` | Log out |
| `boss status` | Check login state (local-only by default; `--live` runs a low-frequency read-only probe) |
| `boss doctor` | Diagnose environment, dependencies, credential integrity, and network; local-only by default, `--live-probe` opts into a read-only probe |
| `boss me` | My info (profile / resume / expectations / application records) |

## Discovery

| Command | Description |
|---------|-------------|
| `boss search <query>` | Search jobs (`--url` web filters, comma multi-select, `--welfare` filtering, `--sort score` local sorting, `--preset`) |
| `boss recommend` | Restricted: blocked by default in low-risk mode (avoids auto-reading recommendation streams) |
| `boss detail <security_id>` | Job detail (`--job-id` uses the fast path) |
| `boss show <#>` | Re-view a numbered result from the last search |
| `boss cities` | 40 supported cities |

## Explicit bulk crawl

`crawl` is an explicitly triggered, restricted Research Mode Chrome task. `run`, `start`, and `resume` require shared `operating_mode=research`; MCP remains assisted-only and exposes only local `status/results/shortlist` operations for an existing run. Install `uv sync --extra crawl` first; the crawler starts only the isolated `<data-dir>/crawl/chrome-profile` and never attaches to a daily Chrome profile.

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

Candidate workflow: `boss agent crawl --run-id <run_id> --resume <resume-name>` runs “completed crawl → shortlist → ai fit → score ordering” without opening a browser. Only `boss agent crawl --query <query> --city <city> --allow-crawl --resume <resume-name>` starts a new real Chrome crawl. On `risk_stopped` or `budget_stopped`, Agent returns the `run_id` and resume command instead of retrying indefinitely or recreating the session.

After every page, `<data-dir>/crawl/runs/<run_id>/jobs.json`, `jobs.csv`, and a filtered/frozen `jobs.xlsx` are updated. XLSX keeps the complete values but every data row is a fixed-height single line, so long content is visually clipped rather than expanding the row. JSON/CSV/XLSX and `crawl results` omit `security_id`, job IDs, selectors, recruiter names, and recruiter titles by default; those remain in restricted local SQLite state, and `boss clean --privacy` deletes crawl runs, budgets, and exports. Codes `37` / `38`, a security page, a missing job-list container, an exhausted budget, or a stop request checkpoint and stop immediately; stdout remains a JSON envelope containing the resume command.

## Restricted actions

| Command | Description |
|---------|-------------|
| `boss greet <sid> <jid>` | Restricted: blocked by default; greet manually on the official website |
| `boss batch-greet <query>` | Restricted: blocked by default to avoid bulk outreach |
| `boss apply <sid> <jid>` | Restricted: blocked by default; apply manually on the official website |
| `boss exchange <sid>` | Restricted: blocked by default; contact exchange involves personal information |

## Conversation track

| Command | Description |
|---------|-------------|
| `boss chat` | Restricted: blocked by default (session data) |
| `boss chatmsg <sid> [--raw]` | Restricted: blocked by default; `--raw` keeps structured body/link/card fields only after compliance allows it |
| `boss chat-summary <sid>` | Restricted: blocked by default (depends on message content) |
| `boss mark <sid> --label X` | Restricted: blocked by default (writes platform relationship data) |
| `boss interviews` | Interview invitations |
| `boss history` | Browsing history |

## Pipeline & organization

| Command | Description |
|---------|-------------|
| `boss pipeline` / `boss follow-up` / `boss digest` | Restricted: blocked by default (depend on session/interview data) |
| `boss watch add/list/remove/run` | add/list/remove manage local presets; run is blocked by default (avoids automated incremental pulls) |
| `boss shortlist add/list/annotate/compare/remove` | Local shortlist with tags, notes, and offline compare |
| `boss preset add/list/remove` | Search presets |

## Recruiter mode

| Command | Description |
|---------|-------------|
| `boss hr jobs list/offline/online` | Job listing and lifecycle management |
| `boss hr applications` / `hr resume` / `hr chat` / `hr chatmsg` / `hr last-messages` / `hr candidates` / `hr reply` / `hr request-resume` | Restricted: blocked by default — candidate personal-data and messaging workflows belong on the official recruiter UI |

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
