"""批次五十四审计修复（Issue #1739）：SQLite 打开前拒绝符号链接与特殊文件。

`sqlite3.connect` 会跟随符号链接把数据库写进目标文件（可能落在数据目录
之外），与 `clean`（#1712）的策略不一致。现在 CacheStore 与 request_budget
共用 `database_path_problem()` 做 lstat 判定。
"""

import os
import sqlite3

import pytest

from boss_agent_cli.api.request_budget import RequestBudget
from boss_agent_cli.cache.store import CacheDatabaseInvalid, CacheStore


def _symlink_or_skip(link, target) -> None:
	try:
		link.symlink_to(target)
	except (OSError, NotImplementedError):
		pytest.skip("当前文件系统不支持符号链接")


def test_cache_store_refuses_symlink_and_keeps_target(tmp_path):
	target = tmp_path / "empty.bin"
	target.write_bytes(b"")
	db_path = tmp_path / "cache" / "boss_agent.db"
	db_path.parent.mkdir()
	_symlink_or_skip(db_path, target)

	with pytest.raises(CacheDatabaseInvalid) as exc_info:
		CacheStore(db_path)

	assert "符号链接" in str(exc_info.value)
	assert db_path.is_symlink()
	assert target.read_bytes() == b"", "SQLite 不能写穿链接目标"


def test_cache_store_refuses_dangling_symlink(tmp_path):
	target = tmp_path / "missing.db"
	db_path = tmp_path / "cache" / "boss_agent.db"
	db_path.parent.mkdir()
	_symlink_or_skip(db_path, target)

	with pytest.raises(CacheDatabaseInvalid):
		CacheStore(db_path)

	assert not target.exists(), "连接不能新建链接目标"


def test_cache_store_refuses_directory(tmp_path):
	db_path = tmp_path / "cache" / "boss_agent.db"
	db_path.mkdir(parents=True)

	with pytest.raises(CacheDatabaseInvalid) as exc_info:
		CacheStore(db_path)

	assert "不是普通文件" in str(exc_info.value)


def test_cache_store_still_opens_regular_database(tmp_path):
	db_path = tmp_path / "cache" / "boss_agent.db"
	with CacheStore(db_path):
		pass
	assert db_path.is_file()


def _budget(tmp_path) -> RequestBudget:
	return RequestBudget(tmp_path, root=tmp_path)


def test_request_budget_refuses_symlink_and_keeps_target(tmp_path):
	target = tmp_path / "empty.bin"
	target.write_bytes(b"")
	_symlink_or_skip(tmp_path / "request-budget.sqlite3", target)

	with pytest.raises(sqlite3.DatabaseError) as exc_info:
		with _budget(tmp_path).attempt():
			pass

	message = str(exc_info.value)
	assert "符号链接" in message
	assert "不是可用的 SQLite 数据库" in message
	assert target.read_bytes() == b"", "SQLite 不能写穿链接目标"


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有 POSIX FIFO")
def test_request_budget_refuses_fifo(tmp_path):
	os.mkfifo(tmp_path / "request-budget.sqlite3")

	with pytest.raises(sqlite3.DatabaseError) as exc_info:
		with _budget(tmp_path).attempt():
			pass

	assert "不是普通文件" in str(exc_info.value)


def test_request_budget_still_writes_regular_database(tmp_path):
	with _budget(tmp_path).attempt():
		pass

	assert (tmp_path / "request-budget.sqlite3").is_file()
