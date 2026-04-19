from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = REPO_ROOT / "docs"
INDEX_DOC = DOCS_ROOT / "agent-hosts.md"
HOST_DOCS = {
	"Codex": DOCS_ROOT / "integrations" / "codex.md",
	"Claude Code": DOCS_ROOT / "integrations" / "claude-code.md",
	"Cursor": DOCS_ROOT / "integrations" / "cursor.md",
	"Windsurf": DOCS_ROOT / "integrations" / "windsurf.md",
	"Shell Agent": DOCS_ROOT / "integrations" / "shell-agent.md",
}
REQUIRED_COMMANDS = [
	"boss schema",
	"boss status",
	"boss search",
	"boss detail",
	"boss greet",
]


def _read(path: Path) -> str:
	return path.read_text(encoding="utf-8")


def test_agent_host_index_links_all_examples():
	assert INDEX_DOC.exists()
	index_text = _read(INDEX_DOC)

	for host_name, path in HOST_DOCS.items():
		assert path.exists()
		assert f"[{host_name}](" in index_text
		assert path.name in index_text


def test_agent_host_examples_cover_core_agent_loop():
	for host_name, path in HOST_DOCS.items():
		text = _read(path)
		assert "## 适用场景" in text, host_name
		assert "## 最小接入流程" in text, host_name
		assert "## 失败恢复" in text, host_name
		for command in REQUIRED_COMMANDS:
			assert command in text, f"{host_name} missing {command}"


def test_readme_and_quickstart_link_to_host_examples():
	readme_text = _read(REPO_ROOT / "README.md")
	quickstart_text = _read(DOCS_ROOT / "agent-quickstart.md")

	assert "docs/agent-hosts.md" in readme_text
	assert "agent-hosts.md" in quickstart_text
