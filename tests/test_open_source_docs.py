import contextlib
import io
import re
import shlex
import subprocess
import urllib.parse
from importlib.metadata import metadata
from pathlib import Path

import click
import yaml

from boss_agent_cli.main import cli


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


def test_scripts_readme_indexes_every_script():
	"""scripts/README.md 必须索引每个可执行脚本。

	`quality_baseline.py` 这类最常用入口曾长期不在索引里，只能靠读源码发现。
	"""
	indexed = read("scripts/README.md")
	scripts = sorted(path.name for path in (ROOT / "scripts").glob("*.py") if not path.name.startswith("_"))
	assert scripts
	missing = [name for name in scripts if f"## {name}" not in indexed]
	assert not missing, f"scripts/README.md 缺少脚本条目: {missing}"


def test_contributing_documents_project_quality_gates():
	"""贡献者文档必须给出仓库实际使用的一键门禁与 eval 入口。"""
	for path in ("CONTRIBUTING.md", "CONTRIBUTING.en.md"):
		content = read(path)
		assert "scripts/quality_baseline.py" in content, f"{path} 没提一键门禁"
		assert "scripts/smoke_p0.py" in content, f"{path} 没提离线冒烟"
		assert "evals/run_eval.py" in content, f"{path} 没提 MCP eval"


def test_security_policy_describes_current_mode_semantics():
	"""SECURITY.md 不得再描述已不存在的模式级权限差异。

	`compliance.py` 里两种 `operating_mode` 权限一致，`COMPLIANCE_BLOCKED` 仅为
	历史协议兼容保留；安全文档写「不批量触达、不抓取平台数据」会低估真实能力面。
	"""
	content = read("SECURITY.md")
	assert "operating_mode" in content
	assert "两种模式" in content
	assert "平台风控" in content
	assert "默认启用低风险辅助模式" not in content


def test_roadmap_describes_current_mode_semantics():
	"""ROADMAP 不得把已被取代的模式阻断 / 过滤写成现状。

	ADR 0001 / 0002 都已标注 Superseded by the 2026-08 open-capability design，
	两种 `operating_mode` 权限一致；ROADMAP 曾把「默认阻断」和「schema / MCP
	模式过滤」列为已完成能力，与 ADR 状态和 `compliance.py` 矛盾。
	"""
	zh = read("ROADMAP.md")
	en = read("ROADMAP.en.md")
	assert "已被 2026-08 开放能力设计取代" in zh
	assert "权限一致" in zh
	assert "敏感命令默认阻断并交接回平台官网（ADR 0001）" not in zh
	assert "superseded by the 2026-08 open-capability design" in en
	assert "identical access" in en
	assert "sensitive commands are blocked by default and handed back to the official platform (ADR 0001)" not in en


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
	assert '--tag "$GITHUB_REF_NAME"' in content
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
	assert "## Workflow rules" in branch
	assert "CHECKOUTS_THAT_PUSH" in branch
	assert "enable-cache: false" in branch

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


