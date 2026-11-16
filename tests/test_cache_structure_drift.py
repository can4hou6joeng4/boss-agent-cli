"""缓存库结构漂移时的信封契约（Issue #1174）。

`shortlist_records` 缺列（旧版本建的库、迁移中断）时，`shortlist list` /
`shortlist add` 以前把 `sqlite3.OperationalError` 直接抛到命令层：stdout 为空、
stderr 是 traceback，违反「每个命令只输出一个七键信封」的契约。CacheStore 的
连接现在把执行期的数据/结构错误归一为 `CacheDatabaseInvalid`，由 `main.py`
统一输出 `CACHE_DATABASE_INVALID`；锁冲突仍单独报可重试的 `CACHE_BUSY`，
代码写错的 `ProgrammingError` 保持原样上抛。
"""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheBusyError, CacheDatabaseInvalid, CacheStore
from boss_agent_cli.main import cli


def _db_path(tmp_path: Path) -> Path:
	return tmp_path / "cache" / "boss_agent.db"


def _drifted_db(tmp_path: Path) -> Path:
	"""建好真实库后，把 shortlist_records 换成缺列的旧结构。"""
	db_path = _db_path(tmp_path)
	with CacheStore(db_path):
		pass
	conn = sqlite3.connect(db_path)
	try:
		conn.execute("ALTER TABLE shortlist_records RENAME TO shortlist_old")
		conn.execute("CREATE TABLE shortlist_records (security_id TEXT, job_id TEXT)")
		conn.commit()
	finally:
		conn.close()
	return db_path


def _invoke(tmp_path: Path, *args: str) -> Any:
	return CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])


@pytest.mark.parametrize(
	"args",
	[
		("shortlist", "list"),
		("shortlist", "add", "s1", "j1", "--title", "t"),
	],
)
def test_shortlist_structure_drift_returns_envelope(tmp_path, args):
	"""缺列时给 CACHE_DATABASE_INVALID 信封，而不是 traceback。"""
	_drifted_db(tmp_path)
	result = _invoke(tmp_path, *args)
	assert result.exit_code == 1, result.output
	assert result.stderr == ""
	assert "Traceback" not in result.output
	payload = json.loads(result.output)
	assert payload["ok"] is False
	assert payload["command"] == "shortlist"
	assert payload["data"] is None
	assert payload["error"]["code"] == "CACHE_DATABASE_INVALID"
	assert payload["error"]["recoverable"] is True
	assert "备份" in payload["error"]["recovery_action"]
	assert "column" in payload["error"]["message"]
	# 错误路径不代替用户删库：旧库与旧数据原样保留，恢复动作由人执行。
	assert _db_path(tmp_path).exists()


def test_cache_store_translates_query_time_database_error(tmp_path):
	"""执行期 DatabaseError 归一到 CacheDatabaseInvalid，并带上库路径。"""
	db_path = _drifted_db(tmp_path)
	with CacheStore(db_path) as store:
		with pytest.raises(CacheDatabaseInvalid) as excinfo:
			store.list_shortlist()
	assert excinfo.value.path == db_path
	assert "no such column: title" in str(excinfo.value)


@contextmanager
def _write_lock(db_path: Path) -> Iterator[sqlite3.Connection]:
	"""模拟另一个进程持有写锁。"""
	holder = sqlite3.connect(db_path)
	holder.execute("BEGIN IMMEDIATE")
	try:
		yield holder
	finally:
		holder.rollback()
		holder.close()


def test_write_on_locked_database_still_reports_busy(tmp_path, monkeypatch):
	"""执行期锁冲突仍是可重试的 CacheBusyError，不能被说成库损坏。"""
	monkeypatch.setattr("boss_agent_cli.cache.store._BUSY_TIMEOUT_SECONDS", 0.05)
	db_path = _db_path(tmp_path)
	with CacheStore(db_path) as store:
		with _write_lock(db_path):
			with pytest.raises(CacheBusyError) as excinfo:
				store.add_shortlist({"security_id": "s1", "job_id": "j1", "title": "t"})
	assert excinfo.value.path == db_path
	assert "locked" in str(excinfo.value)


def test_programming_error_is_not_reported_as_corrupt_cache(tmp_path):
	"""SQL/绑定参数写错是我们的缺陷，不能被包装成「本地数据损坏」。"""
	with CacheStore(_db_path(tmp_path)) as store:
		with pytest.raises(sqlite3.ProgrammingError):
			store._conn.execute("SELECT ?", (1, 2))
