# Agent Quickstart

Historical `assisted` and `research` settings both expose every implemented capability. Agents must call `boss schema` and route by role, platform, availability, and the workflow goal catalog; missing platform implementations return `NOT_SUPPORTED`.

The shortest path for an AI agent: discover capabilities first, then use fine-grained commands or `boss wizard --input-json` for candidate, recruiter, and long-running workflows.

## 1) Install and prepare the environment

```bash
# Recommended options (pick one)
uv tool install boss-agent-cli   # uv: fast, isolated
pipx install boss-agent-cli      # pipx: isolated
pip install boss-agent-cli       # pip

# Install the browser used during login
patchright install chromium

# Run diagnostics and log in
boss doctor
boss login
boss status
```

Success criteria:
- `boss doctor` returns `ok=true`
- `boss status` returns layered local login health; use `boss status --live` only when you need an online read-only probe

If you plan to wire the CLI into an agent host instead of running commands manually in a terminal, start with [Agent Host Examples](agent-hosts.en.md).

## 2) Complete an agent workflow in three steps

```bash
# Step 1: fetch the self-described capability schema
boss schema

# Step 2: search and narrow down target jobs
boss search "Golang" --city 广州 --welfare "双休,五险一金"
# Complex filters can reuse a URL selected manually on the web UI
boss search --url 'https://www.zhipin.com/web/geek/jobs?query=Golang&city=101280100&experience=104,105'

# Step 3: inspect details and continue the workflow
boss detail <security_id>
boss shortlist add <security_id> <job_id>
boss apply <security_id> <job_id>
```

Or submit a shared workflow and use its explicit `run_id` for status and recovery:

```bash
boss --json wizard --input-json '{"role":"candidate","platform":"zhipin","goal":"job_search","inputs":{"query":"Golang","welfare_conditions":["双休"]}}'
boss --json wizard --status <run_id>
boss --json wizard --resume <run_id>
```

Parsing contract:
- Read JSON envelopes from `stdout` only
- `ok=true` means success; when `ok=false`, inspect `error.code` and `error.recovery_action`
- `boss schema` also returns `supported_platforms`, `supported_recruiter_platforms`, and per-command `availability`, so agents can route tools by `role/platform`

### Candidate crawl orchestration

After `uv sync --extra crawl`, crawl uses only the isolated `<data-dir>/crawl/chrome-profile`. Fine-grained CLI commands can create a task and MCP can read/import it; `boss_wizard` can also advance the shared crawl workflow directly:

```text
boss crawl start <query> --city <city> --pages <n>
→ receive run_id
→ boss_crawl_status(run_id)
→ boss_crawl_results(run_id)
→ boss_crawl_shortlist(run_id, all=true)
→ boss_ai_fit(resume)
```

In the CLI, `boss agent crawl --run-id <run_id> --resume <resume-name>` consumes only a completed run and then performs shortlist + ai fit. Use `--query` and `--city` to start a new crawl:

```bash
boss agent crawl --query "AI engineer" --city 杭州 --pages 3 --with-detail --resume <resume-name>
```

Hooks are disabled by default. Only when you have authorization may you explicitly pass `--hook-profile screenshot-full --hook-dir <directory containing SHA256SUMS>`; this project does not redistribute third-party scripts. To halt a task, run `boss crawl stop <run_id>`. On `risk_stopped`, the shared lock in the same `data_dir` blocks new crawls and resumes before browser construction. Keep the `run_id`; the user must confirm recovery on the intended official business page, run `boss clean --risk-lock`, and explicitly run `boss crawl resume <run_id>`. Recovery belongs in `operator_actions`; agents must not clear the lock. Handle the budget before resuming `budget_stopped`; neither state permits recreating tasks or retrying in a loop. Local status/results/imports remain available.

### Recruiter workflow

Recruiter commands cover candidate search, applications, resumes, chats, contact exchange, replies, and job management:

```bash
# Step 1: discover capabilities
boss schema

# Step 2: search candidates, inspect chats, and manage jobs
boss hr candidates "Python" --city 101010100
boss hr chat --page 1
boss hr jobs list
```

