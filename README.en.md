<div align="center">

# boss-agent-cli

**A local-assist CLI for AI Agents working around BOSS Zhipin**

> Default low-risk mode: local assistance · read-only first · user-triggered · no risk-control bypass · no bulk outreach · no platform-data scraping
>
> Job-seeker: search · welfare filtering · detail review · shortlist · local resume and AI assistance

[![CI](https://github.com/can4hou6joeng4/boss-agent-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/can4hou6joeng4/boss-agent-cli/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/can4hou6joeng4/boss-agent-cli/branch/master/graph/badge.svg)](https://codecov.io/gh/can4hou6joeng4/boss-agent-cli)
[![Python](https://img.shields.io/badge/Python-≥3.10-3776AB?logo=python&logoColor=white&style=flat-square)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg?style=flat-square)](LICENSE)
[![GitHub Release](https://img.shields.io/github/v/release/can4hou6joeng4/boss-agent-cli?style=flat-square)](https://github.com/can4hou6joeng4/boss-agent-cli/releases)
[![PyPI Downloads](https://img.shields.io/pypi/dm/boss-agent-cli?style=flat-square)](https://pypi.org/project/boss-agent-cli/)
[![Contributors](https://img.shields.io/github/contributors/can4hou6joeng4/boss-agent-cli?style=flat-square)](https://github.com/can4hou6joeng4/boss-agent-cli/graphs/contributors)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat-square)](https://github.com/can4hou6joeng4/boss-agent-cli/pulls)
[![Open in Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/can4hou6joeng4/boss-agent-cli)

[Getting Started](docs/getting-started.en.md) · [Install](#-install) · [Quickstart](#-quickstart) · [Roles & Platforms](#-roles--platforms) · [Agent Integration](#-agent-integration) · [Commands](#-commands) · [Troubleshooting](docs/troubleshooting.en.md) · [Architecture](#-architecture) · [Changelog](CHANGELOG.md) · [Roadmap](ROADMAP.en.md)

[中文](README.md) | **English**

<a href="demo/showcase/boss-agent-cli-showcase.mp4" title="Watch the full project showcase video">
  <img src="demo/showcase/boss-agent-cli-showcase.gif" alt="boss-agent-cli project showcase animation" width="100%">
</a>

**[Watch the full showcase video](demo/showcase/boss-agent-cli-showcase.mp4)** · [view the terminal demo](demo/demo-en.gif) · schema-driven · welfare filtering · JSON envelope · open-source engineering quality

<p align="center">
  <img src="demo/demo-en.gif" alt="boss-agent-cli terminal demo (1280×720 / 30fps)" width="100%">
</p>

</div>

## ⚠️ Compliance Boundary

The project enables Low-Risk Assistance Mode by default. It is intentionally scoped to local assistance, read-only-first workflows, and user-triggered actions. CLI commands that would greet, batch-greet, apply, exchange contacts, search recruiter candidates, read candidate resumes/chats, request attachments, or reply to candidates are blocked by default and return the `COMPLIANCE_BLOCKED` error code; users should perform those actions manually on the official website.

## 💡 Why boss-agent-cli?

Traditional job hunting: open a web page → flip through dozens of pages → check each detail → organize shortlists manually → forget who to follow up with.

With AI Agents: `boss search` -> `boss detail` -> `boss shortlist` -> `boss stats` — one local-assist chain for organizing work while sensitive actions stay on the official website.

Every command outputs **structured JSON** that AI Agents parse directly. Default low-risk mode blocks automated outreach, bulk actions, contact exchange, recruiter candidate data workflows, and risk-control retries.

## 🧭 Table of Contents

- [Why boss-agent-cli?](#-why-boss-agent-cli)
- [Demo Assets](#-demo-assets)
- [Core Capabilities](#-core-capabilities)
- [Install](#-install)
- [Quickstart](#-quickstart)
- [Roles & Platforms](#-roles--platforms)
- [Agent Integration](#-agent-integration)
- [Commands](#-commands)
- [Troubleshooting](#-troubleshooting)
- [Configuration](#-configuration)
- [Architecture](#-architecture)
- [Local Storage](#-local-storage)
- [Contributing](#-contributing)

## 🎬 Demo Assets

| Type | Entry | Best for |
|------|-------|----------|
| Project showcase animation | [Homepage autoplay GIF](demo/showcase/boss-agent-cli-showcase.gif) | Quickly understanding the project positioning, schema-driven workflow, JSON envelope, and open-source engineering quality |
| Full showcase video | [16-second MP4](demo/showcase/boss-agent-cli-showcase.mp4) | Viewing the clearer, complete project narrative |
| Terminal interaction demo | [Terminal GIF](demo/demo-en.gif) · [VHS tape](demo/demo-en.tape) | Seeing the CLI commands and output shape directly (1280×720 / 30fps) |
| Reproducible source | [HyperFrames source](demo/hyperframes-showcase/) | Maintaining or iterating the README homepage animation |

## 🌟 Core Capabilities

### Job-seeker workflow

- **Discovery**: keyword search, layered filters, and cached `show` navigation. Commands: `search` `show`
- **Welfare-aware search**: `--welfare "双休,五险一金"` runs real AND matching by paging, fetching details, and checking text fallback. Command: `search --welfare`
- **Local shortlist**: inspect details and organize candidate jobs locally; apply and messaging stay on the official website. Commands: `detail` `show` `shortlist`
- **Pipeline control**: local shortlist and funnel stats from local state. Commands: `shortlist` `stats`
- **Conversation support**: low-risk local organization only by default; message history, labels, and contact exchange are sensitive workflows and blocked by default.
- **AI job-hunting assist**: JD analysis, resume polish, role-targeted optimization, interview prep, and chat coaching. Commands: `ai analyze-jd` `ai polish` `ai optimize` `ai interview-prep` `ai chat-coach`

### Recruiter workflow

- **Recruiter boundary**: candidate search, applications, resumes, chat records, attachment requests, contact exchange, and replies are blocked by default; use the official recruiter UI for those actions.
- **Job lifecycle management**: list, bring online, and take offline recruiter postings. Commands: `hr jobs list` `hr jobs online` `hr jobs offline`

### Platform & integration foundation

- **Schema-first integration**: `boss schema` is the capability source of truth for shell agents, SDKs, and tool-export formats
- **Structured transport**: stdout is JSON-only, stderr is logs-only, which keeps automation stable
- **Platform-aware login**: user-triggered login state is stored locally and encrypted; it is not a risk-control bypass workflow
- **Cross-platform adapter layer**: `Platform` / `RecruiterPlatform` registries are live, with low-risk mode governing sensitive capabilities
- **MCP server with 32 tools**: exposes only the low-risk tool surface by default

## 📦 Install

```bash
# Recommended: install via uv (fast, isolated)
uv tool install boss-agent-cli
patchright install chromium

# Or pipx
pipx install boss-agent-cli
patchright install chromium

# From source
git clone https://github.com/can4hou6joeng4/boss-agent-cli.git
cd boss-agent-cli && uv sync --all-extras
uv run patchright install chromium
```

## 🚀 Quickstart

```bash
# 1. Environment check
boss doctor

# 2. Login (platform-aware fallback chain)
boss login

# 3. Verify login
boss status

# Optional: inspect local platform capability status (no network)
boss platforms
boss platforms --platform qiancheng  # Inspect one platform; 51job alias is supported

# 4. Search Golang jobs in Guangzhou with 双休 + 五险一金
boss search "Golang" --city 广州 --welfare "双休,五险一金"

# 5. View detail → add to local shortlist
boss detail <security_id>
boss shortlist add <security_id> <job_id>

# 6. Export + local stats
boss export "Golang" --city 广州 --count 50 -o jobs.csv
boss stats

# 7. Recruiter mode
boss hr jobs list                       # my job postings
# Candidate search, resumes, chat, replies, attachments, and contact exchange are blocked by default.
```

## 🎭 Roles & Platforms

| Role | Flag | Entry commands |
|------|------|----------------|
| Candidate *(default)* | `--role candidate` | `search` / `detail` / `shortlist` |
| Recruiter | `--role recruiter`, or `boss hr <sub>` shortcut | `hr jobs list`; candidate-data workflows are blocked by default |

| Platform | Candidate | Recruiter | Status |
|----------|:---------:|:---------:|--------|
| BOSS Zhipin (`zhipin`) | ✅ | ✅ | default |
| Zhaopin (`zhilian`)    | 🟡 candidate-side login + read/write flow wired | — | recruiter side is still intentionally unavailable at runtime |
| 51job (`qiancheng`)     | 🚧 registered placeholder | — | returns `NOT_SUPPORTED` until the read-only research gate is satisfied |

`boss platforms` includes `capability_status_legend` in both JSON and terminal output so agents can interpret capability states clearly:

| State | Meaning |
|-------|---------|
| `available` | The local CLI has wired this capability; login requirements still follow the concrete command contract |
| `not_supported` | The current platform adapter does not implement this real workflow; the CLI returns a stable `NOT_SUPPORTED` envelope |
| `placeholder_only` | Registered only for platform identity, aliases, schema/config visibility; it does not mean a real platform capability is wired |
| `low_risk_blocked` | Write actions, sensitive data, or platform-risk boundaries are involved; default low-risk mode blocks the action and points users back to the official UI |

```bash
boss --platform zhilian search "Python"   # pick a platform
boss config set platform zhilian          # set as default
boss --platform qiancheng status          # 51job is currently identity-only
```

Notes: `boss hr ...` currently supports only the default recruiter platform `zhipin-recruiter`; `boss --platform zhilian hr ...` is intentionally rejected at runtime. Architecture notes: [docs/platform-abstraction.en.md](docs/platform-abstraction.en.md).

## 🤖 Agent Integration

The point of this tool is to let AI Agents help organize job-hunting context without automating sensitive platform actions.

### Option 1: Skill install (recommended)

```bash
npx skills add can4hou6joeng4/boss-skill
```

> The Agent Skill for this CLI is managed in the standalone [boss-skill](https://github.com/can4hou6joeng4/boss-skill) repository (the repository itself is the skill), decoupled from the CLI release cadence.

### Option 2: subprocess + JSON parse

```bash
# Step 1: let the Agent read the tool self-description
boss schema
```

```python
# Step 2: chain commands via subprocess (Python example)
import subprocess, json
result = subprocess.run(["boss", "search", "Python", "--city", "北京"],
                        capture_output=True, text=True)
jobs = json.loads(result.stdout)["data"]["items"]
```

### Option 3: MCP integration (Claude Desktop / Cursor)

```json
{
  "mcpServers": {
    "boss-agent": {
      "command": "uvx",
      "args": ["--from", "boss-agent-cli[mcp]", "boss-mcp"]
    }
  }
}
```

See [Agent Quickstart](docs/agent-quickstart.en.md) and [Capability Matrix](docs/capability-matrix.en.md) for the full picture.

## 📚 Commands

`boss schema` currently exposes 35 top-level commands, plus 9 first-level recruiter subcommands under `hr`, grouped below by workflow:

| Stage | Commands |
|-------|----------|
| **Auth** | `login` · `logout` · `status` · `doctor` |
| **Discover** | `search` · `detail` · `show` · `cities` · `history` |
| **Restricted Actions** | `greet` · `batch-greet` · `apply` · `exchange` · `mark` are blocked by default |
| **Restricted Track** | `chat` · `chatmsg` · `chat-summary` · `pipeline` · `follow-up` · `digest` are blocked by default; use `stats` for local state |
| **Organize** | `watch` · `preset` · `shortlist` |
| **Resume** | `resume` · `me` |
| **AI** | `ai config` · `ai analyze-jd` · `ai polish` · `ai optimize` · `ai suggest` · `ai reply` · `ai interview-prep` · `ai chat-coach` |
| **Utility** | `schema` · `platforms` · `export` · `config` · `clean` |
| **Recruiter** | `hr jobs list/offline/online`; candidate-data and messaging workflows are blocked by default |

Full command tables, filter parameters, and welfare-matching internals: **[Command Reference](docs/commands.en.md)**. The capability source of truth is `boss schema` (with `--format openai-tools` / `anthropic-tools` exports for any agent framework).

## 🩺 Troubleshooting

```bash
boss doctor
boss status
# Optional: run an explicit low-frequency read-only live probe
boss status --live
boss doctor --live-probe
```

Every error envelope carries `code` + `recoverable` + `recovery_action`, so agents can react programmatically.

Browser Bridge local diagnostics: the `bridge_daemon`, `bridge_extension`, `bridge_protocol`, `bridge_workspace`, `bridge_exec`, `bridge_fetch`, and `bridge_navigate` checks cover daemon, extension, tab, and basic browser-command health; start the daemon with `python -m boss_agent_cli.bridge.daemon --serve`. Bridge is only for local diagnostics, user-triggered login compatibility, and read-only assistance — do not use it to retry platform risk-control blocks.

Full check reference, CDP launch examples, common fixes, error codes, and the glossary: **[Troubleshooting](docs/troubleshooting.en.md)**. For Cookie, CDP, patchright, real-account, request-rate, or platform-drift issues, read [Platform Risk Boundaries](docs/platform-risk.en.md) first.

## ⚙️ Configuration

```bash
boss config list                      # view all settings
boss config set default_city 广州     # set the default city
boss config reset                     # restore defaults
```

Settings live in `~/.boss-agent/config.json` (default city/salary, request delays, log level, login timeout, CDP URL, export dir). See [Command Reference](docs/commands.en.md) for the full surface.

## 🏗 Architecture

See the [中文版 README](README.md#-技术架构) for the full architecture diagram.

Short version: Click CLI → Compliance Guardrails → AuthManager (Fernet-encrypted tokens) → Platform registries (`zhipin` / `zhilian` / `qiancheng` placeholder) → BossClient → JSON envelope on stdout.

Invariant contracts:
- stdout is JSON-only; stderr holds logs (controlled by `--log-level`)
- Error objects always carry `code` + `recoverable` + `recovery_action`
- `boss schema` is the authoritative capability source for Agents

## 🔌 Local Storage

All state lives under `~/.boss-agent/` — encrypted tokens, cached searches, chat history snapshots, resumes, AI config. Nothing leaves your machine except explicit API calls.

## 🤝 Contributing

See [CONTRIBUTING.en.md](CONTRIBUTING.en.md) (English) or [CONTRIBUTING.md](CONTRIBUTING.md) (中文), and [docs/getting-started.en.md](docs/getting-started.en.md) for the happy path. TL;DR: fork → `feat/xxx` branch → write tests → `python scripts/quality_baseline.py` → PR.

## 📄 License

MIT © [can4hou6joeng4](https://github.com/can4hou6joeng4)

## 👭 Related Communities

- [LINUX DO - a new community for tech enthusiasts](https://linux.do/)
