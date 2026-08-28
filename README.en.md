<div align="center">

<img src="docs/assets/logo.png" width="112" alt="boss-agent-cli logo">

# boss-agent-cli

*🤖 A recruiting-platform CLI for people and AI agents — terminal wizard · welfare filtering · dual-role workflows · JSON envelopes.*

[![CI](https://github.com/can4hou6joeng4/boss-agent-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/can4hou6joeng4/boss-agent-cli/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/can4hou6joeng4/boss-agent-cli/branch/master/graph/badge.svg)](https://codecov.io/gh/can4hou6joeng4/boss-agent-cli)
[![Python](https://img.shields.io/badge/Python-≥3.10-3776AB?logo=python&logoColor=white&style=flat-square)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg?style=flat-square)](LICENSE)
[![GitHub Release](https://img.shields.io/github/v/release/can4hou6joeng4/boss-agent-cli?style=flat-square)](https://github.com/can4hou6joeng4/boss-agent-cli/releases)
[![PyPI Downloads](https://img.shields.io/pypi/dm/boss-agent-cli?style=flat-square)](https://pypi.org/project/boss-agent-cli/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat-square)](https://github.com/can4hou6joeng4/boss-agent-cli/pulls)
[![MCP Toplist](https://mcptoplist.com/badge/glama%2Fcan4hou6joeng4%2Fboss-agent-cli.svg)](https://mcptoplist.com/server/glama%2Fcan4hou6joeng4%2Fboss-agent-cli)

[Getting Started](docs/getting-started.en.md) · [Agent Integration](#-agent-integration) · [Commands](#-commands) · [Troubleshooting](docs/troubleshooting.en.md) · [Roadmap](ROADMAP.en.md) · [中文](README.md) | **English**

<a href="demo/showcase/boss-agent-cli-showcase.mp4" title="Watch the full project showcase video">
  <img src="demo/showcase/boss-agent-cli-showcase.gif" alt="boss-agent-cli project showcase animation" width="100%">
</a>

**[Watch the full showcase video](demo/showcase/boss-agent-cli-showcase.mp4)** · [terminal demo](demo/demo-en.gif) · schema-driven · welfare filtering · JSON envelope

</div>

<p align="center">
  <a href="https://www.atlascloud.ai/?utm_source=github&utm_medium=link&utm_campaign=boss-agent-cli">
    <img src="docs/assets/atlas-cloud-logo.png" alt="Atlas Cloud" width="180">
  </a>
</p>

> 🎁 **[Atlas Cloud](https://www.atlascloud.ai/?utm_source=github&utm_medium=link&utm_campaign=boss-agent-cli)** gives `boss ai` a full-modal, OpenAI-compatible backend — one key for DeepSeek, Qwen, GLM, Kimi, MiniMax, Claude, GPT, and more, with no per-vendor wiring. Just pick `--provider atlas` in `boss ai config` (`base_url=https://api.atlascloud.ai/v1`, default model `deepseek-ai/deepseek-v4-pro`); see [AI model integration](docs/integrations/ai-models.en.md#atlas-cloud-one-key-across-many-model-families) for setup. Budget-friendly [coding plan](https://www.atlascloud.ai/console/coding-plan).

## 🧭 Why

boss-agent-cli unifies job discovery, welfare filtering, local resumes and AI, application and messaging, recruiter candidate workflows, and resumable crawling in one CLI. People run `boss` for a terminal wizard; agents use JSON, schema, MCP, or the Python API against the same workflow state. `boss schema` remains the capability source of truth.

## ⚠️ Runtime Boundary

Historical `operating_mode=assisted|research` configuration remains compatible, but both modes can call every implemented capability and no longer produce mode-level `COMPLIANCE_BLOCKED` errors. Missing platform implementations still return `NOT_SUPPORTED`; authentication, account-risk, and network failures keep structured recovery metadata. Long-running workflows remain bounded by timeout, retry, budget, checkpoint, and stop controls.

## ✨ Features

- **Job discovery**: keyword search + layered filters, with cached `show` navigation — `search` `show` `detail`
- **Welfare filtering (the differentiator)**: `--welfare "双休,五险一金"` pages, fetches details, runs **real AND matching**, and can `--sort score` by local match score — `search --welfare`
- **Terminal wizard**: run `boss` or `boss wizard`, select a role, platform, and goal, then resume the same workflow through JSON, run IDs, or MCP
- **Local shortlist & stats**: inspect details, read web favorited jobs with validity status and sync only active entries, organize candidates with local tags and notes, compare jobs offline, and see funnel stats — `shortlist` `stats` `watch` `preset` `favorites`
- **AI job-hunting assist + local models**: JD analysis, resume polish, role-targeted optimization, keyword suggestions, resume optimization, shortlist fit reports, interview prep, chat coaching; local weights stay outside the Python package via Ollama/vLLM OpenAI-compatible endpoints — `ai analyze-jd` `ai suggest-keywords` `ai resume-optimize` `ai interview-prep` `ai chat-coach` `ai local configure` `ai local smoke`
- **Schema-first + JSON envelope**: stdout is a JSON-only `{ok, data, pagination, error, hints}` envelope, `boss schema` is the capability source of truth, and an **MCP server with 75 tools** exposes every implemented capability
- **Recruiter workflow**: candidate search, recommendations, first contact with built-in read-state cleanup, resumes, chats, replies, and job management — `hr candidates/recommendations/greet/applications/resume/chat/last-messages/reply/request-resume/jobs`
- **Cross-platform layer**: live `Platform` / `RecruiterPlatform` registries, `--platform zhipin|zhilian|qiancheng`

## 🚀 Quickstart

```bash
# Install (uv recommended; the browser core is only for user-triggered login / local export)
uv tool install boss-agent-cli
patchright install chromium

# Human entrypoint: choose role, platform, and goal in the wizard
boss

# Agents and advanced users can still call commands directly
boss doctor                                                   # environment check
boss login                                                    # platform-aware login
boss status                                                   # verify login
boss search "Golang" --city 广州 --welfare "双休,五险一金"     # search + welfare filtering
boss detail <security_id>                                     # view detail
boss shortlist add <security_id> <job_id> --tags backend,remote  # add to local shortlist with local tags
boss shortlist compare --tag remote                           # compare shortlisted jobs offline
boss stats                                                    # local stats

# Recruiter mode
boss hr candidates "Python" --city 101010100
boss hr recommendations --job-id <encJobId>
boss hr jobs list
```

Every command outputs structured JSON (`ok` for success, `exit 0/1`). Full walk-through: [Getting Started](docs/getting-started.en.md).

## 🎭 Roles & Platforms

| Platform | Candidate | Recruiter | Status |
|----------|:--:|:--:|--------|
| BOSS Zhipin (`zhipin`) | ✅ | ✅ | default |
| Zhaopin (`zhilian`) | ✅ candidate-side read-only + local-assist parity | 🟡 `agent` browser/CDP automation V1 | `hr` remains BOSS-only; Zhaopin recruiter automation uses `boss --platform zhilian --role recruiter agent ...` |
| 51job (`qiancheng`) | 🚧 registered placeholder | — | returns `NOT_SUPPORTED` until the read-only research gate is satisfied |

```bash
boss --platform zhilian search "Python"   # pick a platform (also --platform zhipin|zhilian|qiancheng)
boss config set platform zhilian          # set as default
```

`boss hr ...` currently supports only the default recruiter platform `zhipin-recruiter`; Zhaopin recruiter automation is exposed through `agent` and the browser/CDP adapter. Architecture notes: [docs/platform-abstraction.en.md](docs/platform-abstraction.en.md).

## 🤖 Agent Integration

Start here: [Agent Quickstart](docs/agent-quickstart.en.md) · [Capability Matrix](docs/capability-matrix.en.md) · [Host Examples](docs/agent-hosts.en.md)

```json
// Option 1: MCP (recommended) — Claude Desktop / Cursor and other MCP hosts; MCP server with 75 tools
{ "mcpServers": { "boss-agent": { "command": "uvx", "args": ["--from", "boss-agent-cli[mcp]", "boss-mcp"] } } }
```

Prefer not to set up a local Python toolchain? Use the bundled container: `BOSS_UID=$(id -u) BOSS_GID=$(id -g) docker compose run --rm boss-mcp`. The image deliberately ships no browser kernel — run `boss login` on the host first, then mount `~/.boss-agent`. See [Docker integration](docs/integrations/docker.md).

OpenCode can use the checked-in example directly:

```bash
cp examples/opencode/opencode.json ./opencode.json
uv sync --all-extras
uv run boss-mcp --data-dir ./.boss-agent --help
```

After portable/global install, copy the bundle's `examples/opencode.json` into any
OpenCode project. It starts `boss-mcp --data-dir ./.boss-agent`, keeping review,
pending, and logs project-local.

```bash
# Option 2: subprocess — let the Agent read the self-description, then parse stdout JSON
boss schema
```

```python
# Option 3: embed in Python (ships with py.typed)
from boss_agent_cli import AuthManager, BossClient, AuthRequired
with BossClient(AuthManager(...)) as client:
    result = client.search_jobs("Golang", city="北京")
```

## 📚 Commands

`boss schema` exposes 39 top-level commands + 9 first-level recruiter subcommands, grouped by workflow:

- **Auth**: `login` · `logout` · `status` · `doctor`
- **Discover**: `search` · `detail` · `show` · `cities` · `history`
- **Organize**: `watch` · `preset` · `shortlist` · `stats` · `favorites`
- **Resumable crawl**: `crawl configure/run/start/status/results/resume/stop/shortlist`
- **Resume / AI**: `resume` · `me` · `ai analyze-jd` · `ai polish` · `ai optimize` · `ai fit` · `ai suggest-keywords` · `ai resume-optimize` · `ai cover-letter` · `ai interview-prep` · `ai chat-coach` · `ai local`
- **Utility / workflow**: `wizard` · `schema` · `platforms` · `export` · `config` · `clean`
- **Candidate actions**: `greet` · `batch-greet` · `apply` · `exchange` · `chat*` · `pipeline` · `digest`
- **Recruiter**: `hr applications/candidates/recommendations/greet/resume/chat/chatmsg/last-messages/reply/request-resume/jobs`

Full command tables, parameters, and welfare-matching internals: **[Command Reference](docs/commands.en.md)**. The capability source of truth is `boss schema` (with `--format openai-tools` / `anthropic-tools` exports).

Bulk crawl requires `uv sync --extra crawl`. It uses its own `<data-dir>/crawl/chrome-profile` and never attaches to a daily Chrome profile. Hooks are disabled by default; local scripts require both an explicit profile and a directory containing `SHA256SUMS`:

```powershell
boss crawl configure --max-requests 20 --max-details 50 --max-seconds 600 --max-retries 1
boss crawl run "AI" --city 杭州 --pages 3 --with-detail `
  --hook-profile screenshot-full --hook-dir E:\boss-agent-cli-local-hooks\AntiDebug_Breaker
boss crawl resume <run_id>
boss crawl stop <run_id>
boss agent crawl --run-id <run_id> --resume <resume-name>
```

`crawl run` is sequential, checkpoints SQLite state, and incrementally writes JSON / CSV / XLSX artifacts. Request, detail, wall-clock, and retry budgets are fixed; `boss crawl stop` stops at the next safe point. Exports and `crawl results` redact `security_id`, selectors, and recruiter fields; `boss clean --privacy` removes crawl state, budgets, and exports. Fine-grained MCP crawl tools read or import an existing `run_id`; `boss_wizard` can start, resume, and stop the shared workflow. A platform risk code or security page stops the task and returns a resume command.

## 🩺 Troubleshooting

```bash
boss doctor             # environment check
boss status --live      # optional low-frequency read-only probe
boss doctor --live-probe
```

Every error envelope carries `code` + `recoverable` + `recovery_action`, so agents can react programmatically. Browser Bridge local diagnostics cover `bridge_daemon` / `bridge_extension` / `bridge_protocol` / `bridge_workspace` / `bridge_exec` / `bridge_fetch` / `bridge_navigate`; start the daemon with `python -m boss_agent_cli.bridge.daemon --serve`. Every mode stops on platform risk-control blocks; declared adapters must remain bounded, checkpointed, redacted, and explicitly resumed.

Full checks, CDP launch examples, and error codes: **[Troubleshooting](docs/troubleshooting.en.md)**. For Cookie / CDP / patchright / request-rate / drift issues, read [Platform Risk Boundaries](docs/platform-risk.en.md) first.

## ⚙️ Configuration

```bash
boss config list                      # view all settings
boss config set log_level debug       # set the log level
boss config reset                     # restore defaults
```

Settings live in `~/.boss-agent/config.json`: request delays, batch-greet delay, log level, CDP URL, export dir, platform / role.

## 🏗️ Architecture

```
CLI (Click)
  └─ Wizard / WorkflowRunner (TTY + headless JSON + persisted run state)
       └─ Capability metadata (assisted / research compatibility; no mode gate)
       └─ AuthManager ── user-triggered login state (Fernet + PBKDF2 machine-bound encryption)
       └─ Platform registries ── zhipin / zhilian / qiancheng placeholder
       └─ BossClient ── httpx + throttle; CDP / Bridge / patchright compatible for login & export
       └─ CacheStore (SQLite WAL) · AIService (OpenAI-compatible / Ollama / vLLM)
            └─ output.py → JSON envelope → stdout
```

**Invariants**: stdout is JSON-only · stderr holds logs · `exit 0/1` · errors carry `code/recoverable/recovery_action` · `boss schema` is the authoritative capability source.
**Two audiences, one envelope**: `hints.next_actions` holds follow-up commands for the Agent to run; `hints.operator_actions` holds natural-language guidance for the human operator (scan a QR code, adjust filters in the browser — anything done away from the terminal). TTY renders only the latter, to stderr; an Agent should relay it to the operator.
**Command or wizard**: single-shot, stateless capability calls go through top-level commands; anything needing cross-step state, resumability, or handing guidance to a human goes through `boss wizard` (goals listed under `wizard_catalog` in `boss schema`).
**Stack**: Python ≥ 3.10 · Click · httpx · patchright / CDP / Bridge (login, export, and declared browser adapters) · cryptography · sqlite3 (WAL) · pytest (1600+).

## 🔌 Local Storage

All state lives under `~/.boss-agent/` — encrypted tokens, cached searches, shortlist, local resumes, AI config, and external model registry. Model weights are not bundled into the Python package; nothing leaves your machine except explicit API calls or user-confirmed model downloads.

## 🤝 Contributing

See [CONTRIBUTING.en.md](CONTRIBUTING.en.md) and [Getting Started](docs/getting-started.en.md). TL;DR: fork → `feat/xxx` branch → write tests → `python scripts/quality_baseline.py` (on Chinese Windows, set `$env:PYTHONUTF8='1'` first) → PR.

Thanks to everyone who has made boss-agent-cli better — go follow them! ❤️

<a href="https://github.com/can4hou6joeng4/boss-agent-cli/graphs/contributors">
  <img src="./CONTRIBUTORS.svg" alt="contributors" width="1000" />
</a>

## ❤️ Support

- If this project helps you, the most direct support is a [Star ⭐](https://github.com/can4hou6joeng4/boss-agent-cli), or sharing it with someone who is job hunting.
- Hit a problem or have an idea? Open an [Issue](https://github.com/can4hou6joeng4/boss-agent-cli/issues) — or go straight to a PR.
- Curious about the rest of the fleet? Drop anchor at the home port [bobochang.cn](https://bobochang.cn) 🧭.

This project benefits from [geekgeekrun](https://github.com/geekgeekrun/geekgeekrun) · [boss-cli](https://github.com/jackwener/boss-cli) · [opencli](https://github.com/jackwener/opencli) — thanks to all of them.

## ⭐ Star History

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/star-history-dark.svg">
  <img alt="Star History" src="docs/assets/star-history.svg" width="100%">
</picture>

A static SVG generated locally with [mystarhistory](https://github.com/carsteneu/mystarhistory) — served from this repo, with no third-party dependency.

## ⚠️ Disclaimer

Follow applicable law, platform terms, and privacy requirements. Set explicit input, volume, timeout, and stop limits for bulk outreach, candidate data, and browser-adaptation workflows, and protect local credentials and exported artifacts.

## 📑 License & Communities

[MIT](LICENSE) © [can4hou6joeng4](https://github.com/can4hou6joeng4) · [LINUX DO](https://linux.do/)
