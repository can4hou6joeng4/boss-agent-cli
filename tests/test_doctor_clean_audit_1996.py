"""doctor / clean 本地状态边界回归（Issue #1996–#2001）。"""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

import boss_agent_cli.commands.clean as clean_mod
from boss_agent_cli.auth.token_store import TokenStore
from boss_agent_cli.main import cli


def _invoke(*args, tmp_path: Path | None = None):
	cli_args = ["--data-dir", str(tmp_path)] if tmp_path is not None else []
	result = CliRunner().invoke(cli, [*cli_args, *args])
	return result, (json.loads(result.stdout) if result.stdout.strip() else None)


def _broken_salt(tmp_path: Path) -> Path:
	"""(salt 是目录 + session.enc 是普通文件) 的确定性本地状态损坏。"""
	auth_dir = tmp_path / "auth"
	auth_dir.mkdir(parents=True, exist_ok=True)
	(auth_dir / "salt").mkdir()
	(auth_dir / "session.enc").write_bytes(b"not-a-real-token")
	return auth_dir / "salt"


# ── #1996：salt 路径异常不再 traceback / 不再报可重试网络错误 ──────────


def test_token_store_converts_salt_runtime_error_to_oserror(tmp_path: Path) -> None:
	_broken_salt(tmp_path)
	store = TokenStore(tmp_path / "auth")

	with pytest.raises(OSError) as exc_info:
		store.load()

	assert exc_info.value.filename is not None
	assert "salt" in str(exc_info.value.filename)
	assert "salt 路径状态异常" in str(exc_info.value)


def test_status_reports_local_io_error_for_broken_salt(tmp_path: Path) -> None:
	_broken_salt(tmp_path)

	result, payload = _invoke("status", tmp_path=tmp_path)

	assert result.exit_code == 1, result.output
	assert payload is not None and set(payload) == {
		"ok", "schema_version", "command", "data", "pagination", "error", "hints"
	}
	assert payload["ok"] is False
	assert payload["error"]["code"] == "LOCAL_IO_ERROR"
	assert payload["error"]["recoverable"] is False


@patch("boss_agent_cli.commands.doctor.extract_cookies")
@patch("boss_agent_cli.commands.doctor.httpx.get")
@patch("boss_agent_cli.commands.doctor.probe_cdp")
def test_doctor_reports_local_io_error_for_broken_salt(
	mock_cdp, mock_httpx, mock_cookie, tmp_path: Path
) -> None:
	_broken_salt(tmp_path)
	mock_cdp.return_value = None
	mock_httpx.return_value = MagicMock(status_code=200)
	mock_cookie.return_value = None

	result, payload = _invoke("--delay", "45-90", "doctor", tmp_path=tmp_path)

	assert result.exit_code == 1, result.output
	assert payload is not None
	assert payload["error"]["code"] == "LOCAL_IO_ERROR"
	assert payload["error"]["recoverable"] is False
	assert payload["data"]["failed_check"] == "auth_storage"
	assert payload["data"]["checks"], "已完成的检查项必须随错误信封返回"


# ── #1998：auth_salt 检查按 lstat 判定 ────────────────────────────────


def _doctor_checks(tmp_path: Path) -> dict[str, dict]:
	with (
		patch("boss_agent_cli.commands.doctor.AuthManager") as mock_auth,
		patch("boss_agent_cli.commands.doctor.probe_cdp") as mock_cdp,
		patch("boss_agent_cli.commands.doctor.httpx.get") as mock_httpx,
		patch("boss_agent_cli.commands.doctor.extract_cookies") as mock_cookie,
	):
		mock_auth.return_value.check_status.return_value = None
		mock_cdp.return_value = None
		mock_httpx.return_value = MagicMock(status_code=200)
		mock_cookie.return_value = None
		result, payload = _invoke("--delay", "45-90", "doctor", tmp_path=tmp_path)

	assert result.exit_code == 0, result.output
	assert payload is not None
	return {item["name"]: item for item in payload["data"]["checks"]}


def test_doctor_auth_salt_directory_is_error(tmp_path: Path) -> None:
	(tmp_path / "auth").mkdir(parents=True)
	(tmp_path / "auth" / "salt").mkdir()

	checks = _doctor_checks(tmp_path)

	assert checks["auth_salt"]["status"] == "error"
	assert "fail-closed" in checks["auth_salt"]["detail"]


