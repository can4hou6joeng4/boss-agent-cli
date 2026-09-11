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
def test_login_via_cdp_poll_propagates_cdp_death(mock_sleep, mock_probe_cdp):
	"""扫码轮询中途 CDP target 死掉：必须立即抛真实连接错误，而不是吞成 [] 空转到超时。

	回归 code-review Finding：轮询循环若用吞异常的 _matching_cookies（except: return []），
	会在浏览器已关闭后继续空转 timeout 秒，最终误报「扫码超时」——用户对着不存在的浏览器
	白等。轮询应与最终读取同样让 ctx.cookies() 异常传播。
	"""
	mock_context = MagicMock()
	mock_context.pages = []
	# 登录检测阶段未命中，进入轮询后 cookies() 抛错（CDP target closed）
	mock_context.cookies.side_effect = RuntimeError("CDP target closed")
	mock_launcher, _, _ = _mock_cdp_playwright(mock_context)

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher):
		with pytest.raises(RuntimeError, match="CDP target closed"):
			login_via_cdp(timeout=120, platform="zhipin")


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


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_zhilian_login_via_cdp_does_not_navigate_reused_page_when_logged_out(mock_sleep, mock_probe_cdp):
	"""智联未登录但命中用户已打开的 recruiter 页签：不得 goto 走该页（review #406 第 2 条）。

	用户在 /app/recommend 筛选候选人，未登录时不应被导航到登录页 /app/im 丢失页面状态。
	"""
	existing_page = MagicMock()
	existing_page.url = "https://rd6.zhaopin.com/app/recommend"
	mock_context = MagicMock()
	mock_context.pages = [existing_page]
	# 无 at cookie → 未登录；扫码第一轮即出现 at
	call = {"n": 0}

	def _cookies():
		call["n"] += 1
		if call["n"] >= 2:
			return [{"name": "at", "value": "access", "domain": ".zhaopin.com"}]
		return []

	mock_context.cookies.side_effect = _cookies
	mock_launcher, _, _ = _mock_cdp_playwright(mock_context)

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher):
		login_via_cdp(timeout=5, platform="zhilian")

	existing_page.goto.assert_not_called()  # 用户已打开页签不被导航
	mock_context.new_page.assert_not_called()  # 命中既有页签，不新建


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_zhilian_reuse_stalled_page_skips_client_id_evaluate(mock_sleep, mock_probe_cdp):
	"""智联已登录 + 复用卡住页签 + cookie 缺 x-zp-client-id：不得对页面 evaluate（同 #390 挂起）。

	回归 code-review Finding：_extract_zhilian_client_id 内部是 page.evaluate 读 localStorage，
	复用页签卡住时无门禁会永久挂起；页面不可用时应容忍 x_zp_client_id 为空。
	"""
	existing_page = MagicMock()
	existing_page.url = "https://rd6.zhaopin.com/app/recommend"
	existing_page.wait_for_load_state.side_effect = Exception("Timeout 15000ms exceeded")
	mock_context = MagicMock()
	mock_context.pages = [existing_page]
	mock_context.cookies.return_value = [{"name": "at", "value": "access", "domain": ".zhaopin.com"}]
	mock_launcher, _, _ = _mock_cdp_playwright(mock_context)

	with (
		patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher),
		patch("boss_agent_cli.auth.browser._extract_zhilian_client_id") as mock_extract_cid,
	):
		result = login_via_cdp(timeout=1, platform="zhilian")

	mock_extract_cid.assert_not_called()
	existing_page.evaluate.assert_not_called()
	assert "x_zp_client_id" not in result  # 卡住时容忍缺失，不挂起


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_zhilian_empty_client_id_cookie_falls_back_to_localstorage(mock_sleep, mock_probe_cdp):
	"""智联 cookie 里 x-zp-client-id 为**空串**时，必须落到 localStorage 兜底（truthiness，

	非 key-presence）——否则 `if x_zp_client_id:` 为 False 导致结果缺该字段 → TokenRefreshFailed。
	"""
	existing_page = MagicMock()
	existing_page.url = "https://rd6.zhaopin.com/app/im"
	mock_context = MagicMock()
	mock_context.pages = [existing_page]
	mock_context.cookies.return_value = [
		{"name": "at", "value": "access", "domain": ".zhaopin.com"},
		{"name": "x-zp-client-id", "value": "", "domain": ".zhaopin.com"},  # 存在但空串
	]
	mock_launcher, _, _ = _mock_cdp_playwright(mock_context)

	with (
		patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher),
		patch("boss_agent_cli.auth.browser._extract_zhilian_client_id", return_value="cid-from-ls") as mock_extract_cid,
	):
		result = login_via_cdp(timeout=1, platform="zhilian")

	mock_extract_cid.assert_called_once()  # 空串 cookie 必须落 localStorage 兜底
	assert result["x_zp_client_id"] == "cid-from-ls"


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_login_via_cdp_raises_when_final_cookie_read_fails(mock_sleep, mock_probe_cdp):
	"""最终凭证读取阶段 ctx.cookies() 抛错必须让 login_via_cdp 抛异常，不得返回 cookies={}。

	回归 review #406 二审①：最终读取若用吞异常的 _matching_cookies（except: return []），
	登录检测成功后 ctx.cookies() 抛错（如 CDP target closed）会返回 cookies={} 的「成功」，
	而 AuthManager.login 不校验主 cookie，空凭证直接落盘。master 是裸 ctx.cookies() 异常传播。
	"""
	mock_context = MagicMock()
	mock_context.pages = []  # 无既有页签 → 新建
	# 第一次（发现/登录检测阶段）命中 wt2，之后（最终读取阶段）抛 RuntimeError
	call = {"n": 0}

	def _cookies():
		call["n"] += 1
		if call["n"] == 1:
			return [{"name": "wt2", "value": "token", "domain": ".zhipin.com"}]
		raise RuntimeError("CDP target closed")

	mock_context.cookies.side_effect = _cookies
	mock_launcher, _, new_page = _mock_cdp_playwright(mock_context)
	new_page.evaluate.return_value = "UA"

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher):
		with pytest.raises(RuntimeError):
			login_via_cdp(timeout=1, platform="zhipin")


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_zhilian_fresh_login_on_reused_page_extracts_client_id(mock_sleep, mock_probe_cdp):
	"""智联**扫码后**登录 + 复用既有页签（扫码前未登录）：cookie 缺 client_id 时必须仍走
	localStorage 兜底——就绪门禁不得用扫码前的 already_logged_in 旧值（code-review Finding）。

	若误用旧值，page_ready 为 False → 跳过提取 → 结果缺 x_zp_client_id → TokenRefreshFailed。
	"""
	existing_page = MagicMock()
	existing_page.url = "https://rd6.zhaopin.com/app/recommend"
	mock_context = MagicMock()
	mock_context.pages = [existing_page]
	calls = {"n": 0}

	def _cookies():
		calls["n"] += 1
		# 扫码前无 at；扫码后出现 at；始终无 x-zp-client-id cookie
		return [{"name": "at", "value": "acc", "domain": ".zhaopin.com"}] if calls["n"] >= 2 else []

	mock_context.cookies.side_effect = _cookies
	mock_launcher, _, _ = _mock_cdp_playwright(mock_context)

	with (
		patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher),
		patch("boss_agent_cli.auth.browser._extract_zhilian_client_id", return_value="cid-from-ls") as mock_extract_cid,
	):
		result = login_via_cdp(timeout=5, platform="zhilian")

	existing_page.goto.assert_not_called()  # 复用页签不导航（§2）
	mock_extract_cid.assert_called_once()  # 扫码登录后就绪，client_id 走 localStorage 兜底
	assert result["x_zp_client_id"] == "cid-from-ls"


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_zhilian_logged_in_without_recruiter_page_warms_new_page(mock_sleep, mock_probe_cdp):
	"""智联已登录但无 recruiter 页签：新建页签必须回 home（review #406 第 3 条），

	否则 _extract_zhilian_client_id 在 about:blank 读 localStorage 必空 → TokenRefreshFailed。
	"""
	mock_context = MagicMock()
	mock_context.pages = []  # 无既有页签
	mock_context.cookies.return_value = [
		{"name": "at", "value": "access", "domain": ".zhaopin.com"},
		{"name": "x-zp-client-id", "value": "cid", "domain": ".zhaopin.com"},
	]
	mock_launcher, _, new_page = _mock_cdp_playwright(mock_context)
	new_page.evaluate.return_value = "UA"

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher):
		result = login_via_cdp(timeout=1, platform="zhilian")

	mock_context.new_page.assert_called_once()
	# 新建页签必须经 _warm_home_for_runtime 回 zhilian home（domcontentloaded）
	assert new_page.goto.called
	assert new_page.goto.call_args.kwargs["wait_until"] == "domcontentloaded"
	assert result["x_zp_client_id"] == "cid"


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_zhilian_new_page_stalled_home_skips_client_id_evaluate(mock_sleep, mock_probe_cdp):
	"""智联新建页签 + 首页卡住 + cookie 缺 x-zp-client-id：不得对卡住页面 evaluate。

	回归 review #406 二审顺带项：旧判据 `created_page or page_ready` 中 created_page 一短路
	就无视 home_loaded，首页卡住时仍会对卡住页面 page.evaluate 读 localStorage（同 #390 挂起）。
	新建页签的就绪门禁应取 home_loaded 而非恒 True 的 created_page。
	"""
	mock_context = MagicMock()
	mock_context.pages = []  # 无既有页签 → 新建
	mock_context.cookies.return_value = [{"name": "at", "value": "access", "domain": ".zhaopin.com"}]
	mock_launcher, _, new_page = _mock_cdp_playwright(mock_context)
	new_page.evaluate.return_value = "UA"

	with (
		patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher),
		# 首页卡住：_warm_home_for_runtime 返回 False（home_loaded=False）
		patch("boss_agent_cli.auth.browser._warm_home_for_runtime", return_value=False),
		patch("boss_agent_cli.auth.browser._extract_zhilian_client_id") as mock_extract_cid,
	):
		result = login_via_cdp(timeout=1, platform="zhilian")

	mock_extract_cid.assert_not_called()  # 首页卡住时不对卡住页面 evaluate
	assert "x_zp_client_id" not in result  # 容忍缺失，不挂起


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
	# Blocker 回归（review #406）：首页卡住时 UA/stoken 都不得对页面 evaluate
	# （patchright 在执行上下文无法建立时永久挂起、不抛异常）。
	mock_page.evaluate.assert_not_called()


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
def test_login_via_cdp_reuse_prints_context_index_and_account_fingerprint(mock_sleep, mock_probe_cdp, capsys):
	"""复用已登录 context 时，stderr 必须打出 context 序号 + 不可逆账号指纹（多 context 歧义）。"""
	logged_ctx, empty_ctx = _make_logged_and_empty_contexts()
	logged_ctx.pages = []
	empty_ctx.pages = []
	mock_browser = MagicMock()
	mock_browser.contexts = [empty_ctx, logged_ctx]  # 命中第 2 个
	mock_launcher = MagicMock()
	mock_launcher.start.return_value.chromium.connect_over_cdp.return_value = mock_browser

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher):
		login_via_cdp(timeout=1)

	err = capsys.readouterr().err
	assert "context 2/2" in err  # 选中第 2 个 context
	assert "账号指纹" in err
	# 指纹不可逆：不得泄露 cookie 原值
	assert "tok" not in err
	# 多 context 时给出纠正提示
	assert "多个浏览器 context" in err


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
def test_login_via_cdp_new_page_stalled_falls_back_to_cookie_jar(mock_sleep, mock_probe_cdp):
	"""复用登录态但新建页签首页卡住：不得对页面 evaluate，回退 cookie jar。"""
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
	# 首页卡住（home_loaded=False）时 UA 也不得 evaluate（blocker 回归）
	mock_page.evaluate.assert_not_called()


