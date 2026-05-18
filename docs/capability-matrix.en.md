# Capability Matrix

Use this matrix to keep CLI, skills, and MCP integrations aligned across different agent entry points.

Default Low-Risk Assistance Mode: local assistance, read-only first, user-triggered, no risk-control bypass, no bulk outreach, and no platform-data scraping. Capabilities marked as restricted return `COMPLIANCE_BLOCKED` and should be completed manually on the official website.

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

## Job discovery

| Capability | CLI command | Login required | Transport |
|---|---|---|---|
| Job search | `boss search` | Yes | Browser |
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
| Export results | `boss export` | Yes | Browser |

## Conversation management

| Capability | CLI command | Login required | Transport |
|---|---|---|---|
| Conversation list | `boss chat` | Yes | Restricted (blocked by default) |
| Message history | `boss chatmsg` | Yes | Restricted (blocked by default) |
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
| Online resume view | `boss hr resume <geek_id> --job-id <id> --security-id <id>` | Yes | Restricted (blocked by default) |
| Contact exchange | `boss hr resume --exchange --friend-id <friend_id> [--type wechat]` | Yes | Restricted (blocked by default) |
| Reply to candidate | `boss hr reply <friend_id> <message>` | Yes | Restricted (blocked by default) |
| Request attached resume | `boss hr request-resume <friend_id>` | Yes | Restricted (blocked by default) |
| Job listing and online/offline operations | `boss hr jobs` | Yes | httpx |

Notes:
- **Transport**: `httpx` means a direct API call; browser transport remains for compatibility and must not be used to retry risk-control blocks. `AI service` means a third-party model API; do not send platform chat records, candidate resumes, or contact details without authorization.
- For CLI-first integrations, prefer `boss schema` for capability discovery and parameter validation; the schema exposes both `supported_platforms` and `supported_recruiter_platforms`.
- Current platform coverage: `zhipin` has both candidate and recruiter implementations, but sensitive workflows are blocked by default; `zhilian` supports candidate-side login and read-only workflows, while the recruiter side is still unavailable.
- Current auth posture: `zhipin` and `zhilian` keep user-triggered login compatibility, but it must not be used to bypass platform risk controls.
- Use `boss schema` as the source of truth: it currently exposes 34 top-level commands, with 9 first-level recruiter subcommands under `hr`, while `ai` and `resume` remain command-group entries.
