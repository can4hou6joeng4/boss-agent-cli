# Smoke Testing

`scripts/smoke_p0.py` is a gated real-flow smoke harness for the read-only P0 path.

It is intentionally manual-first. Do not run it automatically against a real account in public CI.

## Covered Steps

- `doctor`: validates local environment and login prerequisites.
- `status`: validates that the local encrypted login state can be read and used.
- `search`: validates a minimal job discovery query.
- `detail`: validates a known job detail lookup entrypoint.

## Environment Controls

| Variable | Default | Purpose |
|---|---|---|
| `BOSS_SMOKE_PLATFORM` | `zhipin` | Platform adapter to test. Use `zhilian` for the Zhilian candidate path. |
| `BOSS_SMOKE_QUERY` | `golang` | Query used by the `search` step. Required for live search confidence. |
| `BOSS_SMOKE_SECURITY_ID` | `demo-security-id` | Security ID used by the `detail` step. Use a fresh value from a recent search result. |
| `BOSS_SMOKE_TIMEOUT` | `30` | Per-command timeout in seconds. |
| `BOSS_SMOKE_DRY_RUN` | unset | Set to `1`, `true`, or `yes` to print planned steps without executing commands. |

## Safe Local Usage

Dry run:

```bash
BOSS_SMOKE_DRY_RUN=1 uv run python scripts/smoke_p0.py
```

Zhipin live read-only smoke:

```bash
BOSS_SMOKE_QUERY=golang \
BOSS_SMOKE_SECURITY_ID=<security_id_from_recent_search> \
uv run python scripts/smoke_p0.py
```

Zhilian live read-only smoke:

```bash
BOSS_SMOKE_PLATFORM=zhilian \
BOSS_SMOKE_QUERY=java \
BOSS_SMOKE_SECURITY_ID=<security_id_from_recent_search> \
uv run python scripts/smoke_p0.py
```

## Output Shape

The script prints one JSON object:

```json
{
  "steps": [
    {
      "name": "status",
      "purpose": "验证本地登录态是否存在且可读取",
      "platform": "zhipin",
      "preconditions": ["command:boss"],
      "failure_classification": "env_error",
      "command": ["boss", "status"],
      "status": "pass",
      "ok": true,
      "error_code": null,
      "recovery_action": null,
      "returncode": 0,
      "detail": ""
    }
  ]
}
```

## Status Meanings

- `pass`: command returned a valid JSON envelope with `ok=true`.
- `env_error`: required local setup is missing, or an auth/environment command returned `ok=false`.
- `command_error`: command returned a valid JSON envelope with `ok=false`.
- `contract_error`: stdout was not a valid boss JSON envelope.
- `timeout`: command exceeded `BOSS_SMOKE_TIMEOUT`.
- `dry_run`: command was intentionally not executed.

## Privacy And CI Policy

- 不提交真实 Cookie、token、security_id、联系人姓名、公司名称或原始 stdout 到仓库。
- Use fresh local values for `BOSS_SMOKE_SECURITY_ID`; do not commit them into scripts or docs.
- Smoke output redacts the `detail` command argument so live `security_id` values are not printed by the harness.
- Public CI should run unit tests for the smoke harness, not live platform smoke.
- Private/manual CI may run smoke only with an explicitly provisioned account and redacted logs.

## Intended Use

- local pre-release validation;
- manual debugging checkpoints;
- release-candidate verification;
- future private CI smoke expansion.
