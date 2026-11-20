# Getting Started

This page keeps only the shortest verifiable path. See [README.en.md](../README.en.md) for the full feature overview and [agent-quickstart.en.md](agent-quickstart.en.md) for agent integration.

## 1. Install

```bash
uv tool install boss-agent-cli
# Install the browser core only when needed: boss doctor prints a runnable
# install command when the kernel is missing
boss doctor
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

People can run `boss` or `boss wizard` for the role/platform/goal wizard. Non-TTY and agent calls use structured input and never wait for prompts:

```bash
boss --json wizard --input-json '{"role":"candidate","platform":"zhipin","goal":"shortlist","inputs":{}}'
boss --json wizard --status <run_id>
```

### Reuse the session in your everyday Chrome

If you are signed in to BOSS Zhipin in your regular browser, Chrome 144 or later supports local debugging with your approval.

1. In that Chrome, open `chrome://inspect/#remote-debugging` and enable remote debugging. Keep one BOSS Zhipin job list tab open.
2. Run `boss login --existing-chrome` and select **Allow** in Chrome's connection prompt. When the terminal asks, refresh the original BOSS tab or open your own résumé in that tab. From the repository, run `uv run boss login --existing-chrome`.
3. Run `boss status` to check the saved session. The import waits for the tab's own authentication response and encrypts the verified credentials locally.

If BOSS shows a security page, follow the [login troubleshooting guide](troubleshooting.en.md#login-issues). If Chrome cannot connect, check its debugging setting and prompt. The command leaves your original tab open.

## 3. First platform command

After login, verify the platform path with search and detail before continuing to application, messaging, or recruiter workflows.

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
- Platform: `zhipin`
- Role: `candidate` or `recruiter`
- Full redacted JSON envelope
- Redacted `boss doctor` output

For upstream platform drift, login failures, risk control, Cookie/CDP, or browser automation issues, read [platform-risk.en.md](platform-risk.en.md).
