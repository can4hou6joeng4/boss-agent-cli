from unittest.mock import patch, MagicMock

import pytest

import httpx

from boss_agent_cli.api.browser_client import (
	HOME_URL,
	_HEADLESS_NETWORKIDLE_GRACE_MS,
	_NAV_TIMEOUT_MS,
	BrowserSession,
	_find_reusable_zhipin_page,
)
from boss_agent_cli.api.browser_urls import DEFAULT_CDP_URL, is_zhipin_url


def test_browser_session_defaults():
	session = BrowserSession(cookies={"wt2": "abc"}, user_agent="test-ua")
	assert session._is_cdp is False
	assert session._started is False
	assert session._cookies == {"wt2": "abc"}


def test_fetch_ws_url_success():
	with patch("httpx.get") as mock_get:
		mock_resp = MagicMock()
		mock_resp.json.return_value = {"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/abc"}
		mock_get.return_value = mock_resp
		ws = BrowserSession._fetch_ws_url("http://127.0.0.1:9222")
		assert ws == "ws://127.0.0.1:9222/devtools/browser/abc"


def test_fetch_ws_url_failure():
	with patch("httpx.get", side_effect=httpx.ConnectError("connection refused")):
		ws = BrowserSession._fetch_ws_url("http://127.0.0.1:9222")
		assert ws is None


def test_fetch_ws_url_invalid_json_returns_none():
	with patch("httpx.get") as mock_get:
		mock_resp = MagicMock()
		mock_resp.json.side_effect = ValueError("invalid json")
		mock_get.return_value = mock_resp

		ws = BrowserSession._fetch_ws_url("http://127.0.0.1:9222")

		assert ws is None


def test_fetch_ws_url_missing_websocket_debugger_url_returns_none():
	with patch("httpx.get") as mock_get:
		mock_resp = MagicMock()
		mock_resp.json.return_value = {"Browser": "Chrome"}
		mock_get.return_value = mock_resp

		ws = BrowserSession._fetch_ws_url("http://127.0.0.1:9222")

		assert ws is None


def test_fetch_ws_url_non_object_json_returns_none():
	with patch("httpx.get") as mock_get:
		mock_resp = MagicMock()
		mock_resp.json.return_value = ["not", "a", "devtools", "object"]
		mock_get.return_value = mock_resp

		ws = BrowserSession._fetch_ws_url("http://127.0.0.1:9222")

		assert ws is None


def test_read_devtools_active_port_missing(tmp_path):
	with patch("boss_agent_cli.api.browser_client._CHROME_USER_DATA_CANDIDATES", [tmp_path / "nonexistent"]):
		ws = BrowserSession._read_devtools_active_port()
		assert ws is None


def test_read_devtools_active_port_found(tmp_path):
	port_file = tmp_path / "DevToolsActivePort"
	port_file.write_text("9222\n/devtools/browser/test-id\n")
	with patch("boss_agent_cli.api.browser_client._CHROME_USER_DATA_CANDIDATES", [tmp_path]):
		ws = BrowserSession._read_devtools_active_port()
		assert ws == "ws://127.0.0.1:9222/devtools/browser/test-id"


def test_close_cdp_mode_reused_context_not_closed():
	"""CDP 复用用户 context 时 close() 只关闭 page，不关闭 context"""
	session = BrowserSession(cookies={}, user_agent="")
	session._is_cdp = True
	session._own_context = False  # 复用的 context
	session._started = True
	session._page = MagicMock()
	session._context = MagicMock()
	session._browser = MagicMock()
	session._pw = MagicMock()

	session.close()

	session._page.close.assert_called_once()
	session._context.close.assert_not_called()  # 不关闭用户的 context
	session._browser.close.assert_not_called()


def test_close_cdp_mode_own_context_closed():
	"""CDP 自建 context 时 close() 关闭 page 和 context"""
	session = BrowserSession(cookies={}, user_agent="")
	session._is_cdp = True
	session._own_context = True  # 自建的 context
	session._started = True
	session._page = MagicMock()
	session._context = MagicMock()
	session._browser = MagicMock()
	session._pw = MagicMock()

	session.close()

	session._page.close.assert_called_once()
	session._context.close.assert_called_once()


