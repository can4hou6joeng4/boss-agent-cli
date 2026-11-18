"""风险锁路径状态读取的 fail-closed 回归测试（Issue #1686）。

风险锁是平台风控的硬边界：路径存在性无法判断、悬空链接、目录或 FIFO
都不能被当成「没有锁」；指向普通文件的符号链接仍应正常读取。
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from boss_agent_cli.api import cdp_risk_lock
from boss_agent_cli.api.client import PlatformRiskError


def _lock_path(data_dir: Path) -> Path:
	return cdp_risk_lock.lock_path(data_dir)


def test_dangling_symlink_is_read_as_corrupt_lock(tmp_path: Path) -> None:
	_lock_path(tmp_path).symlink_to(tmp_path / "missing")

	lock = cdp_risk_lock.read_lock(tmp_path)

	assert lock is not None
	assert lock.corrupt is True
	status = cdp_risk_lock.lock_status(tmp_path)
	assert status["locked"] is True
	assert status["corrupt"] is True
	assert status["path"] == str(_lock_path(tmp_path))


def test_dangling_symlink_blocks_transport(tmp_path: Path) -> None:
	_lock_path(tmp_path).symlink_to(tmp_path / "missing")

	with pytest.raises(PlatformRiskError):
		cdp_risk_lock.raise_if_locked(tmp_path)


def test_directory_lock_path_is_read_as_corrupt_lock(tmp_path: Path) -> None:
	_lock_path(tmp_path).mkdir()

	lock = cdp_risk_lock.read_lock(tmp_path)

	assert lock is not None
	assert lock.corrupt is True


def test_metadata_error_is_read_as_corrupt_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	def fail_lstat(self: Path) -> None:
		raise PermissionError("permission denied")

	monkeypatch.setattr(Path, "lstat", fail_lstat)

	lock = cdp_risk_lock.read_lock(tmp_path)

	assert lock is not None
	assert lock.corrupt is True


def test_non_regular_path_is_not_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setattr(cdp_risk_lock, "_lock_path_mode", lambda path: stat.S_IFIFO)

	def must_not_read(path: Path) -> cdp_risk_lock.RiskLock:
		raise AssertionError(f"不能读取非普通文件: {path}")

	monkeypatch.setattr(cdp_risk_lock, "_read_lock_file", must_not_read)

	lock = cdp_risk_lock.read_lock(tmp_path)

	assert lock is not None
	assert lock.corrupt is True


def test_symlink_to_regular_file_is_followed(tmp_path: Path) -> None:
	target = tmp_path / "real-lock.json"
	target.write_text('{"reason": "ACCOUNT_RISK", "platform_code": 36}', encoding="utf-8")
	_lock_path(tmp_path).symlink_to(target)

	lock = cdp_risk_lock.read_lock(tmp_path)

	assert lock is not None
	assert lock.corrupt is False
	assert lock.reason == "ACCOUNT_RISK"
	assert lock.platform_code == 36