def _make_reuse_page_context(*, jar_stoken: str | None) -> tuple[MagicMock, MagicMock]:
	"""构造「已登录 context + 既有 zhipin 页签」，jar_stoken=None 表示 cookie jar 缺 stoken。"""
	logged_ctx = MagicMock()
	cookies = [{"name": "wt2", "value": "tok", "domain": ".zhipin.com"}]
	if jar_stoken is not None:
		cookies.append({"name": "__zp_stoken__", "value": jar_stoken, "domain": ".zhipin.com"})
	logged_ctx.cookies.return_value = cookies
	existing_page = MagicMock()
	existing_page.url = "https://www.zhipin.com/web/geek/job"
	logged_ctx.pages = [existing_page]
	return logged_ctx, existing_page


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_login_via_cdp_reuse_page_extracts_stoken_when_ready(mock_sleep, mock_probe_cdp):
	"""复用既有页签 + jar 缺 stoken：有界确认就绪后采 UA 并提取 stoken（核心新路径 277-298）。"""
	logged_ctx, existing_page = _make_reuse_page_context(jar_stoken=None)
	mock_browser = MagicMock()
	mock_browser.contexts = [logged_ctx]
	mock_launcher = MagicMock()
	mock_launcher.start.return_value.chromium.connect_over_cdp.return_value = mock_browser
	existing_page.evaluate.return_value = "UA"

	with (
		patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher),
		patch("boss_agent_cli.auth.browser._extract_stoken", return_value="page-stoken") as mock_extract,
	):
		result = login_via_cdp(timeout=1)

	existing_page.goto.assert_not_called()  # 复用页签不导航
	existing_page.wait_for_load_state.assert_called_once_with("domcontentloaded", timeout=_NAV_TIMEOUT_MS)
	mock_extract.assert_called_once()  # 页面就绪后提取 stoken
	assert result["stoken"] == "page-stoken"
	assert result["user_agent"] == "UA"  # 就绪后才采 UA


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_login_via_cdp_reuse_page_with_jar_stoken_skips_extract_but_collects_ua(mock_sleep, mock_probe_cdp):
	"""复用既有页签 + jar 已有 stoken：不调 _extract_stoken，但 UA 仍在就绪后采集。"""
	logged_ctx, existing_page = _make_reuse_page_context(jar_stoken="jar-stoken")
	mock_browser = MagicMock()
	mock_browser.contexts = [logged_ctx]
	mock_launcher = MagicMock()
	mock_launcher.start.return_value.chromium.connect_over_cdp.return_value = mock_browser
	existing_page.evaluate.return_value = "UA"

	with (
		patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher),
		patch("boss_agent_cli.auth.browser._extract_stoken") as mock_extract,
	):
		result = login_via_cdp(timeout=1)

	existing_page.goto.assert_not_called()
	mock_extract.assert_not_called()  # jar 已有 stoken，不做页面提取
	assert result["stoken"] == "jar-stoken"
	# UA 仍需就绪门禁后采集（result["user_agent"] 是必需输出）
	existing_page.wait_for_load_state.assert_called_once_with("domcontentloaded", timeout=_NAV_TIMEOUT_MS)
	assert result["user_agent"] == "UA"


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_login_via_cdp_reuse_stalled_page_is_not_evaluated(mock_sleep, mock_probe_cdp):
	"""复用既有页签 + jar 缺 stoken + 页面卡住：_extract_stoken 与 evaluate 都不调用。"""
	logged_ctx, existing_page = _make_reuse_page_context(jar_stoken=None)
	mock_browser = MagicMock()
	mock_browser.contexts = [logged_ctx]
	mock_launcher = MagicMock()
	mock_launcher.start.return_value.chromium.connect_over_cdp.return_value = mock_browser
	existing_page.wait_for_load_state.side_effect = Exception("Timeout 15000ms exceeded")

	with (
		patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher),
		patch("boss_agent_cli.auth.browser._extract_stoken") as mock_extract,
	):
		result = login_via_cdp(timeout=1)

	existing_page.goto.assert_not_called()
	existing_page.wait_for_load_state.assert_called_once_with("domcontentloaded", timeout=_NAV_TIMEOUT_MS)
	mock_extract.assert_not_called()
	existing_page.evaluate.assert_not_called()  # 页面卡住：UA 也不采
	assert result["stoken"] == ""
	assert result["user_agent"] == ""


