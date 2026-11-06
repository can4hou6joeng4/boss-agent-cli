import re
from importlib.metadata import metadata
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


def _github_slug(text: str) -> str:
	text = text.strip().lower()
	text = re.sub(r"[^\w一-鿿\- ]", "", text)
	return text.replace(" ", "-")


def test_commands_doc_toc_matches_headings():
	"""命令参考的目录锚点必须都有对应标题。

	`docs/commands.md` 有 300+ 行，没有目录时只能整页滚动；目录本身也会随章节
	改名而漂移，所以这里校验每个锚点都能落到真实标题上。
	"""
	for path, marker in (("docs/commands.md", "## 目录"), ("docs/commands.en.md", "## Contents")):
		content = read(path)
		assert marker in content, f"{path} 缺少目录"
		section = content.split(marker, 1)[1].split("\n## ", 1)[0]
		links = re.findall(r"\]\(#([^)]+)\)", section)
		assert len(links) >= 5, f"{path} 目录条目过少"
		slugs = {_github_slug(heading) for heading in re.findall(r"^## (.+)$", content, re.M)}
		missing = [anchor for anchor in links if anchor not in slugs]
		assert not missing, f"{path} 目录锚点无对应标题: {missing}"


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


def test_readme_documents_browser_bridge_removal_and_manual_cleanup():
	zh = read("README.md")
	en = read("README.en.md")

	for content in (zh, en):
		assert "3.0.0" in content
		assert "Bridge" in content
		assert "daemon" in content
		assert "existing-browser" in content
		assert "CDP" in content
		assert "python -m boss_agent_cli.bridge.daemon --serve" not in content
		assert "风控" in content or "risk-control" in content.lower()
	assert "停止" in zh
	assert "扩展" in zh
	assert "移除" in zh or "卸载" in zh
	assert "stop" in en.lower()
	assert "extension" in en.lower()
	assert "remove" in en.lower() or "uninstall" in en.lower()


def test_removed_bridge_is_not_shipped_or_advertised_as_an_extra():
	"""源码、扩展入口及安装元数据均不得继续提供已下线组件。"""
	for path in (
		"src/boss_agent_cli/bridge/__init__.py",
		"src/boss_agent_cli/bridge/client.py",
		"src/boss_agent_cli/bridge/daemon.py",
		"src/boss_agent_cli/bridge/protocol.py",
		"extension/manifest.json",
		"extension/background.js",
		"extension/popup.html",
	):
		assert not (ROOT / path).exists(), path
	assert "bridge" not in metadata("boss-agent-cli").get_all("Provides-Extra", [])


def test_release_workflow_verifies_artifacts_before_publishing():
	"""发版工作流必须先跑 `scripts/verify_release.py`，再发布到 PyPI。

	`uv build` 不校验 tag 与 pyproject 版本是否一致，也不检查产物里是否混入本地
	文件；tag 打错时 `uv publish` 会照常把错误产物推上 PyPI。校验脚本早已实现这
	些检查，但工作流此前没有调用它。
	"""
	content = read(".github/workflows/release.yml")

	assert "scripts/verify_release.py" in content
	assert '--tag "${{ github.ref_name }}"' in content
	assert "--require-clean" in content
	assert content.index("scripts/verify_release.py") < content.index("uv publish")


def test_architecture_docs_exist_and_are_linked():
	"""公开架构文档必须存在、中英同步，并从 README / CONTRIBUTING 可达。

	架构知识此前只存在于本地 CLAUDE.md（gitignore）与代码注释里，外部贡献者和
	Agent 宿主看不到四条入口、信封契约与风控边界之间的关系。
	"""
	zh = read("docs/architecture.md")
	en = read("docs/architecture.en.md")

	assert zh.startswith("# 架构")
	assert en.startswith("# Architecture")
	for content in (zh, en):
		assert "boss schema" in content
		assert "stdout" in content
		assert "MCP" in content

	assert "[架构](docs/architecture.md)" in read("README.md")
	assert "[Architecture](docs/architecture.en.md)" in read("README.en.md")
	assert "docs/architecture.md" in read("CONTRIBUTING.md")
	assert "docs/architecture.en.md" in read("CONTRIBUTING.en.md")

	for path, content in (("docs/architecture.md", zh), ("docs/architecture.en.md", en)):
		links = re.findall(r"\]\((\.\.?/[^)#]+|[A-Za-z0-9_./-]+\.md)\)", content)
		assert links, f"{path} 应至少链接命令参考与贡献指南"
		for target in links:
			assert (Path(path).parent / target).exists(), f"{path} 链接不存在: {target}"


