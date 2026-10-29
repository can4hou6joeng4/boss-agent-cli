"""httpx-only：不打开任何浏览器，只读走 httpx，需要页面的能力直接拒绝。"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from boss_agent_cli.api.browser_client import BrowserSession
from boss_agent_cli.api.browser_source import POLICIES, BrowserSourceUnavailable
from boss_agent_cli.api.client import BossClient

_TOKEN = {"cookies": {"wt2": "x"}, "user_agent": "UA", "stoken": "s"}


def _client(tmp_path: Path) -> BossClient:
	auth = SimpleNamespace(
		data_dir=tmp_path,
		_browser_source="httpx-only",
		_platform="zhipin",
		get_token=MagicMock(return_value=dict(_TOKEN)),
		force_refresh=MagicMock(),
	)
	client = BossClient(auth, browser_source="httpx-only")
	return client


def test_policy_has_no_browser_channel(tmp_path) -> None:
	policy = POLICIES["httpx-only"]

	assert policy.channels == ()
	assert policy.allow_browser_launch is False
	assert policy.may_create_context is False
	assert policy.auto_probe_cdp is False
	assert policy.failure_code == "BROWSER_DISABLED"
	assert policy.operator_actions


def test_search_uses_httpx_and_never_builds_a_browser_session(tmp_path) -> None:
	client = _client(tmp_path)
	client._request = MagicMock(return_value={"code": 0, "zpData": {"jobList": []}})
	client._get_browser = MagicMock(side_effect=AssertionError("不该构造浏览器会话"))

	client.search_jobs("Python", page=1)

	client._request.assert_called_once()
	client._get_browser.assert_not_called()


def test_recommend_uses_httpx_as_well(tmp_path) -> None:
	client = _client(tmp_path)
	client._request = MagicMock(return_value={"code": 0, "zpData": {}})
	client._get_browser = MagicMock(side_effect=AssertionError("不该构造浏览器会话"))

	client.recommend_jobs(page=1)

	client._request.assert_called_once()
	client._get_browser.assert_not_called()


def test_page_only_capability_is_refused_with_browser_disabled(tmp_path) -> None:
	client = _client(tmp_path)

	with pytest.raises(BrowserSourceUnavailable) as caught:
		client.greet("security-id", "job-id")

	assert caught.value.code == "BROWSER_DISABLED"


def test_read_request_stays_on_httpx(tmp_path) -> None:
	client = _client(tmp_path)
	client._request = MagicMock(return_value={"code": 0, "zpData": {}})

	client._read_request("GET", "https://www.zhipin.com/wapi/x.json")

	client._request.assert_called_once()


def test_browser_session_reports_the_disabled_source(tmp_path) -> None:
	session = BrowserSession(cookies={}, user_agent="UA", browser_source="httpx-only")

	with pytest.raises(BrowserSourceUnavailable) as caught:
		session._ensure_started()

	assert caught.value.code == "BROWSER_DISABLED"
	assert session._pw is None
