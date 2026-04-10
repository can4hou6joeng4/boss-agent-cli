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