def test_close_headless_mode_closes_browser():
	"""Headless 模式下 close() 关闭整个 browser"""
	session = BrowserSession(cookies={}, user_agent="")
	session._is_cdp = False
	session._started = True
	session._page = MagicMock()
	session._browser = MagicMock()
	session._pw = MagicMock()

	session.close()

	session._browser.close.assert_called_once()


def test_close_is_idempotent_when_cdp_resources_are_partial_and_raise():
	session = BrowserSession(cookies={}, user_agent="")
	session._is_cdp = True
	session._own_context = True
	session._started = True
	session._page = MagicMock()
	session._context = MagicMock()
	session._pw = MagicMock()
	pw = session._pw
	session._page.close.side_effect = RuntimeError("page already closed")
	session._context.close.side_effect = RuntimeError("context already closed")
	session._pw.stop.side_effect = RuntimeError("playwright already stopped")

	session.close()
	session.close()

	assert session._started is False
	# close 现在是真幂等的：第二次调用因 _started 已为 False 直接返回，不重复拆卸。
	# 旧实现会重跑一遍，且在 _is_cdp 被复位后走进 headless 分支执行错误的拆卸。
	assert session._page.close.call_count == 1
	assert session._context.close.call_count == 1
	# playwright driver 在首次 close 时被 stop 并置空，第二次 close 不再重复 stop。
	# 这是有意的：_ensure_playwright() 用 `_pw is None` 判断是否要起新 driver，
	# close 不置空的话，close 后重启会复用一个已 stop 的 driver。
	assert pw.stop.call_count == 1
	assert session._pw is None


def test_close_is_idempotent_when_headless_resources_are_partial_and_raise():
	session = BrowserSession(cookies={}, user_agent="")
	session._is_cdp = False
	session._started = True
	session._browser = MagicMock()
	session._pw = MagicMock()
	pw = session._pw
	session._browser.close.side_effect = RuntimeError("browser already closed")
	session._pw.stop.side_effect = RuntimeError("playwright already stopped")

	session.close()
	session.close()

	assert session._started is False
	assert session._browser.close.call_count == 1
	# 同上：driver 首次 close 即释放并置空，避免 close 后重启复用已 stop 的 driver。
	assert pw.stop.call_count == 1
	assert session._pw is None


def test_try_connect_reuses_existing_context():
	"""CDP 连接应复用用户现有 context，避免创建额外浏览器状态。"""
	session = BrowserSession(cookies={}, user_agent="")
	session._pw = MagicMock()

	mock_browser = MagicMock()
	mock_user_context = MagicMock()
	mock_browser.contexts = [mock_user_context]
	mock_page = MagicMock()
	mock_user_context.new_page.return_value = mock_page

	session._pw.chromium.connect_over_cdp.return_value = mock_browser

	result = session._try_connect("ws://localhost:9222/test")

	assert result is True
	assert session._is_cdp is True
	assert session._own_context is False  # 复用，非自建
	assert session._context is mock_user_context  # 直接使用用户 context
	# 验证：没有创建新 context
	mock_browser.new_context.assert_not_called()
	# 验证：page 在用户 context 中创建
	mock_user_context.new_page.assert_called_once()


def test_try_connect_creates_new_context_when_none_exists():
	"""CDP 连接无已存在 context 时创建新 context 并注入 cookies"""
	session = BrowserSession(cookies={"wt2": "abc"}, user_agent="")
	session._pw = MagicMock()

	mock_browser = MagicMock()
	mock_browser.contexts = []  # 无已存在 context
	mock_new_context = MagicMock()
	mock_browser.new_context.return_value = mock_new_context
	mock_page = MagicMock()
	mock_new_context.new_page.return_value = mock_page

	session._pw.chromium.connect_over_cdp.return_value = mock_browser

	result = session._try_connect("ws://localhost:9222/test")

	assert result is True
	assert session._is_cdp is True
	assert session._own_context is True  # 自建
	# 验证：创建了新 context
	mock_browser.new_context.assert_called_once()
	# 验证：cookies 被注入
	mock_new_context.add_cookies.assert_called_once()
	cookies_arg = mock_new_context.add_cookies.call_args[0][0]
	assert any(c["name"] == "wt2" for c in cookies_arg)


