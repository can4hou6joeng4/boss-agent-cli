from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ISSUE_FORM_PATHS = [
	".github/ISSUE_TEMPLATE/bug_report.yml",
	".github/ISSUE_TEMPLATE/feature_request.yml",
	".github/ISSUE_TEMPLATE/documentation.yml",
]
SUPPORTED_ISSUE_FORM_TYPES = {"markdown", "input", "textarea", "dropdown", "checkboxes"}


def read(path: str) -> str:
	return (ROOT / path).read_text(encoding="utf-8")


def load_yaml(path: str) -> dict:
	content = yaml.safe_load(read(path))
	assert isinstance(content, dict)
	return content


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
		assert "research/platforms/README.md" in content
		assert "response interception" in content
		assert "risk" in content.lower() or "风险" in content
		assert "Windows app" in content or "Windows 客户端" in content
		assert "RPA" in content
		assert "CloakBrowser" in content
		assert "checkpoint" in content.lower()
		assert "stop" in content.lower() or "停止" in content


def test_security_and_readme_link_platform_risk_docs():
	assert "docs/platform-risk.md" in read("README.md")
	assert "docs/platform-risk.en.md" in read("README.en.md")
	assert "docs/platform-risk.md" in read("SECURITY.md")


def test_readme_documents_browser_bridge_diagnostics():
	zh = read("README.md")
	en = read("README.en.md")

	for content in (zh, en):
		assert "bridge_daemon" in content
		assert "bridge_extension" in content
		assert "bridge_protocol" in content
		assert "bridge_workspace" in content
		assert "bridge_exec" in content
		assert "bridge_fetch" in content
		assert "bridge_navigate" in content
		assert "python -m boss_agent_cli.bridge.daemon --serve" in content
		assert "Bridge" in content
		assert "风控" in content or "risk-control" in content.lower()


def test_platform_research_template_covers_adapter_admission_gate():
	index = read("docs/research/platforms/README.md")
	abstraction_zh = read("docs/platform-abstraction.md")
	abstraction_en = read("docs/platform-abstraction.en.md")

	required_sections = [
		"## 准入原则",
		"## 研究模板",
		"### 3. 只读能力",
		"### 5. 禁止能力",
		"### 9. 验收命令",
		"## 平台准入流程",
		"## 第三方样本使用边界",
	]
	for section in required_sections:
		assert section in index

	for token in (
		"zhipin.md",
		"zhaopin.md",
		"lagou.md",
		"liepin.md",
		"xunjin58/zp_api",
		"stealth",
		"response interception",
		"自动滚动抓取",
		"不能直接复制为主线实现",
		"uv run pytest tests/test_agent_docs.py tests/test_open_source_docs.py -q",
	):
		assert token in index

	assert "research/platforms/README.md" in abstraction_zh
	assert "第三方 scraper" in abstraction_zh
	assert "research/platforms/README.md" in abstraction_en
	assert "Third-party scraper" in abstraction_en


def test_platform_research_docs_include_unified_adapter_evaluation():
	for path in (
		"docs/research/platforms/zhipin.md",
		"docs/research/platforms/zhaopin.md",
		"docs/research/platforms/lagou.md",
		"docs/research/platforms/liepin.md",
	):
		content = read(path)
		assert "统一适配器评估" in content or "适配器基线研究" in content, path
		assert "只读" in content, path
		assert "禁止" in content, path
		assert "stealth" in content, path
		assert "response interception" in content, path
		assert "cookie" in content.lower(), path
		assert "token" in content.lower(), path

def test_maintainer_docs_cover_open_source_governance():
	branch = read("docs/maintainer/branch-protection.md")
	release = read("docs/maintainer/release-checklist.md")
	labels = read("docs/maintainer/labels.md")

	assert "Required status checks and approving reviews are intentionally not configured" in branch
	assert "uv run python scripts/quality_baseline.py" in branch
	assert "BOSS_SMOKE_DRY_RUN=1 uv run python scripts/smoke_p0.py" in branch
	assert "uv run python evals/run_eval.py --mode fixture" in branch
	assert "git diff --check master...HEAD" in branch
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
	assert "commit message 格式: `type: 中文描述`" in template
	assert "Co-authored-by" in template
	assert "AI 署名" in template
	assert "（或英文等价）" not in template


def test_update_contributors_pr_runs_required_checks():
	workflow = load_yaml(".github/workflows/update-contributors.yml")
	create_pr = workflow["jobs"]["update-contributors"]["steps"][2]["with"]

	assert create_pr["commit-message"] == "chore: 更新贡献者名单"
	assert "skip ci" not in create_pr["commit-message"].lower()


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


def test_issue_templates_are_valid_structured_forms():
	for path in ISSUE_FORM_PATHS:
		form = load_yaml(path)

		assert {"name", "description", "title", "labels", "body"} <= set(form)

		body = form["body"]
		assert isinstance(body, list)
		assert body

		ids = []
		for item in body:
			assert isinstance(item, dict)
			assert item.get("type") in SUPPORTED_ISSUE_FORM_TYPES

			if item["type"] != "markdown":
				assert item.get("id")
				ids.append(item["id"])

			if item["type"] in {"dropdown", "checkboxes"}:
				attributes = item.get("attributes")
				assert isinstance(attributes, dict)
				options = attributes.get("options")
				assert isinstance(options, list)
				assert options

		assert len(ids) == len(set(ids))


def test_general_ci_and_docs_workflows_are_intentionally_retired():
	for path in (".github/workflows/ci.yml", ".github/workflows/docs.yml"):
		assert not (ROOT / path).exists(), path


def test_quality_baseline_script_matches_blocking_p0_commands():
	content = read("scripts/quality_baseline.py")

	assert '("ruff", ("ruff", "check", "src/boss_agent_cli", "tests", "scripts", "--output-format=concise"))' in content
	assert '("pytest", ("pytest", "-q"))' in content
	assert '("mypy", ("mypy", "src/boss_agent_cli"))' in content
	assert "--skip-mypy" in content


def test_contributing_clarifies_verification_and_tab_indentation():
	zh = read("CONTRIBUTING.md")
	en = read("CONTRIBUTING.en.md")
	pyproject = read("pyproject.toml")

	for content in (zh, en):
		assert "tab" in content.lower()
		assert "indent-width" in content
		assert "uv run pytest tests/ -q" in content
		assert "uv run ruff check src/ tests/" in content
		assert "uv run mypy src/boss_agent_cli" in content
		assert "uv run boss schema --format native" in content

	assert "# Python files intentionally use tabs" in pyproject
