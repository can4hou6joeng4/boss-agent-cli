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
