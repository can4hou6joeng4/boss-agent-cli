"""审计批次五十九：采集导出清理竞态与本地缓存错误分类（Issue #2323、#2324）。

- #2323：执行器在逐页 / 最终导出后重读断点，并发 ``clean --privacy`` 已删行
  时删除刚写出的产物，不能复活隐私数据；
- #2324：transport 的 attempt 回调抛出的本地缓存错误（busy / 记录损坏 /
  SQLite 故障）原样上抛，不被兜成可重试的传输错误，``_with_retry`` 也不重试。

全部离线：transport 只有 fake page 与本地回调，不启动浏览器、不联网。
"""

from __future__ import annotations

import socket
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

import boss_agent_cli.crawler.service as service_mod
from boss_agent_cli.cache.store import CacheBusyError, CacheStore
from boss_agent_cli.crawler.service import CrawlBudget, CrawlService, CrawlSettings
from boss_agent_cli.crawler.transport import DrissionCrawlerSession

_RUN_ID = "run-1"


def _free_port() -> int:
	with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
		connection.bind(("127.0.0.1", 0))
		return int(connection.getsockname()[1])


def _params() -> dict[str, Any]:
	return {
		"query": "AI",
		"city_code": "101210100",
		"pages": 1,
		"with_detail": False,
		"browser": {"profile_path": "/tmp/profile", "cdp_port": 9333},
		"chrome_path": None,
		"hook": {"profile": "none", "source": "user-supplied", "directory": None},
		"limits": {"max_requests": 20, "max_details": 50, "max_seconds": 600, "max_retries": 1},
		"operating_mode": "research",
	}


def _settings(tmp_path: Path) -> CrawlSettings:
	return CrawlSettings(
		query="AI",
		city_code="101210100",
		pages=1,
		with_detail=False,
		profile_path=tmp_path / "profile",
		chrome_path=None,
		cdp_port=_free_port(),
		hook_profile="none",
		max_requests=20,
		max_details=50,
		max_seconds=600,
		max_retries=0,
		operating_mode="research",
	)


class _NoDelayBudget(CrawlBudget):
	def wait(self, kind: str) -> None:
		pass


def _seed(tmp_path: Path) -> Path:
	db_path = tmp_path / "cache" / "boss_agent.db"
	db_path.parent.mkdir(parents=True, exist_ok=True)
	with CacheStore(db_path) as cache:
		cache.create_crawl_run(_RUN_ID, _params(), str(tmp_path / "crawl" / _RUN_ID), status="stopped")
	return db_path


def _service(cache: CacheStore, tmp_path: Path, transport_factory) -> CrawlService:
	return CrawlService(
		cache,
		data_dir=tmp_path,
		transport_factory=transport_factory,
		budget_factory=lambda store: _NoDelayBudget(store),
	)


class _OnePageTransport:
	def __init__(self, *, has_more: bool = True) -> None:
		self.has_more = has_more
		self.page_calls = 0

	def open(self) -> list:
		return []

	def fetch_page(self, query: str, city_code: str, page_no: int) -> dict:
		self.page_calls += 1
		return {
			"code": 0,
			"zpData": {
				"jobList": [
					{"encryptJobId": "job-1", "jobName": "隐私职位标题", "brandName": "隐私公司"}
				],
				"hasMore": self.has_more,
			},
		}

	def close(self, *, quit_browser: bool = True) -> None:
		pass


def _delete_run_rows(db_path: Path) -> None:
	"""模拟 clean --privacy 的第一步：先提交删除数据库行。"""
	with sqlite3.connect(db_path) as connection:
		connection.execute("DELETE FROM crawl_jobs")
		connection.execute("DELETE FROM crawl_runs")


# ── #2323：导出与 clean --privacy 竞态 ─────────────────────────────


