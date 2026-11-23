"""crawl 控制面与详情缓存的三处缺陷（Issue #2642）。

1. 断点里 ``job_id`` 为显式 null 时，``str(None)`` 得到 ``"None"``，详情缓存按
   这个伪主键跨职位共享，后一个职位直接采用前一个职位的描述。
2. 后台恢复预检失败且还原写库失败时，还原异常顶替原始错误，第二个还原也
   不再执行（前台 ``_resume_locked`` 在 #2562 已修，后台路径漏了）。
3. 执行器已持锁、尚未把 queued 改成 running 时，status 同时报
   ``executor_active=true`` 与 ``queue_overdue=true`` 并引导并发 resume。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands.crawl import _LoginSeedUnavailable, _prepare_background_resume
from boss_agent_cli.crawler.operations import crawl_status_from_run
from boss_agent_cli.crawler.service import (
	CrawlRunBudget,
	CrawlService,
	CrawlSettings,
	crawl_execution_lock,
)


def _settings(tmp_path: Path, *, with_detail: bool = True) -> CrawlSettings:
	return CrawlSettings(
		query="AI",
		city_code="101210100",
		pages=1,
		with_detail=with_detail,
		profile_path=(tmp_path / "crawl" / "chrome-profile").resolve(),
		chrome_path=None,
		cdp_port=9222,
		hook_profile="none",
	)


def _db(tmp_path: Path) -> Path:
	return tmp_path / "cache" / "boss_agent.db"


# ── 1. null job_id 不得成为详情缓存的共享键 ─────────────────────────────


def test_pending_details_does_not_share_cache_across_null_job_ids(tmp_path):
	settings = _settings(tmp_path, with_detail=True)
	with CacheStore(_db(tmp_path)) as cache:
		cache.create_crawl_run(
			"run-null", settings.as_dict(), str(tmp_path / "crawl" / "run-null"), status="running"
		)
		for key, security_id in (("job-a", "sec-a"), ("job-b", "sec-b")):
			cache.put_crawl_job(
				"run-null",
				key,
				1,
				{
					"query": "AI",
					"page": 1,
					"rank": 1,
					"job_id": None,
					"security_id": security_id,
					"title": key,
					"post_description": "",
					"detail_status": "not_requested",
				},
				detail_done=False,
			)

	class FakeTransport:
		def __init__(self) -> None:
			self.detail_calls: list[str] = []

		def bind_attempts(self, *args: object, **kwargs: object) -> None:
			return None

		def fetch_detail(self, security_id: str) -> dict[str, object]:
			self.detail_calls.append(security_id)
			return {"zpData": {"jobCard": {"postDescription": f"desc-{security_id}"}}}

	class FakeBudget:
		def wait(self, *args: object, **kwargs: object) -> None:
			return None

	transport = FakeTransport()
	with CacheStore(_db(tmp_path)) as cache:
		service = CrawlService(
			cache, data_dir=tmp_path, transport_factory=lambda _settings: transport  # type: ignore[arg-type]
		)
		service._complete_pending_details(
			"run-null", settings, transport, FakeBudget(), CrawlRunBudget(settings)  # type: ignore[arg-type]
		)
		rows = {item["job_key"]: item for item in cache.list_crawl_jobs("run-null")}

	assert sorted(transport.detail_calls) == ["sec-a", "sec-b"]
	assert rows["job-a"]["payload"]["post_description"] == "desc-sec-a"
	assert rows["job-b"]["payload"]["post_description"] == "desc-sec-b"
	assert rows["job-a"]["detail_done"] is True
	assert rows["job-b"]["detail_done"] is True
	with CacheStore(_db(tmp_path)) as cache:
		assert cache.get_job_desc("None") is None
		assert cache.get_job_desc("") is None


# ── 2. 后台恢复的还原失败不能顶替原始错误 ───────────────────────────────


def test_background_resume_restore_failure_keeps_original_error(tmp_path, monkeypatch):
	settings = _settings(tmp_path, with_detail=False)
	with CacheStore(_db(tmp_path)) as cache:
		cache.create_crawl_run(
			"run-1", settings.as_dict(), str(tmp_path / "crawl" / "run-1"), status="stopped"
		)
		cache.update_crawl_run("run-1", status="stopped", next_page=1, error="previous failure")
	with sqlite3.connect(_db(tmp_path)) as connection:
		connection.execute("UPDATE crawl_runs SET stop_requested = 1 WHERE run_id = 'run-1'")

	def _explode(self, run_id, *, updated_at):
		raise OSError(28, "No space left on device")

	monkeypatch.setattr(CacheStore, "restore_crawl_stop_request", _explode)

	def _preflight() -> None:
		raise _LoginSeedUnavailable("本地登录态不足")

	try:
		_prepare_background_resume(
			tmp_path,
			"run-1",
			pages=None,
			with_detail=False,
			clear_stop=True,
			preflight=_preflight,
		)
	except _LoginSeedUnavailable as exc:
		assert any("stop 标记还原失败" in note for note in getattr(exc, "__notes__", []))
	except OSError as exc:  # pragma: no cover - 还原异常不得顶替原始错误
		raise AssertionError(f"还原异常顶替了原始错误: {exc}") from exc
	else:  # pragma: no cover - 失败路径必须抛出原始异常
		raise AssertionError("后台恢复应抛出原始登录态错误")

	with CacheStore(_db(tmp_path)) as cache:
		run = cache.get_crawl_run("run-1")
	assert run is not None
	assert run["status"] == "stopped"
	assert run["error"] == "previous failure"


# ── 3. 持锁执行器在场时不算排队超时 ─────────────────────────────────────


def test_status_queued_with_live_executor_is_not_overdue(tmp_path):
	settings = _settings(tmp_path, with_detail=False)
	with CacheStore(_db(tmp_path)) as cache:
		cache.create_crawl_run(
			"run-queued", settings.as_dict(), str(tmp_path / "crawl" / "run-queued"), status="queued"
		)
	with sqlite3.connect(_db(tmp_path)) as connection:
		connection.execute(
			"UPDATE crawl_runs SET updated_at = ? WHERE run_id = 'run-queued'", (time.time() - 3600,)
		)

	with CacheStore(_db(tmp_path)) as cache:
		run = cache.get_crawl_run("run-queued")
		assert run is not None
		with crawl_execution_lock(tmp_path, "run-queued"):
			payload = crawl_status_from_run(cache, run, data_dir=tmp_path)

	assert payload["executor_active"] is True
	assert "queue_overdue" not in payload
