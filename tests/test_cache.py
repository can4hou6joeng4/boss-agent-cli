import time
import sqlite3

import pytest

from boss_agent_cli.cache.store import CacheDatabaseInvalid, CacheStore


def test_greet_record(tmp_path):
	store = CacheStore(tmp_path / "test.db")
	assert store.is_greeted("sec_001") is False
	store.record_greet("sec_001", "job_001")
	assert store.is_greeted("sec_001") is True


def test_get_job_id_for_greeted(tmp_path):
	store = CacheStore(tmp_path / "test.db")
	store.record_greet("sec_001", "job_001")
	assert store.get_job_id("sec_001") == "job_001"


def test_search_cache_hit(tmp_path):
	store = CacheStore(tmp_path / "test.db")
	params = {"query": "golang", "city": "杭州", "page": "1"}
	store.put_search(params, '{"jobs": []}')
	result = store.get_search(params)
	assert result == '{"jobs": []}'


def test_search_cache_miss(tmp_path):
	store = CacheStore(tmp_path / "test.db")
	params = {"query": "golang", "city": "杭州", "page": "1"}
	assert store.get_search(params) is None


def test_search_cache_expired(tmp_path):
	store = CacheStore(tmp_path / "test.db", search_ttl_seconds=1)
	params = {"query": "golang", "page": "1"}
	store.put_search(params, '{"jobs": []}')
	time.sleep(1.1)
	assert store.get_search(params) is None


def test_search_cache_different_params(tmp_path):
	store = CacheStore(tmp_path / "test.db")
	params_a = {"query": "golang", "city": "杭州", "page": "1"}
	params_b = {"query": "golang", "city": "北京", "page": "1"}
	store.put_search(params_a, '{"a": 1}')
	store.put_search(params_b, '{"b": 2}')
	assert store.get_search(params_a) == '{"a": 1}'
	assert store.get_search(params_b) == '{"b": 2}'


def test_search_cache_max_100(tmp_path):
	store = CacheStore(tmp_path / "test.db")
	for i in range(105):
		store.put_search({"query": f"q{i}", "page": "1"}, f'{{"i": {i}}}')
	assert store.get_search({"query": "q0", "page": "1"}) is None
	assert store.get_search({"query": "q104", "page": "1"}) is not None


def test_saved_search_crud(tmp_path):
	store = CacheStore(tmp_path / "test.db")
	store.save_saved_search("golang-gz", {"query": "golang", "city": "广州", "welfare": "双休"})
	record = store.get_saved_search("golang-gz")
	assert record is not None
	assert record["params"]["query"] == "golang"
	assert len(store.list_saved_searches()) == 1
	assert store.delete_saved_search("golang-gz") is True
	assert store.get_saved_search("golang-gz") is None


def test_watch_results_only_mark_new_items_once(tmp_path):
	store = CacheStore(tmp_path / "test.db")
	first = store.record_watch_results(
		"golang-gz",
		[
			{"security_id": "sec-1", "job_id": "job-1", "title": "Go 开发"},
			{"security_id": "sec-2", "job_id": "job-2", "title": "Python 开发"},
		],
	)
	second = store.record_watch_results(
		"golang-gz",
		[
			{"security_id": "sec-2", "job_id": "job-2", "title": "Python 开发"},
			{"security_id": "sec-3", "job_id": "job-3", "title": "Rust 开发"},
		],
	)
	assert first["new_count"] == 2
	assert second["new_count"] == 1
	assert second["new_items"][0]["security_id"] == "sec-3"


def test_apply_record_idempotency(tmp_path):
	store = CacheStore(tmp_path / "test.db")
	assert store.is_applied("sec_001", "job_001") is False
	store.record_apply("sec_001", "job_001")
	assert store.is_applied("sec_001", "job_001") is True
	assert store.is_applied("sec_001", "job_002") is False


