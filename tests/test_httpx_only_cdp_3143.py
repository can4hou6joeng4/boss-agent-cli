"""httpx-only 的通道白名单必须优先于 --cdp-url（Issue #3143）。"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from boss_agent_cli.api.client import BossClient


def _client(tmp_path: Path, source: str, cdp_url: str | None) -> BossClient:
	auth = SimpleNamespace(
		data_dir=tmp_path,
		_platform="zhipin",
		get_token=MagicMock(return_value={"cookies": {}, "user_agent": "UA"}),
		force_refresh=MagicMock(),
	)
	return BossClient(auth, cdp_url=cdp_url, browser_source=source)


def test_httpx_only_with_cdp_url_never_reports_browser_only(tmp_path: Path) -> None:
	client = _client(tmp_path, "httpx-only", "http://127.0.0.1:9222")

	assert client.browser_enabled() is False
	assert client.is_browser_only() is False


def test_httpx_only_with_connected_cdp_session_stays_httpx(tmp_path: Path) -> None:
	client = _client(tmp_path, "httpx-only", None)
	client._browser_session = MagicMock(_is_cdp=True)

	assert client.is_browser_only() is False


def test_friend_list_stays_on_httpx_with_cdp_url(tmp_path: Path) -> None:
	client = _client(tmp_path, "httpx-only", "http://127.0.0.1:9222")
	client._request = MagicMock(return_value={"code": 0, "zpData": {}})
	client._browser_request = MagicMock(side_effect=AssertionError("不该走浏览器"))

	assert client.friend_list() == {"code": 0, "zpData": {}}
	client._request.assert_called_once()
	client._browser_request.assert_not_called()


def test_job_card_stays_on_httpx_with_cdp_url(tmp_path: Path) -> None:
	client = _client(tmp_path, "httpx-only", "http://127.0.0.1:9222")
	client._request = MagicMock(return_value={"code": 0, "zpData": {"jobInfo": {}}})
	client._browser_request = MagicMock(side_effect=AssertionError("不该走浏览器"))

	assert client.job_card("demo-security-id") == {"code": 0, "zpData": {"jobInfo": {}}}
	client._request.assert_called_once()
	client._browser_request.assert_not_called()


@pytest.mark.parametrize(
	("source", "expected"),
	[
		("auto", True),
		("stored-cookie", True),
		("existing-browser", True),
		("httpx-only", False),
	],
)
def test_cdp_url_keeps_existing_source_semantics(tmp_path: Path, source: str, expected: bool) -> None:
	client = _client(tmp_path, source, "http://127.0.0.1:9222")

	assert client.is_browser_only() is expected
