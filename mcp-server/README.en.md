# boss-agent-cli MCP Server

Expose `boss-agent-cli` as a default low-risk MCP surface for Claude Desktop, Cursor, and other MCP-compatible hosts. The default server exposes local assistance, job search/detail, local shortlist, local resume, and AI-helper tools. Automated outreach, bulk actions, chat records, candidate personal data, and sensitive recruiter workflows are not exposed by default.

Related docs:
- [Agent Quickstart](../docs/agent-quickstart.en.md)
- [Capability Matrix](../docs/capability-matrix.en.md)

## Install

```bash
uv tool install "boss-agent-cli[mcp]"
```

From source:

```bash
uv sync --all-extras
uv run python mcp-server/server.py
```

## Configure Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "boss-agent-cli": {
      "command": "boss-mcp",
      "args": []
    }
  }
}
```

## Configure Cursor

Add the server in Cursor Settings -> MCP Servers:

```json
{
  "boss-agent-cli": {
    "command": "boss-mcp",
    "args": []
  }
}
```

## Available tools

The current MCP server exposes **32 low-risk tools** by default.

### Auth and environment

| Tool | Description |
|------|-------------|
| `boss_status` | Check the current authenticated session |
| `boss_doctor` | Run environment diagnostics |
| `boss_config` | View or update configuration |
| `boss_clean` | Remove stale cache and temp files |

### Job discovery and local organization

| Tool | Description |
|------|-------------|
| `boss_search` | Search jobs with city, salary, and welfare filters |
| `boss_detail` | Fetch job details |
| `boss_show` | Open a job from the previous search result by index |
| `boss_export` | Export search results to CSV / JSON / HTML (supports `url` for replaying web filters; redacts job_id/security_id/boss_name by default) |
| `boss_cities` | List supported cities |
| `boss_history` | Read browsing history |
| `boss_shortlist_list` | View the local shortlist |
| `boss_shortlist_add` | Add a job to the local shortlist |
| `boss_shortlist_remove` | Remove a job from the local shortlist |
| `boss_preset_add/list/remove` | Manage local search presets |
| `boss_watch_add/list/remove` | Manage local watch presets; `watch run` is not exposed by default |

### User and resume

| Tool | Description |
|------|-------------|
| `boss_me` | User profile, resume, intent, and application history |
| `boss_resume_list` | List local resumes |
| `boss_resume_show` | View a local resume |

### AI assistance

| Tool | Description |
|------|-------------|
| `boss_ai_analyze_jd` | Analyze a job description |
| `boss_ai_optimize` | Optimize a local resume draft for a job description |
| `boss_ai_suggest` | Generate resume improvement suggestions |
| `boss_ai_reply` | Draft replies from user-provided text |
| `boss_ai_interview_prep` | Generate interview preparation from a job description |
| `boss_ai_chat_coach` | Coach communication from user-provided text |

### Recruiter low-risk entry points

| Tool | Description |
|------|-------------|
| `boss_hr_jobs` | Manage job listings and online/offline state |
| `boss_hr_jobs_detail` | View recruiter-side job details |

Sensitive tools such as `boss_greet`, `boss_apply`, `boss_chat`, `boss_chatmsg`, `boss_pipeline`, `boss_digest`, `boss_hr_candidates`, and `boss_hr_reply` are not exposed by default. Direct CLI calls return `COMPLIANCE_BLOCKED` in default low-risk mode.

## Example prompt

After configuration, you can say this directly in Claude Desktop:

> "Help me search for Golang roles in Guangzhou with 双休 and 五险一金, then add promising jobs to the local shortlist."

Claude can call `boss_search`, `boss_detail`, and `boss_shortlist_add`. Applications, messaging, contact exchange, and candidate handling should be completed manually on the official website.

## Transports

### stdio (default)

```bash
boss-mcp
```

### SSE

```bash
boss-mcp --transport sse --host 127.0.0.1 --port 8765
```

Default paths:
- SSE handshake: `/sse`
- Message endpoint: `/messages/`

### HTTP streaming

```bash
boss-mcp --transport http --host 127.0.0.1 --port 8765
```

Default path:
- HTTP streaming: `/mcp`

**Design constraints**:
- `stdio` remains the default behavior so existing integrations do not break
- HTTP transports bind to `127.0.0.1` by default; exposing them remotely requires an explicit `--host 0.0.0.0`
- Authentication and TLS are not built in; add them via a reverse proxy when needed

## Other agent hosts

```bash
boss schema --format openai-tools
boss schema --format anthropic-tools
boss schema --format mcp-tools
```

Then feed the `data.tools` array from stdout into the corresponding SDK.

## Contributing

Development environment:

```bash
cd boss-agent-cli
uv sync --all-extras
uv run pytest tests/test_mcp_server.py -v
```

Style rule: tabs for indentation, and `uv run ruff check src/ tests/` must pass.
