"""`clean --privacy` 缓存库路径状态的 fail-closed 回归测试（Issue #1690）。

数据库路径元数据不可读时，预检不能把它当成「没有库」；必须在删除任何
敏感文件之前报错。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from click.testing import CliRunner
import pytest

from boss_agent_cli.main import cli


def _db_path(data_dir: Path) -> Path:
	return data_dir / "cache" / "boss_agent.db"


def _make_db(data_dir: Path) -> Path:
	path = _db_path(data_dir)
	path.parent.mkdir(parents=True, exist_ok=True)
	sqlite3.connect(path).close()
	return path


def _make_sensitive_files(data_dir: Path) -> tuple[Path, Path]:
	session = data_dir / "auth" / "session.enc"
	session.parent.mkdir(parents=True, exist_ok=True)
	session.write_bytes(b"offline-session")
	resume = data_dir / "resumes" / "candidate.json"
	resume.parent.mkdir(parents=True, exist_ok=True)
	resume.write_text('{"name": "offline"}', encoding="utf-8")
	return session, resume


def _invoke(data_dir: Path, *args: str):
	return CliRunner().invoke(cli, ["--json", "--data-dir", str(data_dir), *args])


def test_privacy_aborts_before_delete_when_db_metadata_unreadable(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	db_path = _make_db(tmp_path)
	session, resume = _make_sensitive_files(tmp_path)
	real_lstat = Path.lstat
	real_exists = Path.exists

	def lstat_fails(self: Path) -> Any:
		if self == db_path:
			raise PermissionError("permission denied")
		return real_lstat(self)

	def exists_lies(self: Path) -> bool:
		if self == db_path:
			# 模拟 Python 3.14：exists() 吞掉权限错误并返回 False。
			return False
		return real_exists(self)

	monkeypatch.setattr(Path, "lstat", lstat_fails)
	monkeypatch.setattr(Path, "exists", exists_lies)

	result = _invoke(tmp_path, "clean", "--privacy")

	assert result.exit_code == 1, result.output
	payload: dict[str, Any] = json.loads(result.stdout)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "LOCAL_IO_ERROR"
	assert str(db_path) in result.output or "boss_agent.db" in result.output
	assert session.exists()
	assert resume.exists()


def test_privacy_still_cleans_when_db_is_missing(tmp_path: Path) -> None:
	session, resume = _make_sensitive_files(tmp_path)

	result = _invoke(tmp_path, "clean", "--privacy")

	assert result.exit_code == 0, result.output
	payload: dict[str, Any] = json.loads(result.stdout)
	assert payload["ok"] is True
	assert not session.exists()
	assert not resume.exists()


def test_privacy_reports_database_directory_before_delete(tmp_path: Path) -> None:
	_db_path(tmp_path).mkdir(parents=True)
	session, resume = _make_sensitive_files(tmp_path)

	result = _invoke(tmp_path, "clean", "--privacy")

	assert result.exit_code == 1, result.output
	payload: dict[str, Any] = json.loads(result.stdout)
	assert payload["ok"] is False
	assert session.exists()
	assert resume.exists()
