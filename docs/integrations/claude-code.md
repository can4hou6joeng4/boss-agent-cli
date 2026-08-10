# Claude Code Integration Example

Applies to the current `boss-agent-cli` open-capability CLI contract as of August 10, 2026.

## Good fit when

- your team distributes job-hunt capability to agents via MCP
- you want Claude Code rules to enforce a stable BOSS Zhipin workflow
- you want `boss` exposed as a reliable shell capability for Claude Code

## Minimal integration

Preferred path: connect the MCP server (Claude Code supports MCP).

```bash
claude mcp add boss-agent -- uvx --from boss-agent-cli[mcp] boss-mcp
```

If you prefer a rules-file workflow, you can add guidance like this:

```markdown
When the user asks to search jobs, inspect job details, or organize candidate jobs locally:
1. Run `boss schema` first
2. Then run `boss status`
3. If not logged in, run `boss login`
4. Use `boss search` for discovery
5. Use `boss detail` for a full job view
6. Use `boss shortlist add`, `boss apply`, or `boss greet` according to the user's goal
7. In recruiter workflows, use `boss hr candidates`, `boss hr applications`, `boss hr resume`, `boss hr chat`, and `boss hr reply` as discovered from schema
8. Read stdout JSON only; do not parse stderr
```

Minimal candidate-side command chain:

```bash
boss schema
boss status
boss search "Golang" --city 广州 --welfare "双休,五险一金"
boss detail <security_id>
boss shortlist add <security_id> <job_id>
```

Minimal recruiter-side command chain:

```bash
boss schema
boss status
boss hr jobs list
boss hr candidates "Python" --city 101010100
```

Integration advice:

- treat `boss schema` as the source of truth for capabilities and arguments
- feed `boss detail` output back into the context before deciding whether to shortlist, apply, or greet
- when `ok=false`, prefer `error.recovery_action` before inventing your own retry logic

## Recovery flow

Recommended order:

```bash
boss doctor
boss status
boss login
boss search "Golang" --city 广州
```

Common recovery actions:

- login expired: run `boss login` again
- invalid parameters: go back to `boss schema` or `boss cities`
- environment issue: run `boss doctor` first; do not continue sensitive actions through automation
