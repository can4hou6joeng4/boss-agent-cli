# Branch Protection

The default branch is `master`. Pull requests and pushes to `master` run the
`CI` workflow (`.github/workflows/ci.yml`): ruff, mypy, pre-commit, a CLI/schema
smoke check, and the offline pytest matrix on Python 3.11 / 3.12 / 3.14. It uses
a read-only token, no secrets, and works for fork PRs. Maintainers must still run
the local quality gate below before merging.

## Required settings

- Enforce rules for administrators.
- Disable force pushes.
- Disable branch deletions.
- Require conversation resolution when review threads are used for blocking feedback.

Required status checks and approving reviews are intentionally not configured.
The absence of a remote check is not evidence that a pull request passed: the
maintainer records local verification in the pull request before merging.

## Local quality gate

Run the canonical local baseline from a clean worktree rebased onto the current
`master`:

```bash
uv sync --all-extras
uv run python scripts/quality_baseline.py
BOSS_SMOKE_DRY_RUN=1 uv run python scripts/smoke_p0.py
uv run python evals/run_eval.py --mode fixture
git diff --check master...HEAD
```

`scripts/quality_baseline.py` covers ruff (`src/boss_agent_cli`, `tests`,
`scripts`), the full offline pytest suite, and mypy. Changes to packaging,
Docker, release automation, or platform-specific launchers require their own
focused verification in addition to this baseline.

## Verification

Run the full branch protection check first:

```bash
gh api repos/can4hou6joeng4/boss-agent-cli/branches/master/protection
```

The response should show:

```json
{
	"allow_force_pushes": {
		"enabled": false
	},
	"allow_deletions": {
		"enabled": false
	}
}
```

Then verify that no required status checks are configured:

```bash
gh api repos/can4hou6joeng4/boss-agent-cli/branches/master/protection \
	--jq '.required_status_checks // null'
```

The expected result is `null`. CI is back (restored 2026-10-09) but required
check contexts remain intentionally unconfigured; if they are ever added, update
this runbook and the repository's governance tests in the same pull request.
