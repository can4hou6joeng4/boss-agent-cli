"""第八轮审计批次：Issue #2223–#2225 的回归测试。

- #2223：缓存层本地 JSON 列容忍孤立代理字符，采集任务不再永久卡死。
- #2224：候选池逐行容错，单条坏记录不影响其余记录读取。
- #2225：残留事务不再被误判为缓存库损坏。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheDatabaseInvalid, CacheStore, CacheWriteRejected
from boss_agent_cli.main import cli


def _store(tmp_path: Path) -> CacheStore:
	return CacheStore(tmp_path / "cache" / "boss_agent.db")


# ── #2223：孤立代理字符 ─────────────────────────────────────────────


def test_crawl_job_payload_accepts_lone_surrogate(tmp_path: Path) -> None:
	with _store(tmp_path) as store:
		store.create_crawl_run("run-1", {"query": "AI"}, str(tmp_path / "out"))
		store.put_crawl_job(
			"run-1", "job-1", 1, {"title": "\ud800", "company": "某公司"}, detail_done=False
		)

		job = store.get_crawl_job("run-1", "job-1")

	assert job is not None
	assert job["payload"]["title"] == "\ud800"
	assert job["payload"]["company"] == "某公司"


def test_make_search_key_accepts_lone_surrogate(tmp_path: Path) -> None:
	key = CacheStore._make_search_key({"query": "\ud800"})

	assert len(key) == 64
	assert key == CacheStore._make_search_key({"query": "\ud800"})


def test_make_search_key_keeps_normal_hashes(tmp_path: Path) -> None:
	params = {"query": "AI 工程师", "city": "杭州"}
	expected = hashlib.sha256(json.dumps(params, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

	assert CacheStore._make_search_key(params) == expected


# ── #2224：候选池逐行容错 ───────────────────────────────────────────


def _seed_shortlist(store: CacheStore) -> None:
	store.add_shortlist({"security_id": "s1", "job_id": "j1", "title": "正常职位", "company": "A"})
	store.add_shortlist({"security_id": "s2", "job_id": "j2", "title": "坏职位", "company": "B"})
	# created_at 是 REAL 列，BLOB 值不会被亲和性转换。
	store._conn.execute("UPDATE shortlist_records SET created_at = X'00' WHERE security_id = 's2'")
	store._conn.commit()


def test_list_shortlist_skips_corrupt_rows(tmp_path: Path) -> None:
	with _store(tmp_path) as store:
		_seed_shortlist(store)
		items = store.list_shortlist()
		items_with_corrupt, corrupt = store.list_shortlist_with_corrupt()

	assert [item["security_id"] for item in items] == ["s1"]
	assert [item["security_id"] for item in items_with_corrupt] == ["s1"]
	assert corrupt == ["j2"]


def test_shortlist_list_command_reports_corrupt_entries(tmp_path: Path) -> None:
	with _store(tmp_path) as store:
		_seed_shortlist(store)

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "shortlist", "list"])

	envelope = json.loads(result.stdout)
	assert envelope["ok"] is True
	assert [item["security_id"] for item in envelope["data"]] == ["s1"]
	assert envelope["hints"]["corrupt"] == ["j2"]
	assert any("已损坏" in action for action in envelope["hints"]["operator_actions"])


# ── #2225：残留事务不按库损坏上报 ───────────────────────────────────


def test_leftover_transaction_does_not_break_reserve(tmp_path: Path) -> None:
	with _store(tmp_path) as store:
		store._conn.execute("BEGIN")
		store._conn.execute("DELETE FROM crawl_budget")

		wait = store.reserve_crawl_budget("budget-key", now=time.time(), interval=0.0)

		assert wait == 0.0
		assert store._conn.in_transaction is False


def test_transaction_within_transaction_is_not_cache_corruption(tmp_path: Path) -> None:
	with _store(tmp_path) as store:
		translated = store._conn._translate(
			sqlite3.OperationalError("cannot start a transaction within a transaction")
		)

	assert isinstance(translated, CacheWriteRejected)
	assert not isinstance(translated, CacheDatabaseInvalid)