def test_start_headless_tolerates_networkidle_timeout():
	"""Headless 预热不应因 networkidle 等待超时而直接失败。"""
	logger = MagicMock()
	session = BrowserSession(cookies={"wt2": "abc"}, user_agent="UA", logger=logger)
	session._pw = MagicMock()

	mock_browser = MagicMock()
	mock_context = MagicMock()
	mock_page = MagicMock()
	mock_page.wait_for_load_state.side_effect = Exception("Timeout 30000ms exceeded")
	mock_context.new_page.return_value = mock_page
	mock_browser.new_context.return_value = mock_context
	session._pw.chromium.launch.return_value = mock_browser

	session._start_headless()

	assert session._started is True
	assert session._is_cdp is False
	mock_page.goto.assert_called_once_with(
		HOME_URL,
		wait_until="domcontentloaded",
		timeout=_NAV_TIMEOUT_MS,
	)
	mock_page.wait_for_load_state.assert_called_once_with(
		"networkidle",
		timeout=_HEADLESS_NETWORKIDLE_GRACE_MS,
	)
	logger.debug.assert_any_call(
		"[boss] CDP 不可用（提示：需以 --remote-debugging-port=9222 启动 Chrome），降级到 headless patchright"
	)
	assert any("headless 首页未进入 networkidle" in call.args[0] for call in logger.debug.call_args_list)


def test_ensure_started_falls_back_to_patchright_when_bridge_and_cdp_fail():
	session = BrowserSession(cookies={}, user_agent="")
	mock_pw = MagicMock()
	sentinel = {"headless_started": False}

	def mark_headless_started():
		sentinel["headless_started"] = True
		session._started = True

	with (
		patch.object(session, "_try_bridge", return_value=False) as mock_try_bridge,
		patch("boss_agent_cli.api.browser_client.sync_playwright") as mock_sync_playwright,
		patch.object(session, "_try_cdp", return_value=False) as mock_try_cdp,
		patch.object(session, "_start_headless", side_effect=mark_headless_started) as mock_start_headless,
	):
		mock_sync_playwright.return_value.start.return_value = mock_pw

		session._ensure_started()

	assert sentinel["headless_started"] is True
	assert session._started is True
	assert session._pw is mock_pw
	mock_try_bridge.assert_called_once()
	mock_sync_playwright.assert_called_once()
	mock_try_cdp.assert_called_once()
	mock_start_headless.assert_called_once()


def test_try_cdp_attempts_http_ws_and_devtools_urls_before_falling_back():
	session = BrowserSession(cookies={}, user_agent="", cdp_url="http://127.0.0.1:9333")

	with (
		patch.object(session, "_try_connect", return_value=False) as mock_try_connect,
		patch.object(
			BrowserSession, "_fetch_ws_url", side_effect=[None, "ws://127.0.0.1:9222/devtools/browser/default"]
		) as mock_fetch_ws_url,
		patch.object(
			BrowserSession, "_read_devtools_active_port", return_value="ws://127.0.0.1:9222/devtools/browser/file"
		) as mock_read_port,
	):
		result = session._try_cdp()

	assert result is False
	mock_read_port.assert_called_once()
	assert [call.args[0] for call in mock_try_connect.call_args_list] == [
		"http://127.0.0.1:9333",
		DEFAULT_CDP_URL,
		"ws://127.0.0.1:9222/devtools/browser/default",
		"ws://127.0.0.1:9222/devtools/browser/file",
	]
	assert [call.args[0] for call in mock_fetch_ws_url.call_args_list] == [
		"http://127.0.0.1:9333",
		DEFAULT_CDP_URL,
	]


def test_request_returns_browser_evaluation_json_and_marks_throttle():
	session = BrowserSession(cookies={}, user_agent="")
	session._started = True
	session._page = MagicMock()
	session._throttle = MagicMock()
	expected = {"code": 0, "zpData": {"jobs": []}}
	session._page.evaluate.return_value = expected

	result = session.request(
		"POST",
		"https://www.zhipin.com/wapi/zpgeek/search/joblist.json",
		params={"query": "python", "page": 1},
		data={"city": "101020100"},
	)

	assert result == expected
	session._throttle.wait.assert_called_once()
	session._throttle.mark.assert_called_once()
	evaluate_script, evaluate_args = session._page.evaluate.call_args.args
	assert "fetch(fetchUrl, options)" in evaluate_script
	assert evaluate_args == {
		"method": "POST",
		"url": "https://www.zhipin.com/wapi/zpgeek/search/joblist.json",
		"params": {"query": "python", "page": 1},
		"data": {"city": "101020100"},
		"referer": "https://www.zhipin.com/web/geek/job",
	}


