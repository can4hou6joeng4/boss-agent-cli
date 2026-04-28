# Agent Quickstart

The shortest path for an AI agent to get productive with `boss-agent-cli`: discover capabilities first, then complete a search-to-action loop. Recruiter workflows use the same JSON contract through `boss hr`.

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
- `boss status` returns the current authenticated session
- If you are using `zhilian`, pass the platform explicitly: `boss --platform zhilian doctor && boss --platform zhilian login`

If you plan to wire the CLI into an agent host instead of running commands manually in a terminal, start with [Agent Host Examples](agent-hosts.en.md).

## 2) Complete the minimal agent loop in three steps

```bash
# Step 1: fetch the self-described capability schema
boss schema

# Step 2: search and narrow down target jobs
boss search "Golang" --city 广州 --welfare "双休,五险一金"

# Step 3: inspect details and take action
boss detail <security_id>
boss greet <security_id> <job_id>
```

Parsing contract:
- Read JSON envelopes from `stdout` only
- `ok=true` means success; when `ok=false`, inspect `error.code` and `error.recovery_action`
- `boss schema` also returns `supported_platforms`, `supported_recruiter_platforms`, and per-command `availability`, so agents can route tools by `role/platform`

### Minimal recruiter loop

If your agent operates for recruiters or hiring teams, use `boss hr` directly:

```bash
# Step 1: discover capabilities
boss schema

# Step 2: access recruiter-side workflows
boss hr applications
boss hr candidates "Golang"

# Step 3: contact candidates
boss hr reply <friend_id> "你好，方便聊一下岗位吗？"
boss hr request-resume <friend_id> --job-id <job_id>
```

Recommended usage:
- Treat the `hr` command group returned by `boss schema` as the source of truth for recruiter capabilities
- `boss hr <subcommand>` switches to recruiter mode automatically, so you do not need to infer `--role` yourself
- Candidate-side and recruiter-side commands share the same `stdout JSON / stderr logs` contract
- `hr` currently supports `zhipin-recruiter` only; if the active platform is `zhilian`, recruiter commands are rejected intentionally

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
- `RATE_LIMITED`: wait and retry
- `INVALID_PARAM`: correct the input parameters, such as city, welfare filters, or page number

Further reading:
- [Agent Host Examples](agent-hosts.en.md)
- [Capability Matrix](capability-matrix.en.md)
