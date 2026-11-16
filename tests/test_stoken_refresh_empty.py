"""刷新通道返回空 stoken 的失败语义（Issue #1305）。

headless `refresh_stoken` 可以返回空串（首页迟迟不产出 stoken 时的兜底）；
原先把空串当成功存进登录态，旧 stoken 被覆盖，之后所有请求拿空凭证打平台、
重试到预算耗尽，而 `TOKEN_REFRESH_FAILED` 永远不会产生。现在与 CDP 路径
同口径报刷新失败，并保留旧 token。
"""

from unittest.mock import MagicMock, patch

import pytest

from boss_agent_cli.auth.manager import AuthManager, TokenRefreshFailed


def _make_store(token: dict | None = None):
	store = MagicMock()
	store.load.return_value = token
	lock = MagicMock()
	lock.__enter__.return_value = None
	lock.__exit__.return_value = None
	store.refresh_lock.return_value = lock
	return store


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("boss_agent_cli.auth.manager.refresh_stoken")
@patch("boss_agent_cli.auth.manager.probe_cdp", return_value=False)
def test_headless_empty_stoken_is_refresh_failure(mock_probe, mock_refresh, mock_store_cls, tmp_path):
	current = {"cookies": {"wt2": "cookie"}, "stoken": "old-token", "user_agent": "ua"}
	store = _make_store(token=dict(current))
	mock_store_cls.return_value = store
	mock_refresh.return_value = ""

	manager = AuthManager(tmp_path)

	with pytest.raises(TokenRefreshFailed, match="未返回 stoken"):
		manager.force_refresh()

	store.save.assert_not_called()
	assert manager._token is None


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("boss_agent_cli.auth.manager.refresh_stoken_via_cdp")
@patch("boss_agent_cli.auth.manager.probe_cdp", return_value=True)
def test_cdp_whitespace_stoken_is_refresh_failure(mock_probe, mock_refresh, mock_store_cls, tmp_path):
	current = {"cookies": {"wt2": "cookie"}, "stoken": "old-token", "user_agent": "ua"}
	store = _make_store(token=dict(current))
	mock_store_cls.return_value = store
	mock_refresh.return_value = "   "

	manager = AuthManager(tmp_path)

	with pytest.raises(TokenRefreshFailed, match="未返回 stoken"):
		manager.force_refresh(cdp_url="http://127.0.0.1:9222")

	store.save.assert_not_called()


def test_refresh_stoken_raises_when_page_yields_no_stoken():
	from boss_agent_cli.auth import browser

	mock_page = MagicMock()
	mock_context = MagicMock()
	mock_context.new_page.return_value = mock_page
	mock_context.cookies.return_value = []
	mock_browser = MagicMock()
	mock_browser.new_context.return_value = mock_context
	mock_chromium = MagicMock()
	mock_chromium.launch.return_value = mock_browser
	mock_playwright = MagicMock()
	mock_playwright.chromium = mock_chromium
	mock_launcher = MagicMock()
	mock_launcher.__enter__.return_value = mock_playwright
	mock_launcher.__exit__.return_value = None

	with (
		patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher),
		patch("boss_agent_cli.auth.browser._warm_home_for_runtime", return_value=False),
		patch("boss_agent_cli.auth.browser.resolve_proxy", return_value=None),
	):
		with pytest.raises(RuntimeError, match="页面未生成 stoken"):
			browser.refresh_stoken({"wt2": "cookie"}, "ua")
