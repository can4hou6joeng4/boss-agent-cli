"""招聘记录与 watch 缓存边界回归（Issue #1971–#1974）。"""

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands.stats import _collect_stats


def _store(path: Path) -> CacheStore:
	return CacheStore(path)


def test_record_recruiter_greet_missing_row_raises(tmp_path: Path) -> None:
	"""预约行被外部清理后 UPDATE 命中 0 行必须抛错，不能静默假装记上了（#1971）。"""
	cache = _store(tmp_path / "cache.db")
	try:
		assert cache.claim_recruiter_greet("geek", "job") is None
		cache._conn.execute("DELETE FROM recruiter_greet_records")
		cache._conn.commit()
		with pytest.raises(sqlite3.OperationalError, match="招聘者招呼记录不存在"):
			cache.record_recruiter_greet("geek", "job")
	finally:
		cache.close()


def test_record_recruiter_greet_quota_limit_missing_row_raises(tmp_path: Path) -> None:
	"""quota_limited 写入同样不能对缺失记录静默成功（#1971）。"""
	cache = _store(tmp_path / "cache.db")
	try:
		with pytest.raises(sqlite3.OperationalError, match="招聘者招呼记录不存在"):
			cache.record_recruiter_greet("geek", "job", status="quota_limited")
	finally:
		cache.close()


def test_record_recruiter_greet_updates_existing_row(tmp_path: Path) -> None:
	"""正常路径仍把 pending 行更新成 sent（#1971 回归护栏）。"""
	cache = _store(tmp_path / "cache.db")
	try:
		assert cache.claim_recruiter_greet("geek", "job") is None
		cache.record_recruiter_greet("geek", "job")
		assert cache.claim_recruiter_greet("geek", "job") == "sent"
	finally:
		cache.close()


def test_watch_job_key_keeps_account_when_job_id_missing() -> None:
	"""缺 job_id 时账号指纹仍必须入键，两个账号的已见记录不能互相串扰（#1973）。"""
	item = {"security_id": "sec-1"}
	key_a = CacheStore._make_watch_job_key(item, account="account-a")
	key_b = CacheStore._make_watch_job_key(item, account="account-b")
	assert key_a.startswith("account-a:")
	assert key_b.startswith("account-b:")
	assert key_a != key_b
	assert CacheStore._make_watch_job_key(item) == "sec-1:"


def test_watch_job_key_hash_branch_includes_account_and_escapes_surrogate() -> None:
	"""hash 分支同样带上账号，且孤立代理项不再让编码抛错（#1972 #1973）。"""
	item = {"title": "坏字符 \ud800"}
	key_a = CacheStore._make_watch_job_key(item, account="account-a")
	key_b = CacheStore._make_watch_job_key(item, account="account-b")
	assert key_a.startswith("account-a:")
	assert key_b.startswith("account-b:")
	assert key_a != key_b
	assert not CacheStore._make_watch_job_key(item).startswith("account-")


def test_record_watch_results_accepts_lone_surrogate_payload(tmp_path: Path) -> None:
	"""平台 JSON 的孤立代理项不再让整批 watch 结果以 UnicodeEncodeError 丢弃（#1972）。"""
	cache = _store(tmp_path / "cache.db")
	path = tmp_path / "cache.db"
	try:
		items = [{"job_id": "job-1", "title": "坏字符 \ud800", "nested": {"k": "\udfff"}}]
		result = cache.record_watch_results("search-1", items)
		assert result["new_count"] == 1
	finally:
		cache.close()
	row = sqlite3.connect(str(path)).execute(
		"SELECT payload FROM watch_hits WHERE search_name = ?", ("search-1",)
	).fetchone()
	payload = json.loads(row[0])
	assert payload["title"] == "坏字符 \ud800"


def test_record_watch_results_rolls_back_on_midway_failure(tmp_path: Path) -> None:
	"""循环中途异常回滚整轮，不把半截插入留给同一连接上的后续写提交（#1972）。"""
	path = tmp_path / "cache.db"
	cache = _store(path)
	try:
		original = cache._make_watch_job_key
		calls = {"count": 0}

		def failing(item: dict[str, Any], *, account: str = "") -> str:
			calls["count"] += 1
			if calls["count"] == 2:
				raise RuntimeError("fixture failure")
			return original(item, account=account)

		cache._make_watch_job_key = failing  # type: ignore[method-assign]
		with pytest.raises(RuntimeError, match="fixture failure"):
			cache.record_watch_results(
				"search-1",
				[{"job_id": "job-1"}, {"job_id": "job-2"}],
			)
	finally:
		cache.close()
	rows = sqlite3.connect(str(path)).execute("SELECT COUNT(*) FROM watch_hits").fetchone()[0]
	assert rows == 0


def test_stats_window_excludes_timestamp_drift(tmp_path: Path) -> None:
	"""BLOB / 非数值文本漂移的 first_seen_at 不能永远满足窗口条件而虚高计数（#1974）。"""
	path = tmp_path / "cache.db"
	cache = _store(path)
	now = time.time()
	try:
		with cache._conn:
			cache._conn.execute(
				"INSERT INTO watch_hits VALUES (?, ?, ?, ?, ?)", ("s", "k1", "{}", now, now)
			)
			cache._conn.execute(
				"INSERT INTO watch_hits VALUES (?, ?, ?, ?, ?)",
				("s", "k2", "{}", sqlite3.Binary(b"\x00drift"), now),
			)
			cache._conn.execute(
				"INSERT INTO watch_hits VALUES (?, ?, ?, ?, ?)", ("s", "k3", "{}", "not-a-timestamp", now)
			)
	finally:
		cache.close()
	data = _collect_stats(path, 1)
	assert data["window"]["watch_hits"] == 1
