"""crawl 断点身份、页码与后台恢复的审计回归（Issue #2029–#2032）。"""

from __future__ import annotations

import json

import pytest

from boss_agent_cli.cache.store import CacheRecordInvalid, CacheStore
from boss_agent_cli.commands.crawl import _prepare_background_resume
from boss_agent_cli.crawler.service import CrawlSettings, _normalize_job


def _cache_path(tmp_path):
	return tmp_path / "cache" / "boss_agent.db"


def _seed_run(cache: CacheStore, tmp_path, *, params=None, status="running") -> None:
	cache.create_crawl_run(
		"run-1", params if params is not None else {"pages": 5}, str(tmp_path / "out"), status=status
	)


def test_job_key_does_not_use_rotating_security_id() -> None:
	"""securityId 每次搜索请求都会轮换，不能当去重身份（Issue #2029）。"""
	first, _ = _normalize_job({"securityId": "sec-A", "jobName": "AI 工程师"}, "q", 1, 2)
	again, _ = _normalize_job({"securityId": "sec-B", "jobName": "AI 工程师"}, "q", 1, 2)
	assert first == again == "1:2:AI 工程师"

	with_job_id, _ = _normalize_job({"encryptJobId": "job-1", "securityId": "sec-A"}, "q", 1, 1)
	assert with_job_id == "job-1"


def test_crawl_run_rejects_next_page_beyond_pages(tmp_path) -> None:
	"""next_page 上界是 params.pages + 1；超大值必须按记录损坏拒绝（Issue #2030）。"""
	with CacheStore(_cache_path(tmp_path)) as cache:
		_seed_run(cache, tmp_path)
		cache._conn.execute("UPDATE crawl_runs SET next_page = 6 WHERE run_id = 'run-1'")
		cache._conn.commit()
		assert cache.get_crawl_run("run-1")["next_page"] == 6

		cache._conn.execute("UPDATE crawl_runs SET next_page = 7 WHERE run_id = 'run-1'")
		cache._conn.commit()
		with pytest.raises(CacheRecordInvalid) as excinfo:
			cache.get_crawl_run("run-1")
	assert "pages + 1" in str(excinfo.value)

	with CacheStore(_cache_path(tmp_path)) as cache:
		cache._conn.execute("UPDATE crawl_runs SET next_page = 1000000000 WHERE run_id = 'run-1'")
		cache._conn.commit()
		with pytest.raises(CacheRecordInvalid):
			cache.get_crawl_run("run-1")


def test_crawl_job_negative_page_no_rejected(tmp_path) -> None:
	"""结果表的 page_no 与 next_page 同口径拒绝非正数（Issue #2031）。"""
	with CacheStore(_cache_path(tmp_path)) as cache:
		_seed_run(cache, tmp_path)
		cache.put_crawl_job("run-1", "k1", 1, {"title": "AI 工程师"}, detail_done=False)
		cache._conn.execute("UPDATE crawl_jobs SET page_no = -3 WHERE run_id = 'run-1'")
		cache._conn.commit()
		with pytest.raises(CacheRecordInvalid) as excinfo:
			cache.list_crawl_jobs("run-1")
		assert "page_no" in str(excinfo.value)
		with pytest.raises(CacheRecordInvalid):
			cache.get_crawl_job("run-1", "k1")


def _settings_dict(tmp_path) -> dict:
	return CrawlSettings(
		query="q",
		city_code="101",
		pages=5,
		with_detail=False,
		profile_path=tmp_path / "chrome-profile",
		chrome_path=None,
		cdp_port=9222,
		hook_profile="none",
	).as_dict()


def test_background_resume_completed_corrupt_params_reports_invalid(tmp_path) -> None:
	"""completed 快速路径不能跳过设置校验（Issue #2032）。"""
	corrupt = {**_settings_dict(tmp_path), "pages": "abc"}
	with CacheStore(_cache_path(tmp_path)) as cache:
		_seed_run(cache, tmp_path, params=corrupt, status="completed")

	with pytest.raises(CacheRecordInvalid) as excinfo:
		_prepare_background_resume(
			tmp_path,
			"run-1",
			pages=None,
			with_detail=False,
			clear_stop=True,
			preflight=lambda: None,
		)
	assert "params.pages" in str(excinfo.value)

	# 对照：同一断点参数合法时仍按 completed 返回，不启动 worker。
	with CacheStore(_cache_path(tmp_path)) as cache:
		cache._conn.execute(
			"UPDATE crawl_runs SET params = ? WHERE run_id = 'run-1'",
			(json.dumps(_settings_dict(tmp_path), ensure_ascii=False),),
		)
		cache._conn.commit()
	assert _prepare_background_resume(
		tmp_path,
		"run-1",
		pages=None,
		with_detail=False,
		clear_stop=True,
		preflight=lambda: None,
	) == ("completed", False)
