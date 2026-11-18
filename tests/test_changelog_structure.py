"""CHANGELOG 结构：`[Unreleased]` 在最前，已发布版本严格降序（Issue #1676）。

3.4.92 发版时把 `## [Unreleased]` 原地替换成版本标题、又在原段落后插入新的
`[Unreleased]`，导致 3.4.92 段落在文件中停在了 3.4.91 上方。发版流程是手工
替换标题，没有结构化断言时同类错位会再次发生。
"""

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_HEADING = re.compile(r"^## \[([^\]]+)\]")


def _section_names() -> list[str]:
	return [
		match.group(1)
		for line in (_ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
		if (match := _HEADING.match(line))
	]


def _version_key(version: str) -> tuple[int, ...]:
	return tuple(int(part) for part in version.split("."))


def test_unreleased_is_the_first_section() -> None:
	names = _section_names()
	assert names, "CHANGELOG.md 没有任何版本段"
	assert names[0] == "Unreleased"


def test_released_versions_are_strictly_descending() -> None:
	released = [name for name in _section_names() if name != "Unreleased"]
	assert all(
		_version_key(previous) > _version_key(current)
		for previous, current in zip(released, released[1:])
	), "CHANGELOG 版本段必须严格降序；发版时不要移动或重复插入旧版本标题"
