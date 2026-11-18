"""批次五十六审计修复（Issue #1743）：剩余 SQLite 打开点拒绝符号链接。

`stats` 用会解析链接目标的 `path_mode` 判定后直接 connect：缓存库是符号链接时
会读取外部库，并在目标旁创建 WAL 的 `-shm` / `-wal`。automation 的
`.run-lock.sqlite3` / `.control-lock.sqlite3` 直接 connect：链接会让 SQLite
把锁库写进目标文件。三处现在与 CacheStore / clean（#1739 / #1712）同口径。
"""

import json

from click.testing import CliRunner
import pytest

from boss_agent_cli.automation.storage import AutomationStateError, AutomationStore
from boss_agent_cli.main import cli


def _invoke(data_dir, *args: str):
	return CliRunner().invoke(cli, ["--data-dir", str(data_dir), "--json", *args])


def _symlink_or_skip(link, target) -> None:
	try:
		link.symlink_to(target)
	except (OSError, NotImplementedError):
		pytest.skip("当前文件系统不支持符号链接")


def test_stats_refuses_symlinked_cache_db(tmp_path):
	data_dir = tmp_path / "data"
	(tmp_path / "data" / "cache").mkdir(parents=True)
	target = tmp_path / "external.db"
	target.write_bytes(b"")
	_symlink_or_skip(data_dir / "cache" / "boss_agent.db", target)

	result = _invoke(data_dir, "stats")

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "LOCAL_IO_ERROR"
	assert "符号链接" in payload["error"]["message"]
	assert target.read_bytes() == b"", "SQLite 不能写穿链接目标"
	assert not (tmp_path / "external.db-shm").exists()


def test_stats_refuses_dangling_cache_db_symlink(tmp_path):
	data_dir = tmp_path / "data"
	(tmp_path / "data" / "cache").mkdir(parents=True)
	target = tmp_path / "missing.db"
	_symlink_or_skip(data_dir / "cache" / "boss_agent.db", target)

	result = _invoke(data_dir, "stats")

	assert result.exit_code == 1, result.output
	assert json.loads(result.stdout)["error"]["code"] == "LOCAL_IO_ERROR"
	assert not target.exists(), "连接不能新建链接目标"


def test_stats_missing_cache_db_still_reports_not_established(tmp_path):
	data_dir = tmp_path / "data"
	data_dir.mkdir()

	result = _invoke(data_dir, "stats")

	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	assert payload["ok"] is True
	assert "缓存尚未建立" in payload["data"]["note"]


def test_execution_lock_refuses_symlink_and_keeps_target(tmp_path):
	store = AutomationStore(tmp_path)
	target = tmp_path / "outside-run-lock.sqlite3"
	_symlink_or_skip(store.root / ".run-lock.sqlite3", target)

	with pytest.raises(AutomationStateError) as exc_info:
		with store.execution_lock():
			pass

	assert "符号链接" in str(exc_info.value)
	assert not target.exists(), "SQLite 不能新建链接目标"


def test_control_lock_refuses_symlink_and_keeps_target(tmp_path):
	store = AutomationStore(tmp_path)
	target = tmp_path / "outside-control-lock.sqlite3"
	_symlink_or_skip(store.root / ".control-lock.sqlite3", target)

	with pytest.raises(AutomationStateError) as exc_info:
		store.request_stop("manual-stop")

	assert "符号链接" in str(exc_info.value)
	assert not target.exists()


def test_missing_lock_databases_still_work(tmp_path):
	store = AutomationStore(tmp_path)
	with store.execution_lock():
		pass
	store.request_stop("manual-stop")

	assert (store.root / ".run-lock.sqlite3").is_file()
	assert (store.root / ".control-lock.sqlite3").is_file()
