"""缺少职位列表容器的风险检查必须识别 DrissionPage 的 NoneElement（Issue #1276）。

`ChromiumPage.ele()` 未命中时返回 `NoneElement` 实例（`__bool__` 为 False、
`__eq__(None)` 为 True），不是 `None`；用 `is None` 判断会让这项检查永远不触发。
"""

import pytest

from boss_agent_cli.crawler.transport import CrawlRiskError, DrissionCrawlerSession


class _NoneElement:
	"""DrissionPage NoneElement 的关键语义。"""

	def __eq__(self, other):
		return other is None

	def __bool__(self):
		return False


class _Found:
	def __bool__(self):
		return True


class _Page:
	def __init__(self, element):
		self.url = "https://www.zhipin.com/web/geek/jobs"
		self.html = "<html></html>"
		self._element = element

	def ele(self, selector, timeout=0):
		return self._element


def _session(tmp_path):
	session = DrissionCrawlerSession(
		profile_path=tmp_path / "profile",
		chrome_path=None,
		cdp_port=9333,
		hook_profile="none",
		hook_dir=None,
	)
	return session


def test_none_element_without_job_list_is_a_risk_page(tmp_path):
	session = _session(tmp_path)
	session._page = _Page(_NoneElement())

	with pytest.raises(CrawlRiskError) as excinfo:
		session._raise_if_security_page(require_job_list=True)

	assert "职位列表容器" in str(excinfo.value)


def test_plain_none_without_job_list_is_a_risk_page(tmp_path):
	session = _session(tmp_path)
	session._page = _Page(None)

	with pytest.raises(CrawlRiskError):
		session._raise_if_security_page(require_job_list=True)


def test_found_job_list_does_not_raise(tmp_path):
	session = _session(tmp_path)
	session._page = _Page(_Found())

	session._raise_if_security_page(require_job_list=True)


def test_missing_job_list_is_ignored_when_not_required(tmp_path):
	session = _session(tmp_path)
	session._page = _Page(_NoneElement())

	session._raise_if_security_page()
