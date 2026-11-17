"""缓存层并发写回归（Issue #1509 / #1510）。

- watch 命中写入改为 INSERT OR IGNORE + UPDATE：另一个进程在本进程 SELECT
  与 INSERT 之间插入同一主键时，旧实现抛 IntegrityError 并被误判成库损坏。
- shortlist / crawl 迁移在 BEGIN IMMEDIATE 后重新读取 schema：并发首次打开
  不再重复 ALTER 抛 duplicate column；真正的迁移失败仍要上抛。
"""

import json
import sqlite3
import threading
import time

import pytest

from boss_agent_cli.cache.store import CacheDatabaseInvalid, CacheStore


def _raw_item(job_id: str = "job-1", security_id: str = "sec-1") -> dict:
	return {
		"job_id": job_id,
		"securityId": security_id,
		"jobName": "后端开发",
		"brandName": "TestCo",
	}


# ── watch 并写入 ────────────────────────────────────────────────────


def test_watch_insert_conflict_after_select_window_does_not_raise(tmp_path):
	"""另一个连接在 SELECT 与 INSERT 之间提交同一行：不得抛 IntegrityError。

	用未提交的写事务精确复现旧实现的窗口：本连接看不到对方未提交的行，
	先 SELECT 未命中，随后 INSERT 等待写锁；对方提交后旧实现撞主键。
	SQLite 连接只能在本线程使用，因此 B 连接在 worker 内创建。
	"""
	db = tmp_path / "cache" / "watch.db"
	store_a = CacheStore(db)
	item = _raw_item()
	key = CacheStore._make_watch_job_key(item, account="acct")
	payload = json.dumps(item, ensure_ascii=False, sort_keys=True)
	result: dict[str, object] = {}
	errors: list[BaseException] = []
	ready = threading.Event()
	go = threading.Event()

	def write_from_b() -> None:
		try:
			store_b = CacheStore(db)
			ready.set()
			go.wait(timeout=10)
			result["value"] = store_b.record_watch_results("daily", [item], account="acct")
			store_b.close()
		except BaseException as exc:  # pragma: no cover - 旧实现会走到这里
			errors.append(exc)

	try:
		thread = threading.Thread(target=write_from_b)
		thread.start()
		assert ready.wait(timeout=10)
		# A 先取写锁并写入未提交行；B 的 SELECT 看不到它。
		store_a._conn.execute("BEGIN IMMEDIATE")
		store_a._conn.execute(
			"INSERT INTO watch_hits (search_name, job_key, payload, first_seen_at, last_seen_at) "
			"VALUES (?, ?, ?, ?, ?)",
			("daily", key, payload, 1.0, 1.0),
		)
		go.set()
		time.sleep(0.2)
		store_a._conn.commit()
		thread.join(timeout=10)

		assert not thread.is_alive()
		assert errors == []
		assert result["value"] == {
			"new_count": 0,
			"seen_count": 1,
			"new_items": [],
			"total_count": 1,
		}
	finally:
		store_a.close()


def test_two_processes_recording_same_watch_hit(tmp_path):
	"""两个连接并发写同一批 watch 结果：合计恰好一次新增，且都不抛错。"""
	db = tmp_path / "cache" / "watch.db"
	CacheStore(db).close()
	items = [_raw_item("job-1"), _raw_item("job-2")]
	barrier = threading.Barrier(2)
	results: list[dict] = []
	errors: list[BaseException] = []

	def worker() -> None:
		store = CacheStore(db)
		try:
			barrier.wait(timeout=10)
			results.append(store.record_watch_results("daily", items, account="acct"))
		except BaseException as exc:
			errors.append(exc)
		finally:
			store.close()

	threads = [threading.Thread(target=worker) for _ in range(2)]
	for thread in threads:
		thread.start()
	for thread in threads:
		thread.join(timeout=10)

	assert errors == []
	assert len(results) == 2
	assert sum(int(result["new_count"]) for result in results) == 2
	assert sum(int(result["seen_count"]) for result in results) == 2
	with CacheStore(db) as store:
		rows = store._conn.execute("SELECT COUNT(*) FROM watch_hits").fetchone()
		assert rows is not None and rows[0] == 2


# ── 并发迁移 ────────────────────────────────────────────────────────

_LEGACY_SCHEMA = """
	CREATE TABLE shortlist_records (
		security_id TEXT NOT NULL,
		job_id TEXT NOT NULL,
		title TEXT NOT NULL,
		company TEXT NOT NULL,
		city TEXT NOT NULL,
		salary TEXT NOT NULL,
		source TEXT NOT NULL,
		created_at REAL NOT NULL,
		PRIMARY KEY (security_id, job_id)
	);
	CREATE TABLE crawl_runs (
		run_id TEXT PRIMARY KEY,
		params TEXT NOT NULL,
		status TEXT NOT NULL,
		next_page INTEGER NOT NULL,
		output_dir TEXT NOT NULL,
		error TEXT NOT NULL DEFAULT '',
		created_at REAL NOT NULL,
		updated_at REAL NOT NULL
	);
	CREATE TABLE crawl_jobs (
		run_id TEXT NOT NULL,
		job_key TEXT NOT NULL,
		page_no INTEGER NOT NULL,
		payload TEXT NOT NULL,
		detail_done INTEGER NOT NULL DEFAULT 0,
		updated_at REAL NOT NULL,
		PRIMARY KEY (run_id, job_key)
	);
"""


