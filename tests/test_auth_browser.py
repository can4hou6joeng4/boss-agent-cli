from unittest.mock import MagicMock, patch

import pytest

from boss_agent_cli.auth.browser import (
	HOME_URL,
	LOGIN_PAGE_URL,
	_NAV_TIMEOUT_MS,
	_NETWORKIDLE_GRACE_MS,
	_find_zhilian_recruiter_page,
	_is_zhilian_url,
	_safe_user_agent,
	_warm_home_for_runtime,
	login_via_cdp,
	login_via_browser,
	refresh_stoken,
	refresh_stoken_via_cdp,
)


def _mock_playwright_context(mock_browser: MagicMock) -> MagicMock:
	mock_chromium = MagicMock()
	mock_chromium.launch.return_value = mock_browser
	mock_playwright = MagicMock()
	mock_playwright.chromium = mock_chromium
	mock_context_manager = MagicMock()
	mock_context_manager.__enter__ = MagicMock(return_value=mock_playwright)
	mock_context_manager.__exit__ = MagicMock(return_value=False)
	return mock_context_manager


def _mock_cdp_playwright(mock_context: MagicMock) -> tuple[MagicMock, MagicMock, MagicMock]:
	mock_page = MagicMock()
	mock_context.new_page.return_value = mock_page

	mock_browser = MagicMock()
	mock_browser.contexts = [mock_context]

	mock_playwright = MagicMock()
	mock_playwright.chromium.connect_over_cdp.return_value = mock_browser

	mock_launcher = MagicMock()
	mock_launcher.start.return_value = mock_playwright
	return mock_launcher, mock_playwright, mock_page


class _UrlPage:
	def __init__(self, url: str) -> None:
		self.url = url


def test_zhilian_url_host_validation_uses_exact_hostname() -> None:
	assert _is_zhilian_url("https://zhaopin.com/")
	assert _is_zhilian_url("https://RD6.ZHAOPIN.COM./app/im")
	assert not _is_zhilian_url("https://rd6.zhaopin.com.evil.example/app/im")
	assert not _is_zhilian_url("https://evil.example/app/im?next=https://rd6.zhaopin.com/app/im")
	assert not _is_zhilian_url("not-a-url-with-zhaopin.com")


def test_find_zhilian_recruiter_page_rejects_embedded_hostname() -> None:
	fake_chat = _UrlPage("https://rd6.zhaopin.com.evil.example/app/im")
	fake_recommend = _UrlPage("https://evil.example/app/recommend?next=https://rd6.zhaopin.com/app/im")
	valid_page = _UrlPage("https://rd6.zhaopin.com/profile")

	selected = _find_zhilian_recruiter_page([fake_chat, fake_recommend, valid_page])

	assert selected is valid_page


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_login_via_cdp_stops_playwright_on_timeout(mock_sleep, mock_probe_cdp):
	mock_context = MagicMock()
	mock_context.cookies.return_value = []
	mock_launcher, mock_playwright, mock_page = _mock_cdp_playwright(mock_context)

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher):
		with pytest.raises(TimeoutError):
			login_via_cdp(timeout=1)

	mock_page.close.assert_called_once()
	mock_playwright.stop.assert_called_once()


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_login_via_cdp_tolerates_user_agent_extraction_failure(mock_sleep, mock_probe_cdp):
	mock_context = MagicMock()
	mock_context.cookies.side_effect = [
		[{"name": "wt2", "value": "token", "domain": ".zhipin.com"}],
		[{"name": "wt2", "value": "token", "domain": ".zhipin.com"}],
	]
	mock_launcher, mock_playwright, mock_page = _mock_cdp_playwright(mock_context)
	mock_page.evaluate.side_effect = RuntimeError("user agent unavailable")

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher):
		result = login_via_cdp(timeout=1)

	assert result["cookies"]["wt2"] == "token"
	assert result["user_agent"] == ""
	mock_page.close.assert_called_once()
	mock_playwright.stop.assert_called_once()


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_zhilian_login_via_cdp_reuses_recruiter_page(mock_sleep, mock_probe_cdp):
	mock_page = MagicMock()
	mock_page.url = "https://rd6.zhaopin.com/app/im?sessionId=abc"
	mock_page.evaluate.return_value = "UA"
	mock_context = MagicMock()
	mock_context.pages = [mock_page]
	mock_context.cookies.return_value = [
		{"name": "at", "value": "access", "domain": ".zhaopin.com"},
		{"name": "rt", "value": "refresh", "domain": ".zhaopin.com"},
		{"name": "x-zp-client-id", "value": "cid", "domain": ".zhaopin.com"},
	]
	mock_launcher, mock_playwright, _mock_new_page = _mock_cdp_playwright(mock_context)

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher):
		result = login_via_cdp(timeout=1, platform="zhilian")

	assert result["cookies"]["at"] == "access"
	assert result["x_zp_client_id"] == "cid"
	mock_context.new_page.assert_not_called()
	mock_page.goto.assert_not_called()
	mock_page.close.assert_not_called()
	mock_playwright.stop.assert_called_once()


