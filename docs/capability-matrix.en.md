# Capability Matrix

Use this matrix to keep CLI, skills, and MCP integrations aligned across different agent entry points.

Compatibility modes `assisted` and `research` can both call every implemented capability and no longer produce mode-level `COMPLIANCE_BLOCKED`. Missing platform implementations return `NOT_SUPPORTED`; long workflows remain bounded, redacted, checkpointed, and stoppable. Use `boss schema` availability and risk metadata as the source of truth.

## Auth and environment

| Capability | CLI command | Login required | Transport |
|---|---|---|---|
| Shared workflow | `boss` / `boss wizard` | Depends on goal | TTY / JSON / MCP + SQLite checkpoint |
| Protocol discovery | `boss schema` | No | Local |
| Log in | `boss login` | No | User-triggered login |
| Log out | `boss logout` | No | Local |
| Session status | `boss status` | Yes | httpx |
| Environment diagnostics | `boss doctor` | No | Hybrid |
| Config management | `boss config` | No | Local |
| Cache cleanup | `boss clean` | No | Local |
| Resumable crawl | `boss crawl run/start/resume`, plus `configure/status/results/stop` | Yes | Isolated DrissionPage profile with fixed budgets, checkpoints, and stop control |

## Job discovery

| Capability | CLI command | Login required | Transport |
|---|---|---|---|
| Job search | `boss search` | Yes | Browser; supports `--url` web-filter reuse and comma-separated multi-select filters |
| Personalized recommendations | `boss recommend` | Yes | Platform adapter |
| Job detail | `boss detail` | Yes | httpx first, browser fallback |
| Show by index | `boss show` | No | Local cache |
| City catalog | `boss cities` | No | httpx |
| Browsing history | `boss history` | Yes | httpx |

## Candidate actions

| Capability | CLI command | Login required | Transport |
|---|---|---|---|
| Greet a recruiter | `boss greet` | Yes | Browser / platform adapter |
| Batch greet after search | `boss batch-greet` | Yes | Bounded search + platform adapter |
| Apply or start the conversation | `boss apply` | Yes | Browser / platform adapter |
| Export results | `boss export` | Yes | Browser; supports `--url` web-filter reuse |

## Conversation management

| Capability | CLI command | Login required | Transport |
|---|---|---|---|
| Conversation list | `boss chat` | Yes | Existing-browser read session when Bridge is connected; otherwise httpx with stored credentials |
| Message history | `boss chatmsg [--raw]` | Yes | Existing-browser read session when Bridge is connected; otherwise httpx with stored credentials; `--raw` preserves structured body/link/job-card fields |
| Conversation summary | `boss chat-summary` | Yes | Platform adapter + local processing |
| Contact labels | `boss mark` | Yes | Platform adapter |
| Contact exchange | `boss exchange` | Yes | Platform adapter |
| Interview invites | `boss interviews` | Yes | httpx |

## Workflow management

| Capability | CLI command | Login required | Transport |
|---|---|---|---|
| Pipeline view | `boss pipeline` | Yes | Platform reads + local aggregation |
| Follow-up filtering | `boss follow-up` | Yes | Platform reads + local aggregation |
| Daily digest | `boss digest` | Yes | Platform reads + local aggregation |
| Incremental watch | `boss watch run` | Yes | Platform read + local state; add/list/remove are local |
| Search presets | `boss preset` | No | Local |
| Shortlist management | `boss shortlist` | No | Local |
| Shortlist management (shows favorite validity, syncs active only) | `boss favorites` | No | Platform read-only + Local |

## User profile

| Capability | CLI command | Login required | Transport |
|---|---|---|---|
| My profile | `boss me` | Yes | httpx |

## Resume management

| Capability | CLI command | Login required | Transport |
|---|---|---|---|
| Local resume management | `boss resume` | Depends | Local (`init` can bootstrap from the online profile) |

## AI capabilities

| Capability | CLI command | Login required | Transport |
|---|---|---|---|
| AI configuration | `boss ai config` | No | Local |
| JD match analysis | `boss ai analyze-jd` | No | AI service |
| Resume polishing | `boss ai polish` | No | AI service |
| Role-targeted optimization | `boss ai optimize` | No | AI service |
| Resume improvement suggestions | `boss ai suggest` | No | AI service |
| Draft chat replies | `boss ai reply` | No | AI service |
| Mock interview prep | `boss ai interview-prep` | No | AI service |
| Chat coaching | `boss ai chat-coach` | No | AI service |

## Data insights

| Capability | CLI command | Login required | Transport |
|---|---|---|---|
| Application funnel stats | `boss stats` | No | Local |

## Recruiter workflow

| Capability | CLI command | Login required | Transport |
|---|---|---|---|
| Application inbox | `boss hr applications` | Yes | Recruiter platform adapter |
| Candidate search | `boss hr candidates` | Yes | Recruiter platform adapter |
| Recruiter chat list | `boss hr chat` | Yes | Recruiter platform adapter |
| Chat message history | `boss hr chatmsg <friend_id>` | Yes | Recruiter platform adapter |
| Recent-message summaries | `boss hr last-messages [--friend-id <id>]` | Yes | Recruiter platform adapter |
| Online resume view | `boss hr resume <geek_id> --job-id <id> --security-id <id>` | Yes | Recruiter platform adapter |
| Contact exchange | `boss hr resume --exchange --friend-id <friend_id> [--type wechat]` | Yes | Recruiter platform adapter |
| Reply to candidate | `boss hr reply <friend_id> <message>` | Yes | Recruiter platform adapter |
| Request attached resume | `boss hr request-resume <friend_id>` | Yes | Recruiter platform adapter |
| Job listing and online/offline operations | `boss hr jobs` | Yes | httpx |

Notes:
- **Transport**: `httpx` means a direct API call. Risk-control blocks stop the workflow. Browser/hook adapters may not retry without bounds and must preserve checkpoints and redaction. `AI service` means a third-party model API; do not send chat records, resumes, or contact details without authorization.
- **Existing-browser sessions**: a connected Bridge is this slice's explicit-use signal, but it does not prove that the target page is logged in. Exhausted candidates return `BROWSER_SESSION_NOT_FOUND` plus `boss doctor`; no Bridge plus no stored credentials still returns `AUTH_REQUIRED` plus `boss login`. The browser path does not inherit httpx stoken-refresh or rate-limit retries.
- For CLI-first integrations, prefer `boss schema` for capability discovery and parameter validation; the schema exposes both `supported_platforms` and `supported_recruiter_platforms`.
- Current platform coverage: `zhipin` has both candidate and recruiter implementations; `zhilian` supports candidate-side workflows and recruiter automation through the `agent` browser/CDP adapter V1; `qiancheng` / 51job is a registered placeholder adapter whose real workflows return `NOT_SUPPORTED`.
- `crawl` uses an isolated Chrome profile, cross-process rate budgets, SQLite checkpoints, and the `crawl stop` kill switch. Fine-grained MCP crawl tools provide local `crawl_status/results/shortlist` operations for existing runs, while `boss_wizard` can start, resume, and stop the shared workflow. The default Hook is `none`; local Hook directories must provide `SHA256SUMS`. Risk codes, a security page, or an exhausted budget stop it and return a resume command.
- Use `boss schema` as the source of truth: it currently exposes 39 top-level commands, with 9 first-level recruiter subcommands under `hr`, while `ai` and `resume` remain command-group entries.
