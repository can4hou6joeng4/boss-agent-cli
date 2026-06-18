"""CacheStore 扩展测试 — 覆盖 saved_search、watch、apply、shortlist 表操作。"""

import time

import pytest

from boss_agent_cli.cache.store import CacheStore


@pytest.fixture()
def store(tmp_path):
	db = tmp_path / "cache" / "test.db"
	s = CacheStore(db)
	yield s
	s.close()


# ── saved_search ────────────────────────────────────────────────────


def test_save_and_get_saved_search(store):
	"""保存搜索条件后应能通过名称获取。"""
	params = {"query": "python", "city": "101010100"}
	store.save_saved_search("daily", params)
	result = store.get_saved_search("daily")
	assert result is not None
	assert result["name"] == "daily"
	assert result["params"] == params


def test_get_nonexistent_saved_search(store):
	"""获取不存在的保存搜索应返回 None。"""
	assert store.get_saved_search("nonexistent") is None


def test_list_saved_searches_order(store):
	"""列表应按更新时间倒序排列。"""
	store.save_saved_search("old", {"q": "a"})
	time.sleep(0.01)
	store.save_saved_search("new", {"q": "b"})
	results = store.list_saved_searches()
	assert len(results) == 2
	assert results[0]["name"] == "new"
	assert results[1]["name"] == "old"


def test_update_saved_search_preserves_created_at(store):
	"""更新保存搜索时应保留原始创建时间。"""
	store.save_saved_search("test", {"q": "a"})
	original = store.get_saved_search("test")
	time.sleep(0.01)
	store.save_saved_search("test", {"q": "b"})
	updated = store.get_saved_search("test")
	assert updated["created_at"] == original["created_at"]
	assert updated["updated_at"] > original["updated_at"]
	assert updated["params"] == {"q": "b"}


def test_delete_saved_search(store):
	"""删除保存搜索应返回 True，不存在时返回 False。"""
	store.save_saved_search("test", {"q": "a"})
	assert store.delete_saved_search("test") is True
	assert store.get_saved_search("test") is None
	assert store.delete_saved_search("test") is False


def test_delete_saved_search_cleans_watch_hits(store):
	"""删除保存搜索时应同时清除关联的 watch_hits。"""
	store.save_saved_search("test", {"q": "a"})
	store.record_watch_results("test", [{"security_id": "s1", "job_id": "j1"}])
	store.delete_saved_search("test")
	# 重新录入同名搜索，不应该有旧 watch 记录
	result = store.record_watch_results("test", [{"security_id": "s1", "job_id": "j1"}])
	assert result["new_count"] == 1


# ── watch_hits ──────────────────────────────────────────────────────


def test_watch_new_items(store):
	"""首次录入 watch 结果应全部标记为新增。"""
	items = [
		{"security_id": "s1", "job_id": "j1", "title": "前端"},
		{"security_id": "s2", "job_id": "j2", "title": "后端"},
	]
	result = store.record_watch_results("search1", items)
	assert result["new_count"] == 2
	assert result["seen_count"] == 0
	assert result["total_count"] == 2


def test_watch_seen_items(store):
	"""重复录入相同项目应标记为已见。"""
	items = [{"security_id": "s1", "job_id": "j1"}]
	store.record_watch_results("search1", items)
	result = store.record_watch_results("search1", items)
	assert result["new_count"] == 0
	assert result["seen_count"] == 1


def test_watch_mixed_items(store):
	"""混合新旧项目应分别计数。"""
	store.record_watch_results("search1", [{"security_id": "s1", "job_id": "j1"}])
	result = store.record_watch_results("search1", [
		{"security_id": "s1", "job_id": "j1"},
		{"security_id": "s2", "job_id": "j2"},
	])
	assert result["new_count"] == 1
	assert result["seen_count"] == 1


def test_watch_different_searches_independent(store):
	"""不同搜索名的 watch 记录应互相独立。"""
	item = [{"security_id": "s1", "job_id": "j1"}]
	store.record_watch_results("search_a", item)
	result = store.record_watch_results("search_b", item)
	assert result["new_count"] == 1


