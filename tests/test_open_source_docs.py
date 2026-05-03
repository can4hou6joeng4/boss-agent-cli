from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
	return (ROOT / path).read_text(encoding="utf-8")


def test_getting_started_docs_exist_and_cover_happy_path():
	zh = read("docs/getting-started.md")
	en = read("docs/getting-started.en.md")

	for content in (zh, en):
		assert "uv tool install boss-agent-cli" in content
		assert "patchright install chromium" in content
		assert "boss doctor" in content
		assert "boss status" in content
		assert "boss schema --format native" in content
		assert "JSON" in content
		assert "security_id" in content


def test_readme_and_contributing_link_getting_started_docs():
	assert "docs/getting-started.md" in read("README.md")
	assert "docs/getting-started.en.md" in read("README.en.md")
	assert "docs/getting-started.md" in read("CONTRIBUTING.md")
	assert "docs/getting-started.en.md" in read("CONTRIBUTING.en.md")


def test_platform_risk_docs_exist_and_cover_sensitive_boundaries():
	zh = read("docs/platform-risk.md")
	en = read("docs/platform-risk.en.md")

	for content in (zh, en):
		assert "Cookie" in content
		assert "CDP" in content
		assert "patchright" in content
		assert "rate" in content.lower() or "频率" in content
		assert "security_id" in content
		assert "BOSS_SMOKE_DRY_RUN" in content
		assert "redact" in content.lower() or "脱敏" in content


def test_security_and_readme_link_platform_risk_docs():
	assert "docs/platform-risk.md" in read("README.md")
	assert "docs/platform-risk.en.md" in read("README.en.md")
	assert "docs/platform-risk.md" in read("SECURITY.md")


def test_maintainer_docs_cover_open_source_governance():
	branch = read("docs/maintainer/branch-protection.md")
	release = read("docs/maintainer/release-checklist.md")
	labels = read("docs/maintainer/labels.md")

	assert "required status checks" in branch
	assert "test (3.10)" in branch
	assert "test (3.11)" in branch
	assert "test (3.12)" in branch
	assert "test (3.13)" in branch
	assert "lint" in branch
	assert "typecheck" in branch
	assert "docs" in branch
	assert "allow_force_pushes" in branch
	assert "allow_deletions" in branch

	assert "uv run pytest tests/ -q" in release
	assert "uv run ruff check src/ tests/" in release
	assert "uv run mypy src/boss_agent_cli" in release
	assert "uv build" in release
	assert "uv publish" in release
	assert "schema" in release
	assert "redact" in release.lower()

	assert "good first issue" in labels
	assert "platform-drift" in labels
	assert "contract" in labels
	assert "triage" in labels


def test_contributing_links_maintainer_governance_docs():
	assert "docs/maintainer/release-checklist.md" in read("CONTRIBUTING.md")
	assert "docs/maintainer/labels.md" in read("CONTRIBUTING.md")
	assert "docs/maintainer/release-checklist.md" in read("CONTRIBUTING.en.md")
	assert "docs/maintainer/labels.md" in read("CONTRIBUTING.en.md")


def test_pull_request_template_requires_quality_and_risk_checks():
	template = read(".github/PULL_REQUEST_TEMPLATE.md")

	assert "uv run pytest tests/ -q" in template
	assert "uv run ruff check src/ tests/" in template
	assert "uv run mypy src/boss_agent_cli" in template
	assert "docs/platform-risk.md" in template
	assert "docs/maintainer/release-checklist.md" in template
	assert "JSON 信封" in template
	assert "Token / 密码 / Cookie / security_id" in template


def test_issue_templates_collect_contract_and_platform_context():
	bug = read(".github/ISSUE_TEMPLATE/bug_report.yml")
	feature = read(".github/ISSUE_TEMPLATE/feature_request.yml")
	docs = read(".github/ISSUE_TEMPLATE/documentation.yml")

	assert "platform" in bug
	assert "role" in bug
	assert "security_id" in bug
	assert "平台漂移" in bug
	assert "JSON 信封" in bug
	assert "redacted" in bug or "脱敏" in bug

	assert "platform" in feature
	assert "role" in feature
	assert "JSON 信封" in feature
	assert "Agent" in feature

	assert "docs-parity" in docs
	assert "README.en.md" in docs
