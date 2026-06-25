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

`boss schema` currently exposes 36 top-level commands, plus 9 first-level recruiter
subcommands under `hr`, grouped below by workflow stage.

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
