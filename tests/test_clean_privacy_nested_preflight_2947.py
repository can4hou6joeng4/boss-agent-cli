"""隐私清理必须在删除前检查嵌套目录，且不跟随符号链接（Issue #2947）。"""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from boss_agent_cli.commands import clean as clean_module
from boss_agent_cli.main import cli


TARGETS = [
	("resumes", "本地简历"),
	("chat-history", "聊天快照"),
	("chat-export", "导出文件"),
	("crawl", "crawl 任务与导出"),
]


def _make_tree(tmp_path: Path, directory: str) -> tuple[Path, Path, Path, Path]:
	data_dir = tmp_path / "data"
	session = data_dir / "auth" / "session.enc"
	session.parent.mkdir(parents=True)
	session.write_bytes(b"fixture-session")
	index = data_dir / "cache" / "index_cache.json"
	index.parent.mkdir()
	index.write_bytes(b"{}")
	leaf = data_dir / directory / "nested" / "locked" / "fixture.json"
	leaf.parent.mkdir(parents=True)
	leaf.write_bytes(b"fixture-content")
	return data_dir, session, index, leaf


def _invoke(data_dir: Path) -> tuple[int, dict[str, Any]]:
	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(data_dir), "clean", "--privacy"])
	return result.exit_code, json.loads(result.stdout)


def _assert_preflight_failure(
	code: int,
	payload: dict[str, Any],
	*,
	target: str,
	path: Path,
	session: Path,
	index: Path,
	leaf: Path,
) -> None:
	assert code == 1
	assert payload["ok"] is False
	assert set(payload) == {"ok", "schema_version", "command", "data", "pagination", "error", "hints"}
	assert payload["error"]["code"] == "LOCAL_IO_ERROR"
	assert session.read_bytes() == b"fixture-session"
	assert index.read_bytes() == b"{}"
	assert leaf.read_bytes() == b"fixture-content"
	assert payload["data"]["results"] == []
	assert payload["data"]["completed"] == []
	assert payload["data"]["total_bytes_freed"] == 0
	assert any(item["target"] == target and item["path"] == str(path) for item in payload["data"]["failed"])
	assert target not in payload["data"]["remaining"]


@pytest.mark.parametrize(("directory", "target"), TARGETS)
@pytest.mark.parametrize("permissions", [0o500, 0o600], ids=["not-writable", "not-searchable"])
def test_nested_directory_permissions_preserve_session_before_any_deletion(
	tmp_path: Path, directory: str, target: str, permissions: int,
) -> None:
	data_dir, session, index, leaf = _make_tree(tmp_path, directory)
	locked = leaf.parent
	locked.chmod(permissions)
	try:
		if os.access(locked, os.W_OK | os.X_OK):
			pytest.skip("当前用户或文件系统不执行目录权限限制")
		code, payload = _invoke(data_dir)
	finally:
		locked.chmod(0o700)

	_assert_preflight_failure(
		code, payload, target=target, path=locked, session=session, index=index, leaf=leaf,
	)
	assert "目录不可写" in payload["data"]["failed"][0]["reason"]


@pytest.mark.parametrize(("directory", "target"), TARGETS)
@pytest.mark.parametrize("stage", ["open", "iterate"])
def test_nested_enumeration_errors_preserve_session_before_any_deletion(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: str, target: str, stage: str,
) -> None:
	data_dir, session, index, leaf = _make_tree(tmp_path, directory)
	locked = leaf.parent
	original_iterdir = Path.iterdir

	def fail_iterdir(path: Path):
		if path != locked:
			return original_iterdir(path)
		if stage == "open":
			raise PermissionError(errno.EACCES, "fixture enumeration denied", str(path))

		def partial_entries():
			yield from original_iterdir(path)
			raise OSError(errno.EIO, "fixture enumeration interrupted", str(path))

		return partial_entries()

	monkeypatch.setattr(Path, "iterdir", fail_iterdir)
	code, payload = _invoke(data_dir)

	_assert_preflight_failure(
		code, payload, target=target, path=locked, session=session, index=index, leaf=leaf,
	)
	assert "fixture enumeration" in payload["data"]["failed"][0]["reason"]


def test_nested_lstat_error_preserves_session_and_reports_child_path(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	data_dir, session, index, leaf = _make_tree(tmp_path, "resumes")
	original_lstat = Path.lstat

	def fail_lstat(path: Path, *args, **kwargs):
		if path == leaf:
			raise PermissionError(errno.EACCES, "fixture metadata denied", str(path))
		return original_lstat(path, *args, **kwargs)

	monkeypatch.setattr(Path, "lstat", fail_lstat)
	code, payload = _invoke(data_dir)

	_assert_preflight_failure(
		code, payload, target="本地简历", path=leaf, session=session, index=index, leaf=leaf,
	)
	assert "fixture metadata denied" in payload["data"]["failed"][0]["reason"]


@pytest.mark.parametrize(("directory", "target"), TARGETS)
def test_normal_nested_tree_still_cleans_successfully(tmp_path: Path, directory: str, target: str) -> None:
	data_dir, session, index, leaf = _make_tree(tmp_path, directory)

	code, payload = _invoke(data_dir)

	assert code == 0
	assert payload["ok"] is True
	assert not session.exists()
	assert not index.exists()
	assert not (data_dir / directory).exists()
	assert not leaf.exists()
	assert any(item["target"] == target and item["cleaned"] >= 1 for item in payload["data"]["results"])


def test_nested_links_are_not_enumerated_or_checked_for_directory_permissions(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	data_dir, session, index, leaf = _make_tree(tmp_path, "resumes")
	outside = tmp_path / "outside"
	outside.mkdir()
	outside_file = outside / "keep.json"
	outside_file.write_bytes(b"outside-content")
	links = [leaf.parent / name for name in ("directory-link", "file-link", "dangling-link", "cycle-link")]
	links[0].symlink_to(outside, target_is_directory=True)
	links[1].symlink_to(outside_file)
	links[2].symlink_to(tmp_path / "missing", target_is_directory=True)
	links[3].symlink_to(data_dir / "resumes", target_is_directory=True)
	original_iterdir = Path.iterdir
	original_writable = clean_module._is_writable

	def guarded_iterdir(path: Path):
		assert path not in [outside, *links], f"不得枚举链接或其目标：{path}"
		return original_iterdir(path)

	def guarded_writable(path: Path) -> bool:
		assert path not in [outside, *links], f"不得检查链接目标的目录权限：{path}"
		return original_writable(path)

	monkeypatch.setattr(Path, "iterdir", guarded_iterdir)
	monkeypatch.setattr(clean_module, "_is_writable", guarded_writable)
	outside.chmod(0o500)
	try:
		code, payload = _invoke(data_dir)
	finally:
		outside.chmod(0o700)

	assert code == 0
	assert payload["ok"] is True
	assert not session.exists()
	assert not index.exists()
	assert not (data_dir / "resumes").exists()
	assert outside_file.read_bytes() == b"outside-content"