def test_cold_start_wait_constants_stay_lean():
	"""回归护栏：冷启动两处等待须保持精简（曾各浪费 ~3s/搜索）。

	- 自动探测默认 localhost:9222 的超时必须短于显式 --cdp-url 的超时；
	- headless 首页 networkidle 宽限须 <=1s（zhipin 首页基本进不了 networkidle，
	  domcontentloaded 后 JS 环境即可发请求，长宽限是纯浪费）。
	"""
	from boss_agent_cli.api.browser_client import (
		_CDP_AUTO_PROBE_TIMEOUT,
		_CDP_PROBE_TIMEOUT,
		_HEADLESS_NETWORKIDLE_GRACE_MS,
	)

	assert _CDP_AUTO_PROBE_TIMEOUT < _CDP_PROBE_TIMEOUT
	assert _CDP_AUTO_PROBE_TIMEOUT <= 1
	assert _HEADLESS_NETWORKIDLE_GRACE_MS <= 1000


def test_fetch_ws_url_honors_short_auto_probe_timeout():
	"""默认自动探测应以短超时调用 httpx，避免无调试端口时白等。"""
	with patch("httpx.get") as mock_get:
		mock_get.return_value = MagicMock(json=lambda: {"webSocketDebuggerUrl": "ws://x"})
		BrowserSession._fetch_ws_url("http://127.0.0.1:9222", timeout=1)
		assert mock_get.call_args.kwargs["timeout"] == 1


# ── issue #334: CDP 复用已打开的 zhipin 页签，不再每次新开+导航 ─────────────


class _FakePage:
	def __init__(self, url):
		self.url = url


def test_is_zhipin_url_uses_exact_hostname():
	assert is_zhipin_url("https://www.zhipin.com/web/geek/job")
	assert is_zhipin_url("https://ZHIPIN.COM./")  # 大小写 + 尾点归一化
	assert not is_zhipin_url("https://zhipin.com.evil.example/web/geek/job")
	assert not is_zhipin_url("https://evil.example/?next=https://www.zhipin.com/")
	assert not is_zhipin_url("not-a-url-with-zhipin.com")


def test_find_reusable_zhipin_page_prefers_open_zhipin_tab():
	ctx = MagicMock()
	other = _FakePage("https://mail.google.com/")
	fake = _FakePage("https://zhipin.com.evil.example/web/geek/job")
	real = _FakePage("https://www.zhipin.com/web/geek/recommend")
	ctx.pages = [other, fake, real]

	assert _find_reusable_zhipin_page(ctx) is real


def test_find_reusable_zhipin_page_none_when_no_zhipin_tab():
	ctx = MagicMock()
	ctx.pages = [_FakePage("https://mail.google.com/")]
	assert _find_reusable_zhipin_page(ctx) is None


def test_try_connect_reuses_open_zhipin_page_without_new_tab_or_nav():
	"""CDP 复用 context 且已有 zhipin 页签时：不新开 page、不导航（修 #334）。"""
	session = BrowserSession(cookies={}, user_agent="")
	session._pw = MagicMock()

	mock_browser = MagicMock()
	mock_user_context = MagicMock()
	open_zhipin_page = _FakePage("https://www.zhipin.com/web/geek/job")
	mock_user_context.pages = [open_zhipin_page]
	mock_browser.contexts = [mock_user_context]
	session._pw.chromium.connect_over_cdp.return_value = mock_browser

	result = session._try_connect("ws://localhost:9222/test")

	assert result is True
	assert session._is_cdp is True
	assert session._own_context is False
	assert session._own_page is False  # 复用用户页签
	assert session._page is open_zhipin_page
	mock_user_context.new_page.assert_not_called()  # 不新开标签页
	assert open_zhipin_page.url == "https://www.zhipin.com/web/geek/job"  # 未被导航覆盖