def test_adr_index_covers_every_record():
	"""ADR 目录必须有索引，索引覆盖每份记录，且从架构文档与贡献指南可达。

	此前只有 0001/0002 两份 ADR，之后的浏览器来源策略、信封/错误码契约与 MCP
	子进程架构都没有决策记录，贡献者只能从代码反推。
	"""
	adr_dir = ROOT / "docs" / "adr"
	records = sorted(path.name for path in adr_dir.glob("[0-9][0-9][0-9][0-9]-*.md"))
	assert records, "docs/adr 应至少有一份 ADR"

	index = read("docs/adr/README.md")
	linked = set(re.findall(r"\]\((\d{4}-[^)]+\.md)\)", index))
	assert linked == set(records), f"索引与记录不一致：缺少 {set(records) - linked}，多余 {linked - set(records)}"
	for name in records:
		content = read(f"docs/adr/{name}")
		assert content.startswith("# "), f"{name} 缺少标题"
		assert "Status" in content, f"{name} 缺少 Status"

	for path in ("docs/architecture.md", "docs/architecture.en.md", "CONTRIBUTING.md", "CONTRIBUTING.en.md"):
		content = read(path)
		assert "docs/adr/README.md" in content or "adr/README.md" in content, path


def test_marketing_submission_doc_marks_itself_as_historical_snapshot():
	"""投稿模板里的计数与版本必须明确是历史快照，避免被当成当前事实照抄。"""
	content = read("docs/marketing/awesome-submissions.md")

	assert "Historical snapshot" in content
	assert "PyPI" in content
	assert "`boss schema`" in content
	assert "2026-08-27 快照" in content


def test_codeowners_paths_exist():
	"""CODEOWNERS 里的路径必须真实存在。

	`/SKILL.md` 在 Agent Skill 迁出后仍留在规则里，指向一个已不存在的文件；
	评审者按 CODEOWNERS 找负责人时会被引到空处。
	"""
	content = read(".github/CODEOWNERS")
	missing: list[str] = []
	checked: list[str] = []
	for line in content.splitlines():
		stripped = line.strip()
		if not stripped or stripped.startswith("#"):
			continue
		path = stripped.split()[0]
		if any(char in path for char in "*?[]"):
			continue
		checked.append(path)
		if not (ROOT / path.lstrip("/")).exists():
			missing.append(path)

	assert checked, "CODEOWNERS 应至少声明一个具体路径"
	assert not missing, f"以下 CODEOWNERS 路径不存在: {missing}"


def test_security_policy_supported_versions_match_current_minor():
	"""SECURITY.md 的支持版本表必须指向当前 minor。

	它曾停在 1.17.x，而项目已到 3.2.0：用户会以为当前版本不再接收安全修复，
	报告漏洞前也可能先降级到旧版本。
	"""
	from boss_agent_cli import __version__

	minor = ".".join(__version__.split(".")[:2])
	content = read("SECURITY.md")

	assert f"| {minor}.x" in content, f"SECURITY.md 支持版本表未更新到 {minor}.x"
	assert f"< {minor}" in content, f"SECURITY.md 的「不再支持」区间未更新到 < {minor}"


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

	assert "zhaopin" not in index
	assert "智联" not in index

	assert "research/platforms/README.md" in abstraction_zh
	assert "第三方 scraper" in abstraction_zh
	assert "research/platforms/README.md" in abstraction_en
	assert "Third-party scraper" in abstraction_en


