<div align="center">

# boss-agent-cli

**A CLI tool designed for AI Agents to interact with BOSS Zhipin**

> Search jobs · Welfare filtering · Personalized recommendations · Auto-greeting · Job pipeline · Incremental watch · AI resume optimization

[![CI](https://github.com/can4hou6joeng4/boss-agent-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/can4hou6joeng4/boss-agent-cli/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-≥3.10-3776AB?logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![GitHub Release](https://img.shields.io/github/v/release/can4hou6joeng4/boss-agent-cli)](https://github.com/can4hou6joeng4/boss-agent-cli/releases)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/can4hou6joeng4/boss-agent-cli/pulls)

[Install](#-install) · [Quickstart](#-quickstart) · [Agent Integration](#-agent-integration) · [Commands](#-commands) · [Architecture](#-architecture) · [Changelog](CHANGELOG.md)

[中文](README.md) | **English**

</div>

---

## 💡 Why boss-agent-cli?

Traditional job hunting: open a web page → flip through dozens of pages → check each detail → manually greet → forget who to follow up with.

With AI Agents: `boss search` → `boss ai optimize` → `boss batch-greet` → `boss pipeline` — one chain closes the entire loop.

Every command outputs **structured JSON** that AI Agents parse directly. No fragile HTML scraping, no brittle selectors.

## 🌟 Core Capabilities

- **34 CLI commands** covering the full loop: search → detail → greet → chat → follow-up → apply
- **JSON envelope output** on stdout, logs on stderr — Agent-friendly by design
- **4-tier login fallback**: Cookie extract → CDP → QR httpx → patchright QR scan
- **CDP mode** connects your local Chrome for real browser fingerprint and automatic stoken refresh
- **Welfare filter** (`--welfare "双休,五险一金"`) with client-side AND logic and parallel detail fetching
- **AI resume optimization** with OpenAI / Claude / Gemini / Qwen / DeepSeek multi-model support
- **AI chat reply drafting** based on recruiter message context
- **Investment funnel stats** — greeted / applied / shortlist conversion rates
- **MCP server** with 31 tools, works out of the box with Claude Desktop / Cursor

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
```

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

See [Agent Quickstart](docs/agent-quickstart.md) and [Capability Matrix](docs/capability-matrix.md) for the full picture.

## 📚 Commands

34 commands, grouped by stage:

| Stage | Commands |
|-------|----------|
| **Auth** | `login` · `logout` · `status` · `doctor` |
| **Discover** | `search` · `recommend` · `detail` · `show` · `cities` · `history` |
| **Act** | `greet` · `batch-greet` · `apply` · `exchange` · `mark` |
| **Track** | `chat` · `chatmsg` · `chat-summary` · `interviews` · `pipeline` · `follow-up` · `digest` · `stats` |
| **Organize** | `watch` · `preset` · `shortlist` |
| **Resume** | `resume` · `me` |
| **AI** | `ai config` · `ai analyze-jd` · `ai polish` · `ai optimize` · `ai suggest` · `ai reply` |
| **Utility** | `schema` · `export` · `config` · `clean` |

Run `boss <cmd> --help` for options, or `boss schema` for the complete JSON self-description.

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

See [CONTRIBUTING.md](CONTRIBUTING.md). TL;DR: fork → `feat/xxx` branch → write tests → `uv run pytest` → PR.

## 📄 License

MIT © [can4hou6joeng4](https://github.com/can4hou6joeng4)
