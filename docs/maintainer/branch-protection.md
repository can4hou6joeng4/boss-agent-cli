# Branch Protection

The default branch is `master`. Pull requests and pushes to `master` run the
`CI` workflow (`.github/workflows/ci.yml`): ruff, mypy, pre-commit, a CLI/schema
smoke check, and the offline pytest matrix on Python 3.11 / 3.12 / 3.13 / 3.14. It uses
a read-only token, no secrets, and works for fork PRs. Maintainers must still run
the local quality gate below before merging.

## Required settings

Classic branch protection on `master`:

- Enforce rules for administrators.
- Disable force pushes.
- Disable branch deletions.
- Require conversation resolution when review threads are used for blocking feedback.

The `master` repository ruleset (active since 2026-10-09) carries the merge gate:

- Squash merge only.
- Required status checks, matched by CI job name:

  ```text
  Lint & typecheck
  Test (Python 3.11)
  Test (Python 3.12)
  Test (Python 3.13)
  Test (Python 3.14)
  ```

  Renaming a job or changing the Python matrix in `ci.yml` without updating the
  ruleset leaves a required check that never reports, which blocks every pull
  request.
- Review threads must be resolved before merging.
- Repository administrators are bypass actors.

Approving reviews are intentionally not required. A green remote check is not
evidence that a pull request is ready: the maintainer records local
verification in the pull request before merging.

### Pull requests whose CI waits for approval

GitHub holds `pull_request` runs for first-time contributors from forks and for
pull requests opened by the `update-contributors` workflow with `GITHUB_TOKEN`.
Until a maintainer approves the waiting run, the required checks never report
and the pull request stays blocked:

```bash
gh api -X POST repos/can4hou6joeng4/boss-agent-cli/actions/runs/<run_id>/approve
```

If a fork branch is based on an older `master` whose CI job names differ from
the required checks, update the branch first, then approve the new run:

```bash
gh api -X PUT repos/can4hou6joeng4/boss-agent-cli/pulls/<number>/update-branch \
	-f expected_head_sha=<head_sha>
```

A manual `workflow_dispatch` run of `ci.yml` on the branch does not attach its
checks to the pull request.

## Workflow rules

Workflows under `.github/workflows/` hold write tokens and, for `release.yml`,
the PyPI token. `tests/test_open_source_docs.py` enforces these rules offline,
so a pull request that breaks one fails the required checks:

- Every `uses:` is pinned to a full commit SHA with the exact version in a
  trailing comment: `actions/checkout@<40-hex-sha> # v7.0.1`. A tag such as
  `@v7` can be moved upstream to any commit; a SHA cannot. Dependabot reads the
  comment and keeps updating both.
- `run:` scripts never expand `${{ ... }}`. The expression is substituted
  before the shell parses the script, so quotes in a tag or branch name become
  code. Pass the value through `env:` or use the runner's own variable
  (`$GITHUB_REF_NAME`).
- A `pull_request_target` workflow never checks out or references the pull
  request head. `update-contributors.yml` checks out the base branch;
  `labeler.yml` and `dependabot-auto-merge.yml` check out nothing.
- `release.yml` restores no caches (`enable-cache: false`, no `actions/cache`).
  CI on `master` writes the uv cache, and a release build that restored it
  would run whatever that cache contains next to the PyPI token.
- `actions/checkout` sets `persist-credentials` explicitly. It is `true` only
  in the jobs listed in `CHECKOUTS_THAT_PUSH` in that test file, which push a
  commit or a branch afterwards.

To pin a new action, resolve the tag and copy the SHA:

```bash
gh api repos/<owner>/<repo>/commits/<tag> --jq .sha
```

An optional local audit covers more than the tests do:

```bash
uvx zizmor==1.25.2 --persona regular --offline .github/workflows
```

Four findings are expected and accepted: `dangerous-triggers` on the two
`pull_request_target` workflows above, and `use-trusted-publishing` and
`superfluous-actions` on `release.yml`. Moving to PyPI Trusted Publishing needs
the publisher registered on pypi.org before the token is removed from the
workflow.

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

Classic branch protection does not configure required status checks; the
ruleset owns them. This prints `true`:

```bash
gh api repos/can4hou6joeng4/boss-agent-cli/branches/master/protection \
	--jq '.required_status_checks == null'
```

Then list the ruleset's required checks:

```bash
gh api repos/can4hou6joeng4/boss-agent-cli/rules/branches/master \
	--jq '.[] | select(.type == "required_status_checks") | .parameters.required_status_checks[].context'
```

The output must equal the list under "Required settings". `tests/test_open_source_docs.py`
checks that this list matches the job names in `ci.yml`; update the ruleset,
this runbook, and that test in the same pull request.