def test_shortlist_crud(tmp_path):
	store = CacheStore(tmp_path / "test.db")
	item = {
		"security_id": "sec_001",
		"job_id": "job_001",
		"title": "Go 开发",
		"company": "TestCo",
		"city": "广州",
		"salary": "20-30K",
		"source": "search",
		"tags": ["远程", "双休"],
		"note": "优先沟通",
	}
	assert store.is_shortlisted("sec_001", "job_001") is False
	store.add_shortlist(item)
	assert store.is_shortlisted("sec_001", "job_001") is True
	items = store.list_shortlist()
	assert len(items) == 1
	assert items[0]["title"] == "Go 开发"
	assert items[0]["tags"] == ["远程", "双休"]
	assert items[0]["note"] == "优先沟通"
	assert store.remove_shortlist("sec_001", "job_001") is True
	assert store.is_shortlisted("sec_001", "job_001") is False


def test_shortlist_migration_adds_tags_note_without_data_loss(tmp_path):
	db_path = tmp_path / "old.db"
	conn = sqlite3.connect(db_path)
	conn.execute("""
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
		)
	""")
	conn.execute(
		"INSERT INTO shortlist_records (security_id, job_id, title, company, city, salary, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
		("sec_001", "job_001", "Go 开发", "TestCo", "广州", "20-30K", "search", 1.0),
	)
	conn.commit()
	conn.close()

	store = CacheStore(db_path)
	items = store.list_shortlist()
	assert len(items) == 1
	assert items[0]["title"] == "Go 开发"
	assert items[0]["tags"] == []
	assert items[0]["note"] == ""

	columns = {row[1] for row in store._conn.execute("PRAGMA table_info(shortlist_records)").fetchall()}
	assert {"tags", "note"} <= columns


def test_resume_job_link_crud(tmp_path):
	store = CacheStore(tmp_path / "test.db")
	store.link_resume_to_job("default", "sec_001", "job_001", "后端工程师", "测试公司")
	apps = store.get_resume_applications("default")
	assert len(apps) == 1
	assert apps[0]["job_title"] == "后端工程师"
	assert apps[0]["status"] == "prepared"

	# 更新状态
	ok = store.update_job_link_status("default", "sec_001", "job_001", "applied", "已投递")
	assert ok is True
	apps = store.get_resume_applications("default")
	assert apps[0]["status"] == "applied"
	assert apps[0]["notes"] == "已投递"

	# 反向查询
	resumes = store.get_job_resumes("sec_001", "job_001")
	assert len(resumes) == 1
	assert resumes[0]["resume_name"] == "default"

	# 删除关联
	ok = store.remove_job_link("default", "sec_001", "job_001")
	assert ok is True
	assert store.get_resume_applications("default") == []


def test_resume_job_link_repeat_preserves_tracking(tmp_path):
	"""重复关联同一职位不能把投递状态、备注和首次关联时间抹掉。

	`INSERT OR REPLACE` 整行替换：脚本重跑一次 `resume link`，已经推进到
	`applied` 的记录会悄悄退回 `prepared`，备注清空，`linked_at` 也被改成当前时间。
	"""
	store = CacheStore(tmp_path / "test.db")
	store.link_resume_to_job("default", "sec_001", "job_001", "后端工程师", "测试公司")
	assert store.update_job_link_status("default", "sec_001", "job_001", "applied", "已投递") is True
	linked_at = store.get_resume_applications("default")[0]["linked_at"]

	time.sleep(0.01)
	store.link_resume_to_job("default", "sec_001", "job_001", "", "")

	app = store.get_resume_applications("default")[0]
	assert app["status"] == "applied"
	assert app["notes"] == "已投递"
	assert app["linked_at"] == linked_at
	assert app["job_title"] == "后端工程师"
	assert app["company"] == "测试公司"