@pytest.mark.parametrize("delete_on_write", [1, 2])
def test_export_does_not_resurrect_privacy_cleaned_run(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch, delete_on_write: int,
) -> None:
	"""逐页导出（第 1 次写）与最终导出（第 2 次写）后都不能复活已清理的数据。"""
	db_path = _seed(tmp_path)
	output_dir = tmp_path / "crawl" / _RUN_ID
	real_write = service_mod.write_run_outputs
	calls = 0

	def write_then_clean(path: Path, rows: list[dict[str, Any]]) -> dict[str, str]:
		nonlocal calls
		calls += 1
		if calls == delete_on_write:
			_delete_run_rows(db_path)
		return real_write(path, rows)

	monkeypatch.setattr(service_mod, "write_run_outputs", write_then_clean)

	cache = CacheStore(db_path)
	try:
		transport = _OnePageTransport()
		service = _service(cache, tmp_path, lambda _settings: transport)
		with pytest.raises(KeyError):
			service.resume(_RUN_ID, operating_mode="research")

		assert transport.page_calls == 1, "断点消失后不能再发平台请求"
		assert cache.get_crawl_run(_RUN_ID) is None
	finally:
		cache.close()

	assert calls >= delete_on_write
	for extension in ("json", "csv", "xlsx"):
		assert not (output_dir / f"jobs.{extension}").exists(), "隐私产物不能复活"


def test_completed_fast_path_still_drops_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""回归：completed 快速路径的 #2241 收口不受新增 helper 影响。"""
	db_path = _seed(tmp_path)
	output_dir = tmp_path / "crawl" / _RUN_ID
	with CacheStore(db_path) as cache:
		cache.update_crawl_run(_RUN_ID, status="completed", next_page=1, list_finished=True)
	real_write = service_mod.write_run_outputs

	def write_then_clean(path: Path, rows: list[dict[str, Any]]) -> dict[str, str]:
		written = real_write(path, rows)
		_delete_run_rows(db_path)
		return written

	monkeypatch.setattr(service_mod, "write_run_outputs", write_then_clean)

	cache = CacheStore(db_path)
	try:
		service = _service(cache, tmp_path, lambda _settings: None)
		with pytest.raises(KeyError):
			service.resume(_RUN_ID, operating_mode="research")
	finally:
		cache.close()

	for extension in ("json", "csv", "xlsx"):
		assert not (output_dir / f"jobs.{extension}").exists()


# ── #2324：本地缓存错误不能被吞成传输错误 ─────────────────────────


def test_transport_preserves_cache_busy_from_attempt_callback(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	session = DrissionCrawlerSession(
		profile_path=tmp_path / "profile",
		chrome_path=None,
		cdp_port=_free_port(),
		hook_profile="none",
		hook_dir=None,
	)
	session._page = object()
	monkeypatch.setattr(session, "_start_joblist_listener", lambda: None)
	monkeypatch.setattr(session, "_raise_if_platform_risk_page", lambda: None)

	@contextmanager
	def claim(kind: str):
		raise CacheBusyError(None, "database is locked")
		yield

	session.bind_attempts(claim)

	with pytest.raises(CacheBusyError):
		session.fetch_page("AI", "101210100", 1)


def test_with_retry_does_not_retry_cache_errors(tmp_path: Path) -> None:
	cache = CacheStore(tmp_path / "cache.db")
	try:
		service = CrawlService(cache, data_dir=tmp_path, transport_factory=lambda _settings: None)
		calls: list[int] = []

		def action() -> dict[str, Any]:
			calls.append(1)
			raise CacheBusyError(None, "database is locked")

		with pytest.raises(CacheBusyError):
			service._with_retry(action, max_retries=3)

		assert len(calls) == 1, "本地写锁冲突重试不会自愈"
	finally:
		cache.close()


def test_with_retry_does_not_retry_corrupt_records(tmp_path: Path) -> None:
	from boss_agent_cli.cache.store import CacheRecordInvalid

	cache = CacheStore(tmp_path / "cache.db")
	try:
		service = CrawlService(cache, data_dir=tmp_path, transport_factory=lambda _settings: None)
		calls: list[int] = []

		def action() -> dict[str, Any]:
			calls.append(1)
			raise CacheRecordInvalid("crawl_run", _RUN_ID, "params", "不是 JSON 对象")

		with pytest.raises(CacheRecordInvalid):
			service._with_retry(action, max_retries=3)

		assert len(calls) == 1
	finally:
		cache.close()
