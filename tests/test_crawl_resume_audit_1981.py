"""crawl 恢复边界回归：详情失败、翻页预算与 hook 审计记录（Issue #1981–#1983）。"""

from __future__ import annotations

import socket
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.crawler.controls import CrawlProfileBusy
from boss_agent_cli.crawler.service import CrawlBudget, CrawlService, CrawlSettings
from boss_agent_cli.crawler.transport import DrissionCrawlerSession


def _free_port() -> int:
	with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
		connection.bind(("127.0.0.1", 0))
		return int(connection.getsockname()[1])


class _NoDelayBudget(CrawlBudget):
	def wait(self, kind: str) -> None:
		pass


def _settings(tmp_path: Path, *, pages: int = 2, with_detail: bool = False, max_retries: int = 0) -> CrawlSettings:
	return CrawlSettings(
		query="AI",
		city_code="101210100",
		pages=pages,
		with_detail=with_detail,
		profile_path=tmp_path / "profile",
		chrome_path=None,
		cdp_port=_free_port(),
		hook_profile="none",
		max_requests=20,
		max_details=50,
		max_seconds=600,
		max_retries=max_retries,
		operating_mode="research",
	)


def _service(cache: CacheStore, tmp_path: Path, transport_factory):
	return CrawlService(
		cache,
		data_dir=tmp_path,
		transport_factory=transport_factory,
		budget_factory=_NoDelayBudget,
	)


class _DetailFailingTransport:
	"""列表正常、详情始终返回空 jobCard 的平台桩。"""

	def __init__(self) -> None:
		self.page_calls: list[int] = []
		self.detail_calls: list[str] = []
		self.pages = {
			1: {
				"code": 0,
				"zpData": {
					"jobList": [
						{
							"encryptJobId": "job-1",
							"securityId": "sec-1",
							"jobName": "测试职位",
							"brandName": "测试公司",
						}
					],
					"hasMore": True,
				},
			},
			2: {"code": 0, "zpData": {"jobList": [], "hasMore": False}},
		}

	def open(self) -> list:
		return []

	def fetch_page(self, query: str, city_code: str, page_no: int) -> dict:
		self.page_calls.append(page_no)
		return self.pages[page_no]

	def fetch_detail(self, security_id: str) -> dict:
		self.detail_calls.append(security_id)
		return {"code": 0, "zpData": {}}

	def close(self, *, quit_browser: bool = True) -> None:
		pass


def test_detail_failure_keeps_paging_and_exporting(tmp_path: Path) -> None:
	"""单个职位详情反复失败不再卡死列表翻页与导出（#1981）。"""
	transport = _DetailFailingTransport()
	cache = CacheStore(tmp_path / "cache.db")
	try:
		service = _service(cache, tmp_path, lambda settings: transport)
		run_id = service.create(_settings(tmp_path, pages=2, with_detail=True))

		outcome = service.resume(run_id, operating_mode="research")

		assert outcome.status == "completed"
		assert transport.page_calls == [1, 2], "详情失败不能阻止继续翻页"
		assert outcome.output_paths, "详情缺口不能让导出缺失"
		jobs = cache.list_crawl_jobs(run_id)
		assert len(jobs) == 1
		assert jobs[0]["detail_done"] is False
		assert jobs[0]["payload"]["detail_status"] == "detail-error"
		# 同一轮内不重复重试同一条目：初始补全 + 两页之后都会被跳过。
		assert transport.detail_calls == ["sec-1"]
	finally:
		cache.close()


def test_detail_failure_does_not_stop_on_following_pages(tmp_path: Path) -> None:
	"""后续页新增的待补条目仍会被处理，已有失败条目只是被跳过（#1981）。"""
	transport = _DetailFailingTransport()
	transport.pages[1]["zpData"]["jobList"].append(
		{"encryptJobId": "job-2", "securityId": "sec-2", "jobName": "第二个职位"}
	)
	transport.pages[2] = {
		"code": 0,
		"zpData": {
			"jobList": [{"encryptJobId": "job-3", "securityId": "sec-3", "jobName": "第三个职位"}],
			"hasMore": False,
		},
	}
	cache = CacheStore(tmp_path / "cache.db")
	try:
		service = _service(cache, tmp_path, lambda settings: transport)
		run_id = service.create(_settings(tmp_path, pages=2, with_detail=True))

		outcome = service.resume(run_id, operating_mode="research")

		assert outcome.status == "completed"
		assert transport.page_calls == [1, 2]
		# 第 1 页两个条目各尝试一次；翻到第 2 页后只有新条目 job-3 会尝试。
		assert sorted(transport.detail_calls) == ["sec-1", "sec-2", "sec-3"]
	finally:
		cache.close()