def test_resume_job_link_repeat_applies_explicit_values(tmp_path):
	"""本次显式给出的标题/公司仍然覆盖旧值。"""
	store = CacheStore(tmp_path / "test.db")
	store.link_resume_to_job("default", "sec_001", "job_001", "旧标题", "旧公司")
	store.link_resume_to_job("default", "sec_001", "job_001", "新标题", "新公司")

	app = store.get_resume_applications("default")[0]
	assert app["job_title"] == "新标题"
	assert app["company"] == "新公司"
	assert app["status"] == "prepared"


def test_resume_job_link_rotating_security_id_keeps_tracking(tmp_path):
	"""重新关联同一职位时 security_id 已轮换，不能新增一行并退回 prepared（Issue #901）。"""
	store = CacheStore(tmp_path / "test.db")
	store.link_resume_to_job("default", "sid-request-1", "job_001", "后端工程师", "测试公司")
	assert store.update_job_link_status("default", "sid-request-1", "job_001", "applied", "已投递") is True
	linked_at = store.get_resume_applications("default")[0]["linked_at"]

	time.sleep(0.01)
	store.link_resume_to_job("default", "sid-request-2", "job_001", "后端工程师", "测试公司")

	apps = store.get_resume_applications("default")
	assert len(apps) == 1
	assert apps[0]["security_id"] == "sid-request-2"
	assert apps[0]["status"] == "applied"
	assert apps[0]["notes"] == "已投递"
	assert apps[0]["linked_at"] == linked_at


def test_remove_resume_links_clears_only_that_resume(tmp_path):
	"""删除简历的全部分关联只影响同名简历，其它简历的关联保留。"""
	store = CacheStore(tmp_path / "test.db")
	store.link_resume_to_job("default", "sec_001", "job_001", "后端", "公司甲")
	store.link_resume_to_job("default", "sec_002", "job_002", "前端", "公司乙")
	store.link_resume_to_job("v2", "sec_001", "job_001", "后端", "公司甲")

	assert store.remove_resume_links("default") == 2
	assert store.get_resume_applications("default") == []
	assert len(store.get_resume_applications("v2")) == 1
	assert store.remove_resume_links("default") == 0


def test_resume_job_link_multiple(tmp_path):
	store = CacheStore(tmp_path / "test.db")
	store.link_resume_to_job("v1", "sec_001", "job_001", "后端", "公司甲")
	store.link_resume_to_job("v2", "sec_001", "job_001", "后端", "公司甲")
	resumes = store.get_job_resumes("sec_001", "job_001")
	assert len(resumes) == 2


def test_job_desc_cache_roundtrip(tmp_path):
	store = CacheStore(tmp_path / "test.db")
	assert store.get_job_desc("sec_001") is None
	store.put_job_desc("sec_001", "提供双休和五险一金")
	assert store.get_job_desc("sec_001") == "提供双休和五险一金"


def test_job_desc_cache_expires(tmp_path):
	store = CacheStore(tmp_path / "test.db", search_ttl_seconds=0)
	store.put_job_desc("sec_001", "提供双休")
	time.sleep(0.01)
	assert store.get_job_desc("sec_001") is None


def test_job_desc_cache_ignores_empty(tmp_path):
	store = CacheStore(tmp_path / "test.db")
	store.put_job_desc("", "非空描述")
	store.put_job_desc("sec_001", "")
	assert store.get_job_desc("sec_001") is None
	assert store.get_job_desc("") is None


@pytest.mark.parametrize("step", [0.0, 1.0])
def test_job_desc_cache_evicts_oldest_on_overflow(tmp_path, monkeypatch, step):
	"""超过容量后只保留最近写入的描述，不按职位 ID 排序。"""
	monkeypatch.setattr("boss_agent_cli.cache.store._MAX_JOB_DESC_CACHE", 3)
	now = 1000.0
	monkeypatch.setattr("boss_agent_cli.cache.store.time.time", lambda: now)
	with CacheStore(tmp_path / "test.db") as store:
		for i in range(5):
			now += step
			store.put_job_desc(f"job-{4 - i}", f"描述 {i}")
		assert store._conn.execute("SELECT COUNT(*) FROM job_desc_cache").fetchone()[0] == 3
		assert store.get_job_desc("job-4") is None
		assert store.get_job_desc("job-3") is None
		assert store.get_job_desc("job-2") == "描述 2"
		assert store.get_job_desc("job-1") == "描述 3"
		assert store.get_job_desc("job-0") == "描述 4"


