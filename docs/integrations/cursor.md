# Cursor Integration Example

Applies to the current `boss-agent-cli` CLI contract as of April 28, 2026.

Cursor is a VS Code-based AI-first IDE. Its Composer agent can run terminal commands directly, and Cursor 0.42+ can also attach MCP servers. This guide covers two options: native MCP integration (recommended) and shell-command integration (fallback).

## Good fit when

- you want Composer to drive the full job-hunt flow inside Cursor
- you want `boss` exposed as MCP tools instead of pasted shell snippets
- you already maintain `.cursor/rules/` and want to add BOSS Zhipin capability

## Minimal integration

Cursor supports two approaches. Use whichever fits your setup.

### Option 1: MCP server integration (recommended)

In Cursor Settings → MCP, add a stdio server that points to this repo's `mcp-server/server.py`:

```json
{
  "mcpServers": {
    "boss-agent-cli": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/boss-agent-cli",
        "run",
        "python",
        "mcp-server/server.py"
      ]
    }
  }
}
```

Once enabled, Composer will automatically discover `boss_search`, `boss_detail`, `boss_greet`, and the rest of the 49 MCP tools. No extra shell prompt glue is required.

### Option 2: shell-command integration

Add a rule like this to `.cursor/rules/boss-agent-cli.mdc`:

```markdown
---
description: BOSS Zhipin job-hunt capability
globs:
alwaysApply: false
---

When the user asks to search jobs, inspect job details, greet recruiters, or continue a candidate workflow:
1. Run `boss schema` first to learn the capability surface
2. Then run `boss status` to check authentication
3. If not logged in, run `boss login` and ask the user to scan if needed
4. Use `boss search <query> --city <city> --welfare <keywords>` for discovery
5. Use `boss detail <security_id>` to inspect a matching job
6. Use `boss greet <security_id> <job_id>` when it is time to reach out
7. Read stdout JSON only; when `ok=false`, inspect `error.recovery_action`
```

Minimal command chain:

```bash
boss schema
boss status
boss search "Golang" --city 广州 --welfare "双休,五险一金"
boss detail <security_id>
boss greet <security_id> <job_id>
```

## Fields to parse

- `ok`: whether the command succeeded
- `data`: jobs, details, or action results
- `hints.next_actions`: suggested next command
- `error.code`: recovery routing
- `error.recovery_action`: how the agent should recover

## Recovery flow

Recommended order:

```bash
boss doctor
boss status
boss login
```

Common branches:

- `AUTH_REQUIRED` / `AUTH_EXPIRED`: run `boss login` again
- `INVALID_PARAM`: return to `boss schema` and validate parameter names
- `RATE_LIMITED`: wait before retrying; do not spam `boss greet`
- `ACCOUNT_RISK`: start a CDP Chrome session (for example via a `boss-chrome` alias), then retry

## Advanced ideas

- Wire `boss ai interview-prep <jd>` and `boss ai chat-coach <chat>` into Composer for resume matching and communication coaching
- Generate daily markdown reports with `boss digest --format md -o daily.md` and preview them directly in Cursor