def test_doctor_auth_salt_dangling_symlink_is_error(tmp_path: Path) -> None:
	(tmp_path / "auth").mkdir(parents=True)
	os.symlink(tmp_path / "missing-target", tmp_path / "auth" / "salt")

	checks = _doctor_checks(tmp_path)

	assert checks["auth_salt"]["status"] == "error"


def test_doctor_auth_salt_missing_is_warn(tmp_path: Path) -> None:
	(tmp_path / "auth").mkdir(parents=True)

	checks = _doctor_checks(tmp_path)

	assert checks["auth_salt"]["status"] == "warn"


def test_doctor_auth_salt_regular_file_is_ok(tmp_path: Path) -> None:
	(tmp_path / "auth").mkdir(parents=True)
	(tmp_path / "auth" / "salt").write_bytes(b"x" * 16)

	checks = _doctor_checks(tmp_path)

	assert checks["auth_salt"]["status"] == "ok"


# ── #2001：--live-probe help 与默认网络探测一致 ──────────────────────


def test_doctor_live_probe_help_mentions_default_connectivity_check() -> None:
	result = CliRunner().invoke(cli, ["doctor", "--help"])

	assert result.exit_code == 0, result.output
	assert "站点连通性" in result.output


# ── #1997：非常规锁删除后 local_lock_removed 不再误报 ────────────────


def test_clean_risk_lock_directory_reports_removed(tmp_path: Path) -> None:
	(tmp_path / "cdp_risk_lock.json").mkdir()

	result, payload = _invoke("clean", "--risk-lock", tmp_path=tmp_path)

	assert result.exit_code == 0, result.output
	assert payload is not None
	assert payload["data"]["risk_lock"]["local_lock_removed"] is True
	assert not (tmp_path / "cdp_risk_lock.json").exists()


def test_clean_risk_lock_absent_still_reports_false(tmp_path: Path) -> None:
	result, payload = _invoke("clean", "--risk-lock", tmp_path=tmp_path)

	assert result.exit_code == 0, result.output
	assert payload is not None
	assert payload["data"]["risk_lock"]["local_lock_removed"] is False


# ── #1999：--privacy 中途失败时 remaining 包含 crawl ─────────────────


def test_clean_privacy_failure_lists_crawl_in_remaining(tmp_path: Path, monkeypatch) -> None:
	(tmp_path / "chat-history").mkdir()

	def flaky_clean_path(path, *, dry_run, root=None):
		if Path(path).name == "chat-history":
			raise OSError(errno.EACCES, "Permission denied", str(path))
		return 0, 0, 0

	monkeypatch.setattr(clean_mod, "_clean_path", flaky_clean_path)
	result, payload = _invoke("clean", "--privacy", tmp_path=tmp_path)

	assert result.exit_code == 1, result.output
	assert payload is not None
	assert "crawl 任务与导出" in payload["data"]["remaining"]
	assert "导出文件" in payload["data"]["remaining"]


# ── #2000：符号链接的字节数按链接本身统计 ────────────────────────────


def test_clean_dir_counts_link_size_not_target_size(tmp_path: Path) -> None:
	target = tmp_path / "target.bin"
	target.write_bytes(b"x" * 4096)
	os.utime(target, (0, 0))
	snapshot_dir = tmp_path / "chat-history"
	snapshot_dir.mkdir()
	os.symlink(target, snapshot_dir / "snapshot-link.json")
	expected_size = (snapshot_dir / "snapshot-link.json").lstat().st_size

	freed, count, skipped = clean_mod._clean_dir(snapshot_dir, days=0, dry_run=False)

	assert count == 1
	assert freed == expected_size
	assert freed != 4096
	assert target.exists(), "链接目标必须保留"


def test_path_stats_link_uses_lstat(tmp_path: Path) -> None:
	target = tmp_path / "target.bin"
	target.write_bytes(b"x" * 4096)
	link = tmp_path / "link.json"
	os.symlink(target, link)

	size, count = clean_mod._path_stats(link)

	assert count == 1
	assert size == link.lstat().st_size
