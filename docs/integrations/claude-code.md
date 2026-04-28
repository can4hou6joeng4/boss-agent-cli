# Claude Code Integration Example

Applies to the current `boss-agent-cli` CLI contract as of April 28, 2026.

## Good fit when

- your team distributes job-hunt capability through Skills
- you want Claude Code rules to enforce a stable BOSS Zhipin workflow
- you want `boss` exposed as a reliable shell capability for Claude Code

## Minimal integration

Preferred path: install the Skill.

```bash
npx skills add can4hou6joeng4/boss-agent-cli
```

If you prefer a rules-file workflow, you can add guidance like this:

```markdown
When the user asks to search jobs, inspect job details, greet recruiters, or continue recruiter-side communication:
1. Run `boss schema` first
2. Then run `boss status`
3. If not logged in, run `boss login`
4. Use `boss search` for discovery
5. Use `boss detail` for a full job view
6. Use `boss greet` for the first outbound action
7. In recruiter workflows, use `boss hr applications / candidates / reply / request-resume`
8. Read stdout JSON only; do not parse stderr
```

Minimal candidate-side command chain:

```bash
boss schema
boss status
boss search "Golang" --city 广州 --welfare "双休,五险一金"
boss detail <security_id>
boss greet <security_id> <job_id>
```

Minimal recruiter-side command chain:

```bash
boss schema
boss status
boss hr applications
boss hr candidates "Golang"
boss hr reply <friend_id> "你好"
```

Integration advice:

- treat `boss schema` as the source of truth for capabilities and arguments
- feed `boss detail` output back into the context before deciding whether to call `boss greet`
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
- environment issue: run `boss doctor` first, then decide whether it is safe to continue with `boss greet`
