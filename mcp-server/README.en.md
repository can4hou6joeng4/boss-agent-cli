# boss-agent-cli MCP Server

Expose the full candidate, recruiter, local, AI, and workflow surface of `boss-agent-cli` to Claude Desktop, Cursor, and other MCP-compatible hosts. Historical assisted/research settings have identical capability access; missing platform implementations still return `NOT_SUPPORTED`.

Related docs:
- [Agent Quickstart](../docs/agent-quickstart.en.md)
- [Capability Matrix](../docs/capability-matrix.en.md)

## Install

```bash
uv tool install "boss-agent-cli[mcp,crawl]"  # use [mcp] only when crawl tools are not needed
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

The current MCP server exposes **75 implemented tools**.

### Shared workflow

| Tool | Description |
|------|-------------|
| `boss_wizard` | Run a role/platform/goal workflow, or query, resume, and stop it by explicit `run_id`; shares state with the human `boss wizard` |

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
| `boss_watch_add/list/remove/run` | Manage and execute job watches |

### Candidate actions and conversations

| Tool | Description |
|------|-------------|
| `boss_greet` / `boss_batch_greet` / `boss_apply` | Greet, bounded batch greet, and apply/start a conversation |
| `boss_chat` / `boss_chatmsg` / `boss_chat_summary` | Conversation list, message history, and summary |
| `boss_mark` / `boss_exchange` | Contact labels and contact exchange |
| `boss_pipeline` / `boss_follow_up` / `boss_digest` | Candidate progress, follow-up selection, and digest |

### Existing crawl tasks

| Tool | Description |
|------|-------------|
| `boss_crawl_status` | Read cursor, job count, detail progress, risk state, and resume command |
| `boss_crawl_results` | Read persisted jobs for a run, filtered by page/detail state if needed |
| `boss_crawl_shortlist` | Import one run's jobs into the local shortlist without a platform call |

Fine-grained crawl tools read or import existing tasks; `boss_wizard` goals `crawl_start/crawl_resume/crawl_stop` can start, resume, or stop the shared workflow. The default Hook is `none`; local Hook directories must contain `SHA256SUMS`. This project does not redistribute third-party scripts.

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

### Recruiter workflow

| Tool | Description |
|------|-------------|
| `boss_hr_jobs` | Manage job listings and online/offline state |
| `boss_hr_jobs_detail` | View recruiter-side job details |
| `boss_hr_applications` / `boss_hr_candidates` | Applications and candidate search |
| `boss_hr_recommendations` / `boss_hr_greet` | Recommended candidates, greeting preview, and operator-approved first contact with read-receipt verification |
| `boss_hr_resume` / `boss_hr_exchange` / `boss_hr_request_resume` | Online resumes, contact exchange, and attached-resume requests |
| `boss_hr_chat` / `boss_hr_chatmsg` / `boss_hr_last_messages` / `boss_hr_reply` | Recruiter conversation reads and replies |

Every implemented tool is exposed. `ACCOUNT_RISK`, `AUTH_REQUIRED`, `RATE_LIMITED`, and `NOT_SUPPORTED` still use the standard JSON error envelope; agents should follow `error.recovery_action`.

## Example prompt

After configuration, you can say this directly in Claude Desktop:

> "Help me search for Golang roles in Guangzhou with 双休 and 五险一金, then add promising jobs to the local shortlist."

Claude can run a full workflow through `boss_wizard` or compose fine-grained tools such as `boss_search`, `boss_detail`, `boss_shortlist_add`, and `boss_apply`.

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
