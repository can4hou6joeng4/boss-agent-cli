"""简历文件名清洗的 Windows 边界（Issue #1507）。

``_safe_filename`` 只替换 ``/``、``\\``、NUL 时，Windows 上 ``name="D:evil"``
会生成 ``D:evil.json``；``ntpath.join(r"C:\\base", "D:evil.json")`` 的结果是
``D:evil.json``，脱离 base 目录。这里用 ``PureWindowsPath`` 断言函数输出，
不依赖真实 Windows 盘符，macOS / Linux 上同样可跑。
"""

import json
from pathlib import PureWindowsPath

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.resume.models import ResumeData
from boss_agent_cli.resume.store import (
	ResumeImportError,
	ResumeStore,
	_WINDOWS_RESERVED_DEVICE_NAMES,
	_portable_filename,
	_safe_filename,
)

_BASE = PureWindowsPath(r"C:\Users\tester\.boss-agent\resumes")


def _joined(name: str) -> PureWindowsPath:
	return _BASE / f"{_safe_filename(name)}.json"


def test_drive_letter_name_does_not_escape_base():
	"""`D:evil` 生成的文件名不能带 `:`，拼接后仍留在 base 目录。"""
	safe = _safe_filename("D:evil")

	assert ":" not in safe
	joined = _joined("D:evil")
	assert joined.parent == _BASE
	assert joined.drive == "C:"


@pytest.mark.parametrize(
	"name",
	["D:evil", "C:\\Windows\\evil", "\\\\server\\share\\evil", "../escape", "/etc/passwd", "a\0b"],
)
def test_path_components_never_escape_base(name):
	joined = _joined(name)

	assert joined.parent == _BASE
	assert joined.drive == "C:"
	assert ":" not in joined.name
	assert "/" not in joined.name and "\\" not in joined.name


@pytest.mark.parametrize(
	"name",
	[
		"CON", "con", "PRN", "AUX", "NUL",
		"COM1", "COM9", "LPT1", "LPT9",
		"CON.txt", "nul.json", "AUX.tar.gz",
	],
)
def test_windows_reserved_device_names_are_prefixed(name):
	safe = _safe_filename(name)

	assert safe.startswith("_")
	joined = _joined(name)
	stem = joined.name.split(".", 1)[0].upper()
	assert stem not in _WINDOWS_RESERVED_DEVICE_NAMES
	assert joined.parent == _BASE


@pytest.mark.parametrize("name", [".", "..", "..."])
def test_dot_only_names_do_not_point_at_directories(name):
	safe = _safe_filename(name)

	assert safe.strip(".") != ""
	joined = _joined(name)
	assert joined.parent == _BASE
	assert joined.stem.strip(".") != ""


@pytest.mark.parametrize("char", ['*', '?', '|', '<', '>', '"', '\t', '\n'])
def test_safe_filename_replaces_windows_illegal_characters(char):
	"""`init` / `clone` 的合法名称要清洗成 Windows 可落盘的文件名（Issue #1768）。"""
	assert _portable_filename(f"A{char}B") == "A_B"


def test_new_resume_with_illegal_characters_uses_portable_filename(tmp_path):
	store = ResumeStore(tmp_path / "resumes")
	store.save(ResumeData(name="A*B", title="开发"))

	assert [path.name for path in store._dir.glob("*.json")] == ["A_B.json"]
	loaded = store.get("A*B")
	assert loaded is not None and loaded.name == "A*B"


def test_legacy_resume_file_with_illegal_characters_stays_reachable(tmp_path):
	"""旧版本在 macOS / Linux 上写出的 `A*B.json` 仍能读、改、删。"""
	store = ResumeStore(tmp_path / "resumes")
	legacy = store._dir / "A*B.json"
	legacy.write_text(json.dumps({"name": "A*B", "title": "旧"}), encoding="utf-8")

	loaded = store.get("A*B")
	assert loaded is not None and loaded.title == "旧"
	loaded.title = "新"
	store.save(loaded)
	assert [path.name for path in store._dir.glob("*.json")] == ["A*B.json"]
	assert store.delete("A*B") is True
	assert list(store._dir.glob("*.json")) == []


def test_store_round_trips_drive_letter_name_inside_base(tmp_path):
	"""即使名称里带盘符，保存 / 读取也都在 resumes 目录内完成。"""
	store = ResumeStore(tmp_path / "resumes")
	store.save(ResumeData(name="D:evil", title="开发"))

	written = list(store._dir.glob("*.json"))
	assert [path.name for path in written] == ["D_evil.json"]
	assert all(path.parent == store._dir for path in written)
	assert not list(store._dir.parent.glob("D*.json"))

	loaded = store.get("D:evil")
	assert loaded is not None
	assert loaded.name == "D:evil"


@pytest.mark.parametrize("name", ["D:evil", "CON", "nul.txt", "...", "../escape", "C:\\abs"])
def test_import_rejects_unsafe_names(tmp_path, name):
	"""导入外部 JSON 时不静默改名：不安全的 name 直接报可修复的错误。"""
	store = ResumeStore(tmp_path / "resumes")
	src = tmp_path / "input.json"
	src.write_text(json.dumps({"name": name, "title": "开发"}), encoding="utf-8")

	with pytest.raises(ResumeImportError) as excinfo:
		store.import_file(src)

	assert "name" in str(excinfo.value)
	assert list(store._dir.glob("*.json")) == []


def test_import_keeps_name_with_windows_illegal_characters(tmp_path):
	"""导入不改名也不拒绝：文件名可移植，按原名称仍能找到（Issue #1768）。"""
	store = ResumeStore(tmp_path / "resumes")
	src = tmp_path / "input.json"
	src.write_text(json.dumps({"name": "A*B", "title": "开发"}), encoding="utf-8")

	assert store.import_file(src).name == "A*B"
	assert [path.name for path in store._dir.glob("*.json")] == ["A_B.json"]
	assert store.exists("A*B")


def test_resume_import_cli_reports_invalid_param_for_drive_letter(tmp_path):
	"""CLI 边界同样把不安全名称报成 INVALID_PARAM，而不是写到目录外。"""
	src = tmp_path / "input.json"
	src.write_text(json.dumps({"name": "D:evil", "title": "开发"}), encoding="utf-8")

	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "resume", "import", str(src)]
	)

	assert result.exit_code == 1, result.output
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert "name" in parsed["error"]["message"]
	assert not list((tmp_path / "resumes").glob("*.json"))
