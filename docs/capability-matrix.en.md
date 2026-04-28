# Capability Matrix

Use this matrix to keep CLI, skills, and MCP integrations aligned across different agent entry points.

## Auth and environment

| Capability | CLI command | Login required | Transport |
|---|---|---|---|
| Protocol discovery | `boss schema` | No | Local |
| Log in | `boss login` | No | Browser |
| Log out | `boss logout` | No | Local |
| Session status | `boss status` | Yes | httpx |
| Environment diagnostics | `boss doctor` | No | Hybrid |
| Config management | `boss config` | No | Local |
| Cache cleanup | `boss clean` | No | Local |

## Job discovery

| Capability | CLI command | Login required | Transport |
|---|---|---|---|
| Job search | `boss search` | Yes | Browser |
| Personalized recommendations | `boss recommend` | Yes | Browser |
| Job detail | `boss detail` | Yes | httpx first, browser fallback |
| Show by index | `boss show` | No | Local cache |
| City catalog | `boss cities` | No | httpx |
| Browsing history | `boss history` | Yes | httpx |

## Candidate actions

| Capability | CLI command | Login required | Transport |
|---|---|---|---|
| Greet a recruiter | `boss greet` | Yes | Browser |
| Batch greet after search | `boss batch-greet` | Yes | Browser |
| Apply or start the conversation | `boss apply` | Yes | Browser |
| Export results | `boss export` | Yes | Browser |

## Conversation management

| Capability | CLI command | Login required | Transport |
|---|---|---|---|
| Conversation list | `boss chat` | Yes | httpx |
| Message history | `boss chatmsg` | Yes | httpx |
| Conversation summary | `boss chat-summary` | Yes | httpx |
| Contact labels | `boss mark` | Yes | httpx |
| Contact exchange | `boss exchange` | Yes | httpx |
| Interview invites | `boss interviews` | Yes | httpx |

## Workflow management

| Capability | CLI command | Login required | Transport |
|---|---|---|---|
| Pipeline view | `boss pipeline` | Yes | httpx |
| Follow-up filtering | `boss follow-up` | Yes | httpx |
| Daily digest | `boss digest` | Yes | httpx |
| Incremental watch | `boss watch` | Yes | Browser |
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
| Application inbox | `boss hr applications` | Yes | httpx |
| Candidate search | `boss hr candidates` | Yes | httpx |
| Recruiter chat list | `boss hr chat` | Yes | httpx |
| Online resume view | `boss hr resume` | Yes | httpx |
| Reply to candidate | `boss hr reply` | Yes | httpx |
| Request attached resume | `boss hr request-resume` | Yes | httpx |
| Job listing and online/offline operations | `boss hr jobs` | Yes | httpx |

Notes:
- **Transport**: `httpx` means a direct API call, `Browser` means a CDP/patchright flow for actions that need a real browser fingerprint, and `AI service` means a third-party model API.
- For CLI-first integrations, prefer `boss schema` for capability discovery and parameter validation; the schema exposes both `supported_platforms` and `supported_recruiter_platforms`.
- Current platform coverage: `zhipin` supports both candidate and recruiter workflows; `zhilian` already supports candidate-side login plus read/write actions, while the recruiter side is still unavailable.
- Current auth posture: `zhipin` keeps the four-tier fallback login chain; `zhilian` now supports the candidate-side browser login foundation (Cookie / CDP / browser fallback), while recruiter auth is not implemented yet.
- Use `boss schema` as the source of truth: it currently exposes 33 top-level commands, with 7 first-level recruiter subcommands under `hr`, while `ai` and `resume` remain command-group entries.
