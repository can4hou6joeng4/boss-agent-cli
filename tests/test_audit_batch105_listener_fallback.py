"""批次 105：职位监听器启动失败必须走页面内降级（Issue #2428）。"""

from __future__ import annotations

import json
from typing import Any

from boss_agent_cli.crawler.transport import DrissionCrawlerSession


class _Found:
	def __bool__(self) -> bool:
		return True


class _Listener:
	def __init__(self, *, start_error: Exception | None = None) -> None:
		self._start_error = start_error
		self.start_calls = 0
		self.wait_calls = 0

	def stop(self) -> None:
		pass

	def start(self, *args: Any, **kwargs: Any) -> None:
		self.start_calls += 1
		if self._start_error is not None:
			raise self._start_error

	def wait(self, **kwargs: Any) -> None:
		self.wait_calls += 1
		return None


class _Page:
	def __init__(self, listener: _Listener) -> None:
		self.url = "https://www.zhipin.com/web/geek/jobs?city=101010100&query=python"
		self.html = '<html><body><div class="job-list-box"></div></body></html>'
		self.listen = listener
		self.get_calls: list[str] = []

	def get(self, url: str) -> None:
		self.get_calls.append(url)

	def ele(self, selector: str, timeout: float = 0) -> _Found:
		return _Found()

	def run_js(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
		return {
			"status": 200,
			"text": json.dumps({"code": 0, "zpData": {"jobList": []}}),
			"url": "https://www.zhipin.com/wapi/zpgeek/search/joblist.json",
			"content_type": "application/json",
		}


def _session(tmp_path: Any, page: _Page) -> DrissionCrawlerSession:
	session = DrissionCrawlerSession(
		profile_path=tmp_path / "profile",
		chrome_path=None,
		cdp_port=9333,
		hook_profile="none",
		hook_dir=None,
		timeout=1,
	)
	session._page = page
	return session


def test_listener_start_failure_falls_back_to_in_page_fetch(tmp_path: Any) -> None:
	listener = _Listener(start_error=RuntimeError("listener unavailable"))
	page = _Page(listener)
	session = _session(tmp_path, page)

	payload = session.fetch_page("python", "101010100", 1)

	assert payload["code"] == 0
	# 监听器 start 只尝试一次且不抛；等待路径被完全跳过，直接走页面内同源请求。
	assert listener.start_calls == 1
	assert listener.wait_calls == 0
	assert page.get_calls


def test_listener_start_failure_leaves_session_usable(tmp_path: Any) -> None:
	listener = _Listener(start_error=RuntimeError("listener unavailable"))
	session = _session(tmp_path, _Page(listener))

	session._start_joblist_listener()

	assert session._listener_ready is False


def test_listener_ready_after_successful_start(tmp_path: Any) -> None:
	listener = _Listener()
	session = _session(tmp_path, _Page(listener))

	session._start_joblist_listener()

	assert session._listener_ready is True


def test_wait_for_packet_skips_when_listener_not_ready(tmp_path: Any) -> None:
	listener = _Listener(start_error=RuntimeError("listener unavailable"))
	session = _session(tmp_path, _Page(listener))
	session._start_joblist_listener()

	assert session._wait_for_joblist_packet(1) is None
	assert listener.wait_calls == 0
