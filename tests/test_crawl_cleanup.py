"""crawl 周期的本地清理必须逐步兜底（Issue #1277）。

`_run` 的 finally 原先顺序执行「budget 落库 → 终态落库 → 关闭浏览器」：budget
写失败会让终态与 close 都被跳过，run 永远停在 running，采集 Chrome 继续占用
共享 profile；且 finally 的异常会覆盖原始异常。现在三步各自兜底，正常路径上
第一个清理错误照常抛出，传播中的原始异常不被覆盖。
"""

from pathlib import Path

import pytest

from boss_agent_cli.cache.store import CacheBusyError, CacheStore
from boss_agent_cli.crawler.controls import CrawlRuntimeUnavailable
from boss_agent_cli.crawler.service import CrawlBudget, CrawlService, CrawlSettings


class _Pages:
	def __init__(self, on_fetch=None) -> None:
		self.on_fetch = on_fetch
		self.closed = False

	def open(self):
		return []

	def fetch_page(self, query, city_code, page_no):
		if self.on_fetch is not None:
			self.on_fetch(page_no)
		return {"code": 0, "zpData": {"jobList": [], "hasMore": False}}

	def close(self, *, quit_browser=True):
		self.closed = True


class _NoDelayBudget(CrawlBudget):
	def wait(self, kind: str) -> None:
		pass


class _BudgetBomb:
	"""包一层真实 cache：budget 落库永远失败，其余方法委托。"""

	def __init__(self, cache: CacheStore) -> None:
		self._cache = cache
		self.statuses: list[str] = []

	def __getattr__(self, name):
		return getattr(self._cache, name)

	def update_crawl_run_budget(self, *args, **kwargs):
		raise CacheBusyError(None, "budget locked")

	def update_crawl_run(self, *args, **kwargs):
		self.statuses.append(str(kwargs.get("status")))
		return self._cache.update_crawl_run(*args, **kwargs)


class _TerminalWriteBomb:
	"""第一次 update_crawl_run（running）成功，终态写入失败。"""

	def __init__(self, cache: CacheStore) -> None:
		self._cache = cache
		self.calls = 0

	def __getattr__(self, name):
		return getattr(self._cache, name)

	def update_crawl_run(self, *args, **kwargs):
		self.calls += 1
		if self.calls > 1:
			raise CacheBusyError(None, "terminal locked")
		return self._cache.update_crawl_run(*args, **kwargs)


def _settings(tmp_path: Path) -> CrawlSettings:
	return CrawlSettings(
		query="AI",
		city_code="101210100",
		pages=1,
		with_detail=False,
		profile_path=tmp_path / "profile",
		chrome_path=None,
		cdp_port=9333,
		hook_profile="none",
		operating_mode="research",
	)


def _service(tmp_path: Path, cache, transport):
	return CrawlService(
		cache,
		data_dir=tmp_path,
		transport_factory=lambda settings: transport,
		budget_factory=_NoDelayBudget,
	)


def test_budget_write_failure_still_writes_terminal_state_and_closes(tmp_path):
	cache = CacheStore(tmp_path / "cache" / "boss_agent.db")
	transport = _Pages()
	bomb = _BudgetBomb(cache)
	service = _service(tmp_path, bomb, transport)
	with cache:
		with pytest.raises(CacheBusyError):
			service.create_and_run(_settings(tmp_path))
	# 终态确实落库：run 不再停在 running/queued。
	assert "stopped" in bomb.statuses
	# 浏览器必须被关闭，不能继续占用共享 profile。
	assert transport.closed is True


def test_terminal_write_failure_still_closes_browser(tmp_path):
	cache = CacheStore(tmp_path / "cache" / "boss_agent.db")
	transport = _Pages()
	service = _service(tmp_path, _TerminalWriteBomb(cache), transport)
	with cache:
		with pytest.raises(CacheBusyError):
			service.create_and_run(_settings(tmp_path))
	assert transport.closed is True


def test_original_exception_is_not_replaced_by_cleanup_failure(tmp_path):
	class _ExplodingTransport:
		def open(self):
			raise CrawlRuntimeUnavailable("boom")

		def close(self, *, quit_browser=True):
			self.closed = True

	cache = CacheStore(tmp_path / "cache" / "boss_agent.db")
	transport = _ExplodingTransport()
	service = _service(tmp_path, _BudgetBomb(cache), transport)
	with cache:
		with pytest.raises(CrawlRuntimeUnavailable, match="boom"):
			service.create_and_run(_settings(tmp_path))
	# close 仍然被调用：清理失败不能阻断浏览器关闭。
	assert transport.closed is True


def test_clean_cycle_does_not_raise_cleanup_error(tmp_path):
	cache = CacheStore(tmp_path / "cache" / "boss_agent.db")
	transport = _Pages()
	service = _service(tmp_path, cache, transport)
	with cache:
		outcome = service.create_and_run(_settings(tmp_path))
	assert outcome.status == "completed"
	assert transport.closed is True
