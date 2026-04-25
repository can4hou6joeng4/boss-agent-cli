import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
	return (ROOT / path).read_text(encoding="utf-8")


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
	assert "## 2) 三步跑通 Agent 闭环" in content
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
	assert "33 个顶层命令" in content
	assert "7 个一级招聘者子命令" in content


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


def test_schema_description_mentions_current_top_level_command_count():
	from boss_agent_cli.commands.schema import SCHEMA_DATA

	count = len(SCHEMA_DATA["commands"])
	assert f"{count} 个顶层命令" in SCHEMA_DATA["description"]


def test_readme_en_mentions_current_mcp_tool_count():
	content = _read("README.en.md")
	tool_count = len(_load_mcp_tools())
	assert f"MCP server with {tool_count} tools" in content


def test_glama_metadata_exists_and_declares_owner():
	content = json.loads(_read("glama.json"))
	assert content["$schema"] == "https://glama.ai/mcp/schemas/server.json"
	assert "can4hou6joeng4" in content["maintainers"]


def test_pyproject_exposes_boss_mcp_script():
	pyproject = _read("pyproject.toml")
	assert 'boss-mcp = "boss_agent_cli.mcp_server:run"' in pyproject
	assert '"mcp>=1.0.0"' in pyproject


def test_schema_main_and_modules_command_count_consistent():
	"""防漂移：main.py 注册命令去掉 schema 自身后，应与 SCHEMA_DATA 完全一致。"""
	import re

	from boss_agent_cli.commands.schema import SCHEMA_DATA

	main_text = _read("src/boss_agent_cli/main.py")
	registered = re.findall(r'cli\.add_command\([^,]+,\s*"([^"]+)"', main_text)
	registered_set = set(registered) - {"schema"}

	schema_set = set(SCHEMA_DATA["commands"].keys())

	assert registered_set == schema_set, (
		f"main.py 注册命令与 SCHEMA_DATA 不一致："
		f"仅在 main: {registered_set - schema_set}，仅在 schema: {schema_set - registered_set}"
	)
	assert len(registered) == len(set(registered)), "main.py 存在重复注册命令"
