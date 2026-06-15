# Branch Protection

The default branch is `master`. Keep branch protection aligned with the quality bar described in `AGENTS.md`.

## Required Settings

- Require a pull request before merging.
- Require at least 1 approving review.
- Enforce rules for administrators.
- Disable force pushes.
- Disable branch deletions.
- Require conversation resolution when review threads are used for blocking feedback.
- Require status checks before merging.

## Required status checks

Select the concrete check contexts emitted by `.github/workflows/ci.yml`:

- `P0 quality baseline`
- `test (3.10)`
- `test (3.11)`
- `test (3.12)`
- `test (3.13)`
- `lint`
- `typecheck`

`P0 quality baseline` is the canonical blocking quality gate. It runs `scripts/quality_baseline.py`, which covers ruff, the full offline pytest suite, and mypy with the same command used locally.

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

Then verify the pull request review gate through its dedicated endpoint:

```bash
gh api repos/can4hou6joeng4/boss-agent-cli/branches/master/protection/required_pull_request_reviews
```

The response should show:

```json
{
	"required_approving_review_count": 1
}
```

If `required_status_checks` is missing, configure required status checks in GitHub repository settings before treating branch protection as complete.
