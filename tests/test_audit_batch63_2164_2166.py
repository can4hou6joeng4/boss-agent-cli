"""本地缓存 BLOB 存储类漂移与装饰器错误分类（Issue #2164 / #2165 / #2166）。

SQLite 的 TEXT 亲和性不强制类型：BLOB 存储类与 TEXT 内容比较时永不相等。
#2147 只让读取展示路径兼容了 BLOB，运行 / 删除 / 保存 / 去重路径仍按 TEXT
查找；装饰器也没有缓存异常分支。本文件锁定三条修复。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheRecordInvalid, CacheStore, CacheWriteRejected
from boss_agent_cli.display import handle_auth_errors
from boss_agent_cli.main import cli


def _make_store(tmp_path: Path) -> tuple[CacheStore, Path]:
	db = tmp_path / "cache" / "boss_agent.db"
	db.parent.mkdir(parents=True, exist_ok=True)
	store = CacheStore(db)
	return store, db


def _insert_blob_saved_search(db: Path, name: bytes, params: str = '{"query": "python"}') -> None:
	conn = sqlite3.connect(db)
	conn.execute(
		"INSERT INTO saved_searches (name, params, created_at, updated_at) VALUES (?, ?, ?, ?)",
		(sqlite3.Binary(name), params, 1.0, 2.0),
	)
	conn.commit()
	conn.close()


def test_blob_saved_search_is_readable_runnable_and_deletable(tmp_path: Path) -> None:
	"""BLOB 名称的 preset / watch：列表可见、按名读取成功、删除生效。"""
	store, db = _make_store(tmp_path)
	try:
		_insert_blob_saved_search(db, b"blobwatch")

		items, corrupt = store.list_saved_searches_with_corrupt()
		assert [item["name"] for item in items] == ["blobwatch"]
		assert corrupt == []

		record = store.get_saved_search("blobwatch")
		assert record is not None
		assert record["name"] == "blobwatch"
		assert record["params"] == {"query": "python"}

		assert store.delete_saved_search("blobwatch") is True
		assert store.get_saved_search("blobwatch") is None
	finally:
		store.close()


def test_blob_saved_search_delete_removes_watch_hits(tmp_path: Path) -> None:
	"""删除 BLOB 名称的 watch 时命中历史一并清掉（两种存储类都匹配）。"""
	store, db = _make_store(tmp_path)
	try:
		_insert_blob_saved_search(db, b"blobwatch")
		conn = sqlite3.connect(db)
		conn.execute(
			"INSERT INTO watch_hits (search_name, job_key, payload, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
			(sqlite3.Binary(b"blobwatch"), "jid-1", "{}", 1.0, 2.0),
		)
		conn.commit()
		conn.close()

		assert store.delete_saved_search("blobwatch") is True
		conn = sqlite3.connect(db)
		assert conn.execute("SELECT COUNT(*) FROM watch_hits").fetchone()[0] == 0
		conn.close()
	finally:
		store.close()


def test_save_saved_search_normalizes_blob_row(tmp_path: Path) -> None:
	"""同名保存把 BLOB 漂移行归一成单一 TEXT 行，不再出现两条记录。"""
	store, db = _make_store(tmp_path)
	try:
		_insert_blob_saved_search(db, b"blobwatch")

		store.save_saved_search("blobwatch", {"query": "go"})

		conn = sqlite3.connect(db)
		rows = conn.execute("SELECT name, typeof(name), params FROM saved_searches").fetchall()
		conn.close()
		assert rows == [("blobwatch", "text", json.dumps({"query": "go"}, ensure_ascii=False, sort_keys=True))]
	finally:
		store.close()


def test_watch_run_all_reports_vanished_record(tmp_path: Path) -> None:
	"""--all 对运行时已消失的记录输出显式条目，不再静默少跑一个 watch。"""
	store, _db = _make_store(tmp_path)
	store.save_saved_search("p1", {"query": "Golang", "city": "广州"})
	store.close()

	runner = CliRunner()
	with patch.object(CacheStore, "get_saved_search", return_value=None):
		result = runner.invoke(cli, ["--data-dir", str(tmp_path), "watch", "run", "--all"])

	assert result.exit_code == 0, result.output
	envelope = json.loads(result.output)
	assert envelope["ok"] is True
	data = envelope["data"]
	assert data["mode"] == "all"
	assert data["total"] == 1
	assert data["watches"] == [
		{
			"name": "p1",
			"error": "SAVED_SEARCH_INVALID",
			"message": "watch 记录在运行前已不存在（可能被并发删除）；请用 watch list 确认后重新创建",
		}
	]
	assert data["invalid"] == ["p1"]


def test_shortlist_batch_dedupes_blob_identity(tmp_path: Path) -> None:
	"""BLOB 存储类的 security_id / job_id 与平台文本按同一去重键比较。"""
	store, db = _make_store(tmp_path)
	try:
		conn = sqlite3.connect(db)
		conn.execute(
			"INSERT INTO shortlist_records (security_id, job_id, title, company, city, salary, source, tags, note, lid, created_at) "
			"VALUES (?, ?, 't1', 'c1', '', '', 'favorites', '', '', '', 1.0)",
			(sqlite3.Binary(b"S"), sqlite3.Binary(b"J")),
		)
		conn.commit()
		conn.close()

		result = store.add_shortlist_batch(
			[{"security_id": "S", "job_id": "J", "title": "t2", "company": "c2"}], source="favorites"
		)

		assert result == {"imported_count": 0, "existing_count": 1, "skipped_count": 0}
		rows = store.list_shortlist()
		assert len(rows) == 1
		# 已存在的同来源记录保留原 title，只按同一去重键计数。
		assert rows[0]["title"] == "t1"
	finally:
		store.close()


def _invoke_decorated(exc: Exception, tmp_path: Path) -> dict:
	@handle_auth_errors("probe")
	def impl(ctx, _exc=exc):
		raise _exc

	ctx = MagicMock()
	ctx.obj = {"json_output": True, "data_dir": tmp_path, "platform": "zhipin", "role": "candidate"}
	with patch("boss_agent_cli.display.handle_error_output") as mock_err:
		impl(ctx)
	return mock_err.call_args.kwargs


def test_decorator_maps_record_invalid_to_cache_error(tmp_path: Path) -> None:
	"""CacheRecordInvalid 报 CACHE_RECORD_INVALID，而不是可重试的 NETWORK_ERROR。"""
	kwargs = _invoke_decorated(
		CacheRecordInvalid("shortlist", "sid", "created_at", "期望数值时间戳，实际是 str"), tmp_path
	)

	assert kwargs["code"] == "CACHE_RECORD_INVALID"
	assert kwargs["recoverable"] is True
	# 恢复动作来自登记表，不是兜底分支的泛化「重试」。
	assert kwargs["recovery_action"] != "重试"


def test_decorator_maps_write_rejected_to_invalid_param(tmp_path: Path) -> None:
	"""CacheWriteRejected 报 INVALID_PARAM，并明确库本身完好、无需删除。"""
	kwargs = _invoke_decorated(
		CacheWriteRejected(str(tmp_path / "cache" / "boss_agent.db"), "CHECK constraint failed: status"),
		tmp_path,
	)

	assert kwargs["code"] == "INVALID_PARAM"
	assert kwargs["recoverable"] is False
	assert "库本身完好、无需删除" in str(kwargs["recovery_action"])
