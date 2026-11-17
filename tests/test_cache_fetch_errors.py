"""缓存库取数阶段错误的统一契约（Issue #1350）。

数据页损坏但 `execute` 已成功返回首行时，`fetchone` / `fetchall` / 游标迭代
会抛裸 `sqlite3.DatabaseError`。这些读取路径必须和 execute 一样归一到
`CacheDatabaseInvalid`：`crawl status` / `results` 给「备份后删库」指引，
crawl resume 也不再被 `_run` 吞成可无限恢复的 stopped。锁冲突仍是可重试的
`CacheBusyError`。
"""

import json
import sqlite3
import struct
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from boss_agent_cli.auth.token_store import TokenStore
from boss_agent_cli.cache.store import (
	CacheBusyError,
	CacheDatabaseInvalid,
	CacheStore,
	_GuardedCursor,
	is_cache_busy_error,
	is_cache_local_io_error,
)
from boss_agent_cli.crawler.service import CrawlService, CrawlSettings
from boss_agent_cli.main import cli

_MALFORMED = "database disk image is malformed"


def _db_path(tmp_path: Path) -> Path:
	return tmp_path / "cache" / "boss_agent.db"


def _settings(tmp_path: Path) -> CrawlSettings:
	return CrawlSettings(
		query="AI",
		city_code="101210100",
		pages=1,
		with_detail=False,
		profile_path=tmp_path / "crawl" / "chrome-profile",
		chrome_path=None,
		cdp_port=9222,
		hook_profile="none",
	)


def _seed_run(tmp_path: Path, run_id: str = "run-1") -> None:
	with CacheStore(_db_path(tmp_path)) as cache:
		cache.create_crawl_run(
			run_id,
			_settings(tmp_path).as_dict(),
			str(tmp_path / "crawl" / run_id),
			status="completed",
		)
		cache.put_crawl_job(run_id, "job-1", 1, {"job_id": "job-1"}, detail_done=False)


def _raise_malformed(*_args: Any, **_kwargs: Any) -> Any:
	raise sqlite3.DatabaseError(_MALFORMED)


def _raise_locked(*_args: Any, **_kwargs: Any) -> Any:
	raise sqlite3.OperationalError("database is locked")


def _invoke(tmp_path: Path, *args: str) -> Any:
	return CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])


# ── 取数路径的错误转换 ────────────────────────────────────────────────


def test_list_crawl_jobs_fetch_error_is_cache_database_invalid(tmp_path, monkeypatch):
	"""`list_crawl_jobs` 的 fetchall 坏页错误带库路径归一。"""
	_seed_run(tmp_path)
	with CacheStore(_db_path(tmp_path)) as store:
		monkeypatch.setattr(_GuardedCursor, "_cursor_call", _raise_malformed)
		with pytest.raises(CacheDatabaseInvalid) as excinfo:
			store.list_crawl_jobs("run-1")
	assert excinfo.value.path == _db_path(tmp_path)
	assert excinfo.value.detail == _MALFORMED


def test_list_shortlist_fetch_error_is_cache_database_invalid(tmp_path, monkeypatch):
	"""`list_shortlist` 同样不能被裸 `sqlite3.DatabaseError` 穿透。"""
	with CacheStore(_db_path(tmp_path)) as store:
		store.add_shortlist({"security_id": "s1", "job_id": "j1", "title": "t"})
		monkeypatch.setattr(_GuardedCursor, "_cursor_call", _raise_malformed)
		with pytest.raises(CacheDatabaseInvalid) as excinfo:
			store.list_shortlist()
	assert excinfo.value.path == _db_path(tmp_path)


@pytest.mark.parametrize(
	"read",
	[
		lambda cursor: cursor.fetchone(),
		lambda cursor: cursor.fetchall(),
		lambda cursor: cursor.fetchmany(1),
		lambda cursor: next(cursor),
		lambda cursor: list(cursor),
	],
	ids=["fetchone", "fetchall", "fetchmany", "next", "iter"],
)
def test_cursor_read_methods_translate_database_errors(tmp_path, monkeypatch, read):
	"""fetchone / fetchall / fetchmany / __next__ / 迭代全部走同一转换。"""
	with CacheStore(_db_path(tmp_path)) as store:
		cursor = store._conn.execute("SELECT 1 UNION SELECT 2")
		monkeypatch.setattr(_GuardedCursor, "_cursor_call", _raise_malformed)
		with pytest.raises(CacheDatabaseInvalid):
			read(cursor)