def test_platform_research_docs_include_unified_adapter_evaluation():
	for path in (
		"docs/research/platforms/zhipin.md",
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

	for removed in (
		"docs/research/platforms/zhaopin.md",
		"docs/research/platforms/zhaopin-recruiter-evaluation.md",
	):
		assert not (ROOT / removed).exists(), removed

def test_maintainer_docs_cover_open_source_governance():
	branch = read("docs/maintainer/branch-protection.md")
	release = read("docs/maintainer/release-checklist.md")
	labels = read("docs/maintainer/labels.md")

	assert "Approving reviews are intentionally not required" in branch
	assert "Squash merge only" in branch
	assert "actions/runs/<run_id>/approve" in branch
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


def test_branch_protection_required_checks_match_ci_job_names():
	"""规则集按任务名匹配必需检查：文档列出的检查必须与 ci.yml 的任务名和 Python 矩阵一致。"""
	jobs = load_yaml(".github/workflows/ci.yml")["jobs"]
	expected = [jobs["lint"]["name"]] + [
		jobs["test"]["name"].replace("${{ matrix.python-version }}", version)
		for version in jobs["test"]["strategy"]["matrix"]["python-version"]
	]
	branch = read("docs/maintainer/branch-protection.md")
	listed = branch.split("Required status checks, matched by CI job name:", 1)[1].split("```text\n", 1)[1].split("```", 1)[0]

	assert [line.strip() for line in listed.strip().splitlines()] == expected


def test_contributing_links_maintainer_governance_docs():
	for path in ("CONTRIBUTING.md", "CONTRIBUTING.en.md"):
		content = read(path)
		for target in (
			"docs/maintainer/release-checklist.md",
			"docs/maintainer/labels.md",
			"docs/maintainer/branch-protection.md",
			"docs/maintainer/smoke-testing.md",
			"docs/maintainer/readme-badge-policy.md",
		):
			assert target in content, f"{path} 缺少维护者文档链接: {target}"


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


def test_ci_workflow_is_safe_for_fork_pull_requests():
	workflow = load_yaml(".github/workflows/ci.yml")
	triggers = workflow.get("on", workflow.get(True))
	assert isinstance(triggers, dict)
	# fork PR 必须能跑，且不能用拿得到 secret / 写权限的 pull_request_target
	assert "pull_request" in triggers
	assert "pull_request_target" not in triggers
	assert workflow["permissions"] == {"contents": "read"}
	assert workflow["concurrency"]["cancel-in-progress"] is True
	text = read(".github/workflows/ci.yml")
	assert "secrets." not in text
	for command in ("ruff check", "mypy src/boss_agent_cli", "pre-commit run --all-files", "pytest"):
		assert command in text, command
	assert not (ROOT / ".github/workflows/docs.yml").exists()


def test_labeler_workflow_never_runs_pull_request_code():
	workflow = load_yaml(".github/workflows/labeler.yml")
	triggers = workflow.get("on", workflow.get(True))
	assert "pull_request_target" in triggers
	# pull_request_target 拿得到写权限，所以绝不能 checkout 或执行 PR 里的代码
	text = read(".github/workflows/labeler.yml")
	assert "actions/checkout" not in text
	assert "run:" not in text
	assert "secrets." not in text
	assert workflow["permissions"] == {}
	job = workflow["jobs"]["label"]
	assert job["permissions"] == {"contents": "read", "pull-requests": "write"}


def test_dependabot_stays_low_noise():
	config = load_yaml(".github/dependabot.yml")
	ecosystems = {update["package-ecosystem"]: update for update in config["updates"]}
	assert set(ecosystems) == {"uv", "github-actions"}
	for update in ecosystems.values():
		assert update["schedule"]["interval"] == "weekly"
		assert update["open-pull-requests-limit"] <= 3
		assert update["groups"]


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
