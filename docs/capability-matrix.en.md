# Capability Matrix

Use this matrix to keep CLI, skills, and MCP integrations aligned across different agent entry points.

The default low-risk `assisted` mode remains local, read-only first, and user-triggered; restricted capabilities return `COMPLIANCE_BLOCKED`. Explicitly run `boss config set operating_mode research` to enable capabilities declared for Research Mode. Research runs must remain bounded, redacted, checkpointed, and stoppable; use `boss schema` and `compliance.capabilities` as the source of truth.

## Auth and environment

| Capability | CLI command | Login required | Transport |
|---|---|---|---|
| Protocol discovery | `boss schema` | No | Local |
| Log in | `boss login` | No | User-triggered login |
| Log out | `boss logout` | No | Local |
| Session status | `boss status` | Yes | httpx |
| Environment diagnostics | `boss doctor` | No | Hybrid |
| Config management | `boss config` | No | Local |
| Cache cleanup | `boss clean` | No | Local |
| Restricted research crawl | `boss crawl run/start/resume`, plus `configure/status/results/stop` | Yes | Isolated DrissionPage profile; MCP remains assisted-only and can only read or import an existing run |

## Job discovery

| Capability | CLI command | Login required | Transport |
|---|---|---|---|
| Job search | `boss search` | Yes | Browser; supports `--url` web-filter reuse and comma-separated multi-select filters |
| Personalized recommendations | `boss recommend` | Yes | Restricted (blocked by default) |
| Job detail | `boss detail` | Yes | httpx first, browser fallback |
| Show by index | `boss show` | No | Local cache |
| City catalog | `boss cities` | No | httpx |
| Browsing history | `boss history` | Yes | httpx |

## Candidate actions

| Capability | CLI command | Login required | Transport |
|---|---|---|---|
| Greet a recruiter | `boss greet` | Yes | Restricted (blocked by default) |
| Batch greet after search | `boss batch-greet` | Yes | Restricted (blocked by default) |
| Apply or start the conversation | `boss apply` | Yes | Restricted (blocked by default) |
| Export results | `boss export` | Yes | Browser; supports `--url` web-filter reuse |

## Conversation management

| Capability | CLI command | Login required | Transport |
|---|---|---|---|
| Conversation list | `boss chat` | Yes | Restricted (blocked by default) |
| Message history | `boss chatmsg [--raw]` | Yes | Restricted (blocked by default); `--raw` preserves structured body/link/job-card fields only after compliance allows the command |
| Conversation summary | `boss chat-summary` | Yes | Restricted (blocked by default) |
| Contact labels | `boss mark` | Yes | Restricted (blocked by default) |
| Contact exchange | `boss exchange` | Yes | Restricted (blocked by default) |
| Interview invites | `boss interviews` | Yes | httpx |

## Workflow management

| Capability | CLI command | Login required | Transport |
|---|---|---|---|
| Pipeline view | `boss pipeline` | Yes | Restricted (blocked by default) |
| Follow-up filtering | `boss follow-up` | Yes | Restricted (blocked by default) |
| Daily digest | `boss digest` | Yes | Restricted (blocked by default) |
| Incremental watch | `boss watch run` | Yes | Restricted (blocked by default); add/list/remove are local |
| Search presets | `boss preset` | No | Local |
| Shortlist management | `boss shortlist` | No | Local |

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
| Application inbox | `boss hr applications` | Yes | Restricted (blocked by default) |
| Candidate search | `boss hr candidates` | Yes | Restricted (blocked by default) |
| Recruiter chat list | `boss hr chat` | Yes | Restricted (blocked by default) |
| Chat message history | `boss hr chatmsg <friend_id>` | Yes | Restricted (blocked by default) |
| Recent-message summaries | `boss hr last-messages [--friend-id <id>]` | Yes | Restricted (blocked by default) |
| Online resume view | `boss hr resume <geek_id> --selector <csel_...> --security-id <id>` | Yes | Restricted (blocked by default) |
| Contact exchange | `boss hr resume --exchange --friend-id <friend_id> [--type wechat]` | Yes | Restricted (blocked by default) |
| Reply to candidate | `boss hr reply <friend_id> <message>` | Yes | Restricted (blocked by default) |
| Request attached resume | `boss hr request-resume <friend_id>` | Yes | Restricted (blocked by default) |
| Job listing and online/offline operations | `boss hr jobs` | Yes | httpx |

Notes:
- **Transport**: `httpx` means a direct API call. Assisted Mode stops on risk-control blocks. Research Mode may run explicitly declared browser/hook adapters, but not unbounded retries, and must preserve checkpoints and redaction. `AI service` means a third-party model API; do not send chat records, resumes, or contact details without authorization.
- For CLI-first integrations, prefer `boss schema` for capability discovery and parameter validation; the schema exposes both `supported_platforms` and `supported_recruiter_platforms`.
- Current platform coverage: `zhipin` has both candidate and recruiter implementations, but sensitive workflows are blocked by default; `zhilian` supports candidate-side workflows and recruiter automation through the `agent` browser/CDP adapter V1; `qiancheng` / 51job is a registered placeholder adapter whose real workflows return `NOT_SUPPORTED`.
- Current auth posture: `zhipin` and `zhilian` keep user-triggered login compatibility; risk-control research belongs only in explicit Research Mode adapters and must not bypass platform risk controls.
- `crawl` is a user-triggered sequential Research Mode task using an isolated Chrome profile, cross-process rate budget, SQLite checkpoints, and the `crawl stop` kill switch; MCP remains assisted-only and exposes only local `crawl_status/results/shortlist` operations for an existing run. The default Hook is `none`; users may select a Hook only when they have authorization to provide the original local files and `SHA256SUMS`. Candidate `agent crawl` consumes only completed runs by default; a new crawl requires `operating_mode=research` and `--allow-crawl`. Risk codes, a security page, or an exhausted budget stop it and return a resume command.
- Use `boss schema` as the source of truth: it currently exposes 37 top-level commands, with 9 first-level recruiter subcommands under `hr`, while `ai` and `resume` remain command-group entries.
