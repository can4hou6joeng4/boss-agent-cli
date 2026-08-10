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
- If you are using `zhilian`, pass the platform explicitly: `boss --platform zhilian doctor && boss --platform zhilian login`

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

Hooks are disabled by default. Only when you have authorization may you explicitly pass `--hook-profile screenshot-full --hook-dir <directory containing SHA256SUMS>`; this project does not redistribute third-party scripts. To halt a task, run `boss crawl stop <run_id>`. When `crawl_status` reports `risk_stopped` or `budget_stopped`, do not recreate the task or retry in a loop. Keep the `run_id`; after handling the page, run `boss crawl resume <run_id>`.

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
- `hr` currently supports `zhipin-recruiter` only; use `boss --platform zhilian --role recruiter agent ...` for Zhaopin recruiter automation
- When platform responses map to `ACCOUNT_RISK` or `RATE_LIMITED`, stop automated access instead of retrying a batch

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
- `wt2` present but `stoken` missing: treat it as partial auth; start Chrome with a CDP debugging port and run `boss login --cdp`, or run `boss login` again
- `RATE_LIMITED`: wait and retry
- `NOT_SUPPORTED`: switch to a platform or workflow goal reported as available by schema
- `WORKFLOW_TIMEOUT`: retain the `run_id`, adjust the timeout, and run `boss wizard --resume <run_id>`
- `INVALID_PARAM`: correct the input parameters, such as city, welfare filters, or page number

## 4) Export tool protocols

```bash
boss schema --format openai-tools
boss schema --format anthropic-tools
boss schema --format mcp-tools
```

Further reading:
- [Agent Host Examples](agent-hosts.en.md)
- [Capability Matrix](capability-matrix.en.md)