@patch("boss_agent_cli.auth.browser._extract_stoken", return_value="fresh-stoken")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_login_via_browser_tolerates_networkidle_timeout(mock_sleep, mock_extract_stoken):
	mock_page = MagicMock()
	mock_page.wait_for_load_state.side_effect = Exception("Timeout 30000ms exceeded")
	mock_page.evaluate.return_value = "UA"

	mock_context = MagicMock()
	mock_context.new_page.return_value = mock_page
	mock_context.cookies.side_effect = [
		[{"name": "wt2", "value": "token", "domain": ".zhipin.com"}],
		[{"name": "wt2", "value": "token", "domain": ".zhipin.com"}],
	]

	mock_browser = MagicMock()
	mock_browser.new_context.return_value = mock_context

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=_mock_playwright_context(mock_browser)):
		result = login_via_browser(timeout=2, platform="zhipin")

	assert result["stoken"] == "fresh-stoken"
	assert result["user_agent"] == "UA"
	mock_browser.new_context.assert_called_once()
	mock_page.goto.assert_any_call(LOGIN_PAGE_URL, wait_until="domcontentloaded")
	mock_page.goto.assert_any_call(HOME_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
	mock_page.wait_for_load_state.assert_called_once_with("networkidle", timeout=_NETWORKIDLE_GRACE_MS)
	mock_extract_stoken.assert_called_once_with(mock_page)
	mock_browser.close.assert_called_once()


@patch("boss_agent_cli.auth.browser._extract_stoken", return_value="fresh-stoken")
def test_refresh_stoken_tolerates_networkidle_timeout(mock_extract_stoken):
	mock_page = MagicMock()
	mock_page.wait_for_load_state.side_effect = Exception("Timeout 30000ms exceeded")

	mock_context = MagicMock()
	mock_context.new_page.return_value = mock_page

	mock_browser = MagicMock()
	mock_browser.new_context.return_value = mock_context

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=_mock_playwright_context(mock_browser)):
		result = refresh_stoken({"wt2": "cookie"}, "UA")

	assert result == "fresh-stoken"
	mock_browser.new_context.assert_called_once_with(user_agent="UA")
	mock_context.add_cookies.assert_called_once()
	mock_page.goto.assert_called_once_with(HOME_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
	mock_page.wait_for_load_state.assert_called_once_with("networkidle", timeout=_NETWORKIDLE_GRACE_MS)
	mock_extract_stoken.assert_called_once_with(mock_page)
	mock_browser.close.assert_called_once()


@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_login_via_browser_falls_back_to_cookie_jar_when_home_nav_stalls(mock_sleep):
	# 登录页 goto 成功；首页两次 goto 均卡住（超时）→ home_loaded=False
	mock_page = MagicMock()
	mock_page.goto.side_effect = [
		None,  # 登录页
		TimeoutError("Timeout 15000ms exceeded"),  # 首页 attempt1
		None,  # about:blank 重置
		TimeoutError("Timeout 15000ms exceeded"),  # 首页 attempt2
		None,  # 最后一次 about:blank 重置
	]
	mock_page.wait_for_load_state.side_effect = Exception("Timeout 3000ms exceeded")
	mock_page.evaluate.return_value = "UA"

	mock_context = MagicMock()
	mock_context.new_page.return_value = mock_page
	mock_context.cookies.side_effect = [
		[{"name": "wt2", "value": "token", "domain": ".zhipin.com"}],
		[
			{"name": "wt2", "value": "token", "domain": ".zhipin.com"},
			{"name": "__zp_stoken__", "value": "jar-stoken", "domain": ".zhipin.com"},
		],
	]

	mock_browser = MagicMock()
	mock_browser.new_context.return_value = mock_context

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=_mock_playwright_context(mock_browser)):
		with patch("boss_agent_cli.auth.browser._extract_stoken") as mock_extract:
			result = login_via_browser(timeout=2, platform="zhipin")

	assert result["stoken"] == "jar-stoken"
	assert result["user_agent"] == "UA"
	# 首页未加载时不得对页面 evaluate 提取 stoken（避免 patchright 永久挂起）
	mock_extract.assert_not_called()
	mock_browser.close.assert_called_once()


def test_refresh_stoken_returns_empty_when_home_nav_stalls():
	mock_page = MagicMock()
	mock_page.goto.side_effect = TimeoutError("Timeout 15000ms exceeded")
	mock_page.wait_for_load_state.side_effect = Exception("Timeout 3000ms exceeded")

	mock_context = MagicMock()
	mock_context.new_page.return_value = mock_page

	mock_browser = MagicMock()
	mock_browser.new_context.return_value = mock_context

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=_mock_playwright_context(mock_browser)):
		with patch("boss_agent_cli.auth.browser._extract_stoken") as mock_extract:
			result = refresh_stoken({"wt2": "cookie"}, "UA")

	assert result == ""
	mock_extract.assert_not_called()
	mock_browser.close.assert_called_once()


