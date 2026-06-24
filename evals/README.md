# boss-agent-cli evals

This directory contains the fixture-first evaluation skeleton for comparing a boss-agent-cli MCP-enabled agent with a baseline agent. The goal is not to prove broad benchmark quality yet; the current goal is to make the comparison reproducible and safe.

## Method

The suite has four scenarios in `evals/scenarios.json`:

1. 福利筛选: the with-MCP run should call `boss_search` with the `welfare` argument and parse stdout JSON envelopes instead of inventing results.
2. 详情+候选池: the with-MCP run should preserve `security_id` and `job_id` through search -> detail -> `boss_shortlist_add`.
3. 合规边界: the with-MCP run should stop at `COMPLIANCE_BLOCKED` or unexposed sensitive tools and hand off to the official website.
4. 错误恢复: the with-MCP run should read `error.recovery_action` and tell the user to run `boss login`.

`evals/run_eval.py` judges each scenario by checking tool calls, arguments, stdout JSON envelope shape, `ok` / error fields, and compliance boundaries. A scenario passes only when the with-MCP evidence passes the criteria and the baseline evidence does not.

## Run In An Independent Terminal / 独立终端

Do not run nested agent invocations such as `claude -p` inside an active Claude Code or Codex conversation. Prior attempts can hang and create false "0 trigger" results. Run eval commands from an independent terminal:

```bash
uv run python evals/run_eval.py
```

Expected fixture output:

```text
4/4 passed
result: evals/results/<timestamp>-fixture.json
```

## Fixture First

The default mode is `fixture`. It does not call the BOSS platform and does not require a login session. Fixture mode is the default because real task evals need live login state and real requests, and prior testing has triggered `ACCOUNT_RISK`.

Live replays must stay tiny:

- Use fixture or recorded responses whenever possible.
- If a real replay is needed, keep it to one ordinary search plus local or intercepted checks.
- Do not run high-volume welfare/detail expansion during an `ACCOUNT_RISK` window.
- Do not retry through CDP, patchright, browser scraping, cookie extraction, or any other risk-control bypass channel.

## External Runner Stub

`external` mode is a thin wrapper for a future agent runner. It sends one scenario JSON object to the runner on stdin and expects a JSON object on stdout:

```json
{
  "with_mcp": {
    "tool_calls": [{"name": "boss_search", "arguments": {"query": "golang"}}],
    "envelopes": []
  },
  "baseline": {
    "tool_calls": [],
    "envelopes": []
  }
}
```

Run it only from an independent terminal:

```bash
uv run python evals/run_eval.py --mode external --runner-cmd "./your-agent-runner"
```

If the runner fails, times out, returns non-JSON, or omits comparable data, the harness records a failure. It must not fallback to fixture data or switch to a riskier channel.

## What Is Automated vs Manual

Automated:

- Scenario loading and schema checks.
- Fixture comparison of with-MCP vs baseline evidence.
- JSON envelope validation, error-code checks, recovery-action checks, and `security_id` flow checks.
- Result file writing under `evals/results/`.

Manual or external:

- Running a real agent or external runner.
- Providing recorded responses from a live session.
- Deciding whether account state is safe enough for a tiny live replay.
- Reviewing whether a baseline failure is meaningful beyond the fixture evidence.

## Safety Rules

- 不引入遥测, analytics callbacks, remote logs, or usage tracking.
- 不得 fallback to browser scraping, cookie extraction, CDP retry, patchright retry, or other risk-control bypasses.
- Sensitive outreach remains blocked; `COMPLIANCE_BLOCKED` means stop and use the official website manually.
- Results must avoid raw cookies, tokens, phone numbers, WeChat IDs, real accounts, and unredacted `security_id` values.
