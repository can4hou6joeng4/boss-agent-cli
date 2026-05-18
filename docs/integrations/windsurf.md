# Windsurf Integration Example

Applies to the current `boss-agent-cli` low-risk CLI contract as of May 18, 2026.

Windsurf is Codeium's agentic IDE. Cascade is the primary agent surface and supports both MCP servers and project-level `.windsurfrules`. This guide covers two options: native MCP integration (recommended) and rules-file integration (fallback).

## Good fit when

- you want Cascade to search, inspect, and organize jobs while sensitive actions stay manual
- you want `boss` registered as MCP tools instead of pasting terminal commands
- you already have project rules in `.windsurfrules` and want to add BOSS Zhipin constraints

## Minimal integration

Windsurf supports two approaches. Choose the one that fits your setup.

### Option 1: MCP server integration (recommended)

In Windsurf Settings → Cascade → MCP Servers, add:

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

Once enabled, Cascade will enumerate the default low-risk MCP surface, including `boss_search`, `boss_detail`, `boss_show`, `boss_shortlist_*`, local resume tools, and AI helpers. Sensitive tools such as greet/apply/chat/candidate workflows are not exposed by default.

### Option 2: `.windsurfrules` integration

Append guidance like this to the project root `.windsurfrules`:

```markdown
## BOSS Zhipin job-hunt capability

When the task involves job discovery, job-detail inspection, or local job organization:
1. Run `boss schema` first to learn the capability surface and argument shapes
2. Then run `boss status` to verify authentication
3. If not logged in, run `boss login` and ask the user to scan if needed
4. Use `boss search`, preferably with `--welfare` for precise filtering
5. Use `boss detail <security_id>` once a hit looks promising
6. Use `boss shortlist add <security_id> <job_id>` for local organization; outbound action stays manual on the official website
7. Consume stdout JSON only; when `ok=false`, read `error.recovery_action` before retrying
```

Minimal command chain:

```bash
boss schema
boss status
boss search "Golang" --city 广州 --welfare "双休,五险一金"
boss detail <security_id>
boss shortlist add <security_id> <job_id>
```

## Fields to parse

- `ok`: whether the command succeeded
- `data`: jobs, details, or action results
- `hints.next_actions`: suggested next command
- `error.code`: recovery routing
- `error.recovery_action`: how Cascade should recover

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
- `RATE_LIMITED`: wait before retrying; do not continue sensitive automation
- `ACCOUNT_RISK`: stop automation and use the official website manually

## Advanced ideas

- Hook `boss ai reply <message>` and `boss ai chat-coach <chat>` into Cascade so it can help with communication quality
- Use `boss stats` and `boss shortlist_list` for local-state summaries; platform conversation digests are blocked by default
