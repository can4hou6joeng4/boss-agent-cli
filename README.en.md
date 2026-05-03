<div align="center">

# boss-agent-cli

**A CLI tool designed for AI Agents to interact with BOSS Zhipin, for both job-seekers and recruiters**

> Job-seeker: search · welfare filtering · personalized recommendations · auto-greeting · pipeline · incremental watch · AI resume optimization
>
> Recruiter: candidate search · chat reply · resume request · job publish management · cross-platform adapter layer

[![CI](https://github.com/can4hou6joeng4/boss-agent-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/can4hou6joeng4/boss-agent-cli/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/can4hou6joeng4/boss-agent-cli/branch/master/graph/badge.svg)](https://codecov.io/gh/can4hou6joeng4/boss-agent-cli)
[![Python](https://img.shields.io/badge/Python-≥3.10-3776AB?logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![GitHub Release](https://img.shields.io/github/v/release/can4hou6joeng4/boss-agent-cli)](https://github.com/can4hou6joeng4/boss-agent-cli/releases)
[![PyPI Downloads](https://img.shields.io/pypi/dm/boss-agent-cli)](https://pypi.org/project/boss-agent-cli/)
[![Contributors](https://img.shields.io/github/contributors/can4hou6joeng4/boss-agent-cli)](https://github.com/can4hou6joeng4/boss-agent-cli/graphs/contributors)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/can4hou6joeng4/boss-agent-cli/pulls)
[![Open in Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/can4hou6joeng4/boss-agent-cli)

[Getting Started](docs/getting-started.en.md) · [Install](#-install) · [Quickstart](#-quickstart) · [Roles & Platforms](#-roles--platforms) · [Agent Integration](#-agent-integration) · [Commands](#-commands) · [Troubleshooting](#-troubleshooting) · [Architecture](#-architecture) · [Changelog](CHANGELOG.md) · [Roadmap](ROADMAP.en.md)

[中文](README.md) | **English**

<img src="demo.gif" alt="boss-agent-cli terminal demo" width="100%">

</div>

---

## 💡 Why boss-agent-cli?

Traditional job hunting: open a web page → flip through dozens of pages → check each detail → manually greet → forget who to follow up with.

With AI Agents: `boss search` → `boss ai optimize` → `boss batch-greet` → `boss pipeline` — one chain closes the entire loop.

Every command outputs **structured JSON** that AI Agents parse directly. No fragile HTML scraping, no brittle selectors.

## 🧭 Table of Contents

- [Why boss-agent-cli?](#-why-boss-agent-cli)
- [Core Capabilities](#-core-capabilities)
- [Install](#-install)
- [Quickstart](#-quickstart)
- [Roles & Platforms](#-roles--platforms)
- [Agent Integration](#-agent-integration)
- [Commands](#-commands)
- [Troubleshooting](#-troubleshooting)
- [Architecture](#-architecture)
- [Local Storage](#-local-storage)
- [Contributing](#-contributing)

## 🌟 Core Capabilities

### Job-seeker workflow

- **Discovery**: keyword search, layered filters, recommendations, and cached `show` navigation. Commands: `search` `recommend` `show`
- **Welfare-aware search**: `--welfare "双休,五险一金"` runs real AND matching by paging, fetching details, and checking text fallback. Command: `search --welfare`
- **Action loop**: inspect, greet, batch-greet, and apply in one flow. Commands: `detail` `greet` `batch-greet` `apply`
- **Pipeline control**: follow-up reminders, daily digest, and funnel stats for the full application lifecycle. Commands: `pipeline` `follow-up` `digest` `stats`
- **Conversation ops**: chat list, message history, structured summaries, labels, and contact exchange. Commands: `chat` `chatmsg` `chat-summary` `mark` `exchange`
- **AI job-hunting assist**: JD analysis, resume polish, role-targeted optimization, interview prep, and chat coaching. Commands: `ai analyze-jd` `ai polish` `ai optimize` `ai interview-prep` `ai chat-coach`

### Recruiter workflow

- **Candidate operations**: incoming applications, candidate search, recruiter chat list, inline resume view, and resume requests. Commands: `hr applications` `hr candidates` `hr chat` `hr resume` `hr request-resume`
- **Recruiter messaging**: reply to candidates while keeping the same JSON contract as the candidate side. Command: `hr reply`
- **Job lifecycle management**: list, bring online, and take offline recruiter postings. Commands: `hr jobs list` `hr jobs online` `hr jobs offline`

### Platform & integration foundation

- **Schema-first integration**: `boss schema` is the capability source of truth for shell agents, SDKs, and tool-export formats
- **Structured transport**: stdout is JSON-only, stderr is logs-only, which keeps automation stable
- **Platform-aware login**: `zhipin` uses Cookie → CDP → QR httpx → patchright; `zhilian` uses Cookie → CDP → browser login
- **Cross-platform adapter layer**: `Platform` / `RecruiterPlatform` registries are live; BOSS is available on both candidate and recruiter sides, and Zhaopin already has candidate-side login plus read/write flow wired in
- **MCP server with 49 tools**: ready for Claude Desktop / Cursor / Windsurf, including recruiter-side tools without wrapping your own bridge

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

# 2. Login (automatic 4-tier fallback)
boss login

# 3. Verify login
boss status

# 4. Search Golang jobs in Guangzhou with 双休 + 五险一金
boss search "Golang" --city 广州 --welfare "双休,五险一金"

# 5. View detail → greet → apply
boss show 1
boss greet <security_id> <job_id>
boss apply <security_id> <job_id>

# 6. AI-powered chat reply
boss ai reply "请问什么时候方便聊一下？"

# 7. Investment funnel analysis
boss stats --days 30

# 8. Recruiter mode (HR workflow)
boss hr applications                    # candidate applications
boss hr candidates "Golang"             # search candidates
boss hr reply <friend_id> "Hi"          # reply to candidate
boss hr jobs list                       # my job postings
```

## 🎭 Roles & Platforms

boss-agent-cli covers both the job-seeker and the recruiter side, with a pluggable platform layer for future adapters.

| Role | Flag | Entry commands |
|------|------|----------------|
| Candidate *(default)* | `--role candidate` | `search` / `greet` / `apply` |
| Recruiter | `--role recruiter`, or `boss hr <sub>` shortcut | `hr applications` / `hr candidates` / `hr jobs` |

| Platform | Candidate | Recruiter | Status |
|----------|:---------:|:---------:|--------|
| BOSS Zhipin (`zhipin`) | ✅ | ✅ | default |
| Zhaopin (`zhilian`)    | 🟡 candidate-side login + read/write flow wired | — | recruiter side is still intentionally unavailable at runtime |

```bash
# pick a platform
boss --platform zhilian search "Python"
boss config set platform zhilian
```

Notes:
- `boss login` follows the current platform selection
- `boss --platform zhilian login` is available for candidate-side auth
- candidate-side `search / detail / recommend / user_info / greet / apply` are wired for `zhilian`
- `boss --platform zhilian hr ...` is still intentionally rejected at runtime because recruiter support is not implemented yet

Architecture notes: [docs/platform-abstraction.en.md](docs/platform-abstraction.en.md).

## 🤖 Agent Integration

The whole point of this tool is to let AI Agents drive the job hunt.

```bash
# Step 1: let the Agent read the tool self-description
boss schema

# Step 2: the Agent chains commands via subprocess + JSON parse
# Example (Python):
import subprocess, json
result = subprocess.run(["boss", "search", "Python", "--city", "北京"],
                        capture_output=True, text=True)
jobs = json.loads(result.stdout)["data"]["items"]
```

**MCP integration** (Claude Desktop / Cursor):

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

`boss schema` currently exposes 33 top-level commands, plus 7 first-level recruiter subcommands under `hr`, grouped below by workflow:

| Stage | Commands |
|-------|----------|
| **Auth** | `login` · `logout` · `status` · `doctor` |
| **Discover** | `search` · `recommend` · `detail` · `show` · `cities` · `history` |
| **Act** | `greet` · `batch-greet` · `apply` · `exchange` · `mark` |
| **Track** | `chat` · `chatmsg` · `chat-summary` · `interviews` · `pipeline` · `follow-up` · `digest` · `stats` |
| **Organize** | `watch` · `preset` · `shortlist` |
| **Resume** | `resume` · `me` |
| **AI** | `ai config` · `ai analyze-jd` · `ai polish` · `ai optimize` · `ai suggest` · `ai reply` · `ai interview-prep` · `ai chat-coach` |
| **Utility** | `schema` · `export` · `config` · `clean` |
| **Recruiter** | `hr applications` · `hr resume` · `hr chat` · `hr jobs list/offline/online` · `hr candidates` · `hr reply` · `hr request-resume` |

Run `boss <cmd> --help` for options, or `boss schema` for the complete JSON self-description.

**Export for any agent framework** — no MCP required:

```bash
boss schema --format openai-tools      # OpenAI Functions / Tools API
boss schema --format anthropic-tools   # Claude Tool Use API
```

## 🩺 Troubleshooting

If something misbehaves, always start with:

```bash
boss doctor   # outputs JSON with 7 diagnostic checks
```

<details>
<summary>📖 Common diagnostic checks</summary>

| Check | What it means |
|-------|---------------|
| `python_version` | Python ≥ 3.10 installed |
| `patchright_chromium` | Chromium installed |
| `cookie_extract` | Local browser cookies accessible |
| `auth_session` | Encrypted session file readable |
| `auth_token_quality` | Core tokens (wt2 / stoken) present |
| `cookie_completeness` | Auxiliary tokens (wbg / zp_at) |
| `cdp` | Chrome DevTools Protocol reachable |
| `network` | zhipin.com reachable |

</details>

<details>
<summary>📖 Login issues</summary>

For Cookie, CDP, patchright, real-account, request-rate, or platform-drift issues, read [Platform Risk Boundaries](docs/platform-risk.en.md) first.

### Cookie extraction fails

```bash
# Force re-login via QR scan
boss logout && boss login
```

### BOSS detects automation (code 36 / `ACCOUNT_RISK`)

BOSS Zhipin's risk system flags headless browsers. Fix by attaching to your real Chrome:

```bash
# 1. Quit Chrome completely, then:
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.boss-agent/chrome-cdp-profile" \
  --no-first-run

# 2. In another terminal:
boss login --cdp
boss search "python" --city 北京
```

### Token expired mid-session

```bash
# stoken (core session token) expires after ~24h
# Re-login — auth_token_quality will report the issue
boss logout && boss login
```

</details>

<details>
<summary>📖 Browser / patchright issues</summary>

### `patchright install chromium` fails

```bash
# macOS / Linux: ensure write access to ~/Library/Caches (macOS) or ~/.cache (Linux)
# Windows: run as admin once
pip install --upgrade patchright
patchright install chromium --with-deps
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

</details>

<details>
<summary>📖 Search / API errors</summary>

### `code 36` / `ACCOUNT_RISK`

Risk control detected automation. Switch to CDP mode (see Login issues above) or wait 24h.

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

</details>

<details>
<summary>📖 Error codes & agent-friendly recovery</summary>

Every error response contains `code`, `recoverable`, and `recovery_action`, so agents can react programmatically.

| Error Code | Meaning | Agent Recovery |
|------------|---------|----------------|
| `AUTH_REQUIRED` | Not logged in | `boss login` |
| `AUTH_EXPIRED` | Session expired | `boss login` |
| `RATE_LIMITED` | Too many requests | Wait and retry |
| `TOKEN_REFRESH_FAILED` | stoken refresh failed | `boss login` |
| `ACCOUNT_RISK` | Risk-control block (code 36) | Switch to CDP Chrome |
| `JOB_NOT_FOUND` | Job removed or invalid | Skip |
| `ALREADY_GREETED` | Already messaged recruiter | Skip |
| `ALREADY_APPLIED` | Already applied | Skip |
| `GREET_LIMIT` | Daily greet quota hit | Pause until tomorrow |
| `NETWORK_ERROR` | Connection failed | Retry with backoff |
| `INVALID_PARAM` | Bad argument | Fix parameter |
| `AI_NOT_CONFIGURED` | AI service not set up | `boss ai config` |
| `AI_API_ERROR` | AI provider call failed | Retry / check key |
| `AI_PARSE_ERROR` | AI response not JSON | Retry |

</details>

<details>
<summary>📖 Glossary (Chinese terms kept in code)</summary>

| Term | Meaning |
|------|---------|
| `stoken` | Session token — core auth credential for BOSS API |
| `wt2` | Long-lived bearer token, paired with stoken |
| `wbg` / `zp_at` | Auxiliary cookies used by wapi endpoints |
| `security_id` | Per-job opaque ID returned by search; required by detail / greet / apply |
| `encrypt_job_id` | Alternative job ID for the httpx fast path (skips browser) |
| `CDP` | Chrome DevTools Protocol — how we attach to your real Chrome for realistic fingerprints |
| `wapi` | BOSS Zhipin internal JSON API (behind `www.zhipin.com/wapi/...`) |

These terms appear in JSON responses and error messages as-is — we deliberately don't translate them to keep parity with BOSS's own naming.

</details>

## 🏗 Architecture

See [中文版 README](README.md#-技术架构) for the full architecture diagram.

Short version: Click CLI → AuthManager (Fernet-encrypted tokens) → BossClient (httpx + CDP browser session) → JSON envelope on stdout.

Invariant contracts:
- stdout is JSON-only; stderr holds logs (controlled by `--log-level`)
- Error objects always carry `code` + `recoverable` + `recovery_action`
- `boss schema` is the authoritative capability source for Agents

## 🔌 Local Storage

All state lives under `~/.boss-agent/` — encrypted tokens, cached searches, chat history snapshots, resumes, AI config. Nothing leaves your machine except explicit API calls.

## 🤝 Contributing

See [CONTRIBUTING.en.md](CONTRIBUTING.en.md) (English) or [CONTRIBUTING.md](CONTRIBUTING.md) (中文). TL;DR: fork → `feat/xxx` branch → write tests → `uv run pytest` → PR.

## 📄 License

MIT © [can4hou6joeng4](https://github.com/can4hou6joeng4)

## 👭 Related Communities

- [LINUX DO - a new community for tech enthusiasts](https://linux.do/)
