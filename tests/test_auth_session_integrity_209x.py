"""auth 会话完整性回归：刷新旧值、限流误判、探针页风控、扫码轮询异常。

覆盖 Issue #2091–#2093、#2095：

- #2091：首页未加载时 stoken 刷新把 jar 里的刷新前旧值当新令牌返回；
- #2092：复用会话探测把限流当成登录失效，触发清空用户 Cookie 的强制重登；
- #2093：身份头探针页命中安全验证 / 访问受限页时被吞成空结果；
- #2095：扫码轮询吞掉 cookie 读取异常，浏览器已关闭时空转到「扫码超时」。
"""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from boss_agent_cli.api.client import AccountRiskError, EnvironmentRiskError
from boss_agent_cli.auth import browser as browser_auth
from boss_agent_cli.auth.manager import AuthManager, TokenRefreshFailed

HOME_URL = "https://www.zhipin.com/"
SECURITY_URL = "https://www.zhipin.com/web/passport/zp/security.html"
RESTRICTED_URL = "https://www.zhipin.com/web/passport/zp/403.html?code=31"


def _playwright_manager(browser: MagicMock) -> MagicMock:
	chromium = MagicMock()
	chromium.launch.return_value = browser
	playwright = MagicMock()
	playwright.chromium = chromium
	manager = MagicMock()
	manager.__enter__ = MagicMock(return_value=playwright)
	manager.__exit__ = MagicMock(return_value=False)
	return manager


def _stalled_page(context: MagicMock) -> MagicMock:
	page = MagicMock()
	page.goto.side_effect = TimeoutError("Timeout 15000ms exceeded")
	page.wait_for_load_state.side_effect = Exception("Timeout 3000ms exceeded")
	context.new_page.return_value = page
	return page


def _make_store(token: dict | None = None) -> MagicMock:
	store = MagicMock()
	store.load.return_value = token
	lock = MagicMock()
	lock.__enter__.return_value = None
	lock.__exit__.return_value = None
	store.refresh_lock.return_value = lock
	return store


# ── #2091 刷新通道不得把刷新前的旧 stoken 当新值返回 ──────────────────


def test_refresh_stoken_rejects_unchanged_jar_value():
	context = MagicMock()
	_stalled_page(context)
	context.cookies.return_value = [{"name": "__zp_stoken__", "value": "old-stoken", "domain": ".zhipin.com"}]
	browser = MagicMock()
	browser.new_context.return_value = context

	with patch.object(browser_auth, "sync_playwright", return_value=_playwright_manager(browser)):
		with pytest.raises(RuntimeError, match="页面未生成 stoken"):
			browser_auth.refresh_stoken({"wt2": "cookie", "__zp_stoken__": "old-stoken"}, "UA")


def test_refresh_stoken_accepts_changed_jar_value():
	context = MagicMock()
	_stalled_page(context)
	context.cookies.return_value = [{"name": "__zp_stoken__", "value": "new-stoken", "domain": ".zhipin.com"}]
	browser = MagicMock()
	browser.new_context.return_value = context

	with patch.object(browser_auth, "sync_playwright", return_value=_playwright_manager(browser)):
		result = browser_auth.refresh_stoken({"wt2": "cookie", "__zp_stoken__": "old-stoken"}, "UA")

	assert result == "new-stoken"


def test_refresh_stoken_accepts_jar_value_when_no_previous_stoken():
	context = MagicMock()
	_stalled_page(context)
	context.cookies.return_value = [{"name": "__zp_stoken__", "value": "jar-stoken", "domain": ".zhipin.com"}]
	browser = MagicMock()
	browser.new_context.return_value = context

	with patch.object(browser_auth, "sync_playwright", return_value=_playwright_manager(browser)):
		result = browser_auth.refresh_stoken({"wt2": "cookie"}, "UA")

	assert result == "jar-stoken"


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("boss_agent_cli.auth.manager.refresh_stoken")
@patch("boss_agent_cli.auth.manager.probe_cdp", return_value=False)
def test_force_refresh_rejects_headless_unchanged_token(mock_probe, mock_refresh, mock_store_cls, tmp_path):
	"""headless 通道返回原 stoken：不写回登录态，报 TOKEN_REFRESH_FAILED（#2091）。"""
	current = {"cookies": {"wt2": "cookie"}, "stoken": "old-token", "user_agent": "ua"}
	store = _make_store(token=dict(current))
	mock_store_cls.return_value = store
	mock_refresh.return_value = "old-token"

	manager = AuthManager(tmp_path)

	with pytest.raises(TokenRefreshFailed, match="仍是原 stoken"):
		manager.force_refresh()

	store.save.assert_not_called()


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("boss_agent_cli.auth.manager.refresh_stoken_via_cdp")
@patch("boss_agent_cli.auth.manager.probe_cdp", return_value=True)
def test_force_refresh_rejects_cdp_unchanged_token(mock_probe, mock_refresh, mock_store_cls, tmp_path):
	"""CDP 通道兜底读回 jar 里被平台拒绝的同一 stoken：同样不写回（#2091）。"""
	current = {"cookies": {"wt2": "cookie"}, "stoken": "old-token", "user_agent": "ua"}
	store = _make_store(token=dict(current))
	mock_store_cls.return_value = store
	mock_refresh.return_value = "old-token"

	manager = AuthManager(tmp_path)

	with pytest.raises(TokenRefreshFailed, match="仍是原 stoken"):
		manager.force_refresh(cdp_url="http://127.0.0.1:9222")

	store.save.assert_not_called()


