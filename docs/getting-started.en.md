# Getting Started

This page keeps only the shortest verifiable path. See [README.en.md](../README.en.md) for the full feature overview and [agent-quickstart.en.md](agent-quickstart.en.md) for agent integration.

## 1. Install

```bash
uv tool install boss-agent-cli
patchright install chromium
```

Source checkout:

```bash
git clone https://github.com/can4hou6joeng4/boss-agent-cli.git
cd boss-agent-cli
uv sync --all-extras
uv run patchright install chromium
```

## 2. Local preflight

```bash
boss doctor
boss status
boss schema --format native
```

Expected behavior:

- `boss doctor` returns `ok:true` or an `ok:false` envelope with a clear `recovery_action`.
- `boss status` reports whether the current login state is usable.
- `boss schema --format native` returns the JSON envelope that describes CLI capabilities.

## 3. First read-only command

After login, start with read-only commands before debugging write actions.

```bash
boss search "Golang" --city 广州 --welfare "双休"
boss detail <security_id>
```

`security_id` comes from the `search` JSON response. Redact it in issues. Never paste real `security_id`, cookies, tokens, phone numbers, WeChat IDs, names, or company-private information.

## 4. JSON envelope contract

Agent-readable stdout should be one JSON envelope:

```json
{
	"ok": true,
	"schema_version": "1.0",
	"command": "schema",
	"data": {},
	"pagination": null,
	"error": null,
	"hints": null
}
```

Failure output:

```json
{
	"ok": false,
	"schema_version": "1.0",
	"command": "status",
	"data": null,
	"pagination": null,
	"error": {
		"code": "AUTH_REQUIRED",
		"message": "Not logged in",
		"recoverable": true,
		"recovery_action": "boss login"
	},
	"hints": null
}
```

## 5. Developer verification

Run the same verification matrix before and after code changes:

```bash
uv run pytest tests/ -q
uv run ruff check src/ tests/
uv run mypy src/boss_agent_cli
uv run boss --help
uv run boss schema --format native
```

For documentation-only changes, run at least:

```bash
uv run pytest tests/test_agent_docs.py tests/test_open_source_docs.py -q
git diff --check
```

## 6. Before filing an issue

Bug reports should include:

- `boss --version`
- Python version
- Operating system
- Platform: `zhipin` or `zhilian`
- Role: `candidate` or `recruiter`
- Full redacted JSON envelope
- Redacted `boss doctor` output

For upstream platform drift, login failures, risk control, Cookie/CDP, or browser automation issues, read [platform-risk.en.md](platform-risk.en.md).