WORKFLOW_PATHS = sorted(
	f".github/workflows/{path.name}"
	for pattern in ("*.yml", "*.yaml")
	for path in (ROOT / ".github/workflows").glob(pattern)
)
USES_LINE = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)[ \t]*(#.*)?$", re.MULTILINE)
PINNED_ACTION = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
VERSION_COMMENT = re.compile(r"^# v\d+\.\d+\.\d+$")
# 这些任务在 checkout 之后要推送提交或分支，必须把令牌留在工作区；其余任务一律不留。
CHECKOUTS_THAT_PUSH = {
	(".github/workflows/star-history.yml", "star-history"),
	(".github/workflows/update-contributors.yml", "update-contributors"),
}


def _workflow_triggers(path: str) -> set[str]:
	workflow = load_yaml(path)
	# PyYAML 按 YAML 1.1 把裸键 `on` 解析成布尔值 True
	triggers = workflow.get("on", workflow.get(True))
	if isinstance(triggers, str):
		return {triggers}
	assert isinstance(triggers, (dict, list)), path
	return set(triggers)


def _workflow_steps(path: str) -> list[tuple[str, dict]]:
	steps = []
	for job_name, job in load_yaml(path)["jobs"].items():
		for step in job.get("steps", []):
			steps.append((job_name, step))
	return steps


def test_workflow_guards_see_every_workflow():
	assert len(WORKFLOW_PATHS) >= 9
	assert ".github/workflows/release.yml" in WORKFLOW_PATHS


def test_workflow_actions_are_pinned_to_commit_sha():
	"""第三方 action 必须钉到完整提交 SHA，并在行尾注明精确版本。

	`@v7` 这类标签可以被上游改指向任意提交；发布工作流持有 PyPI 令牌，任何一个
	被改写的标签都能读到它。提交 SHA 不可变，Dependabot 按行尾的版本注释照常升级。
	"""
	seen = 0
	for path in WORKFLOW_PATHS:
		for reference, comment in USES_LINE.findall(read(path)):
			seen += 1
			if reference.startswith("./"):
				continue
			assert PINNED_ACTION.match(reference), (
				f"{path}: `{reference}` 没有钉到 40 位提交 SHA；"
				"写成 `owner/repo@<sha> # vX.Y.Z`（见 docs/maintainer/branch-protection.md）"
			)
			assert VERSION_COMMENT.match(comment), (
				f"{path}: `{reference}` 行尾缺少 `# vX.Y.Z` 版本注释，Dependabot 与评审都靠它识别版本"
			)
	assert seen >= 18


def test_workflow_run_scripts_do_not_expand_expressions():
	"""`run:` 脚本里不直接展开 `${{ }}`，取值一律经 `env:` 传入。

	表达式在 shell 解析之前就被替换成原文：标签名、分支名、PR 标题里的引号和分号
	会变成命令。经环境变量传入后，shell 只把它当作数据。
	"""
	checked = 0
	for path in WORKFLOW_PATHS:
		for job_name, step in _workflow_steps(path):
			script = step.get("run")
			if script is None:
				continue
			checked += 1
			assert "${{" not in script, f"{path} / {job_name}: run 脚本里展开了表达式，改为经 env: 传入"
	assert checked >= 10


def test_pull_request_target_workflows_never_check_out_pull_request_code():
	"""`pull_request_target` 拿得到写权限和 secret，绝不能检出或引用 PR 的 head。"""
	targeted = [path for path in WORKFLOW_PATHS if "pull_request_target" in _workflow_triggers(path)]
	assert ".github/workflows/update-contributors.yml" in targeted
	for path in targeted:
		text = read(path)
		for marker in ("pull_request.head", "github.head_ref", "refs/pull/"):
			assert marker not in text, f"{path}: pull_request_target 工作流引用了 PR head（{marker}）"
		for job_name, step in _workflow_steps(path):
			if str(step.get("uses", "")).startswith("actions/checkout@"):
				assert "ref" not in (step.get("with") or {}), (
					f"{path} / {job_name}: pull_request_target 下 checkout 不得指定 ref，默认检出的是基线分支"
				)


def test_release_workflow_does_not_restore_caches():
	"""发布构建不吃缓存：master 上的 CI 写入的缓存会被带进持有 PyPI 令牌的环境。"""
	steps = _workflow_steps(".github/workflows/release.yml")
	setup_uv = [step for _, step in steps if str(step.get("uses", "")).startswith("astral-sh/setup-uv@")]
	assert len(setup_uv) == 1
	# setup-uv 的 enable-cache 默认值是 auto，在托管 runner 上等于开启，必须显式关闭
	assert setup_uv[0]["with"]["enable-cache"] is False
	for _, step in steps:
		assert not str(step.get("uses", "")).startswith("actions/cache"), "发布工作流不得使用 actions/cache"


def test_checkout_keeps_credentials_only_where_a_push_follows():
	"""`actions/checkout` 默认把令牌写进 .git/config；不推送的任务必须显式关闭。"""
	keeps = set()
	checkouts = 0
	for path in WORKFLOW_PATHS:
		for job_name, step in _workflow_steps(path):
			if not str(step.get("uses", "")).startswith("actions/checkout@"):
				continue
			checkouts += 1
			options = step.get("with") or {}
			assert "persist-credentials" in options, f"{path} / {job_name}: checkout 必须显式声明 persist-credentials"
			assert isinstance(options["persist-credentials"], bool), f"{path} / {job_name}"
			if options["persist-credentials"]:
				keeps.add((path, job_name))
	assert checkouts >= 6
	assert keeps == CHECKOUTS_THAT_PUSH


def test_dependabot_stays_low_noise():
	config = load_yaml(".github/dependabot.yml")
	ecosystems = {update["package-ecosystem"]: update for update in config["updates"]}
	# pre-commit 生态负责钩子仓库的 rev（ruff 本身走 uv 生态，钩子用项目环境）。
	assert set(ecosystems) == {"uv", "github-actions", "pre-commit"}
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


def _bilingual_pairs() -> list[tuple[str, str]]:
	pairs: list[tuple[str, str]] = []
	for zh_path in sorted((ROOT / "docs").glob("*.md")):
		if zh_path.name.endswith(".en.md"):
			continue
		en_path = zh_path.with_name(f"{zh_path.stem}.en.md")
		if en_path.exists():
			pairs.append((zh_path.relative_to(ROOT).as_posix(), en_path.relative_to(ROOT).as_posix()))
	for path in ("README.md", "CONTRIBUTING.md", "ROADMAP.md", "SUPPORT.md"):
		en_path = f"{path[:-3]}.en.md"
		if (ROOT / en_path).exists():
			pairs.append((path, en_path))
	return pairs


def _heading_levels(path: str) -> list[int]:
	levels: list[int] = []
	in_fence = False
	for line in read(path).splitlines():
		stripped = line.strip()
		if stripped.startswith("```") or stripped.startswith("~~~"):
			in_fence = not in_fence
			continue
		if in_fence:
			continue
		match = re.match(r"^(#{1,6})\s", line)
		if match:
			levels.append(len(match.group(1)))
	return levels


def test_bilingual_docs_share_the_same_heading_structure():
	"""中英对照文档的标题层级必须一致，防止一侧新增章节、另一侧没跟上。"""
	pairs = _bilingual_pairs()
	assert len(pairs) >= 14
	for zh_path, en_path in pairs:
		assert _heading_levels(zh_path) == _heading_levels(en_path), f"{zh_path} 与 {en_path} 的标题结构不一致"


ERROR_TABLE_HEADINGS = {
	"docs/troubleshooting.md": "## 错误码与自动修复",
	"docs/troubleshooting.en.md": "## Error codes & agent-friendly recovery",
}


def _troubleshooting_error_codes(path: str) -> set[str]:
	content = read(path)
	heading = ERROR_TABLE_HEADINGS[path]
	section = content[content.index(heading) + len(heading) :]
	end = section.find("\n## ")
	if end != -1:
		section = section[:end]
	return set(re.findall(r"^\|\s*`([A-Z][A-Z0-9_]+)`\s*\|", section, re.MULTILINE))


def test_troubleshooting_error_tables_list_the_same_registered_codes():
	"""两个排障文档的错误码表必须列出同一组码，且每个码都在登记表里。"""
	from boss_agent_cli.schema.error_codes import ERROR_CODES

	zh_codes = _troubleshooting_error_codes("docs/troubleshooting.md")
	en_codes = _troubleshooting_error_codes("docs/troubleshooting.en.md")

	assert zh_codes, "错误码表解析不到任何条目"
	assert zh_codes == en_codes, f"中文版缺 {sorted(en_codes - zh_codes)}，英文版缺 {sorted(zh_codes - en_codes)}"
	assert zh_codes <= set(ERROR_CODES), f"未在 ERROR_CODES 登记：{sorted(zh_codes - set(ERROR_CODES))}"


def test_support_docs_route_every_request_channel():
	"""求助入口必须齐全，且 README 中英都要能到达。"""
	for path in ("SUPPORT.md", "SUPPORT.en.md"):
		content = read(path)
		assert "discussions" in content.lower()
		assert "issues/new/choose" in content
		assert "SECURITY.md" in content
		assert "docs/troubleshooting" in content

	assert "[获取支持](SUPPORT.md)" in read("README.md")
	assert "[Getting Support](SUPPORT.en.md)" in read("README.en.md")
	assert "](SECURITY.md)" in read("README.md")
	assert "](SECURITY.md)" in read("README.en.md")


def _tracked_markdown_files() -> list[Path]:
	"""扫描已跟踪文档，外加尚未 `git add` 但没被忽略的新文件。

	`--others --exclude-standard` 让本地新建的文档也参与检查，同时排除
	`.git/info/exclude` 里的本地研究笔记。
	"""
	result = subprocess.run(
		["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "*.md"],
		cwd=ROOT,
		capture_output=True,
		check=True,
	)
	return [ROOT / name for name in result.stdout.decode("utf-8").split("\0") if name and (ROOT / name).exists()]


def _relative_link_targets(content: str) -> list[str]:
	targets: list[str] = []
	in_fence = False
	for line in content.splitlines():
		stripped = line.strip()
		if stripped.startswith("```") or stripped.startswith("~~~"):
			in_fence = not in_fence
			continue
		if in_fence:
			continue
		for match in re.finditer(r"\]\(([^)\s]+)\)", line):
			target = match.group(1)
			if target.startswith(("http://", "https://", "#", "mailto:", "data:", "/", "<")):
				continue
			target = target.split("#", 1)[0].split("?", 1)[0]
			if not target or "{" in target:
				continue
			targets.append(target)
	return targets


def test_markdown_relative_links_resolve():
	"""被跟踪文档里的相对链接必须指向真实存在的文件。

	`CHANGELOG.md` 曾长期链到从未提交的 `docs/research/platforms/zhaopin.md`，
	读者点开是 404。守卫只扫 git 跟踪的文档，本地研究笔记不参与。
	"""
	checked = 0
	broken: list[str] = []
	for path in _tracked_markdown_files():
		for target in _relative_link_targets(path.read_text(encoding="utf-8")):
			checked += 1
			if not (path.parent / target).resolve().exists():
				broken.append(f"{path.relative_to(ROOT).as_posix()} -> {target}")
	assert checked >= 200, f"只检查到 {checked} 个相对链接，正则可能失效"
	assert not broken, "相对链接目标不存在:\n" + "\n".join(broken)


def test_code_of_conduct_is_reachable_from_entry_docs():
	"""行为准则必须存在，且入口文档中英都能到达。

	`CODE_OF_CONDUCT.md` 2026-11 之前一直躺在仓库根目录，没有任何文档链接它；
	GitHub 社区健康页之外基本不可达。
	"""
	assert (ROOT / "CODE_OF_CONDUCT.md").exists()
	for path in ("README.md", "README.en.md", "CONTRIBUTING.md", "CONTRIBUTING.en.md"):
		assert "CODE_OF_CONDUCT.md" in read(path), f"{path} 没有指向 CODE_OF_CONDUCT.md"


APPROVED_BADGE_HOSTS = {"img.shields.io", "github.com", "codecov.io"}


def _readme_badge_hosts(path: str) -> list[tuple[str, str]]:
	"""返回 README 里所有外链图片的 (host, 原始 URL)，涵盖 markdown badge 与 HTML <img>。"""
	content = read(path)
	urls = re.findall(r"!\[[^\]]*\]\((https?://[^)\s]+)\)", content)
	urls += re.findall(r"<img[^>]+src=[\"'](https?://[^\"']+)[\"']", content)
	urls += re.findall(r"srcset=[\"'](https?://[^\"']+)[\"']", content)
	return [(urllib.parse.urlsplit(url).hostname or "", url) for url in urls]


def test_readme_badges_come_from_approved_sources():
	"""README 首页 badge 只能来自真源白名单，且政策文档列出当前清单。

	2026-07-28 外部提交（`366c71d`）给两个 README 加了 MCP Toplist
	（mcptoplist.com）目录站 badge，与 2026-05-03 的
	`docs/maintainer/readme-badge-policy.md`「第三方目录站导流 badge 默认
	不允许」冲突，直到 2026-11 才被发现移除。白名单与政策文档里的
	「当前首页 badge 清单」保持同步。
	"""
	checked = 0
	for path in ("README.md", "README.en.md"):
		hosts = _readme_badge_hosts(path)
		assert len(hosts) >= 5, f"{path} 只解析到 {len(hosts)} 个外链图片，正则可能失效"
		checked += len(hosts)
		unapproved = [f"{host} ({url})" for host, url in hosts if host not in APPROVED_BADGE_HOSTS]
		assert not unapproved, f"{path} 含未批准的 badge 主机: " + ", ".join(unapproved)
		assert "mcptoplist.com" not in read(path), f"{path} 仍残留 MCP Toplist badge"

	policy = read("docs/maintainer/readme-badge-policy.md")
	assert "## 当前首页 badge 清单" in policy
	assert "MCP Toplist" in policy and "已移除" in policy
	assert checked >= 10, f"只检查到 {checked} 个 badge，正则可能失效"


_SHELL_FENCE_LANGS = {"", "bash", "sh", "shell", "console", "powershell", "pwsh", "cmd", "bat"}
#: 角度占位符代表「用户自己替换」的取值；只掩码取值本身，选项仍参与解析，
#: 这样 `<security_id>` 所在行的选项拼写错误也能被发现。
_ANGLE_PLACEHOLDER = re.compile(r"<[^<>\s]+>")
#: 其余无法可靠还原成命令行的写法（JSON 片段、变量、续行、管道、示例路径）；
#: 整行跳过，不当作可执行示例。
_COMMAND_PLACEHOLDERS = ("{", "}", "$", "`", "...", "|", "/path/to/")


def _documented_boss_commands() -> list[tuple[str, int, str]]:
	"""收集 README 与 docs 下 shell 代码块里以 ``boss `` 开头的示例命令。"""
	paths = [
		Path("README.md"),
		Path("README.en.md"),
		Path("mcp-server/README.md"),
		Path("mcp-server/README.en.md"),
		*sorted(Path("docs").rglob("*.md")),
	]
	commands: list[tuple[str, int, str]] = []
	for path in paths:
		if not (ROOT / path).exists():
			continue
		fence_lang: str | None = None
		for lineno, line in enumerate(read(str(path)).splitlines(), 1):
			stripped = line.strip()
			if stripped.startswith("```"):
				fence_lang = None if fence_lang is not None else stripped[3:].strip()
				continue
			if fence_lang not in _SHELL_FENCE_LANGS:
				continue
			if not stripped.startswith("boss ") or stripped.startswith("boss-agent"):
				continue
			if any(marker in stripped for marker in _COMMAND_PLACEHOLDERS):
				continue
			commands.append((str(path), lineno, stripped))
	return commands


def _resolve_documented_command(argv: list[str]) -> tuple[click.Command, list[str]]:
	"""沿 Click 的 Group 子命令树把 argv 拆成目标命令与它自己的参数。"""
	command: click.Command = cli
	rest = list(argv)
	while isinstance(command, click.Group) and rest and rest[0] in command.commands:
		command = command.commands[rest[0]]
		rest = rest[1:]
	return command, rest


def test_documented_boss_commands_parse_with_click():
	"""文档里的 ``boss`` 示例必须能被真实 CLI 解析，防止照抄即报用法错误。

	README 曾把 ``boss config reset`` 写成不带 ``KEY`` 的「恢复默认」，而该命令
	要求单个配置键——用户照抄只会拿到 ``Missing parameter: key``。这里从 shell
	代码块提取示例，把 ``<占位符>`` 掩码成合法取值后，按 CLI 的 Group 树下钻到
	目标命令跑一次参数解析（不执行回调）；无法还原的写法整行跳过。
	"""
	checked = 0
	failures: list[str] = []
	for path, lineno, line in _documented_boss_commands():
		try:
			tokens = shlex.split(line)
		except ValueError:
			continue
		if len(tokens) < 2 or tokens[1].startswith("<"):
			# `boss <cmd> --help` 之类：命令名本身是占位符，无从解析。
			continue
		try:
			argv = shlex.split(_ANGLE_PLACEHOLDER.sub("1", line))[1:]
		except ValueError:
			continue
		for terminator in ("#", "&&", "||"):
			if terminator in argv:
				argv = argv[: argv.index(terminator)]
		command, rest = _resolve_documented_command(argv)
		try:
			# `--version` / `--help` 的 eager 回调会直接写 stdout，这里吞掉以免刷屏。
			with contextlib.redirect_stdout(io.StringIO()):
				ctx = command.make_context(command.name, rest)
				ctx.close()
		except click.exceptions.Exit:
			# eager 选项以 Exit(0) 结束解析，示例本身合法。
			pass
		except click.ClickException as exc:
			failures.append(f"{path}:{lineno}: {line}\n    {exc.format_message()}")
		checked += 1
	assert not failures, "文档示例无法被 CLI 解析：\n" + "\n".join(failures)
	assert checked >= 150, f"只解析到 {checked} 条文档命令，提取逻辑可能失效"
