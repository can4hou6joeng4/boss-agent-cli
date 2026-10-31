# Codex Integration Example

Applies to the current `boss-agent-cli` open-capability CLI contract as of August 10, 2026.

## Good fit when

- the agent runs terminal commands directly
- you need an end-to-end read-first chain such as `schema -> status -> search -> detail -> shortlist`
- you want stdout JSON to feed the next decision step programmatically

## Minimal integration

Teach the agent to follow this working pattern:

```text
When the task involves BOSS Zhipin search, detail inspection, or local job organization:
1. Run `boss schema` first to get capabilities and arguments
2. Run `boss status` to check authentication
3. If not logged in, run `boss login` and tell the user to complete the QR flow if needed
4. Use `boss search` for discovery
5. Use `boss detail` after a promising hit
6. Use `boss shortlist add` for local organization; draft job-specific materials from verified user facts
7. In recruiter workflows, use the candidate, application, resume, chat, reply, and job commands exposed by `boss schema`
8. Parse stdout JSON only; when `ok=false`, inspect `error.code` and `error.recovery_action`
```

Minimal candidate-side command chain:

```bash
boss schema
boss status
boss search "Golang" --city 广州 --welfare "双休,五险一金"
boss detail <security_id>
boss shortlist add <security_id> <job_id>
```

## Authorization boundary

Default to read-only discovery, detail inspection, local shortlist management, exports, and draft generation. Do not call `boss apply`, `boss greet`, `boss batch-greet`, or any command that writes to a platform unless the user has named the specific job in the current conversation and confirms immediately before the action.

Process one external action at a time: perform it, read the explicit platform result, then ask before moving to the next job. Do not queue outreach, run unattended bulk actions, reuse identical greetings, or retry an action whose result is unknown. On `ACCOUNT_RISK`, `ENVIRONMENT_RISK`, a verification page, or a rate limit, stop and preserve the checkpoint; never bypass the platform control.

Minimal recruiter-side chain:

```bash
boss schema
boss status
boss hr jobs list
boss hr candidates "Python" --city 101010100
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
- `RATE_LIMITED`: stop external actions, keep the checkpoint, and wait for an explicit user decision before any retry

### Message view loads, then turns blank

If a BOSS message view stays loading and then becomes blank without a login page, verification prompt, access restriction, or a clear browser crash, treat it as a client-side message-view loop rather than evidence of account risk or message delivery.

Keep only the actual message-list URL and the local conversation reference. Return to that recorded list once, wait until it is stable, then reopen the target conversation once for read-only verification. If the same loop happens again, stop automatic message-view recovery, leave any pending send as outcome unknown, and do not reload the conversation, guess a conversation URL, create repeated tabs, or resend a message.
