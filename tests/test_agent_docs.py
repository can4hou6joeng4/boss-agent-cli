from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
	return (ROOT / path).read_text(encoding="utf-8")


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
	assert "30" in content


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