def test_fetch_busy_error_stays_retryable(tmp_path, monkeypatch):
	"""取数阶段的锁冲突仍报 CacheBusyError，不能被说成库损坏。"""
	with CacheStore(_db_path(tmp_path)) as store:
		store.add_shortlist({"security_id": "s1", "job_id": "j1", "title": "t"})
		monkeypatch.setattr(_GuardedCursor, "_cursor_call", _raise_locked)
		with pytest.raises(CacheBusyError) as excinfo:
			store.list_shortlist()
	assert excinfo.value.path == _db_path(tmp_path)
	assert "database is locked" in str(excinfo.value)


# ── 本地 IO 失败不落 CACHE_DATABASE_INVALID（Issue #1467） ─────────────


_LOCAL_IO_MESSAGES = (
	"attempt to write a readonly database",
	"disk I/O error",
	"database or disk is full",
	"unable to open database file",
)


def _raise_local_io(message: str) -> Any:
	def raise_error(*_args: Any, **_kwargs: Any) -> Any:
		raise sqlite3.OperationalError(message)
	return raise_error


@pytest.mark.parametrize("message", _LOCAL_IO_MESSAGES)
def test_local_io_error_is_not_translated_to_cache_database_invalid(tmp_path, monkeypatch, message):
	"""只读目录 / 磁盘满 / 设备 IO 故障不能被说成库损坏让人删库（Issue #1467）。"""
	with CacheStore(_db_path(tmp_path)) as store:
		store.add_shortlist({"security_id": "s1", "job_id": "j1", "title": "t"})
		monkeypatch.setattr(_GuardedCursor, "_cursor_call", _raise_local_io(message))
		with pytest.raises(sqlite3.OperationalError) as excinfo:
			store.list_shortlist()
	assert not isinstance(excinfo.value, CacheDatabaseInvalid)
	assert str(excinfo.value) == message
	assert is_cache_local_io_error(excinfo.value)


def test_open_local_io_error_is_not_cache_database_invalid(tmp_path, monkeypatch):
	"""打开阶段的本地 IO 失败同样保留原始异常（Issue #1467）。"""
	db_path = _db_path(tmp_path)
	db_path.parent.mkdir(parents=True, exist_ok=True)

	class _FailingConnection:
		def execute(self, *_args: Any, **_kwargs: Any) -> Any:
			raise sqlite3.OperationalError("unable to open database file")

	monkeypatch.setattr("boss_agent_cli.cache.store.sqlite3.connect", lambda *_a, **_k: _FailingConnection())
	with pytest.raises(sqlite3.OperationalError) as excinfo:
		CacheStore(db_path)
	assert not isinstance(excinfo.value, CacheDatabaseInvalid)
	assert str(excinfo.value) == "unable to open database file"


@pytest.mark.parametrize("message", _LOCAL_IO_MESSAGES)
def test_cache_busy_and_local_io_predicates_are_mutually_exclusive(message):
	exc = sqlite3.OperationalError(message)
	assert is_cache_local_io_error(exc)
	assert not is_cache_busy_error(exc)


@pytest.mark.parametrize("message", ["database is locked", "database table is locked", "database is busy"])
def test_busy_messages_are_not_local_io_errors(message):
	exc = sqlite3.OperationalError(message)
	assert is_cache_busy_error(exc)
	assert not is_cache_local_io_error(exc)


def test_local_io_predicate_only_matches_operational_errors():
	"""其他 sqlite 错误（库损坏、写错的 SQL）不属于本地 IO。"""
	assert not is_cache_local_io_error(sqlite3.DatabaseError("disk I/O error"))
	assert not is_cache_local_io_error(RuntimeError("disk I/O error"))
	assert not is_cache_local_io_error(sqlite3.OperationalError("no such column: title"))


