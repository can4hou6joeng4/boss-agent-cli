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

## Adoption Metrics & Telemetry

Project adoption is measured only through two passive sources: PyPI downloads and GitHub Insights (stars / clones / traffic).

Do not add any runtime telemetry, usage instrumentation, anonymous analytics callbacks, or remote logs, even when optional or anonymized. Except for explicit API calls initiated by the user, data should not leave the local machine; telemetry would break that commitment.

Before investing in promotion, examples, or integrations, check those passive signals instead of collecting usage data through telemetry.

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
2. Register it in `commands/register.py` (`register_candidate_commands` / `register_recruiter_commands`; `main.py` only holds global options and does not attach commands directly)
3. Describe it in `src/boss_agent_cli/commands/schema.py` (under `SCHEMA_DATA["commands"]`); when the top-level command count changes, also update the "共 N 个顶层命令" count inside `SCHEMA_DATA["description"]` (a test asserts the count matches the command table length)
4. Add tests in `tests/test_commands.py` or a new file matching the command name
5. Update `docs/commands.md` and `docs/commands.en.md` (command cheat-sheet)
6. Update `AGENTS.md` (command-count invariant) and the command counts in `docs/capability-matrix.md` / `docs/capability-matrix.en.md`; `tests/test_agent_docs.py` hard-codes these count strings, so update it in the same change
7. Update `README.md` and `README.en.md` (command reference table)
8. Update the relevant module's `CLAUDE.md`
9. If the command is useful for Agents via MCP, also register it in `src/boss_agent_cli/mcp_server.py` (add a Tool to the `TOOLS` list and a branch in `_build_args`); when the tool name does not map onto its compliance command identifier (e.g. `boss_hr_*` tools map to `recruiter-*`), register it in `_MCP_TOOL_COMPLIANCE_COMMAND_OVERRIDES` or the low-risk filtering will not apply; `mcp-server/server.py` is a hand-maintained re-export list — add a line for each new public symbol; when the tool count changes, update "MCP server with N tools" in `README.en.md` (a test asserts it against `len(TOOLS)`)

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

On error, the envelope must contain `error.code`, `error.recoverable`, and `error.recovery_action`. See `SCHEMA_DATA["error_codes"]` in `src/boss_agent_cli/commands/schema.py` for the current enum.

## Testing Philosophy

- **TDD encouraged**: write the test before the implementation. CI coverage is tracked on [Codecov](https://codecov.io/gh/can4hou6joeng4/boss-agent-cli), baseline 80%.
- **Mock external I/O**: `AuthManager`, `BossClient`, `CacheStore`, and `AIService` are mock boundaries — tests should not hit the real BOSS Zhipin API.
- **Error-path parity**: for every success path, add at least one error path test (auth expired, rate-limited, invalid param, etc.).

## Adding an AI Provider

`boss ai` keeps a table of OpenAI-compatible providers in `PROVIDER_BASE_URLS` (`src/boss_agent_cli/ai/config.py`). New providers are welcome, **including PRs authored by the provider's own engineers** — that is how `atlas` got here.

But every row transfers a maintenance liability to this project: when a provider changes domains, changes pricing, or retires a model, the docs go stale silently and no test can catch it. Hence the three rules below.

### 1. Docs carry only falsifiable claims

Entries in `docs/integrations/ai-models.md` describe **capability boundaries only** — OpenAI compatibility, the `provider/model` namespace, which models are reachable, and that the authoritative model list is whatever the server actually supports.

Not accepted: security-posture claims ("security gateway", "guardrails", "zero-trust", "tool governance"), pricing promises ("zero-markup", "cheapest"), ratings or rankings, and scale figures that go stale silently ("200+ models"). The test is simple: **can this sentence be falsified with a single API call?** If not, it does not belong in first-party docs.

This applies to **all** entries, including ones already in the table — existing entries are being brought in line, not just new submissions.

### 2. Vendor-authored entries need verifiable attribution

If you work for the provider and are submitting your own company's entry, please include one verifiable link in the PR — either:

- a commit authored from a corporate-domain address (`@yourcompany.com`), **or**
- a public reference from the provider's own site or GitHub org pointing at this repo / this PR

This is not about trust; it is about maintainability — when the endpoint moves, someone needs to be reachable. There is precedent: for one earlier provider, both the PR and the author's account later disappeared from GitHub, and the orphaned entry was then silently deleted by an unrelated refactor and went unnoticed for a long time.

This rule is not applied retroactively.

### 3. Removal policy

A provider endpoint that is **unavailable across two consecutive minor releases** is removed from `PROVIDER_BASE_URLS` outright — no deprecation cycle, no advance notice, and **not treated as a breaking change**.

This rule is what lets the first two stay permissive: because removal is cheap, admission does not have to be strict.

### Chain updates

Adding a provider touches five places (`boss schema` does not enumerate providers, so it needs no change):

1. `PROVIDER_BASE_URLS` in `src/boss_agent_cli/ai/config.py`
2. The `--provider` help text in `src/boss_agent_cli/commands/ai_cmd.py` — it is the only documentation of the value domain, because that option has no `click.Choice` validation
3. `docs/integrations/ai-models.md` and `docs/integrations/ai-models.en.md` (both languages must stay in sync)
4. `tests/test_ai_config.py`: a base-URL resolution test plus the membership assertion in `test_provider_base_urls_completeness`
5. The `[Unreleased]` section of `CHANGELOG.md`

> **Watch out for brand-adjacent names.** `--provider` accepts a free string, so a typo raises nothing. `boss ai config` echoes `resolved_base_url`, and `tests/test_ai_config_cmd.py` asserts that all `*router` providers resolve to distinct endpoints — if your provider's name is close to an existing one, confirm that test still passes.

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
