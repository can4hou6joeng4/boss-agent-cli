"""风险锁主位置写盘失败时的后备持久化（Issue #1270）。

主锁文件本身不可写（被目录占位、权限损坏）时，`write_lock` 把锁写到
`<data_dir>/cache/` 下的后备位置，保证新进程的 `read_lock` / `raise_if_locked`
仍能 fail-closed 读到阻断；两处都失败才抛出，调用方保留进程内锁并在信封里
说明「新进程不再受保护」。`clean --risk-lock` 必须同时清理两份。
"""

import json

import pytest
from click.testing import CliRunner

from boss_agent_cli.api import cdp_risk_lock
from boss_agent_cli.main import cli


def _block_primary(data_dir):
	"""把主锁路径占成目录：写入时 `os.replace` 抛 OSError，读时按损坏处理。"""
	path = cdp_risk_lock.lock_path(data_dir)
	path.mkdir(parents=True, exist_ok=True)
	return path


def test_primary_write_failure_falls_back_and_is_readable(tmp_path):
	_block_primary(tmp_path)

	lock = cdp_risk_lock.write_lock(tmp_path, None, reason="ACCOUNT_RISK", platform_code=36, now=1.0)

	assert lock.reason == "ACCOUNT_RISK"
	fallback = cdp_risk_lock.lock_paths(tmp_path)[1]
	assert fallback.exists()
	read = cdp_risk_lock.read_lock(tmp_path)
	assert read is not None
	assert read.corrupt is False
	assert read.reason == "ACCOUNT_RISK"
	with pytest.raises(Exception):
		cdp_risk_lock.raise_if_locked(tmp_path)


def test_both_locations_failing_raises(tmp_path):
	_block_primary(tmp_path)
	cdp_risk_lock.lock_paths(tmp_path)[1].mkdir(parents=True)

	with pytest.raises(OSError):
		cdp_risk_lock.write_lock(tmp_path, None)


def test_primary_success_removes_stale_fallback(tmp_path):
	fallback = cdp_risk_lock.lock_paths(tmp_path)[1]
	fallback.parent.mkdir(parents=True)
	fallback.write_text("{}", encoding="utf-8")

	cdp_risk_lock.write_lock(tmp_path, None)

	assert cdp_risk_lock.lock_path(tmp_path).exists()
	assert not fallback.exists()


def test_clear_lock_removes_both_locations(tmp_path):
	_block_primary(tmp_path)
	cdp_risk_lock.write_lock(tmp_path, None)
	# 主位置被占成目录时无法写主锁文件；手工移除目录后写主锁，构造两份并存。
	cdp_risk_lock.lock_path(tmp_path).rmdir()
	cdp_risk_lock.lock_path(tmp_path).write_text("{}", encoding="utf-8")

	assert cdp_risk_lock.clear_lock(tmp_path) is True
	assert not cdp_risk_lock.lock_path(tmp_path).exists()
	assert not cdp_risk_lock.lock_paths(tmp_path)[1].exists()
	assert cdp_risk_lock.clear_lock(tmp_path) is False


def test_lock_status_reports_fallback_path(tmp_path):
	_block_primary(tmp_path)
	cdp_risk_lock.write_lock(tmp_path, None, now=1.0)

	status = cdp_risk_lock.lock_status(tmp_path)

	assert status["locked"] is True
	assert status["path"] == str(cdp_risk_lock.lock_paths(tmp_path)[1])


def test_clean_risk_lock_removes_both_locations(tmp_path):
	_block_primary(tmp_path)
	cdp_risk_lock.write_lock(tmp_path, None)
	cdp_risk_lock.lock_path(tmp_path).rmdir()
	cdp_risk_lock.lock_path(tmp_path).write_text("{}", encoding="utf-8")

	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "clean", "--risk-lock"])

	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	target = next(item for item in payload["data"]["results"] if item["target"] == "CDP 风控锁")
	assert target["cleaned"] == 2
	assert not cdp_risk_lock.lock_path(tmp_path).exists()
	assert not cdp_risk_lock.lock_paths(tmp_path)[1].exists()
