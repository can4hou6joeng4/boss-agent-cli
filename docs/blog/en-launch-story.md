# Blog Draft: I Let My AI Agent Handle the Job Hunt

> English blog draft for Hacker News (Show HN) / Reddit r/ClaudeAI / Dev.to / LinkedIn. Ready to copy-paste.

---

## Title Candidates

- **A**: Show HN: I built a CLI that lets Claude apply to jobs for me
- **B**: boss-agent-cli — 33 top-level commands + 7 recruiter subcommands + 43 MCP tools for AI-driven job hunting
- **C**: I open-sourced the CLI that turns Claude into my recruiter liaison

Recommended: **A** (HN-friendly).

---

## Body

### TL;DR

`boss-agent-cli` is an open-source CLI tool designed for AI agents to handle the full job-hunting workflow on [BOSS Zhipin](https://www.zhipin.com/) (China's largest recruitment platform). Every command outputs structured JSON; 49 MCP tools plug into Claude Desktop, Cursor, or any MCP-compatible host. MIT licensed, 1000+ tests, dual job-seeker + recruiter workflow, and v1.11.0 on PyPI.

```bash
uv tool install boss-agent-cli
patchright install chromium
boss login
boss search "golang" --welfare "remote work,5 insurances 1 fund"
boss ai reply "When are you available to chat?"
```

### Why build this

I've been letting Claude drive repetitive workflows for a year — bug triage, log diffing, PR reviews. Every time I tried to hand off the job hunt, I hit the same wall:

- HTML scrapers break the moment the site ships a CSS change
- Playwright recordings are one-shot; batch runs get fingerprinted
- Reverse-engineered APIs die to rate limits within an hour

So I wrote what I actually wanted: a CLI where **every command outputs JSON on stdout**, and the agent is the primary caller. Humans can still run it, but the design target is an LLM doing 40 tool calls in a loop.

### Three design decisions worth explaining

#### 1. Four-tier login fallback

User environments are hostile. Any single auth method fails for *someone*. So `boss login` tries, in order:

1. Extract cookies from local Chrome (`browser-cookie3`) — instant, no QR scan
2. Attach to user's Chrome via CDP (`--remote-debugging-port=9222`) — real fingerprint, automatic token rotation
3. Fetch QR code via `httpx` and render in terminal — works without a browser
4. Spin up a patchright headless browser as last resort

Login success rate is effectively 100% because at least one path always works.

#### 2. Parallel welfare filtering

BOSS Zhipin's search API returns welfare tags as a *subset* — a job with "双休" (two-day weekends) on its detail page might not show the tag in search results. Filtering on the list response misses matches.

The fix: `ThreadPoolExecutor` with 3 workers running detail fetches in parallel, then AND-filtering the complete welfare array. A shared `RequestThrottle` with Gaussian jitter prevents rate-limit hits.

#### 3. MCP server as the primary integration surface

Model Context Protocol is Anthropic's standard for exposing tools to AI agents. The `mcp-server/` directory ships a stdio server that wraps the CLI into 43 MCP tools, including recruiter-side operations and AI workflow helpers.

In `claude_desktop_config.json`:

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

Then I can say in Claude Desktop: *"Find Python jobs in Shanghai paying over 30K, greet the top 5."* Claude chains `boss_search` → `boss_show` → `boss_greet` on its own.

### The feature I'm most proud of: AI reply drafts

`boss ai reply` turns a recruiter's message into 2-3 reply drafts based on your resume and chat context:

```bash
$ boss ai reply "Are you available for a call this week?" \
    --resume senior-backend --tone concise
```

```json
{
  "intent_analysis": "Recruiter wants to schedule initial call",
  "reply_drafts": [
    {
      "style": "concise",
      "text": "Tue/Wed 7-9pm works. Prefer phone or video?",
      "suitable_when": "clear interest in moving fast"
    }
  ],
  "key_points": ["pin a time slot", "ask communication mode"],
  "avoid": ["vague commitments", "excessive pleasantries"]
}
```

### Engineering stats (for the skeptics)

- 1042 pytest tests, roughly 86% line coverage
- CI matrix across Python 3.10 / 3.11 / 3.12 / 3.13
- Ruff lint + pre-commit hooks
- Drift-detection meta-test: schema ↔ main.py registration must stay aligned
- SemVer releases + CHANGELOG + PyPI + GitHub Release double-channel publish

### Roadmap (help wanted)

- Zhilian real adapter beyond the current skeleton
- MCP HTTP Streaming transport
- More recruiter-side platform adapters beyond BOSS Zhipin

### Links

- GitHub: https://github.com/can4hou6joeng4/boss-agent-cli
- PyPI: `pip install boss-agent-cli`
- Roadmap: [ROADMAP.md](https://github.com/can4hou6joeng4/boss-agent-cli/blob/master/ROADMAP.md)
- Open issues labeled `good first issue`: https://github.com/can4hou6joeng4/boss-agent-cli/labels/good%20first%20issue

MIT licensed. All data stays on your machine — no telemetry, no cloud sync, no analytics. Questions, PRs, bug reports welcome.

---

## Submission checklist

- [ ] Show HN (Tuesday 22:00 CST = Tuesday 07:00 SF)
- [ ] Reddit r/ClaudeAI
- [ ] Reddit r/LocalLLaMA
- [ ] Dev.to (tags: python, ai, cli, opensource)
- [ ] LinkedIn (personal network)
- [ ] Twitter/X thread with gif demo
