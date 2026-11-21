"""批次 88 审计修复：crawl 的 UTF-8 键安全化与详情阶段本地缓存错误（Issue #2350、#2351）。"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from boss_agent_cli.cache.store import CacheBusyError, CacheStore
from boss_agent_cli.crawler.service import CrawlBudget, CrawlService, CrawlSettings


def _free_port() -> int:
	with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
		connection.bind(("127.0.0.1", 0))
		return int(connection.getsockname()[1])


def _settings(tmp_path: Path, *, with_detail: bool = False) -> CrawlSettings:
	return CrawlSettings(
		query="AI",
		city_code="101210100",
		pages=1,
		with_detail=with_detail,
		profile_path=tmp_path / "profile",
		chrome_path=None,
		cdp_port=_free_port(),
		hook_profile="none",
		operating_mode="research",
	)


class _NoDelayBudget(CrawlBudget):
	def wait(self, kind: str, on_wait=None) -> None:
		pass


class _DetailBusyBudget(CrawlBudget):
	"""列表请求放行，详情请求前的预算等待模拟写锁冲突。"""

	def wait(self, kind: str, on_wait=None) -> None:
		if kind != "list":
			raise CacheBusyError(None, "database is locked")


class _DetailTransport:
	def __init__(self, page: dict) -> None:
		self.page = page
		self.detail_calls: list[str] = []

	def open(self) -> list:
		return []

	def fetch_page(self, query: str, city_code: str, page_no: int) -> dict:
		return self.page

	def fetch_detail(self, security_id: str) -> dict:
		self.detail_calls.append(security_id)
		return {"code": 0, "zpData": {"jobCard": {"postDescription": "职位描述"}}}

	def close(self, *, quit_browser: bool = True) -> None:
		pass


def _page(*jobs: dict) -> dict:
	return {"code": 0, "zpData": {"jobList": list(jobs), "hasMore": False}}


# ── Issue #2350：孤立代理项 job id 不能把任务卡成 stopped ────────────


def test_surrogate_job_id_completes_with_encodable_key(tmp_path):
	transport = _DetailTransport(_page({"encryptJobId": "\ud800", "jobName": "AI 工程师"}))
	cache = CacheStore(tmp_path / "cache" / "boss_agent.db")
	service = CrawlService(
		cache,
		data_dir=tmp_path,
		transport_factory=lambda settings: transport,
		budget_factory=_NoDelayBudget,
	)
	with cache:
		outcome = service.create_and_run(_settings(tmp_path))

		assert outcome.status == "completed"
		assert outcome.error == ""
		[item] = cache.list_crawl_jobs(outcome.run_id)
		# 键必须是可绑定的普通文本：同一职位重复抓取得到同一个键。
		item["job_key"].encode("utf-8")


def test_surrogate_title_in_fallback_key_completes(tmp_path):
	transport = _DetailTransport(_page({"encryptJobId": "", "jobName": "标题\udcff"}))
	cache = CacheStore(tmp_path / "cache" / "boss_agent.db")
	service = CrawlService(
		cache,
		data_dir=tmp_path,
		transport_factory=lambda settings: transport,
		budget_factory=_NoDelayBudget,
	)
	with cache:
		outcome = service.create_and_run(_settings(tmp_path))

		assert outcome.status == "completed"
		[item] = cache.list_crawl_jobs(outcome.run_id)
		assert "1:1:" in item["job_key"]
		item["job_key"].encode("utf-8")


# ── Issue #2351：详情阶段的缓存锁冲突原样上抛，不报 completed ─────────


def test_detail_cache_busy_propagates_instead_of_completing(tmp_path):
	transport = _DetailTransport(
		_page({"encryptJobId": "job-1", "securityId": "s1", "jobName": "AI 工程师"})
	)
	cache = CacheStore(tmp_path / "cache" / "boss_agent.db")
	service = CrawlService(
		cache,
		data_dir=tmp_path,
		transport_factory=lambda settings: transport,
		budget_factory=_DetailBusyBudget,
	)
	with cache, pytest.raises(CacheBusyError):
		service.create_and_run(_settings(tmp_path, with_detail=True))