# ── 真实坏页 ──────────────────────────────────────────────────────────


def _overflow_pages(store: CacheStore) -> tuple[int, list[int]]:
	"""返回页大小与 crawl_jobs / shortlist_records 的溢出页号。

	dbstat 未编译时跳过真实坏页用例；上面的模拟用例仍覆盖转换逻辑。
	"""
	page_size = int(store._conn.execute("PRAGMA page_size").fetchone()[0])
	try:
		rows = store._conn.execute(
			"SELECT pageno FROM dbstat WHERE name IN ('crawl_jobs', 'shortlist_records') AND pagetype = 'overflow'"
		).fetchall()
	except (CacheDatabaseInvalid, sqlite3.DatabaseError):
		pytest.skip("当前 SQLite 构建未提供 dbstat")
	return page_size, [int(row[0]) for row in rows]


def _corrupt_overflow_pages(db_path: Path, *, page_size: int, pages: list[int]) -> None:
	"""把溢出页的链指针写成非法页号：读取 payload 时 SQLite 报库损坏。"""
	data = bytearray(db_path.read_bytes())
	for pageno in pages:
		offset = (pageno - 1) * page_size
		data[offset : offset + 4] = struct.pack(">I", 0xFFFFFFFF)
	db_path.write_bytes(bytes(data))


def _multi_page_corrupt_db(tmp_path: Path) -> Path:
	"""构造多页 payload 后写坏所有溢出页。

	前 20 行是小 payload：无 ORDER BY 的扫描首行仍可读，坏页要到后续取数才
	暴露；crawl_jobs 的迁移只读 run_id / job_key（不读溢出页），打开阶段不受
	影响。
	"""
	db_path = _db_path(tmp_path)
	big = "x" * 12000
	with CacheStore(db_path) as store:
		for index in range(20):
			store.put_crawl_job("run-1", f"small-{index}", 1, {"job_id": f"small-{index}"}, detail_done=False)
		for index in range(120):
			store.put_crawl_job("run-1", f"big-{index}", 2, {"job_id": f"big-{index}", "text": big}, detail_done=False)
		for index in range(120):
			store.add_shortlist(
				{
					"security_id": f"s{index}",
					"job_id": f"j{index}",
					"title": "t",
					"company": "c",
					"city": "杭州",
					"salary": "20-30K",
					"source": "favorites",
					"note": big,
				}
			)
		for index in range(20):
			store.add_shortlist(
				{
					"security_id": f"tail{index}",
					"job_id": f"tailj{index}",
					"title": "t",
					"company": "c",
					"city": "杭州",
					"salary": "20-30K",
					"source": "favorites",
					"note": "small",
				}
			)
		page_size, pages = _overflow_pages(store)
	assert pages
	_corrupt_overflow_pages(db_path, page_size=page_size, pages=pages)
	return db_path


def test_corrupted_data_pages_raise_cache_database_invalid(tmp_path):
	"""真实多页表的坏页在列表查询与取数阶段都归一为 CacheDatabaseInvalid。"""
	db_path = _multi_page_corrupt_db(tmp_path)
	with CacheStore(db_path) as store:
		with pytest.raises(CacheDatabaseInvalid):
			store.list_crawl_jobs("run-1")
		with pytest.raises(CacheDatabaseInvalid):
			store.list_shortlist()
		# 无 ORDER BY 的扫描是惰性取数：execute 与首行成功，坏页在后续 fetch 暴露。
		cursor = store._conn.execute("SELECT job_key, payload FROM crawl_jobs")
		assert cursor.fetchone() is not None
		with pytest.raises(CacheDatabaseInvalid) as excinfo:
			cursor.fetchall()
	assert _MALFORMED in str(excinfo.value)


