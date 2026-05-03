# Release Checklist

Use this checklist before publishing a release tag.

## 1. Confirm scope

- The release has a single coherent theme.
- Breaking changes are called out in `CHANGELOG.md`.
- CLI JSON envelope changes are documented.
- `boss schema --format native` still describes the released capability surface.

## 2. Local verification

```bash
uv sync --all-extras
uv run pytest tests/ -q
uv run ruff check src/ tests/
uv run mypy src/boss_agent_cli
uv run boss --help
uv run boss schema --format native
BOSS_SMOKE_DRY_RUN=1 uv run python scripts/smoke_p0.py
git diff --check
```

## 3. Sensitive data check

- No cookies, tokens, phone numbers, WeChat IDs, real names, company-private data, or live `security_id` values appear in commits.
- Issue and smoke-test examples use redacted placeholders.
- Release notes do not include raw live command output.
- Demo files do not expose private account information.

## 4. Package check

```bash
uv build
```

Inspect generated artifacts:

```bash
python -m tarfile -l dist/*.tar.gz | sed -n '1,80p'
python -m zipfile -l dist/*.whl | sed -n '1,80p'
```

## 5. Publish

Create an annotated release tag only after CI is green:

```bash
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin vX.Y.Z
```

The release workflow runs tests, builds the package, creates a GitHub Release, and publishes to PyPI with:

```bash
uv publish
```

## 6. Post-release

- Confirm the GitHub Release exists.
- Confirm PyPI shows the new version.
- Confirm `uv tool install --upgrade boss-agent-cli` can install the release.
- Create follow-up issues for known limitations instead of hiding them in release notes.
