# Branch Protection

The default branch is `master`. Keep branch protection aligned with the quality bar described in `AGENTS.md`.

## Required Settings

- Require a pull request before merging.
- Enforce rules for administrators.
- Disable force pushes.
- Disable branch deletions.
- Require conversation resolution when review threads are used for blocking feedback.
- Require status checks before merging.

> Approving reviews are intentionally **not** required. This is a single-maintainer
> repository with `enforce_admins` enabled, so requiring an approval would make the
> maintainer's own release and fix PRs unmergeable. The blocking gate here is the
> status-check set below plus admin enforcement, not human approval.

## Required status checks

Select the concrete check contexts emitted by `.github/workflows/ci.yml`:

- `P0 quality baseline`
- `test (3.10)`
- `test (3.11)`
- `test (3.12)`
- `test (3.13)`
- `test (3.14)`
- `lint`
- `typecheck`
- `docker`

`P0 quality baseline` is the canonical blocking quality gate. It runs `scripts/quality_baseline.py`, which covers ruff (`src/boss_agent_cli`, `tests`, `scripts`), the full offline pytest suite, and mypy with the same command used locally. The same job also runs `scripts/smoke_p0.py` in offline dry-run mode so the JSON-envelope smoke contract is checked on every push and pull request.

`docker` builds the container image and verifies the CLI entry point, the MCP stdio handshake, and that the image runs as non-root. It exists so the shipped `Dockerfile` cannot rot silently the way an unreferenced one did before.

The project may also require documentation checks when `.github/workflows/docs.yml` is enabled:

- `docs`

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

Then verify that the required status check contexts match the CI matrix:

```bash
gh api repos/can4hou6joeng4/boss-agent-cli/branches/master/protection \
  --jq '.required_status_checks.contexts'
```

Every `test (3.x)` entry in `.github/workflows/ci.yml` must appear in that list. When a
Python version is added to the matrix, add its context **after** the new job has passed
at least once on `master` — marking a context that has never reported as required will
block every subsequent merge.

If `required_status_checks` is missing, configure required status checks in GitHub repository settings before treating branch protection as complete.
