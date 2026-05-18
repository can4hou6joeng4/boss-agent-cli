import importlib.util
import json
import re
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
	return (ROOT / path).read_text(encoding="utf-8")


def _existing(paths: list[str] | tuple[str, ...]) -> list[str]:
	return [path for path in paths if (ROOT / path).exists()]


def _doc_paths_for_compliance_guardrails() -> list[str]:
	return _existing([
		"README.md",
		"README.en.md",
		"SKILL.md",
		"AGENTS.md",
		"CLAUDE.md",
		"docs/agent-quickstart.md",
		"docs/agent-quickstart.en.md",
		"docs/capability-matrix.md",
		"docs/capability-matrix.en.md",
		"docs/platform-risk.md",
		"docs/platform-risk.en.md",
		"mcp-server/README.md",
		"mcp-server/README.en.md",
		"SECURITY.md",
	])


def _extract_readme_badges(path: str) -> list[tuple[str, str, str]]:
	content = _read(path)
	header = content.split("</div>", 1)[0]
	return re.findall(r"\[!\[([^\]]+)\]\(([^)]+)\)\]\(([^)]+)\)", header)


def _load_mcp_tools() -> list:
	_mcp_mock = types.ModuleType("mcp")
	_mcp_server = types.ModuleType("mcp.server")
	_mcp_stdio = types.ModuleType("mcp.server.stdio")
	_mcp_types = types.ModuleType("mcp.types")
	_mcp_server.Server = MagicMock()
	_mcp_stdio.stdio_server = MagicMock()
	_mcp_types.TextContent = MagicMock()
	_mcp_types.Tool = type("Tool", (), {"__init__": lambda self, **kw: self.__dict__.update(kw)})
	_mcp_mock.server = _mcp_server
	_mcp_mock.types = _mcp_types

	sys.modules.setdefault("mcp", _mcp_mock)
	sys.modules.setdefault("mcp.server", _mcp_server)
	sys.modules.setdefault("mcp.server.stdio", _mcp_stdio)
	sys.modules.setdefault("mcp.types", _mcp_types)

	spec = importlib.util.spec_from_file_location("boss_mcp_server", ROOT / "mcp-server/server.py")
	assert spec and spec.loader
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module.TOOLS


def test_agent_quickstart_exists_and_has_core_sections():
	path = ROOT / "docs/agent-quickstart.md"
	assert path.exists(), "docs/agent-quickstart.md should exist"
	content = _read("docs/agent-quickstart.md")
	assert "# Agent Quickstart" in content
	assert "## 1) 安装与环境准备" in content
	assert "## 2) 三步跑通低风险 Agent 闭环" in content
	assert "## 3) 失败恢复与排障" in content
	assert "[Capability Matrix](capability-matrix.md)" in content


def test_capability_matrix_exists_and_covers_core_capabilities():
	path = ROOT / "docs/capability-matrix.md"
	assert path.exists(), "docs/capability-matrix.md should exist"
	content = _read("docs/capability-matrix.md")
	assert "# Capability Matrix" in content
	assert "| 能力 | CLI 命令 |" in content
	assert "`boss schema`" in content
	assert "`boss search`" in content
	assert "`boss detail`" in content
	assert "`boss greet`" in content
	assert "`boss pipeline`" in content
	assert "`boss digest`" in content
	assert "`boss config`" in content
	assert "`boss clean`" in content
	assert "34 个顶层命令" in content
	assert "9 个一级招聘者子命令" in content


def test_readme_and_skill_link_to_new_docs():
	readme = _read("README.md")
	assert "[Agent Quickstart](docs/agent-quickstart.md)" in readme
	assert "[Capability Matrix](docs/capability-matrix.md)" in readme

	skill = _read("SKILL.md")
	assert "[Agent Quickstart](docs/agent-quickstart.md)" in skill
	assert "[Capability Matrix](docs/capability-matrix.md)" in skill


