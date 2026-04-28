# boss-agent-cli MCP Server

Expose `boss-agent-cli` as MCP tools for clients such as Claude Desktop, Cursor, and other MCP-compatible hosts.

Related docs:
- [Agent Quickstart](../docs/agent-quickstart.en.md)
- [Capability Matrix](../docs/capability-matrix.en.md)

## Install

```bash
# 1. Install the boss CLI
uv tool install boss-agent-cli
patchright install chromium

# 2. Install the MCP extra and expose the MCP entry point
uv tool install "boss-agent-cli[mcp]"
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

The current MCP server exposes **49 tools**, covering candidate workflows, AI assistance, and recruiter-side `hr` operations.

### Auth and environment

| Tool | Description |
|------|-------------|
| `boss_status` | Check the current authenticated session |
| `boss_doctor` | Run environment diagnostics |
| `boss_config` | View or update configuration |
| `boss_clean` | Remove stale cache and temp files |

### Job discovery

| Tool | Description |
|------|-------------|
| `boss_search` | Search jobs with city, salary, and welfare filters |
| `boss_recommend` | Get personalized recommendations |
| `boss_detail` | Fetch job details |
| `boss_show` | Open a job from the previous search result by index |
| `boss_cities` | List supported cities |
| `boss_history` | Read browsing history |

### Candidate actions

| Tool | Description |
|------|-------------|
| `boss_greet` | Greet a recruiter |
| `boss_batch_greet` | Greet multiple recruiters from a search result |
| `boss_apply` | Apply or start the conversation immediately |

### Conversation management

| Tool | Description |
|------|-------------|
| `boss_chat` | Conversation list |
| `boss_chatmsg` | Chat message history |
| `boss_chat_summary` | Conversation summary and next-step suggestions |
| `boss_mark` | Manage contact labels |
| `boss_exchange` | Exchange contact information |
| `boss_interviews` | List interview invitations |

### Workflow management

| Tool | Description |
|------|-------------|
| `boss_pipeline` | Candidate pipeline view |
| `boss_follow_up` | Follow-up filtering |
| `boss_digest` | Daily digest |

### User profile

| Tool | Description |
|------|-------------|
| `boss_me` | User profile, resume, intent, and application history |

### Recruiter workflow

| Tool | Description |
|------|-------------|
| `boss_hr_applications` | View candidate applications |
| `boss_hr_candidates` | Search candidates |
| `boss_hr_chat` | Recruiter conversation list |
| `boss_hr_resume` | View online candidate resumes |
| `boss_hr_reply` | Reply to a candidate |
| `boss_hr_request_resume` | Request an attached resume from a candidate |
| `boss_hr_jobs` | Manage job listings and online/offline state |

## Example prompt

After configuration, you can say this directly in Claude Desktop:

> "Help me search for Golang roles in Guangzhou with 双休 and 五险一金."

Claude will call `boss_search` automatically and show the result.

## Transports

### stdio (default, already supported)

Claude Desktop, Cursor, Codex, and similar hosts connect over stdin/stdout by default. The `claude_desktop_config.json` example above uses stdio mode.

```bash
boss-mcp
```

### SSE

For hosts or custom orchestrators that still prefer the traditional MCP SSE transport:

```bash
boss-mcp --transport sse --host 127.0.0.1 --port 8765
```

Default paths:
- SSE handshake: `/sse`
- Message endpoint: `/messages/`

Custom paths:

```bash
boss-mcp --transport sse --port 8765 --sse-path /events --message-path /inbox
```

### HTTP streaming

For hosts that support the newer MCP streamable HTTP transport:

```bash
boss-mcp --transport http --host 127.0.0.1 --port 8765
```

Default path:
- HTTP streaming: `/mcp`

Custom path:

```bash
boss-mcp --transport http --port 8765 --path /rpc
```

**Design constraints**:
- `stdio` remains the default behavior so existing integrations do not break
- HTTP transports bind to `127.0.0.1` by default; exposing them remotely requires an explicit `--host 0.0.0.0`
- Authentication and TLS are not built in; add them via a reverse proxy such as nginx, Caddy, or Cloudflare Tunnel when needed

## Other agent hosts

This MCP server focuses on stdio, SSE, and streamable HTTP. If your agent framework does not support MCP directly, use `boss schema` to generate host-native tool definitions:

```bash
# OpenAI Functions / Tools API (GPT-4 / GPT-5)
boss schema --format openai-tools

# Anthropic Tool Use (Claude API)
boss schema --format anthropic-tools
```

Then feed the `data.tools` array from stdout into the corresponding SDK.

## Contributing

You are welcome to pick up tasks labeled `good first issue` or `help wanted` in the [Issue Tracker](https://github.com/can4hou6joeng4/boss-agent-cli/issues).

Development environment:

```bash
cd boss-agent-cli
uv sync --all-extras
uv run pytest tests/test_mcp_server.py -v  # 65 tests covering tool schema + arg construction
```

Style rule: tabs for indentation, and `uv run ruff check src/ tests/` must pass.