def test_watch_job_key_fallback_to_hash(store):
	"""无 security_id/job_id 时应用哈希生成 key。"""
	items = [{"title": "神秘职位", "company": "匿名"}]
	result = store.record_watch_results("search1", items)
	assert result["new_count"] == 1
	# 再次录入相同项
	result2 = store.record_watch_results("search1", items)
	assert result2["seen_count"] == 1


# ── apply_records ───────────────────────────────────────────────────


def test_apply_record_and_check(store):
	"""记录投递后应能检测到已投递状态。"""
	assert store.is_applied("s1", "j1") is False
	store.record_apply("s1", "j1")
	assert store.is_applied("s1", "j1") is True


def test_apply_idempotent(store):
	"""重复记录投递不应报错。"""
	store.record_apply("s1", "j1")
	store.record_apply("s1", "j1")
	assert store.is_applied("s1", "j1") is True


def test_apply_different_jobs_independent(store):
	"""不同职位的投递记录应互相独立。"""
	store.record_apply("s1", "j1")
	assert store.is_applied("s1", "j1") is True
	assert store.is_applied("s1", "j2") is False


# ── shortlist_records ───────────────────────────────────────────────


def test_shortlist_add_and_list(store):
	"""添加候选池项后应能列出。"""
	item = {
		"security_id": "s1", "job_id": "j1",
		"title": "前端工程师", "company": "字节",
		"city": "北京", "salary": "25-40K",
		"source": "search",
		"tags": ["前端", "远程", "前端", ""],
		"note": "复盘薪资结构",
	}
	store.add_shortlist(item)
	result = store.list_shortlist()
	assert len(result) == 1
	assert result[0]["title"] == "前端工程师"
	assert result[0]["security_id"] == "s1"
	assert result[0]["tags"] == ["前端", "远程"]
	assert result[0]["note"] == "复盘薪资结构"


def test_shortlist_set_tags_and_note(store):
	"""候选池标签和备注应能独立更新。"""
	store.add_shortlist({"security_id": "s1", "job_id": "j1", "title": "", "company": "", "city": "", "salary": "", "source": ""})
	assert store.set_shortlist_tags("s1", "j1", ["远程", "双休", "远程"]) is True
	assert store.set_shortlist_note("s1", "j1", "等待 HR 回复") is True

	result = store.list_shortlist()
	assert result[0]["tags"] == ["远程", "双休"]
	assert result[0]["note"] == "等待 HR 回复"
	assert store.set_shortlist_tags("missing", "j1", ["x"]) is False
	assert store.set_shortlist_note("missing", "j1", "x") is False


def test_shortlist_is_shortlisted(store):
	"""检查候选池存在性。"""
	assert store.is_shortlisted("s1", "j1") is False
	store.add_shortlist({"security_id": "s1", "job_id": "j1", "title": "", "company": "", "city": "", "salary": "", "source": ""})
	assert store.is_shortlisted("s1", "j1") is True


def test_shortlist_remove(store):
	"""移除候选池项应返回 True，不存在时返回 False。"""
	store.add_shortlist({"security_id": "s1", "job_id": "j1", "title": "", "company": "", "city": "", "salary": "", "source": ""})
	assert store.remove_shortlist("s1", "j1") is True
	assert store.is_shortlisted("s1", "j1") is False
	assert store.remove_shortlist("s1", "j1") is False


def test_shortlist_order_by_created_desc(store):
	"""候选池列表应按创建时间倒序。"""
	for i in range(3):
		store.add_shortlist({"security_id": f"s{i}", "job_id": f"j{i}", "title": f"job{i}", "company": "", "city": "", "salary": "", "source": ""})
		time.sleep(0.01)
	result = store.list_shortlist()
	assert result[0]["title"] == "job2"
	assert result[2]["title"] == "job0"


# ── context manager ────────────────────────────────────────────────


def test_context_manager(tmp_path):
	"""CacheStore 应支持 with 语句。"""
	db = tmp_path / "cache" / "ctx.db"
	with CacheStore(db) as store:
		store.record_greet("s1", "j1")
		assert store.is_greeted("s1") is True
