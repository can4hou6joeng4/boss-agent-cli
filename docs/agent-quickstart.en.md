# Agent Quickstart

The shortest path for an AI agent to get productive with `boss-agent-cli`: discover capabilities first, then complete a low-risk search, detail, and local-organization loop. Applications, messaging, and candidate handling stay on the official website.

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

## 2) Complete the low-risk agent loop in three steps

```bash
# Step 1: fetch the self-described capability schema
boss schema

# Step 2: search and narrow down target jobs
boss search "Golang" --city 广州 --welfare "双休,五险一金"
# Complex filters can reuse a URL selected manually on the web UI
boss search --url 'https://www.zhipin.com/web/geek/jobs?query=Golang&city=101280100&experience=104,105'

# Step 3: inspect details and organize locally; apply/message manually on the official website
boss detail <security_id>
boss shortlist add <security_id> <job_id>
```

Parsing contract:
- Read JSON envelopes from `stdout` only
- `ok=true` means success; when `ok=false`, inspect `error.code` and `error.recovery_action`
- `boss schema` also returns `supported_platforms`, `supported_recruiter_platforms`, and per-command `availability`, so agents can route tools by `role/platform`

### Recruiter boundary

Default low-risk mode blocks recruiter candidate search, applications, resumes, chats, contact exchange, and replies. The low-risk recruiter surface keeps job-list management available:

```bash
# Step 1: discover capabilities
boss schema

# Step 2: inspect recruiter job listings
boss hr jobs list

# Candidate handling, messaging, and contact exchange should be completed manually on the official website
```

Recommended usage:
- Treat the `hr` command group returned by `boss schema` as the source of truth for recruiter capabilities
- `boss hr <subcommand>` switches to recruiter mode automatically, so you do not need to infer `--role` yourself
- Candidate-side and recruiter-side commands share the same `stdout JSON / stderr logs` contract
- `hr` currently supports `zhipin-recruiter` only; use `boss --platform zhilian --role recruiter agent ...` for Zhaopin recruiter automation
- Default low-risk mode blocks recruiter candidate screening commands such as `hr candidates`, `hr resume`, and `hr request-resume`; complete those candidate-data workflows manually on the official website
- When a sensitive subcommand returns `COMPLIANCE_BLOCKED`, do not switch automation channels to continue
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
- `INVALID_PARAM`: correct the input parameters, such as city, welfare filters, or page number

Further reading:
- [Agent Host Examples](agent-hosts.en.md)
- [Capability Matrix](capability-matrix.en.md)
