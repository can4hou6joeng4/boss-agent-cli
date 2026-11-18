"""非隐私 clean 的路径状态 fail-closed 回归测试（Issue #1712）。

只清缓存 / 快照时，路径元数据读不出来曾被 ``exists()`` / ``is_file()``
吞成「不存在」或「不是文件」，命令以成功结束，用户以为清理已经完成。
现在除 ``FileNotFoundError`` 外一律上抛，由 main 分流成 ``LOCAL_IO_ERROR``。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from boss_agent_cli.commands import clean as clean_module
from boss_agent_cli.main import cli


def _invoke(data_dir: Path, *args: str) -> tuple[int, dict[str, Any]]:
	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(data_dir), *args])
	return result.exit_code, json.loads(result.stdout)


def _fail_lstat_for(paths: set[Path], monkeypatch: pytest.MonkeyPatch) -> None:
	real_lstat = Path.lstat

	def lstat_fails(self: Path) -> Any:
		if self in paths:
			raise PermissionError("permission denied")
		return real_lstat(self)

	monkeypatch.setattr(Path, "lstat", lstat_fails)


def _make_old_file(path: Path, content: str = "[]") -> Path:
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(content, encoding="utf-8")
	old = time.time() - 31 * 86400
	os.utime(path, (old, old))
	return path


def test_snapshot_directory_metadata_error_fails_instead_of_zero(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	data_dir = tmp_path / "data"
	snapshots = data_dir / "chat-history"
	old = _make_old_file(snapshots / "old.json")
	_fail_lstat_for({snapshots}, monkeypatch)

	exit_code, payload = _invoke(data_dir, "clean", "--days", "30")

	assert exit_code == 1
	assert payload["ok"] is False
	assert payload["error"]["code"] == "LOCAL_IO_ERROR"
	assert old.exists()


def test_snapshot_entry_metadata_error_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	data_dir = tmp_path / "data"
	snapshots = data_dir / "chat-history"
	broken = _make_old_file(snapshots / "broken.json")
	_fail_lstat_for({broken}, monkeypatch)

	exit_code, payload = _invoke(data_dir, "clean", "--days", "30")

	assert exit_code == 1
	assert payload["ok"] is False
	assert payload["error"]["code"] == "LOCAL_IO_ERROR"
	assert broken.exists()


def test_search_cache_db_metadata_error_fails(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	data_dir = tmp_path / "data"
	db_path = data_dir / "cache" / "boss_agent.db"
	db_path.parent.mkdir(parents=True)
	db_path.write_bytes(b"not-a-real-db")
	_fail_lstat_for({db_path}, monkeypatch)

	exit_code, payload = _invoke(data_dir, "clean")

	assert exit_code == 1
	assert payload["ok"] is False
	assert payload["error"]["code"] == "LOCAL_IO_ERROR"
	assert db_path.exists()


def test_index_cache_metadata_error_fails_even_in_dry_run(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	data_dir = tmp_path / "data"
	index_cache = data_dir / "cache" / "index_cache.json"
	index_cache.parent.mkdir(parents=True)
	index_cache.write_text("{}", encoding="utf-8")
	_fail_lstat_for({index_cache}, monkeypatch)

	exit_code, payload = _invoke(data_dir, "clean", "--dry-run")

	assert exit_code == 1
	assert payload["ok"] is False
	assert payload["error"]["code"] == "LOCAL_IO_ERROR"
	assert index_cache.exists()


def test_ancestor_metadata_error_aborts_before_delete(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	data_dir = tmp_path / "data"
	index_cache = data_dir / "cache" / "index_cache.json"
	index_cache.parent.mkdir(parents=True)
	index_cache.write_text("{}", encoding="utf-8")
	_fail_lstat_for({index_cache.parent}, monkeypatch)

	exit_code, payload = _invoke(data_dir, "clean")

	assert exit_code == 1
	assert payload["ok"] is False
	assert payload["error"]["code"] == "LOCAL_IO_ERROR"
	assert index_cache.exists()


def test_snapshot_target_not_a_directory_reports_error(tmp_path: Path) -> None:
	data_dir = tmp_path / "data"
	snapshots = data_dir / "chat-history"
	snapshots.parent.mkdir(parents=True)
	snapshots.write_text("not a directory", encoding="utf-8")

	exit_code, payload = _invoke(data_dir, "clean")

	assert exit_code == 1
	assert payload["ok"] is False
	assert payload["error"]["code"] == "LOCAL_IO_ERROR"
	assert snapshots.exists()


def test_dangling_symlink_cache_db_is_not_created(tmp_path: Path) -> None:
	"""悬空链接不能交给 SQLite：connect 会在链接目标处创建文件。"""
	data_dir = tmp_path / "data"
	db_path = data_dir / "cache" / "boss_agent.db"
	db_path.parent.mkdir(parents=True)
	outside = tmp_path / "outside.db"
	db_path.symlink_to(outside)

	exit_code, payload = _invoke(data_dir, "clean")

	assert exit_code == 1
	assert payload["ok"] is False
	assert payload["error"]["code"] == "LOCAL_IO_ERROR"
	assert not outside.exists()
	assert db_path.is_symlink()


def test_symlink_cache_db_is_not_opened_or_modified(tmp_path: Path) -> None:
	"""链接目标的缓存库也不能被 clean 的 DELETE / VACUUM 改写。"""
	data_dir = tmp_path / "data"
	db_path = data_dir / "cache" / "boss_agent.db"
	db_path.parent.mkdir(parents=True)
	outside = tmp_path / "outside.db"
	outside.write_bytes(b"not-a-real-db")
	db_path.symlink_to(outside)

	exit_code, payload = _invoke(data_dir, "clean", "--all")

	assert exit_code == 1
	assert payload["ok"] is False
	assert payload["error"]["code"] == "LOCAL_IO_ERROR"
	assert outside.read_bytes() == b"not-a-real-db"
	assert db_path.is_symlink()


def test_missing_targets_still_succeed(tmp_path: Path) -> None:
	data_dir = tmp_path / "data"
	data_dir.mkdir()

	exit_code, payload = _invoke(data_dir, "clean")

	assert exit_code == 0
	assert payload["ok"] is True
	assert payload["data"]["total_bytes_freed"] == 0
	assert all(row["cleaned"] == 0 for row in payload["data"]["results"])


def test_path_stats_metadata_error_propagates(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	root = tmp_path / "tree"
	root.mkdir()
	child = root / "child.bin"
	child.write_text("payload", encoding="utf-8")
	_fail_lstat_for({child}, monkeypatch)

	with pytest.raises(PermissionError):
		clean_module._path_stats(root)


def test_privacy_preflight_metadata_error_aborts_before_delete(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	data_dir = tmp_path / "data"
	session = data_dir / "auth" / "session.enc"
	session.parent.mkdir(parents=True)
	session.write_text("secret", encoding="utf-8")
	_fail_lstat_for({data_dir / "auth"}, monkeypatch)

	exit_code, payload = _invoke(data_dir, "clean", "--privacy")

	assert exit_code == 1
	assert payload["ok"] is False
	assert payload["error"]["code"] == "LOCAL_IO_ERROR"
	assert payload["data"]["completed"] == []
	assert payload["data"]["failed"][0]["target"] == "登录会话"
	assert session.exists()
