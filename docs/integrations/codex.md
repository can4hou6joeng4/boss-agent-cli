# Codex Integration Example

Applies to the current `boss-agent-cli` CLI contract as of April 28, 2026.

## Good fit when

- the agent runs terminal commands directly
- you need a multi-step chain such as `schema -> status -> search -> detail -> greet`
- you want stdout JSON to feed the next decision step programmatically

## Minimal integration

Teach the agent to follow this working pattern:

```text
When the task involves BOSS Zhipin search, detail inspection, greeting, or candidate workflow progression:
1. Run `boss schema` first to get capabilities and arguments
2. Run `boss status` to check authentication
3. If not logged in, run `boss login` and tell the user to complete the QR flow if needed
4. Use `boss search` for discovery
5. Use `boss detail` after a promising hit
6. Use `boss greet` when active outreach is appropriate
7. In recruiter workflows, prefer `boss hr applications / candidates / reply / request-resume`
8. Parse stdout JSON only; when `ok=false`, inspect `error.code` and `error.recovery_action`
```

Minimal candidate-side command chain:

```bash
boss schema
boss status
boss search "Golang" --city 广州 --welfare "双休,五险一金"
boss detail <security_id>
boss greet <security_id> <job_id>
```

Minimal recruiter-side chain:

```bash
boss schema
boss status
boss hr applications
boss hr candidates "Golang"
boss hr reply <friend_id> "你好"
```

Recommended fields to parse:

- `ok`: success or failure
- `data`: jobs, details, or action results
- `hints.next_actions`: suggested next command
- `error.code`: recovery routing
- `error.recovery_action`: what the agent should do next

## Recovery flow

Preferred order:

```bash
boss doctor
boss status
boss login
boss search "Golang" --city 广州
```

Common branches:

- `AUTH_REQUIRED` / `AUTH_EXPIRED`: run `boss login` again
- `INVALID_PARAM`: return to `boss schema` and validate arguments
- `RATE_LIMITED`: back off before retrying; do not spam `boss greet`
