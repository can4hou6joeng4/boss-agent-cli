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
uv build --out-dir dist/release-check
uv run python scripts/verify_release.py --artifacts dist/release-check --json
uv run python scripts/verify_release.py --artifacts dist/release-check --fresh-install --python 3.11 --require-clean --json
```

Use a dedicated output directory containing exactly one wheel and one sdist for the
current version. The default verification reads archives without extracting them,
checks versions/metadata/file paths, and reports SHA-256 hashes. It does not install
packages or contact a package index. `--fresh-install` explicitly enables dependency
resolution in a temporary environment without `uv.lock`, then checks installed CLI
and MCP behavior without calling the recruitment platform.

The report distinguishes `passed`, `failed`, and `not_run`. A successful default
check with `git.clean=false` is development evidence, not release readiness. Before
publishing, use `--require-clean`; when the local tag already exists, also pass
`--tag vX.Y.Z` to verify its version and commit. The script never creates or pushes
Git tags and does not replace the local quality baseline or fixture eval. Run
`uv run python evals/run_eval.py --mode fixture` in an independent terminal, not
inside an active agent session.

## 5. Publish

Create an annotated release tag only after the local verification above is green:

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
- Verify the published version in a fresh temporary environment (replace `X.Y.Z`):

  ```bash
  uv run python scripts/verify_release.py --pypi-version X.Y.Z --python 3.11 --json
  ```

  This explicitly downloads packages from PyPI, checks CLI/MCP contracts, and
  leaves the user's installed tools and credentials unchanged.
- Create follow-up issues for known limitations instead of hiding them in release notes.