# ── 命令信封 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
	("args", "command"),
	[
		(("crawl", "status", "run-1"), "crawl"),
		(("crawl", "results", "run-1"), "crawl"),
	],
)
def test_crawl_read_commands_report_cache_database_invalid(tmp_path, monkeypatch, args, command):
	"""`crawl status` / `results` 给「备份后删库」信封，而不是通用 IO 指引。"""
	_seed_run(tmp_path)
	db_path = _db_path(tmp_path)

	def broken(self: CacheStore, run_id: str) -> list[dict[str, Any]]:
		raise CacheDatabaseInvalid(db_path, _MALFORMED)

	monkeypatch.setattr(CacheStore, "list_crawl_jobs", broken)
	result = _invoke(tmp_path, *args)
	assert result.exit_code == 1, result.output
	assert result.stderr == ""
	payload = json.loads(result.output)
	assert payload["ok"] is False
	assert payload["command"] == command
	assert payload["data"] is None
	assert payload["error"]["code"] == "CACHE_DATABASE_INVALID"
	assert payload["error"]["recoverable"] is True
	assert "备份" in payload["error"]["recovery_action"]
	assert str(db_path) in payload["hints"]["operator_actions"][0]


def test_crawl_resume_reports_cache_database_invalid(tmp_path, monkeypatch):
	"""resume 走统一装饰器：坏库不能被兜成 NETWORK_ERROR +「重试」。"""
	_seed_run(tmp_path)
	TokenStore(tmp_path / "auth").save(
		{"cookies": {"wt2": "test-wt2", "wbg": "1", "zp_at": "1"}, "user_agent": "test-agent"}
	)
	db_path = _db_path(tmp_path)

	class _BrokenService:
		def __init__(self, *_args: Any, **_kwargs: Any) -> None:
			pass

		def resume(self, *_args: Any, **_kwargs: Any) -> None:
			raise CacheDatabaseInvalid(db_path, _MALFORMED)

	monkeypatch.setattr("boss_agent_cli.commands.crawl.CrawlService", _BrokenService)
	result = _invoke(tmp_path, "crawl", "resume", "run-1")
	assert result.exit_code == 1, result.output
	assert result.stderr == ""
	payload = json.loads(result.output)
	assert payload["ok"] is False
	assert payload["command"] == "crawl.resume"
	assert payload["error"]["code"] == "CACHE_DATABASE_INVALID"
	assert payload["error"]["recoverable"] is True
	assert "备份" in payload["error"]["recovery_action"]
	assert str(db_path) in payload["hints"]["operator_actions"][0]


# ── crawl 执行不再吞坏库错误 ──────────────────────────────────────────


class _FakeTransport:
	def open(self) -> list[Any]:
		return []

	def close(self) -> None:
		pass


def test_crawl_run_does_not_swallow_cache_database_invalid(tmp_path, monkeypatch):
	"""`_run` 的确定性异常元组包含 CacheDatabaseInvalid：落 stopped 后原样抛出。

	落成普通 stopped 会让调用方拿到 ok=true 与 resume 指引，坏库下无限重复。
	"""
	db_path = _db_path(tmp_path)
	with CacheStore(db_path) as cache:
		cache.create_crawl_run(
			"run-1",
			_settings(tmp_path).as_dict(),
			str(tmp_path / "crawl" / "run-1"),
			status="running",
			next_page=2,
		)
		service = CrawlService(
			cache,
			data_dir=tmp_path,
			transport_factory=lambda _settings: _FakeTransport(),
		)

		calls = {"count": 0}

		def broken(run_id: str) -> list[dict[str, Any]]:
			# 只在首次取数时失败：旧实现把它吞成 stopped 后，收尾处还会再取一次，
			# 第二次放行就会返回 ok=true 的 stopped 信封。
			if calls["count"] == 0:
				calls["count"] += 1
				raise CacheDatabaseInvalid(db_path, _MALFORMED)
			return []

		monkeypatch.setattr(cache, "list_crawl_jobs", broken)
		with pytest.raises(CacheDatabaseInvalid):
			service.resume("run-1")
		run = cache.get_crawl_run("run-1")
	assert run is not None
	assert run["status"] == "stopped"
	assert _MALFORMED in run["error"]
