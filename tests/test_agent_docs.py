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
	assert "| 能力 | CLI 命令 | Skill 入口 | MCP 工具 |" in content
	assert "| 协议发现 | `boss schema` | `schema` | `boss_schema` |" in content
	assert "| 职位搜索 | `boss search` | `search` | `boss_search` |" in content
	assert "| 职位详情 | `boss detail` | `detail` | `boss_detail` |" in content
	assert "| 打招呼 | `boss greet` | `greet` | `boss_greet` |" in content


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
