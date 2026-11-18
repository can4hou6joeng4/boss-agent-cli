"""简历存在性判断的 fail-closed 回归测试（Issue #1692）。

同名覆盖保护不能把「路径状态读不出来」当成「简历不存在」；FIFO / 悬空链接
也不能直接进入 read_text()。
"""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

import pytest

from boss_agent_cli.resume.models import dict_to_resume
from boss_agent_cli.resume.store import ResumeStore


def _store(tmp_path: Path) -> ResumeStore:
	return ResumeStore(tmp_path / "resumes")


def _save_existing(store: ResumeStore, name: str = "mine", title: str = "本地标题") -> Path:
	store.save(dict_to_resume({"name": name, "title": title}))
	return store._path_for(name)


def test_exists_raises_when_path_metadata_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	store = _store(tmp_path)
	path = store._path_for("mine")
	real_lstat = Path.lstat

	def lstat_fails(self: Path) -> Any:
		if self == path:
			raise PermissionError("permission denied")
		return real_lstat(self)

	monkeypatch.setattr(Path, "lstat", lstat_fails)

	with pytest.raises(PermissionError):
		store.exists("mine")


def test_import_guard_does_not_overwrite_when_metadata_unreadable(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	store = _store(tmp_path)
	existing = _save_existing(store)
	original = existing.read_bytes()
	source = tmp_path / "backup.json"
	source.write_text('{"name": "mine", "title": "备份里的旧标题"}', encoding="utf-8")
	real_path_mode = __import__("boss_agent_cli.resume.store", fromlist=["_path_mode"])._path_mode

	def path_mode_fails(path: Path) -> int | None:
		if path == existing:
			raise PermissionError("permission denied")
		return real_path_mode(path)

	monkeypatch.setattr("boss_agent_cli.resume.store._path_mode", path_mode_fails)

	with pytest.raises(PermissionError):
		store.import_file(source)
	assert existing.read_bytes() == original


def test_dangling_symlink_is_existing_and_not_readable(tmp_path: Path) -> None:
	store = _store(tmp_path)
	path = store._path_for("dangling")
	path.symlink_to(tmp_path / "missing")

	assert store.exists("dangling") is True
	raw, error = store.get_with_error("dangling")
	assert raw is None
	assert error is not None and "不是普通文件" in error
	assert store.delete("dangling") is True
	assert not path.is_symlink()


def test_non_regular_path_is_not_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	store = _store(tmp_path)
	path = store._path_for("pipe")
	path.write_bytes(b"placeholder")

	def path_mode_is_fifo(candidate: Path) -> int | None:
		if candidate == path:
			return stat.S_IFIFO
		return None

	def must_not_read(self: Path, *args: Any, **kwargs: Any) -> str:
		raise AssertionError(f"不能读取非普通文件: {self}")

	monkeypatch.setattr("boss_agent_cli.resume.store._path_mode", path_mode_is_fifo)
	monkeypatch.setattr(Path, "read_text", must_not_read)

	raw, error = store.get_with_error("pipe")
	assert raw is None
	assert error == "简历路径不是普通文件"


def test_symlink_to_regular_resume_is_read(tmp_path: Path) -> None:
	store = _store(tmp_path)
	target = tmp_path / "linked.json"
	target.write_text('{"name": "linked", "title": "Linked"}', encoding="utf-8")
	store._path_for("linked").symlink_to(target)

	assert store.exists("linked") is True
	resume = store.get("linked")
	assert resume is not None
	assert resume.title == "Linked"


def test_missing_resume_is_not_existing(tmp_path: Path) -> None:
	store = _store(tmp_path)

	assert store.exists("missing") is False
	assert store.get_with_error("missing") == (None, None)