# ── login --force：不复用既有登录态（Issue #424） ─────────────────


def test_clear_platform_cookies_only_touches_target_domain_variants():
	"""按实际存在的 domain 值逐一清除：.zhipin.com 与 www.zhipin.com 都清，evil-zhipin.com 与其他站点不动。"""
	from boss_agent_cli.auth.browser import _clear_platform_cookies

	ctx = MagicMock()
	ctx.cookies.return_value = [
		{"name": "wt2", "value": "tok", "domain": ".zhipin.com"},
		{"name": "__zp_stoken__", "value": "s", "domain": "www.zhipin.com"},
		{"name": "x", "value": "1", "domain": "evil-zhipin.com"},
		{"name": "y", "value": "2", "domain": "zhipin.com.attacker.net"},
		{"name": "sid", "value": "3", "domain": ".github.com"},
	]

	_clear_platform_cookies(ctx, cookie_domain="zhipin.com")

	cleared = sorted(call.kwargs["domain"] for call in ctx.clear_cookies.call_args_list)
	assert cleared == [".zhipin.com", "www.zhipin.com"]


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_login_via_cdp_force_skips_reuse_clears_platform_cookies_and_scans(mock_sleep, mock_probe_cdp, capsys):
	"""reuse_existing=False：不扫描已登录 context、清掉目标平台 cookie、打开登录页并轮询新登录态。"""
	logged_ctx, existing_page = _make_reuse_page_context(jar_stoken="stale-stoken")
	# 清除后第一次轮询没有 wt2（已清），第二次出现新扫码产生的 wt2
	logged_ctx.cookies.side_effect = [
		[{"name": "wt2", "value": "stale", "domain": ".zhipin.com"}],  # _clear_platform_cookies 枚举
		[],  # 轮询第 1 次：已清
		[{"name": "wt2", "value": "fresh", "domain": ".zhipin.com"}],  # 轮询第 2 次：新登录
		[{"name": "wt2", "value": "fresh", "domain": ".zhipin.com"}],  # 最终读取
	]
	mock_launcher, _, new_page = _mock_cdp_playwright(logged_ctx)
	new_page.evaluate.return_value = "UA"

	with (
		patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher),
		patch("boss_agent_cli.auth.browser._find_logged_in_context") as mock_find,
		patch("boss_agent_cli.auth.browser._extract_stoken", return_value="fresh-stoken"),
	):
		result = login_via_cdp(timeout=3, reuse_existing=False)

	mock_find.assert_not_called()  # 不扫描、不复用
	logged_ctx.clear_cookies.assert_called_once_with(domain=".zhipin.com")
	existing_page.goto.assert_not_called()  # 既有页签不动
	new_page.goto.assert_any_call(LOGIN_PAGE_URL, wait_until="commit", timeout=_NAV_TIMEOUT_MS)
	assert result["cookies"]["wt2"] == "fresh"
	assert result["stoken"] == "fresh-stoken"
	assert "--force" in capsys.readouterr().err


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_login_via_cdp_default_still_reuses_without_clearing(mock_sleep, mock_probe_cdp):
	"""默认 reuse_existing=True 行为不变：复用已登录 context，绝不清 cookie。"""
	logged_ctx, existing_page = _make_reuse_page_context(jar_stoken="jar-stoken")
	mock_browser = MagicMock()
	mock_browser.contexts = [logged_ctx]
	mock_launcher = MagicMock()
	mock_launcher.start.return_value.chromium.connect_over_cdp.return_value = mock_browser

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher):
		result = login_via_cdp(timeout=1)

	logged_ctx.clear_cookies.assert_not_called()
	existing_page.goto.assert_not_called()
	assert result["stoken"] == "jar-stoken"
