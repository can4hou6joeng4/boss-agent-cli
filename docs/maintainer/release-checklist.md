# Release Checklist

Use this checklist before publishing a release tag.

## 1. Confirm scope

- The release has a single coherent theme.
- Breaking changes are called out in `CHANGELOG.md`.
- CLI JSON envelope changes are documented.
- `boss schema --format native` still describes the released capability surface.
- `llms.txt` 的 `Current version` 与能力计数（顶层命令 / 一级招聘者子命令 / MCP 工具）已随本次发版更新；`tests/test_agent_docs.py` 会对照代码校验，漏改会直接失败。
- `SECURITY.md` 的支持版本表已更新到当前 minor（< 当前 minor 标为不再支持）；`tests/test_open_source_docs.py` 会校验。
- GitHub Pages 站点（`demo/index.html` 的版本链接与指标、`demo/llms.txt` 的版本与计数）已同步；`tests/test_agent_docs.py` 会校验（`demo/keynote-animation` 等发布素材是历史版本，不随发版修改）。
- `ROADMAP.md` / `ROADMAP.en.md` 的「已发布」列表已加入当前版本的摘要（`tests/test_agent_docs.py` 会校验当前版本出现在列表里）。

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

Every archive member must also correspond to a git-tracked path (`PKG-INFO` is the
build-generated exception). The build backend reads only the project-root
`.gitignore`, not `.git/info/exclude`, so locally excluded drafts, assets or tests
silently land in the sdist — a local v3.3.0 build grew the source archive from
6.5 MB to 78 MB this way while `--require-clean` still passed. Build release
artifacts from a clean worktree (or verify before publishing) so the local result
matches CI.

The report distinguishes `passed`, `failed`, and `not_run`. A successful default
check with `git.clean=false` is development evidence, not release readiness. Before
publishing, use `--require-clean`; when the local tag already exists, also pass
`--tag vX.Y.Z` to verify its version and commit. The script never creates or pushes
Git tags and does not replace the local quality baseline or fixture eval. Run
`uv run python evals/run_eval.py --mode fixture` in an independent terminal, not
inside an active agent session.

The tag-triggered `release.yml` runs the same artifact check
(`--artifacts dist --tag "$GITHUB_REF_NAME" --require-clean`) between `uv build`
and `uv publish`, so a tag that does not match `pyproject.toml` fails before
anything reaches PyPI.
The release build installs dependencies without restoring any cache; the
reasons are in `docs/maintainer/branch-protection.md` under "Workflow rules".

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