def test_mcp_readme_links_to_quickstart_and_matrix():
	content = _read("mcp-server/README.md")
	assert "[Agent Quickstart](../docs/agent-quickstart.md)" in content
	assert "[Capability Matrix](../docs/capability-matrix.md)" in content


def test_readme_badge_policy_exists_and_has_core_rules():
	content = _read("docs/readme-badge-policy.md")
	assert "# README 第三方 Badge 准入标准" in content
	assert "`README.md` / `README.en.md`" in content
	assert "### 一类：默认允许" in content
	assert "### 二类：默认不允许" in content
	assert "### 4. 双语 README 对称" in content
	assert "### 6. 不形成未经批准的背书" in content


def test_readme_homepage_badges_stay_bilingual_and_symmetric():
	readme_badges = _extract_readme_badges("README.md")
	readme_en_badges = _extract_readme_badges("README.en.md")

	assert readme_badges, "README.md 首页应存在 badge 区"
	assert readme_en_badges, "README.en.md 首页应存在 badge 区"
	assert readme_badges == readme_en_badges, (
		"README 首页 badge 必须保持中英文对称；若新增或删除 badge，"
		"应同步更新 README.md 与 README.en.md 的同一组 badge"
	)


def test_schema_description_mentions_current_top_level_command_count():
	from boss_agent_cli.commands.schema import SCHEMA_DATA

	count = len(SCHEMA_DATA["commands"])
	assert f"{count} 个顶层命令" in SCHEMA_DATA["description"]


def test_readme_en_mentions_current_mcp_tool_count():
	content = _read("README.en.md")
	tool_count = len(_load_mcp_tools())
	assert f"MCP server with {tool_count} tools" in content
	assert "[Roadmap](ROADMAP.en.md)" in content


def test_english_agent_docs_exist_and_are_linked_from_english_entrypoints():
	quickstart = _read("docs/agent-quickstart.en.md")
	assert "# Agent Quickstart" in quickstart
	assert "## 1) Install and prepare the environment" in quickstart
	assert "## 2) Complete the low-risk agent loop in three steps" in quickstart
	assert "## 3) Recovery flow and troubleshooting" in quickstart
	assert "[Capability Matrix](capability-matrix.en.md)" in quickstart

	hosts = _read("docs/agent-hosts.en.md")
	assert "# Agent Host Examples" in hosts
	assert "[Codex](integrations/codex.md)" in hosts
	assert "[Python SDK](integrations/python-sdk.md)" in hosts

	matrix = _read("docs/capability-matrix.en.md")
	assert "# Capability Matrix" in matrix
	assert "| Capability | CLI command | Login required | Transport |" in matrix
	assert "`boss schema`" in matrix
	assert "`boss hr candidates`" in matrix
	assert "34 top-level commands" in matrix
	assert "9 first-level recruiter subcommands" in matrix

	mcp_readme = _read("mcp-server/README.en.md")
	assert "[Agent Quickstart](../docs/agent-quickstart.en.md)" in mcp_readme
	assert "[Capability Matrix](../docs/capability-matrix.en.md)" in mcp_readme

	readme_en = _read("README.en.md")
	assert "[Agent Quickstart](docs/agent-quickstart.en.md)" in readme_en
	assert "[Capability Matrix](docs/capability-matrix.en.md)" in readme_en
	assert "[docs/platform-abstraction.en.md](docs/platform-abstraction.en.md)" in readme_en

	python_sdk = _read("docs/integrations/python-sdk.md")
	assert "[`boss schema --format` options](../capability-matrix.en.md)" in python_sdk
	assert "[MCP integration guide (Claude Desktop / Cursor)](../../mcp-server/README.en.md)" in python_sdk

	models_en = _read("docs/integrations/ai-models.en.md")
	assert "# Recommended AI Models and Providers" in models_en
	assert "## Supported providers" in models_en
	assert "`openrouter`" in models_en
	assert "`gpt-5`" in models_en

	platform_en = _read("docs/platform-abstraction.en.md")
	assert "# Platform Abstraction Design and Migration SOP" in platform_en
	assert "## Why the platform abstraction exists" in platform_en
	assert "## Contract invariants" in platform_en

	assert "[Recommended models and entry points](integrations/ai-models.en.md)" in hosts


