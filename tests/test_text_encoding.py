"""仓库内的文本文件不得含 U+FFFD（替换字符）。

UTF-8 解码失败、编辑器按错误编码另存、复制粘贴中断都会留下 U+FFFD；它在终端
里通常只显示成一个方块，却会把文档和信封文案里的中文句子割断——`tests/
test_system_proxy.py` 的「环境干净」曾长期在「环境」与「净」之间夹着三个
替换字符，门禁全绿、无人发现。这里把整仓扫描固定成一条守卫，出现即点名
文件与行号。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 替换字符本身。写成 chr(0xFFFD)，避免本文件自己成为扫描命中项。
REPLACEMENT_CHAR = chr(0xFFFD)

#: 只扫中文文案集中、且都在版本管理里的目录。
SCANNED_DIRS = ("src", "tests", "docs", "scripts", "mcp-server", "evals", ".github")
TEXT_SUFFIXES = {".py", ".md", ".txt", ".toml", ".yaml", ".yml", ".sh", ".cfg", ".ini"}
#: 根目录的文档与配置文件。`CLAUDE.md` / `AGENTS.md` 是本地文件，不在此列。
ROOT_FILES = (
	"README.md",
	"README.en.md",
	"CHANGELOG.md",
	"CONTRIBUTING.md",
	"CONTRIBUTING.en.md",
	"ROADMAP.md",
	"ROADMAP.en.md",
	"SECURITY.md",
	"CODE_OF_CONDUCT.md",
	"pyproject.toml",
)


def _iter_text_files():
	for name in SCANNED_DIRS:
		base = ROOT / name
		if not base.is_dir():
			continue
		for path in sorted(base.rglob("*")):
			if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
				yield path
	for name in ROOT_FILES:
		path = ROOT / name
		if path.is_file():
			yield path


def test_no_replacement_characters_in_text_files() -> None:
	"""U+FFFD 只会来自编码事故，出现即视为损坏。"""
	offenders = []
	for path in _iter_text_files():
		try:
			text = path.read_text(encoding="utf-8")
		except UnicodeDecodeError:
			offenders.append(f"{path.relative_to(ROOT)}: 不是合法的 UTF-8")
			continue
		for lineno, line in enumerate(text.splitlines(), 1):
			if REPLACEMENT_CHAR in line:
				offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")

	assert not offenders, "文本文件里出现 U+FFFD：\n" + "\n".join(offenders)