def test_refresh_stoken_falls_back_to_cookie_jar_when_home_nav_stalls():
	mock_page = MagicMock()
	mock_page.goto.side_effect = TimeoutError("Timeout 15000ms exceeded")
	mock_page.wait_for_load_state.side_effect = Exception("Timeout 3000ms exceeded")

	mock_context = MagicMock()
	mock_context.new_page.return_value = mock_page
	mock_context.cookies.return_value = [{"name": "__zp_stoken__", "value": "jar-stoken", "domain": ".zhipin.com"}]

	mock_browser = MagicMock()
	mock_browser.new_context.return_value = mock_context

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=_mock_playwright_context(mock_browser)):
		with patch("boss_agent_cli.auth.browser._extract_stoken") as mock_extract:
			result = refresh_stoken({"wt2": "cookie"}, "UA")

	assert result == "jar-stoken"
	mock_extract.assert_not_called()
	mock_browser.close.assert_called_once()


def test_warm_home_for_runtime_reports_false_when_goto_stalls():
	mock_page = MagicMock()
	mock_page.goto.side_effect = TimeoutError("Timeout 15000ms exceeded")
	mock_page.wait_for_load_state.side_effect = Exception("Timeout 3000ms exceeded")

	assert _warm_home_for_runtime(mock_page, HOME_URL, stage="test") is False


def test_warm_home_for_runtime_reports_true_when_goto_succeeds():
	mock_page = MagicMock()
	mock_page.goto.return_value = None
	mock_page.wait_for_load_state.side_effect = Exception("Timeout 3000ms exceeded")

	assert _warm_home_for_runtime(mock_page, HOME_URL, stage="test") is True


def test_warm_home_for_runtime_retries_and_recovers_when_goto_stalls_then_succeeds():
	mock_page = MagicMock()
	# 首次 goto 卡住超时 → about:blank 重置 → 第二次 goto 成功
	mock_page.goto.side_effect = [TimeoutError("Timeout 15000ms exceeded"), None, None]
	mock_page.wait_for_load_state.side_effect = Exception("Timeout 3000ms exceeded")

	assert _warm_home_for_runtime(mock_page, HOME_URL, stage="test") is True
	# home 尝试 2 次 + 1 次 about:blank 重置
	assert mock_page.goto.call_count == 3


def test_warm_home_for_runtime_returns_false_after_all_retries_exhausted():
	mock_page = MagicMock()
	# 两次 goto 均卡住；about:blank 重置成功但无法恢复首页
	mock_page.goto.side_effect = [TimeoutError("Timeout 15000ms exceeded"), None, TimeoutError("Timeout 15000ms exceeded"), None]
	mock_page.wait_for_load_state.side_effect = Exception("Timeout 3000ms exceeded")

	assert _warm_home_for_runtime(mock_page, HOME_URL, stage="test") is False


def test_safe_user_agent_swallows_evaluate_error():
	mock_page = MagicMock()
	mock_page.evaluate.side_effect = RuntimeError("user agent unavailable")

	assert _safe_user_agent(mock_page) == ""


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_login_via_cdp_extracts_stoken_when_home_loads(mock_sleep, mock_probe_cdp):
	mock_context = MagicMock()
	mock_launcher, mock_playwright, mock_page = _mock_cdp_playwright(mock_context)
	mock_page.evaluate.return_value = "UA"
	mock_page.goto.return_value = None
	mock_context.cookies.return_value = [{"name": "wt2", "value": "token", "domain": ".zhipin.com"}]

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher):
		with patch("boss_agent_cli.auth.browser._extract_stoken", return_value="cdp-stoken"):
			result = login_via_cdp(timeout=1)

	assert result["stoken"] == "cdp-stoken"
	assert result["user_agent"] == "UA"


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_login_via_cdp_falls_back_to_cookie_jar_stoken_when_home_stalls(mock_sleep, mock_probe_cdp):
	mock_context = MagicMock()
	mock_launcher, mock_playwright, mock_page = _mock_cdp_playwright(mock_context)
	mock_page.evaluate.return_value = "UA"
	mock_page.goto.side_effect = TimeoutError("Timeout 15000ms exceeded")
	mock_page.wait_for_load_state.side_effect = Exception("Timeout 3000ms exceeded")
	mock_context.cookies.return_value = [
		{"name": "wt2", "value": "token", "domain": ".zhipin.com"},
		{"name": "__zp_stoken__", "value": "jar-stoken", "domain": ".zhipin.com"},
	]

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher):
		with patch("boss_agent_cli.auth.browser._extract_stoken") as mock_extract:
			result = login_via_cdp(timeout=1)

	assert result["stoken"] == "jar-stoken"
	mock_extract.assert_not_called()


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_refresh_stoken_via_cdp_falls_back_to_cookie_jar(mock_sleep, mock_probe_cdp):
	mock_context = MagicMock()
	mock_launcher, mock_playwright, mock_page = _mock_cdp_playwright(mock_context)
	mock_page.goto.side_effect = TimeoutError("Timeout 15000ms exceeded")
	mock_page.wait_for_load_state.side_effect = Exception("Timeout 3000ms exceeded")
	mock_context.cookies.return_value = [{"name": "__zp_stoken__", "value": "jar-stoken", "domain": ".zhipin.com"}]

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher):
		result = refresh_stoken_via_cdp()

	assert result == "jar-stoken"
	mock_playwright.stop.assert_called_once()