Recommended usage:
- Treat the `hr` command group returned by `boss schema` as the source of truth for recruiter capabilities
- `boss hr <subcommand>` switches to recruiter mode automatically, so you do not need to infer `--role` yourself
- Candidate-side and recruiter-side commands share the same `stdout JSON / stderr logs` contract
- `hr` and recruiter-side `agent` automation currently support `zhipin-recruiter` only
- When platform responses map to `ACCOUNT_RISK`, `RATE_LIMITED`, or `ENVIRONMENT_RISK`, stop automated access instead of retrying a batch

## 3) Recovery flow and troubleshooting

Recommended sequence:

```bash
boss doctor
boss logout
boss login
boss status
```

Common recovery actions:
- `AUTH_REQUIRED` / `AUTH_EXPIRED` / `TOKEN_REFRESH_FAILED`: run `boss login` again
- `ACCOUNT_RISK` / `ENVIRONMENT_RISK`: stop automated access and latch local state; codes `31` (IP/environment restriction) / `32` (account restriction) / `35` / `36`, environmental or ambiguous code `37`, HTTP 403 and official `403.html` access-restriction and `verify.html` / `security.html` pages all stop the workflow. `error.details.risk_lock` retains the original reason, available platform code and `explicit_resume_required`
- `ENVIRONMENT_RISK_LOCKED`: the historical environment lock has not been explicitly cleared, so nothing was sent. Token changes, switching httpx / headless / CDP channels and `login --ignore-risk-lock` cannot bypass it. The user must confirm the intended official business page has recovered in the original session, run `boss clean --risk-lock`, and explicitly resume the task. A working homepage does not prove the target page has recovered; local cleanup does not prove server recovery; another risk immediately relatches the lock. Agents must not clear it on their own
- The restriction page's recovery time is a platform hint; do not refresh or unlock automatically. Mobile availability cannot replace acceptance on the intended web page
- Code `37` permits bounded refresh only when credential semantics and explicit expiry or session-expiration semantics appear together. A bare token name, invalid-token wording or a security-verification requirement stops the workflow; security and environmental-risk wording takes precedence over expiry wording
- `wt2` present but `stoken` missing: treat it as partial auth; start Chrome with a CDP debugging port and run `boss login --cdp`, or run `boss login` again
- `RATE_LIMITED`: declared replay-safe reads can use bounded cooldowns; verify write outcomes first
- `REQUEST_BUDGET_EXHAUSTED`: the shared lease, rolling 60-second budget or cooldown exceeded this acquisition's wait limit; the current operation was not sent. Keep the checkpoint, inspect concurrency and `retry_after_seconds`, then explicitly resume without automatic retries or unlocking. A common `data_dir` shares limits by default; `request_budget_dir` explicitly groups directories. See [shared budget settings](commands.en.md#shared-request-budget-and-cooldown). Interrupted confirmation after a write remains `ACTION_UNCONFIRMED`; verify the outcome first
- `NOT_SUPPORTED`: switch to a platform or workflow goal reported as available by schema
- `WORKFLOW_TIMEOUT`: a normal wizard timeout retains its `run_id`; adjust the deadline and explicitly run `boss wizard --resume <run_id>`. If MCP reports `outcome_unknown=true`, verify unconfirmed actions before retrying
- `INVALID_PARAM`: correct the input parameters, such as city, welfare filters, or page number

## 4) Export tool protocols

Ordinary `agent run` never clears recruiter automation stop state. After official verification in the original browser and checks of the blocked page and any unconfirmed actions, the operator can explicitly run `boss --role recruiter agent run --resume`. The MCP parameter `boss_agent_run.resume=true` requires the same explicit recovery instruction. Confirmed action deduplication records are retained.

```bash
boss schema --format openai-tools
boss schema --format anthropic-tools
boss schema --format mcp-tools
```

All three tool formats use the shared runtime catalog of 78 MCP tools, with matching names and input parameters. Native schema continues to describe the CLI's 39 top-level commands.

Further reading:
- [Agent Host Examples](agent-hosts.en.md)
- [Capability Matrix](capability-matrix.en.md)
