# Blog Draft: A Low-Risk Job-Search Toolkit for AI Agents

> English blog draft for Hacker News (Show HN) / Reddit r/ClaudeAI / Dev.to / LinkedIn. Trim per channel before posting.

---

## Title Candidates

- **A**: Show HN: I built a low-risk job-search CLI for Claude and Cursor
- **B**: boss-agent-cli — 35 top-level commands + 35 default low-risk MCP tools
- **C**: An agent-first CLI for searching, filtering, and organizing job leads

Recommended: **A**. It frames the project as an agent integration without implying automated applications or outreach.

---

## Body

### TL;DR

`boss-agent-cli` is an open-source CLI for AI agents to handle the low-risk parts of job discovery and local organization on [BOSS Zhipin](https://www.zhipin.com/). Every command outputs a structured JSON envelope, `boss schema` is the capability source of truth, and MCP exposes 35 default low-risk tools for Claude Desktop, Cursor, and other MCP-compatible hosts.

```bash
uv tool install boss-agent-cli
patchright install chromium
boss doctor
boss login
boss search "golang" --city Shanghai --welfare "two-day weekends,5 insurances 1 fund"
boss detail <security_id> --job-id <job_id>
boss shortlist add <security_id> <job_id>
```

### Why build this

I've been letting agents handle repetitive engineering workflows for a while, but recruiting platforms are not ordinary forms:

- HTML scrapers drift when the site changes markup
- Playwright recordings are fine for demos, not long-term maintenance
- When platform risk controls trigger, switching automation channels is the wrong response

So I built a CLI around the part agents can safely do: **parse JSON from stdout, search and inspect jobs, organize local shortlists, and stop at official-platform handoff points for sensitive actions**.

### Three design decisions

#### 1. Low-Risk Assistance Mode by default

The default posture is local assistance, read-only first, user-triggered, no risk-control bypass, no bulk outreach, and no platform-data scraping. Greetings, applications, contact exchange, recruiter candidate workflows, resumes, chats, and replies are blocked by default with `COMPLIANCE_BLOCKED` and should be completed manually on the official website.

#### 2. Welfare filtering without turning it into a request storm

`--welfare "双休,五险一金"` first uses fields already present on job cards, then reads details only when needed to avoid missing jobs whose benefits appear in the description instead of the list response. The point is better local filtering, not higher request volume.

#### 3. MCP-first agent integration

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

After connecting MCP, an agent can run a low-risk chain such as search -> detail -> local shortlist -> interview prep. Applications, greetings, contact exchange, and recruiter candidate handling remain official-platform handoffs.

### Current boundaries

- `boss schema` currently exposes 35 top-level commands; `hr` has 9 first-level recruiter subcommands; MCP exposes 35 default low-risk tools
- `zhipin` covers the main candidate workflow; `zhilian` supports candidate-side read-only + local-assist parity; `qiancheng` remains a `NOT_SUPPORTED` placeholder
- CI covers Python 3.10 / 3.11 / 3.12 / 3.13, ruff, mypy, docs consistency, and CodeQL
- No telemetry, analytics, or cloud sync; adoption is measured through PyPI downloads and GitHub Insights only

### Links

- GitHub: https://github.com/can4hou6joeng4/boss-agent-cli
- PyPI: https://pypi.org/project/boss-agent-cli/
- Roadmap: https://github.com/can4hou6joeng4/boss-agent-cli/blob/master/ROADMAP.md
- Open issues labeled `good first issue`: https://github.com/can4hou6joeng4/boss-agent-cli/labels/good%20first%20issue

MIT licensed. Data stays local by default. Questions, PRs, and bug reports are welcome.

---

## Submission checklist

- [ ] Show HN
- [ ] Reddit r/ClaudeAI
- [ ] Reddit r/LocalLLaMA
- [ ] Dev.to (tags: python, ai, cli, opensource)
- [ ] LinkedIn
- [ ] Twitter/X thread with gif demo