class _ScrollPage:
	"""每次 to_bottom 加载下一页的页签桩。"""

	url = "https://www.zhipin.com/web/geek/jobs"
	html = "<div class='job-list-box'></div>"

	def __init__(self) -> None:
		self.packets: list[types.SimpleNamespace] = []
		self.navigations = 0
		self.scrolls = 0
		self.listen = types.SimpleNamespace(start=self._clear, stop=lambda: None, wait=self._wait)
		self.scroll = types.SimpleNamespace(down=lambda *args, **kwargs: None, to_bottom=self._next_page)

	def _clear(self, *args, **kwargs) -> None:
		self.packets.clear()

	def get(self, url: str) -> None:
		self.navigations += 1
		self._enqueue(1)

	def _next_page(self) -> None:
		self.scrolls += 1
		self._enqueue(self.scrolls + 1)

	def _enqueue(self, page: int) -> None:
		body = {"code": 0, "zpData": {"jobList": [{"encryptJobId": f"job-{page}"}], "hasMore": True}}
		url = f"https://www.zhipin.com/wapi/zpgeek/search/joblist.json?query=AI&city=101210100&page={page}"
		self.packets.append(
			types.SimpleNamespace(
				url=url,
				response=types.SimpleNamespace(body=body, url=url, status=200, headers={}),
			)
		)

	def _wait(self, timeout: float | None = None, **kwargs):
		if not self.packets:
			return False
		return self.packets.pop(0)


def test_resume_pagination_claims_budget_per_page(tmp_path: Path, monkeypatch) -> None:
	"""恢复到第 N 页时，导航与每次翻页都要单独计入预算与租约（#1982）。"""
	page = _ScrollPage()
	session = DrissionCrawlerSession(
		profile_path=tmp_path / "profile",
		chrome_path=None,
		cdp_port=_free_port(),
		hook_profile="none",
		hook_dir=None,
	)
	session._page = page
	session._page_no = None
	claims: list[str] = []

	@contextmanager
	def claim(kind: str):
		claims.append(kind)
		yield

	session.bind_attempts(claim)
	monkeypatch.setattr("boss_agent_cli.crawler.transport.cancellable_sleep", lambda *args, **kwargs: None)

	payload = session.fetch_page("AI", "101210100", 4)

	assert payload["code"] == 0
	assert claims == ["list-navigation", "list-pagination", "list-pagination", "list-pagination"]


def test_sequential_pagination_claims_budget_once_per_real_request(tmp_path: Path, monkeypatch) -> None:
	"""顺序翻页没有导航请求，只按真实滚动计一次预算，不多记也不空等（#2094）。"""
	page = _ScrollPage()
	session = DrissionCrawlerSession(
		profile_path=tmp_path / "profile",
		chrome_path=None,
		cdp_port=_free_port(),
		hook_profile="none",
		hook_dir=None,
	)
	session._page = page
	session._page_no = 1
	claims: list[str] = []

	@contextmanager
	def claim(kind: str):
		claims.append(kind)
		yield

	session.bind_attempts(claim)
	monkeypatch.setattr("boss_agent_cli.crawler.transport.cancellable_sleep", lambda *args, **kwargs: None)

	payload = session.fetch_page("AI", "101210100", 2)

	assert payload["code"] == 0
	assert page.navigations == 0, "已在第 1 页时不得重新导航"
	assert page.scrolls == 1
	assert claims == ["list-pagination"]


class _BusyOpenTransport:
	def __init__(self) -> None:
		self.close_calls = 0

	def open(self) -> list:
		raise CrawlProfileBusy("采集配置目录正被另一个 Chrome 使用")

	def close(self, *, quit_browser: bool = True) -> None:
		self.close_calls += 1


def test_open_failure_preserves_previous_hook_results(tmp_path: Path) -> None:
	"""打开浏览器前失败不能把断点里的 hook 审计记录清空（#1983）。"""
	cache = CacheStore(tmp_path / "cache.db")
	previous_hooks = [{"name": "screenshot-full", "reason": "", "sha256": "sha", "success": True}]
	try:
		service = _service(cache, tmp_path, lambda settings: _BusyOpenTransport())
		run_id = service.create(_settings(tmp_path))
		cache.update_crawl_run(run_id, status="stopped", next_page=1, hook_results=previous_hooks)

		with pytest.raises(CrawlProfileBusy):
			service.resume(run_id, operating_mode="research")

		run = cache.get_crawl_run(run_id)
		assert run is not None
		assert run["status"] == "stopped"
		assert run["hook_results"] == previous_hooks
	finally:
		cache.close()
