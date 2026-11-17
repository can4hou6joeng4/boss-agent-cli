"""doctor 写探针的符号链接回归测试（Issue #1498）。"""

import errno
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli

ENVELOPE_KEYS = {"ok", "schema_version", "command", "data", "pagination", "error", "hints"}


def _symlink(link: Path, target: Path) -> None:
	try:
		link.symlink_to(target)
	except (NotImplementedError, OSError):
		pytest.skip("当前平台不允许创建符号链接")


def _invoke_doctor(data_dir: Path):
	runner = CliRunner()
	with (
		patch("boss_agent_cli.commands.doctor.AuthManager") as mock_auth,
		patch("boss_agent_cli.commands.doctor.probe_cdp", return_value=None),
		patch("boss_agent_cli.commands.doctor.httpx.get") as mock_httpx,
		patch("boss_agent_cli.commands.doctor.extract_cookies", return_value=None),
	):
		mock_auth.return_value.check_status.return_value = None
		mock_httpx.return_value = MagicMock(status_code=200)
		result = runner.invoke(
			cli,
			["--data-dir", str(data_dir), "--delay", "45-90", "doctor"],
		)
	parsed = json.loads(result.stdout) if result.stdout.strip() else None
	return result.exit_code, parsed


def _check(parsed, name):
	return next(item for item in parsed["data"]["checks"] if item["name"] == name)


def test_doctor_write_probe_does_not_follow_symlink(tmp_path):
	"""调用前已存在的探针路径是符号链接时：不覆盖目标、不删除链接。"""
	auth_dir = tmp_path / "auth"
	auth_dir.mkdir()
	outside = tmp_path / "outside.txt"
	outside.write_text("IMPORTANT-USER-DATA", encoding="utf-8")
	probe = auth_dir / ".doctor-write-test"
	_symlink(probe, outside)

	code, parsed = _invoke_doctor(tmp_path)

	assert code == 0
	assert parsed["ok"] is True
	assert _check(parsed, "data_dir")["status"] == "ok"
	assert probe.is_symlink()
	assert outside.read_text(encoding="utf-8") == "IMPORTANT-USER-DATA"


def test_doctor_write_probe_preserves_existing_probe_path(tmp_path):
	"""调用前已存在的同名探针文件（普通文件）也不能被覆盖或删除。"""
	auth_dir = tmp_path / "auth"
	auth_dir.mkdir()
	probe = auth_dir / ".doctor-write-test"
	probe.write_text("user-data", encoding="utf-8")

	code, parsed = _invoke_doctor(tmp_path)

	assert code == 0
	assert _check(parsed, "data_dir")["status"] == "ok"
	assert probe.read_text(encoding="utf-8") == "user-data"


def test_doctor_write_probe_leaves_no_files_behind(tmp_path):
	"""探针只清理本次创建的唯一临时文件，auth 目录恢复到调用前状态。"""
	auth_dir = tmp_path / "auth"
	auth_dir.mkdir()

	code, parsed = _invoke_doctor(tmp_path)

	assert code == 0
	assert _check(parsed, "data_dir")["status"] == "ok"
	assert list(auth_dir.iterdir()) == []


def test_doctor_write_probe_failure_reports_local_io_error(tmp_path, monkeypatch):
	"""探针写失败不能静默通过：按 LOCAL_IO_ERROR 出错误信封并保留检查项。"""
	auth_dir = tmp_path / "auth"
	auth_dir.mkdir()

	def failing_mkstemp(*args, **kwargs):
		raise PermissionError(errno.EACCES, "denied", str(auth_dir))

	monkeypatch.setattr("boss_agent_cli.commands.doctor.tempfile.mkstemp", failing_mkstemp)

	code, parsed = _invoke_doctor(tmp_path)

	assert code == 1
	assert parsed["ok"] is False
	assert set(parsed) == ENVELOPE_KEYS
	assert parsed["error"]["code"] == "LOCAL_IO_ERROR"
	assert parsed["data"]["summary"] == "broken"
	assert parsed["data"]["failed_check"] == "data_dir"
	assert _check(parsed, "data_dir")["status"] == "error"
	assert list(auth_dir.iterdir()) == []
