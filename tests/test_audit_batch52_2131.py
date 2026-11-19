"""审计批次五十二：status/doctor 的 session 文件状态读取（Issue #2131）。

`_stoken_freshness_check` 原先 exists() 与 stat() 分开调用，并发 logout /
原子替换落在窗口内会让 FileNotFoundError 逃逸成 LOCAL_IO_ERROR；
`assess_auth_health` 的 has_token=False 分支连续三次 exists()，状态自相矛盾，
且 3.14 的 exists() 会把权限错误吞成「没登录」。
"""

from __future__ import annotations

from errno import EACCES, ENOENT
from pathlib import Path

import pytest

from boss_agent_cli.auth.health import assess_auth_health, auth_dir_for_platform

_TOKEN = {"cookies": {"wt2": "t"}, "stoken": "s"}


def _missing(path: Path) -> FileNotFoundError:
	return FileNotFoundError(ENOENT, "No such file or directory", str(path))


def _denied(path: Path) -> PermissionError:
	return PermissionError(EACCES, "Permission denied", str(path))


def _session_path(tmp_path: Path) -> Path:
	return auth_dir_for_platform(tmp_path, "zhipin") / "session.enc"


def _check(report, name: str):
	return next(item for item in report.checks if item.name == name)


def _fake_stat(target: Path, errors: list[Exception], *, fail_after: int = 0):
	"""让 target 的第 ``fail_after + 1`` 次及以后的 stat 依次抛 ``errors``。"""
	real_stat = Path.stat
	state = {"calls": 0}

	def fake(self: Path, *args, **kwargs):
		if self == target:
			state["calls"] += 1
			if state["calls"] > fail_after and errors:
				raise errors.pop(0)
		return real_stat(self, *args, **kwargs)

	return fake


def test_freshness_tolerates_session_removed_between_checks(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	session_path = _session_path(tmp_path)
	session_path.parent.mkdir(parents=True)
	session_path.write_bytes(b"ciphertext")
	# 第一次 stat（旧实现的 exists() 内部）成功，第二次（真正的 stat）抛缺失：
	# 模拟 exists 与 stat 之间被并发 logout 删除。
	monkeypatch.setattr(
		Path,
		"stat",
		_fake_stat(session_path, [_missing(session_path)], fail_after=1),
	)

	report = assess_auth_health(tmp_path, token=_TOKEN)

	assert _check(report, "stoken_freshness").status == "ok"


def test_freshness_with_missing_session_time_stays_ok(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	session_path = _session_path(tmp_path)
	monkeypatch.setattr(Path, "stat", _fake_stat(session_path, [_missing(session_path)]))

	report = assess_auth_health(tmp_path, token=_TOKEN)

	freshness = _check(report, "stoken_freshness")
	assert freshness.status == "ok"
	assert "未提供 session 文件时间" in freshness.detail


def test_freshness_with_unreadable_session_reports_warn(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	session_path = _session_path(tmp_path)
	session_path.parent.mkdir(parents=True)
	session_path.write_bytes(b"ciphertext")
	monkeypatch.setattr(Path, "stat", _fake_stat(session_path, [_denied(session_path)]))

	report = assess_auth_health(tmp_path, token=_TOKEN)

	freshness = _check(report, "stoken_freshness")
	assert freshness.status == "warn"
	assert "无法读取 session 文件时间" in freshness.detail


def test_unreadable_session_without_token_reports_existing(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	session_path = _session_path(tmp_path)
	session_path.parent.mkdir(parents=True)
	session_path.write_bytes(b"ciphertext")
	real_lstat = Path.lstat

	def fake_lstat(self: Path, *args, **kwargs):
		if self == session_path:
			raise _denied(session_path)
		return real_lstat(self, *args, **kwargs)

	monkeypatch.setattr(Path, "lstat", fake_lstat)

	report = assess_auth_health(tmp_path, token=None)

	credential = _check(report, "credential_file")
	assert credential.status == "error"
	assert "未检测到本地登录态" not in credential.detail


def test_missing_session_without_token_still_reports_not_logged_in(tmp_path: Path) -> None:
	report = assess_auth_health(tmp_path, token=None)

	credential = _check(report, "credential_file")
	assert credential.status == "warn"
	assert "未检测到本地登录态" in credential.detail


def test_recent_session_reports_hours(tmp_path: Path) -> None:
	session_path = _session_path(tmp_path)
	session_path.parent.mkdir(parents=True)
	session_path.write_bytes(b"ciphertext")

	report = assess_auth_health(tmp_path, token=_TOKEN)

	freshness = _check(report, "stoken_freshness")
	assert freshness.status == "ok"
	assert "小时前更新" in freshness.detail