# ── #2092 限流不能被当成登录失效 ────────────────────────────────────


@pytest.mark.parametrize(
	("probe_result", "expected"),
	[
		({"code": 9, "message": "请求频率受限"}, "unverified"),
		({"code": "9", "message": "请求频率受限"}, "unverified"),
		({"code": 17, "__auth_response_status__": 429}, "unverified"),
		({"code": 5, "message": "未登录"}, "stale"),
		({"code": 17, "message": "未知状态"}, "stale"),
		({"code": 37, "message": "stoken 已过期"}, "stale"),
		({"code": 0, "zpData": {"name": "x"}}, "valid"),
	],
)
def test_probe_classifies_rate_limit_as_unverified_and_keeps_stale_contract(probe_result, expected):
	page = MagicMock()
	page.url = "https://www.zhipin.com/web/geek/jobs"
	page.evaluate.return_value = probe_result

	assert browser_auth._probe_reused_session(page, platform="zhipin") == expected


@pytest.mark.parametrize("code", [36, 37, 38])
def test_probe_still_raises_platform_risk_before_classification(code):
	page = MagicMock()
	page.url = "https://www.zhipin.com/web/geek/jobs"
	page.evaluate.return_value = {"code": code, "message": "检测到异常行为"}

	with pytest.raises((AccountRiskError, EnvironmentRiskError)):
		browser_auth._probe_reused_session(page, platform="zhipin")


# ── #2093 身份头探针页的风控信号不得被吞 ────────────────────────────


def test_identity_probe_raises_when_page_lands_on_security_page():
	page = MagicMock()
	page.url = SECURITY_URL
	context = MagicMock()
	context.new_page.return_value = page

	with patch.object(browser_auth, "PageIdentityCapture"):
		with pytest.raises(AccountRiskError) as caught:
			browser_auth._capture_identity_from_probe_page(context, HOME_URL)

	assert caught.value.details.get("risk_source") == "security_page"


def test_identity_probe_raises_when_navigation_fails_into_restricted_page():
	page = MagicMock()
	page.url = RESTRICTED_URL
	page.goto.side_effect = RuntimeError("navigation aborted")
	context = MagicMock()
	context.new_page.return_value = page

	with patch.object(browser_auth, "PageIdentityCapture"):
		with pytest.raises(EnvironmentRiskError):
			browser_auth._capture_identity_from_probe_page(context, HOME_URL)


def test_identity_probe_detects_risk_after_identity_wait():
	page = MagicMock()
	type(page).url = PropertyMock(side_effect=[HOME_URL, SECURITY_URL])
	context = MagicMock()
	context.new_page.return_value = page
	capture = MagicMock()
	capture.wait_for_identity.return_value = {"token": "t"}

	with patch.object(browser_auth, "PageIdentityCapture", return_value=capture):
		with pytest.raises(AccountRiskError):
			browser_auth._capture_identity_from_probe_page(context, HOME_URL)


def test_identity_probe_still_returns_empty_on_plain_failure():
	page = MagicMock()
	page.url = "about:blank"
	page.goto.side_effect = TimeoutError("timeout")
	context = MagicMock()
	context.new_page.return_value = page

	with patch.object(browser_auth, "PageIdentityCapture"):
		assert browser_auth._capture_identity_from_probe_page(context, HOME_URL) == {}


# ── #2095 扫码轮询让 cookie 读取异常立即传播 ─────────────────────────


@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_login_via_browser_poll_propagates_cookie_read_failure(mock_sleep):
	page = MagicMock()
	page.url = browser_auth.LOGIN_PAGE_URL
	page.goto.return_value = None
	page.wait_for_load_state.return_value = None
	page.evaluate.return_value = "UA"

	context = MagicMock()
	context.new_page.return_value = page
	context.cookies.side_effect = RuntimeError("target closed")

	browser = MagicMock()
	browser.new_context.return_value = context

	with patch.object(browser_auth, "sync_playwright", return_value=_playwright_manager(browser)):
		with pytest.raises(RuntimeError, match="target closed"):
			browser_auth.login_via_browser(timeout=5, platform="zhipin")
