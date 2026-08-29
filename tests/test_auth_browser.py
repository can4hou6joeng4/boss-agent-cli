from unittest.mock import MagicMock, patch

import pytest

from boss_agent_cli.auth.browser import (
	HOME_URL,
	LOGIN_PAGE_URL,
	_NAV_TIMEOUT_MS,
	_NETWORKIDLE_GRACE_MS,
	_find_zhilian_recruiter_page,
	_is_cookie_domain,
	_is_zhilian_url,
	_is_zhipin_url,
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


# ── 复用已登录 CDP 会话（对齐 #390 之后补齐 #382 意图） ──────────────


def test_zhipin_url_and_cookie_domain_use_exact_host_validation() -> None:
	assert _is_zhipin_url("https://www.zhipin.com/web/geek/job")
	assert not _is_zhipin_url("https://not-zhipin.com/")  # 子串陷阱
	assert not _is_zhipin_url("https://zhipin.com.evil.example/")
	assert _is_cookie_domain(".zhipin.com", ".zhipin.com")
	assert not _is_cookie_domain("not-zhipin.com", ".zhipin.com")  # 子串匹配会误放行


def _make_logged_and_empty_contexts() -> tuple[MagicMock, MagicMock]:
	logged_ctx = MagicMock()
	logged_ctx.cookies.return_value = [
		{"name": "wt2", "value": "tok", "domain": ".zhipin.com"},
		{"name": "__zp_stoken__", "value": "jar-stoken", "domain": ".zhipin.com"},
	]
	empty_ctx = MagicMock()
	empty_ctx.cookies.return_value = []
	return logged_ctx, empty_ctx


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_login_via_cdp_selects_logged_in_context_even_when_not_first(mock_sleep, mock_probe_cdp):
	logged_ctx, empty_ctx = _make_logged_and_empty_contexts()
	logged_ctx.pages = []
	empty_ctx.pages = []

	mock_browser = MagicMock()
	mock_browser.contexts = [empty_ctx, logged_ctx]
	mock_launcher = MagicMock()
	mock_launcher.start.return_value.chromium.connect_over_cdp.return_value = mock_browser

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher):
		result = login_via_cdp(timeout=1)

	assert result["cookies"]["wt2"] == "tok"
	empty_ctx.new_page.assert_not_called()  # 绝不在未登录 context 中创建页签
	logged_ctx.new_page.assert_called_once()


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_login_via_cdp_reuses_existing_zhipin_page_without_login_nav(mock_sleep, mock_probe_cdp):
	logged_ctx, _ = _make_logged_and_empty_contexts()
	existing_page = MagicMock()
	existing_page.url = "https://www.zhipin.com/web/geek/job"
	logged_ctx.pages = [existing_page]

	mock_browser = MagicMock()
	mock_browser.contexts = [logged_ctx]
	mock_launcher = MagicMock()
	mock_launcher.start.return_value.chromium.connect_over_cdp.return_value = mock_browser

	with (
		patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher),
		patch("boss_agent_cli.auth.browser._extract_stoken") as mock_extract,
	):
		result = login_via_cdp(timeout=1)

	existing_page.goto.assert_not_called()  # 不导航登录页、不轮询等待
	existing_page.close.assert_not_called()  # 只清理本次调用创建的页签
	assert result["stoken"] == "jar-stoken"  # 复用页签优先信 cookie jar
	mock_extract.assert_not_called()


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_login_via_cdp_logged_in_without_suitable_page_warms_new_page(mock_sleep, mock_probe_cdp):
	logged_ctx, _ = _make_logged_and_empty_contexts()
	logged_ctx.pages = []
	mock_launcher, mock_playwright, mock_page = _mock_cdp_playwright(logged_ctx)
	mock_page.evaluate.return_value = "UA"

	with (
		patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher),
		patch("boss_agent_cli.auth.browser._extract_stoken", return_value="page-stoken") as mock_extract,
	):
		result = login_via_cdp(timeout=1)

	# 首页确认加载后按 #390 语义走页面提取
	assert result["stoken"] == "page-stoken"
	mock_extract.assert_called_once()
	assert result["user_agent"] == "UA"
	# 新建页签必须经 _warm_home_for_runtime 回首页（domcontentloaded）
	mock_page.goto.assert_called_once()
	assert mock_page.goto.call_args.kwargs["wait_until"] == "domcontentloaded"


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_login_via_cdp_reuse_stalled_page_is_not_evaluated(mock_sleep, mock_probe_cdp):
	"""复用登录态但新页签首页卡住：不得对页面 evaluate，回退 cookie jar。"""
	logged_ctx, _ = _make_logged_and_empty_contexts()
	logged_ctx.pages = []
	mock_launcher, mock_playwright, mock_page = _mock_cdp_playwright(logged_ctx)
	mock_page.goto.side_effect = TimeoutError("Timeout 15000ms exceeded")
	mock_page.wait_for_load_state.side_effect = Exception("Timeout 3000ms exceeded")

	with (
		patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher),
		patch("boss_agent_cli.auth.browser._extract_stoken") as mock_extract,
	):
		result = login_via_cdp(timeout=1)

	assert result["stoken"] == "jar-stoken"
	mock_extract.assert_not_called()
