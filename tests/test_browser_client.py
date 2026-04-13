from unittest.mock import patch, MagicMock

from boss_agent_cli.api.browser_client import BrowserSession


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
	with patch("httpx.get", side_effect=Exception("connection refused")):
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


def test_try_connect_reuses_existing_context():
	"""CDP 连接应复用用户现有 context（规避 automation 检测）"""
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
