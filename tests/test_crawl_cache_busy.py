"""缓存库被其他连接持锁时的信封契约（Issue #1118）。

外部连接持有 `BEGIN IMMEDIATE` 时，打开旧式（rollback journal）库会在
`PRAGMA journal_mode=WAL` 处等到 busy timeout 后抛 `database is locked`；
已经是 WAL 的库则在写操作（如 `crawl stop` 的 stop 标记）上超时。两条路径都
必须落成可重试的 `CACHE_BUSY` 信封：`crawl stop` 此前 stdout 为 0 字节并带
traceback，`crawl resume` 则被兜成 `NETWORK_ERROR`，让 Agent 把本地锁冲突当成
平台网络问题反复重试。
"""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache import store as cache_store
from boss_agent_cli.cache.store import CacheBusyError, CacheStore, is_cache_busy_error
from boss_agent_cli.main import cli


def _db_path(tmp_path: Path) -> Path:
	return tmp_path / "cache" / "boss_agent.db"


def _legacy_db(tmp_path: Path) -> Path:
	"""旧式 rollback-journal 库：打开时切 WAL 需要写锁。"""
	db_path = _db_path(tmp_path)
	db_path.parent.mkdir(parents=True, exist_ok=True)
	conn = sqlite3.connect(db_path)
	conn.execute("CREATE TABLE IF NOT EXISTS placeholder (id INTEGER)")
	conn.commit()
	conn.close()
	return db_path


def _initialized_wal_db(tmp_path: Path) -> Path:
	"""已建好全部表的 WAL 库：初始化不再写 schema，锁只在写操作上暴露。"""
	db_path = _db_path(tmp_path)
	with CacheStore(db_path):
		pass
	return db_path


@contextmanager
def _write_lock(db_path: Path) -> Iterator[sqlite3.Connection]:
	"""模拟另一个进程长时间持有写锁。"""
	holder = sqlite3.connect(db_path)
	holder.execute("BEGIN IMMEDIATE")
	try:
		yield holder
	finally:
		holder.rollback()
		holder.close()


def _invoke(tmp_path: Path, *args: str) -> Any:
	return CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])


def _assert_cache_busy(result: Any, *, command: str) -> dict[str, Any]:
	assert result.exit_code == 1, result.output
	assert result.stderr == ""
	payload = json.loads(result.output)
	assert payload["ok"] is False
	assert payload["command"] == command
	assert payload["data"] is None
	assert payload["error"]["code"] == "CACHE_BUSY"
	assert payload["error"]["recoverable"] is True
	# 恢复动作必须指向「等锁释放后重试」，不能是删除缓存库或网络重试。
	assert "等待占用" in payload["error"]["recovery_action"]
	assert "不需要删除" in payload["error"]["recovery_action"]
	return payload


@pytest.mark.parametrize(
	("args", "command"),
	[
		# 锁在命令体之前就发生，信封按启动期兜底的顶层命令口径（与
		# CACHE_DATABASE_INVALID 一致）；resume 走统一装饰器带完整路径。
		(("crawl", "stop", "run-1"), "crawl"),
		(("crawl", "status", "run-1"), "crawl"),
		(("crawl", "results", "run-1"), "crawl"),
		(("crawl", "shortlist", "run-1", "--selector", "job-1"), "crawl"),
	],
)
def test_crawl_commands_report_cache_busy_when_wal_conversion_is_locked(tmp_path, monkeypatch, args, command):
	"""旧库切 WAL 时被锁：四个只读/停止命令都必须给单个信封（Issue #1118）。"""
	monkeypatch.setattr(cache_store, "_BUSY_TIMEOUT_SECONDS", 0.05)
	db_path = _legacy_db(tmp_path)
	with _write_lock(db_path):
		result = _invoke(tmp_path, *args)
	_assert_cache_busy(result, command=command)


def test_crawl_stop_reports_cache_busy_when_write_lock_is_held(tmp_path, monkeypatch):
	"""WAL 库初始化正常，锁在 `request_crawl_stop` 的 UPDATE 上暴露。"""
	monkeypatch.setattr(cache_store, "_BUSY_TIMEOUT_SECONDS", 0.05)
	db_path = _initialized_wal_db(tmp_path)
	with _write_lock(db_path):
		result = _invoke(tmp_path, "crawl", "stop", "run-1")
	_assert_cache_busy(result, command="crawl")


def test_crawl_resume_reports_cache_busy_not_network_error(tmp_path, monkeypatch):
	"""resume 走统一装饰器：锁冲突不能被兜成可重试的 NETWORK_ERROR。"""
	monkeypatch.setattr(cache_store, "_BUSY_TIMEOUT_SECONDS", 0.05)
	db_path = _legacy_db(tmp_path)
	with _write_lock(db_path):
		result = _invoke(tmp_path, "crawl", "resume", "run-1")
	_assert_cache_busy(result, command="crawl.resume")


def test_cache_store_opens_legacy_db_when_no_writer_holds_the_lock(tmp_path, monkeypatch):
	"""没有锁时必须完成 delete → WAL 的切换，不能把正常旧库误报成 CACHE_BUSY。"""
	monkeypatch.setattr(cache_store, "_BUSY_TIMEOUT_SECONDS", 0.05)
	db_path = _legacy_db(tmp_path)
	with CacheStore(db_path) as cache:
		assert cache.get_crawl_run("missing") is None


def test_is_cache_busy_error_only_matches_lock_messages():
	assert is_cache_busy_error(sqlite3.OperationalError("database is locked"))
	assert is_cache_busy_error(sqlite3.OperationalError("database table is locked"))
	assert not is_cache_busy_error(sqlite3.OperationalError("no such table: crawl_runs"))
	assert not is_cache_busy_error(sqlite3.OperationalError("disk I/O error"))
	# 只有 OperationalError 的锁消息算 busy；其他 sqlite 错误走各自契约。
	assert not is_cache_busy_error(sqlite3.DatabaseError("database is locked"))
	assert not is_cache_busy_error(RuntimeError("database is locked"))


def test_cache_busy_error_message_names_the_database(tmp_path):
	exc = CacheBusyError(_db_path(tmp_path), "database is locked")
	assert str(_db_path(tmp_path)) in str(exc)
	assert "database is locked" in str(exc)
