# Contributing

Thanks for your interest in `boss-agent-cli`! This guide is the English companion of [CONTRIBUTING.md](CONTRIBUTING.md) — both describe the same workflow, so pick whichever fits you.

Before your first contribution, complete the local preflight and developer verification in [Getting Started](docs/getting-started.en.md).

## Development Environment

```bash
git clone https://github.com/can4hou6joeng4/boss-agent-cli.git
cd boss-agent-cli
uv sync --all-extras
uv run pytest tests/ -v

# Enable local commit-time quality gate (recommended)
uv run pre-commit install
```

Python **≥ 3.10** is required. We use [`uv`](https://github.com/astral-sh/uv) for dependency management — `uv sync --all-extras` installs runtime + dev deps in a local `.venv`.

## Coding Standards

- Python source indentation uses **tabs**.
- `indent-width = 4` in `pyproject.toml` is the formatter display width. It does not mean Python files should switch to spaces.
- Use Python >= 3.10 and `X | Y` union syntax.
- Command output must preserve the JSON envelope contract: stdout is agent-readable JSON only, stderr is logs and progress.
- Commit messages use the repository Chinese format `type: 中文描述`.
- Type checking is blocking in CI. New code must pass `uv run mypy src/boss_agent_cli`.
  - ✅ `feat: 新增配置管理命令`
  - ❌ `feat: add config command`  (English description)
  - ❌ `feat: 新增 config 命令`  (mixed English/Chinese, forbidden)
  - Do NOT add `Co-authored-by` trailers or any AI-attribution lines

## Local Verification

Run the full matrix before submitting code changes:

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

## Pull Request Workflow

1. **Fork** the repo and clone your fork.
2. **Branch** from `master`: `git checkout -b feat/your-feature`.
3. **TDD first**: write failing tests, then implementation, then run the suite.
4. **Lint + Test** locally:
   ```bash
   uv run ruff check src/ tests/ mcp-server/
   uv run pytest tests/ -q
   ```
5. **Commit** atomically — one logical change per commit.
6. **Push** and open a PR against `master`.
7. **CI green** is a hard prerequisite before merge (4 Python versions × lint × security scan).

Maintainers will `squash merge`, so the squash title must follow the commit convention above.

## Maintainer Docs

- [Release Checklist](docs/maintainer/release-checklist.md)
- [Labels And Triage](docs/maintainer/labels.md)
- [Branch Protection](docs/maintainer/branch-protection.md)

## Adding a New Command

1. Create a file under `src/boss_agent_cli/commands/`
2. Register it in `main.py`
3. Describe it in `src/boss_agent_cli/commands/schema.py` (under `SCHEMA_DATA["commands"]`)
4. Add tests in `tests/test_commands.py` or a new file matching the command name
5. Update `skills/boss-agent-cli/SKILL.md` (command cheat-sheet)
6. Update `AGENTS.md` (command-count invariant)
7. Update `README.md` and `README.en.md` (command reference table)
8. Update the relevant module's `CLAUDE.md`
9. If the command is useful for Agents via MCP, also register it in `src/boss_agent_cli/mcp_server.py` (add a Tool to the `TOOLS` list and a branch in `_build_args`; `mcp-server/server.py` is a thin wrapper that auto re-exports both)

## Output Contract (Do Not Break)

Every command must output a JSON envelope to **stdout**:

```json
{
  "ok": true,
  "schema_version": "1.0",
  "command": "search",
  "data": [...],
  "pagination": {...},
  "error": null,
  "hints": {...}
}
```

- `stdout` — JSON only. Never use `print()` to stdout directly.
- `stderr` — logs and progress (gated by `--log-level`)
- `exit 0` — success (`ok=true`)
- `exit 1` — failure (`ok=false`)

On error, the envelope must contain `error.code`, `error.recoverable`, and `error.recovery_action`. See `SCHEMA_DATA["error_codes"]` in `src/boss_agent_cli/commands/schema.py` (around line 855) for the current enum.

## Testing Philosophy

- **TDD encouraged**: write the test before the implementation. CI coverage is tracked on [Codecov](https://codecov.io/gh/can4hou6joeng4/boss-agent-cli), baseline 80%.
- **Mock external I/O**: `AuthManager`, `BossClient`, `CacheStore`, and `AIService` are mock boundaries — tests should not hit the real BOSS Zhipin API.
- **Error-path parity**: for every success path, add at least one error path test (auth expired, rate-limited, invalid param, etc.).

## Reporting Issues

Pick the matching template under `.github/ISSUE_TEMPLATE/`:

- **bug_report**: attach `boss doctor` output and the version number
- **feature_request**: describe the user scenario and expected behavior
- **documentation**: typos, missing docs, outdated examples

## Non-Code Contributions

You don't need to write code to help:

- Translation (e.g., `README.en.md` improvements)
- Bug reports with reproduction steps
- Usage examples in new Agent hosts (see `docs/integrations/`)
- Benchmark results on different machines / OS / Chrome versions

## Questions?

Open a [Discussion](https://github.com/can4hou6joeng4/boss-agent-cli/discussions) or comment on a related [Issue](https://github.com/can4hou6joeng4/boss-agent-cli/issues).