def test_try_connect_creates_page_when_no_open_zhipin_tab():
	"""CDP 复用 context 但无 zhipin 页签时：仍新建 page 并导航（回退现状）。"""
	session = BrowserSession(cookies={}, user_agent="")
	session._pw = MagicMock()

	mock_browser = MagicMock()
	mock_user_context = MagicMock()
	mock_user_context.pages = [_FakePage("https://mail.google.com/")]
	mock_new_page = MagicMock()
	mock_user_context.new_page.return_value = mock_new_page
	mock_browser.contexts = [mock_user_context]
	session._pw.chromium.connect_over_cdp.return_value = mock_browser

	result = session._try_connect("ws://localhost:9222/test")

	assert result is True
	assert session._own_page is True
	mock_user_context.new_page.assert_called_once()
	assert session._started is True
	# 首次 CDP 页面必须等待 DOM 就绪，而不是 commit 级等待（首次搜索不再撞上
	# execution context destroyed）。
	mock_new_page.goto.assert_called_once_with(HOME_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)


def test_request_does_not_hang_when_new_page_navigation_stalled():
	"""新建 CDP 页签 goto 超时（_page_ready=False）时，request() 不得对仍在导航的页面
	evaluate（永久挂起，同 #390）；先有界恢复，仍卡住则返回错误信封。"""
	session = BrowserSession(cookies={}, user_agent="")
	session._pw = MagicMock()
	mock_browser = MagicMock()
	mock_ctx = MagicMock()
	mock_ctx.pages = []
	mock_new_page = MagicMock()
	mock_ctx.new_page.return_value = mock_new_page
	mock_browser.contexts = [mock_ctx]
	session._pw.chromium.connect_over_cdp.return_value = mock_browser
	mock_new_page.goto.side_effect = TimeoutError("Timeout 15000ms exceeded")

	assert session._try_connect("ws://localhost:9222/test") is True
	assert session._page_ready is False

	# 页面仍卡住：request 返回错误信封，不调 evaluate
	mock_new_page.wait_for_load_state.side_effect = TimeoutError("still stuck")
	res = session.request("GET", "https://www.zhipin.com/wapi/zpgeek/search/joblist.json")
	assert res["code"] == -1
	mock_new_page.evaluate.assert_not_called()

	# 页面恢复：有界 wait 成功后正常 evaluate，_page_ready 复位
	mock_new_page.wait_for_load_state.side_effect = None
	mock_new_page.wait_for_load_state.return_value = None
	mock_new_page.evaluate.return_value = {"code": 0, "zpData": {}}
	res2 = session.request("GET", "https://www.zhipin.com/wapi/zpgeek/search/joblist.json")
	assert res2["code"] == 0


def test_try_connect_resets_page_ready_on_reuse_after_prior_stall():
	"""上一次尝试新建页签 goto 超时留下 _page_ready=False，下一次复用健康页签时
	必须重置为 True——否则 request() 会对正常页签误跑 wait_for_load_state。"""
	session = BrowserSession(cookies={}, user_agent="")
	session._pw = MagicMock()

	# 第一次：无 zhipin 页签 → 新建，goto 超时 → _page_ready=False
	mock_browser1 = MagicMock()
	ctx1 = MagicMock()
	ctx1.pages = []
	new_page = MagicMock()
	ctx1.new_page.return_value = new_page
	mock_browser1.contexts = [ctx1]
	session._pw.chromium.connect_over_cdp.return_value = mock_browser1
	new_page.goto.side_effect = TimeoutError("Timeout 15000ms exceeded")
	assert session._try_connect("ws://localhost:9222/one") is True
	assert session._page_ready is False

	# 第二次：复用既有 zhipin 页签（不碰 _page_ready 的分支）→ 必须重置为 True
	reused_page = _FakePage("https://www.zhipin.com/web/geek/job")
	mock_browser2 = MagicMock()
	ctx2 = MagicMock()
	ctx2.pages = [reused_page]
	mock_browser2.contexts = [ctx2]
	session._pw.chromium.connect_over_cdp.return_value = mock_browser2
	session._started = False  # 模拟新一轮连接
	assert session._try_connect("ws://localhost:9222/two") is True
	assert session._page_ready is True  # 复用健康页签不被上次残留污染