def test_job_desc_cache_refresh_at_capacity_keeps_recent_value(tmp_path, monkeypatch):
	"""更新已有条目不挤掉其他条目，随后扩容时按刷新后的保存时间淘汰。"""
	monkeypatch.setattr("boss_agent_cli.cache.store._MAX_JOB_DESC_CACHE", 3)
	now = 1000.0
	monkeypatch.setattr("boss_agent_cli.cache.store.time.time", lambda: now)
	with CacheStore(tmp_path / "test.db") as store:
		for i in range(3):
			now += 1
			store.put_job_desc(f"job-{i}", f"描述 {i}")
		now += 1
		store.put_job_desc("job-0", "更新后的描述")
		assert store.get_job_desc("job-0") == "更新后的描述"
		assert store.get_job_desc("job-1") == "描述 1"
		assert store.get_job_desc("job-2") == "描述 2"
		now += 1
		store.put_job_desc("job-3", "新职位描述")
		assert store._conn.execute("SELECT COUNT(*) FROM job_desc_cache").fetchone()[0] == 3
		assert store.get_job_desc("job-0") == "更新后的描述"
		assert store.get_job_desc("job-1") is None
		assert store.get_job_desc("job-2") == "描述 2"
		assert store.get_job_desc("job-3") == "新职位描述"


def test_job_desc_cache_trims_existing_overflow_on_next_write(tmp_path, monkeypatch):
	"""升级前已经超限的缓存，在下一次写入时一次收敛到上限。"""
	monkeypatch.setattr("boss_agent_cli.cache.store._MAX_JOB_DESC_CACHE", 3)
	monkeypatch.setattr("boss_agent_cli.cache.store.time.time", lambda: 1000.0)
	with CacheStore(tmp_path / "test.db") as store:
		store._conn.executemany(
			"INSERT INTO job_desc_cache VALUES (?, ?, ?)",
			[(f"job-{i}", f"描述 {i}", 990.0 + i) for i in range(5)],
		)
		store._conn.commit()
		store.put_job_desc("job-5", "新职位描述")
		assert store._conn.execute("SELECT COUNT(*) FROM job_desc_cache").fetchone()[0] == 3
		for i in range(3):
			assert store.get_job_desc(f"job-{i}") is None
		assert store.get_job_desc("job-3") == "描述 3"
		assert store.get_job_desc("job-4") == "描述 4"
		assert store.get_job_desc("job-5") == "新职位描述"


# ── 缓存库文件本身损坏 ────────────────────────────────────────────────


def test_cache_store_reports_invalid_database_file(tmp_path):
	"""库文件被写成非 SQLite 内容时抛类型化异常，而不是裸 sqlite3.DatabaseError。"""
	db = tmp_path / "cache" / "boss_agent.db"
	db.parent.mkdir(parents=True)
	db.write_bytes(b"this is not a sqlite database")
	with pytest.raises(CacheDatabaseInvalid) as excinfo:
		CacheStore(db)
	assert excinfo.value.path == db
	assert "file is not a database" in str(excinfo.value)


def test_locked_database_is_not_reported_as_corrupt(tmp_path, monkeypatch):
	"""并发锁（OperationalError）在上层可重试，不能被说成库文件损坏。"""
	db = tmp_path / "cache.db"
	CacheStore(db).close()

	class _LockedConnection:
		def execute(self, sql, *args, **kwargs):
			raise sqlite3.OperationalError("database is locked")

	monkeypatch.setattr("boss_agent_cli.cache.store.sqlite3.connect", lambda *a, **k: _LockedConnection())
	with pytest.raises(sqlite3.OperationalError):
		CacheStore(db)