def test_english_roadmap_exists_and_readme_en_links_to_it():
	roadmap_en = _read("ROADMAP.en.md")
	assert "# Roadmap" in roadmap_en
	assert "## Near term (current mainline)" in roadmap_en
	assert "## Mid term (v2.0)" in roadmap_en
	assert "## Long-term vision" in roadmap_en
	assert "[awesome-agents](https://github.com/kyrolabs/awesome-agents)" in roadmap_en


def test_glama_metadata_exists_and_declares_owner():
	content = json.loads(_read("glama.json"))
	assert content["$schema"] == "https://glama.ai/mcp/schemas/server.json"
	assert "can4hou6joeng4" in content["maintainers"]


def test_pyproject_exposes_boss_mcp_script():
	pyproject = _read("pyproject.toml")
	assert 'boss-mcp = "boss_agent_cli.mcp_server:run"' in pyproject
	assert '"mcp>=1.0.0"' in pyproject


def test_agent_facing_docs_keep_low_risk_compliance_boundary():
	"""Agent-facing docs must not reintroduce old automation-first guidance."""
	for path in _doc_paths_for_compliance_guardrails():
		content = _read(path)
		assert "低风险" in content or "low-risk" in content.lower(), path
		assert "use cdp as a risk-control bypass" not in content.lower(), path
		assert "use cdp to bypass" not in content.lower(), path
		assert "启动 CDP Chrome 重试" not in content, path
		assert "Switch to CDP Chrome" not in content, path
		assert "CDP Chrome 重试" not in content, path
		assert "Switch to CDP mode" not in content, path
		assert "完整的求职操作链" not in content, path
		assert "完整操作链" not in content, path
		assert "自动化 + 反检测" not in content, path
		assert "浏览器自动化 + 反检测" not in content, path
		assert "反检测 fork" not in content, path
		assert "高风险操作" not in content, path
		assert "真实指纹" not in content, path
		assert "真实 gid" not in content, path
		assert "嗅探真实" not in content, path


def test_agent_instructions_send_sensitive_actions_to_official_platform():
	for path in _existing(("AGENTS.md", "CLAUDE.md", "README.md", "README.en.md")):
		content = _read(path)
		assert "COMPLIANCE_BLOCKED" in content, path
		assert (
			"平台官网" in content
			or "官方页面" in content
			or "官方平台" in content
			or "official platform" in content.lower()
			or "official website" in content.lower()
		), path
		assert "batch-greet  -> 默认低风险模式阻断" in content or "batch-greet" in content, path


def test_public_docs_do_not_expose_low_risk_mode_config_key():
	for path in _doc_paths_for_compliance_guardrails():
		content = _read(path)
		assert "low_risk_mode" not in content, path
		assert "low_risk_mode=false" not in content, path


def test_config_command_does_not_document_internal_low_risk_policy_key():
	content = _read("README.md")
	config_section = content.split("## ⚙️ 配置", 1)[1].split("## 🏗️ 技术架构", 1)[0]
	assert "low_risk_mode" not in config_section
	assert "低风险辅助模式" not in config_section


def test_schema_main_and_modules_command_count_consistent():
	"""防漂移：Click 顶层命令应与 SCHEMA_DATA 完全一致。"""
	from boss_agent_cli.commands.schema import SCHEMA_DATA
	from boss_agent_cli.main import cli

	registered = list(cli.commands)
	registered_set = set(registered)

	schema_set = set(SCHEMA_DATA["commands"].keys())

	assert registered_set == schema_set, (
		f"Click 顶层命令与 SCHEMA_DATA 不一致："
		f"仅在 Click: {registered_set - schema_set}，仅在 schema: {schema_set - registered_set}"
	)
	assert len(registered) == len(set(registered)), "Click 顶层命令存在重复注册"
