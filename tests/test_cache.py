import time

from boss_agent_cli.cache.store import CacheStore


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
	}
	assert store.is_shortlisted("sec_001", "job_001") is False
	store.add_shortlist(item)
	assert store.is_shortlisted("sec_001", "job_001") is True
	items = store.list_shortlist()
	assert len(items) == 1
	assert items[0]["title"] == "Go 开发"
	assert store.remove_shortlist("sec_001", "job_001") is True
	assert store.is_shortlisted("sec_001", "job_001") is False


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


def test_resume_job_link_multiple(tmp_path):
	store = CacheStore(tmp_path / "test.db")
	store.link_resume_to_job("v1", "sec_001", "job_001", "后端", "公司甲")
	store.link_resume_to_job("v2", "sec_001", "job_001", "后端", "公司甲")
	resumes = store.get_job_resumes("sec_001", "job_001")
	assert len(resumes) == 2