def test_close_does_not_close_reused_user_page():
	"""CDP 复用用户页签（_own_page=False）时 close() 不关闭该页。"""
	session = BrowserSession(cookies={}, user_agent="")
	session._is_cdp = True
	session._own_context = False
	session._own_page = False  # 复用用户页签
	session._started = True
	session._page = MagicMock()
	session._context = MagicMock()
	session._pw = MagicMock()

	session.close()

	session._page.close.assert_not_called()  # 不关用户的页签
	session._context.close.assert_not_called()


# ── 复用页签运行期撞上用户手动导航：失败驱动的一次性恢复（Issue #423） ─────


def _started_cdp_session_with_page(page):
	session = BrowserSession(cookies={}, user_agent="")
	session._is_cdp = True
	session._started = True
	session._page_ready = True
	session._page = page
	session._context = MagicMock()
	session._pw = MagicMock()
	session._throttle = MagicMock()
	return session


def test_is_context_destroyed_error_matches_navigation_races():
	from boss_agent_cli.api.browser_client import _is_context_destroyed_error

	assert _is_context_destroyed_error(RuntimeError("Execution context was destroyed, most likely because of a navigation"))
	assert _is_context_destroyed_error(RuntimeError("Target closed"))
	assert not _is_context_destroyed_error(RuntimeError("Evaluation failed: ReferenceError: fetch is not defined"))
	assert not _is_context_destroyed_error(TimeoutError("Timeout 15000ms exceeded"))


def test_request_recovers_once_after_context_destroyed_mid_navigation():
	"""首次 evaluate 撞上 execution context destroyed：有界等待就绪后重试同一请求一次，成功返回。"""
	page = MagicMock()
	page.evaluate.side_effect = [
		RuntimeError("Execution context was destroyed, most likely because of a navigation."),
		{"code": 0, "zpData": {"ok": True}},
	]
	session = _started_cdp_session_with_page(page)

	res = session.request("GET", "https://www.zhipin.com/wapi/zpgeek/search/joblist.json")

	assert res["code"] == 0
	assert page.evaluate.call_count == 2
	page.wait_for_load_state.assert_called_once_with("domcontentloaded", timeout=_NAV_TIMEOUT_MS)
	assert session._page_ready is True


def test_request_returns_error_envelope_when_page_never_becomes_ready_after_navigation():
	"""恢复等待超时：返回错误信封，不第二次 evaluate、不无界重试。"""
	page = MagicMock()
	page.evaluate.side_effect = RuntimeError("Execution context was destroyed")
	page.wait_for_load_state.side_effect = TimeoutError("still navigating")
	session = _started_cdp_session_with_page(page)

	res = session.request("GET", "https://www.zhipin.com/wapi/zpgeek/search/joblist.json")

	assert res["code"] == -1
	assert page.evaluate.call_count == 1
	assert session._page_ready is False


def test_request_retries_at_most_once_on_repeated_context_destroyed():
	"""恢复后重试仍撞上同类异常：原样上抛，不进入第三次。"""
	page = MagicMock()
	page.evaluate.side_effect = RuntimeError("Execution context was destroyed")
	session = _started_cdp_session_with_page(page)

	with pytest.raises(RuntimeError):
		session.request("GET", "https://www.zhipin.com/wapi/zpgeek/search/joblist.json")

	assert page.evaluate.call_count == 2


def test_request_healthy_path_has_no_extra_wait():
	"""健康路径零额外往返：不调 wait_for_load_state，只 evaluate 一次。"""
	page = MagicMock()
	page.evaluate.return_value = {"code": 0, "zpData": {}}
	session = _started_cdp_session_with_page(page)

	res = session.request("GET", "https://www.zhipin.com/wapi/zpgeek/search/joblist.json")

	assert res["code"] == 0
	page.wait_for_load_state.assert_not_called()
	page.evaluate.assert_called_once()


def test_request_propagates_unrelated_evaluate_errors_without_retry():
	page = MagicMock()
	page.evaluate.side_effect = RuntimeError("Evaluation failed: ReferenceError")
	session = _started_cdp_session_with_page(page)

	with pytest.raises(RuntimeError):
		session.request("GET", "https://www.zhipin.com/wapi/zpgeek/search/joblist.json")

	page.evaluate.assert_called_once()
	page.wait_for_load_state.assert_not_called()