def _make_legacy_db(path, *, rounds: int = 1) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	conn = sqlite3.connect(str(path))
	try:
		# 预先切成 WAL：避免两个初始化连接同时改 journal_mode 时出现
		# 与迁移无关的 SQLITE_BUSY。
		conn.execute("PRAGMA journal_mode=WAL")
		conn.executescript(_LEGACY_SCHEMA)
		conn.execute(
			"INSERT INTO shortlist_records VALUES ('sec', 'job', 't', 'c', '北京', '20K', 'search', 1.0)"
		)
		conn.execute(
			"INSERT INTO crawl_runs (run_id, params, status, next_page, output_dir, error, created_at, updated_at) "
			"VALUES ('run', '{}', 'running', 1, '/tmp/out', '', 1.0, 1.0)"
		)
		for index in range(rounds):
			conn.execute(
				"INSERT INTO crawl_jobs VALUES ('run', ?, 1, '{}', 0, 1.0)",
				(f"job-{index}",),
			)
		conn.commit()
	finally:
		conn.close()


def test_concurrent_first_open_does_not_report_duplicate_column(tmp_path):
	"""两个进程同时首次打开旧库：都正常通过，第二个不抛 CacheDatabaseInvalid。"""
	for attempt in range(5):
		db = tmp_path / f"cache-{attempt}" / "legacy.db"
		_make_legacy_db(db, rounds=3)
		barrier = threading.Barrier(2)
		snapshots: list[dict] = []
		errors: list[BaseException] = []

		def open_store() -> None:
			try:
				barrier.wait(timeout=10)
				store = CacheStore(db)
				try:
					columns = {
						row[1] for row in store._conn.execute("PRAGMA table_info(shortlist_records)").fetchall()
					}
					crawl_columns = {
						row[1] for row in store._conn.execute("PRAGMA table_info(crawl_runs)").fetchall()
					}
					selectors = [row[0] for row in store._conn.execute("SELECT selector FROM crawl_jobs").fetchall()]
					snapshots.append({"shortlist": columns, "crawl": crawl_columns, "selectors": selectors})
				finally:
					store.close()
			except BaseException as exc:
				errors.append(exc)

		threads = [threading.Thread(target=open_store) for _ in range(2)]
		for thread in threads:
			thread.start()
		for thread in threads:
			thread.join(timeout=15)

		assert errors == [], f"第 {attempt} 轮并发迁移失败: {errors!r}"
		assert len(snapshots) == 2
		for snapshot in snapshots:
			assert {"tags", "note", "lid"} <= snapshot["shortlist"]
			assert {"list_finished", "hook_results"} <= snapshot["crawl"]
			selectors = snapshot["selectors"]
			assert all(selectors)
			assert len(set(selectors)) == len(selectors)


def test_second_open_skips_completed_migration(tmp_path):
	"""迁移落盘后再次打开不做任何变更，数据保持原样。"""
	db = tmp_path / "cache" / "legacy.db"
	_make_legacy_db(db, rounds=2)
	with CacheStore(db) as first:
		selectors = sorted(row[0] for row in first._conn.execute("SELECT selector FROM crawl_jobs").fetchall())
	with CacheStore(db) as second:
		assert sorted(row[0] for row in second._conn.execute("SELECT selector FROM crawl_jobs").fetchall()) == selectors
		assert second._conn.execute("SELECT COUNT(*) FROM crawl_jobs").fetchone()[0] == 2
		assert second._conn.execute("SELECT COUNT(*) FROM shortlist_records").fetchone()[0] == 1


def test_real_migration_failure_is_not_swallowed(tmp_path):
	"""真正的迁移失败（唯一索引撞重复 selector）仍上抛为 CacheDatabaseInvalid。"""
	db = tmp_path / "cache" / "broken.db"
	db.parent.mkdir(parents=True)
	conn = sqlite3.connect(str(db))
	try:
		conn.executescript("""
			CREATE TABLE crawl_jobs (
				run_id TEXT NOT NULL,
				job_key TEXT NOT NULL,
				selector TEXT,
				page_no INTEGER NOT NULL,
				payload TEXT NOT NULL,
				detail_done INTEGER NOT NULL DEFAULT 0,
				updated_at REAL NOT NULL,
				PRIMARY KEY (run_id, job_key)
			);
			INSERT INTO crawl_jobs VALUES ('run', 'job-1', 'duplicate', 1, '{}', 0, 1.0);
			INSERT INTO crawl_jobs VALUES ('run', 'job-2', 'duplicate', 1, '{}', 0, 1.0);
		""")
		conn.commit()
	finally:
		conn.close()

	with pytest.raises(CacheDatabaseInvalid):
		CacheStore(db)
