from unittest.mock import MagicMock, patch

import click
import pytest

from boss_agent_cli.api.proxy import ProxyConfigurationError
from boss_agent_cli.auth.manager import AuthManager, TokenRefreshFailed
from boss_agent_cli.commands._login_errors import classify_login_error


def _make_store(token: dict | None = None):
	store = MagicMock()
	store.load.return_value = token
	lock = MagicMock()
	lock.__enter__.return_value = None
	lock.__exit__.return_value = None
	store.refresh_lock.return_value = lock
	return store


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("boss_agent_cli.auth.manager.login_via_cdp")
@patch("boss_agent_cli.auth.manager.extract_cookies")
def test_login_force_cdp_skips_cookie_extraction(mock_extract, mock_login_via_cdp, mock_store_cls, tmp_path):
	store = _make_store()
	mock_store_cls.return_value = store
	mock_login_via_cdp.return_value = {"cookies": {"wt2": "cdp-cookie"}, "stoken": "cdp-token"}

	manager = AuthManager(tmp_path)

	result = manager.login(timeout=45, cdp_url="http://127.0.0.1:9222", force_cdp=True)

	mock_extract.assert_not_called()
	mock_login_via_cdp.assert_called_once_with(cdp_url="http://127.0.0.1:9222", timeout=45, platform="zhipin", reuse_existing=True)
	store.save.assert_called_once_with({"cookies": {"wt2": "cdp-cookie"}, "stoken": "cdp-token"})
	assert result["_method"] == "CDP 扫码"
	assert manager._token["stoken"] == "cdp-token"


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("boss_agent_cli.auth.manager.login_via_browser")
@patch("boss_agent_cli.auth.manager.login_via_cdp")
@patch("boss_agent_cli.auth.manager.probe_cdp")
@patch("boss_agent_cli.auth.manager.extract_cookies")
def test_login_uses_valid_cookie_without_fallback(
	mock_extract,
	mock_probe_cdp,
	mock_login_via_cdp,
	mock_login_via_browser,
	mock_store_cls,
	tmp_path,
):
	store = _make_store()
	mock_store_cls.return_value = store
	cookie_token = {"cookies": {"wt2": "cookie-token"}, "stoken": "cookie-stoken"}
	mock_extract.return_value = cookie_token

	manager = AuthManager(tmp_path)

	with patch.object(manager, "_verify_cookie", return_value=True):
		result = manager.login()

	mock_probe_cdp.assert_not_called()
	mock_login_via_cdp.assert_not_called()
	mock_login_via_browser.assert_not_called()
	store.save.assert_called_once_with(cookie_token)
	assert result["_method"] == "Cookie 提取"


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("boss_agent_cli.auth.manager.login_via_browser")
@patch("boss_agent_cli.auth.manager.login_via_cdp")
@patch("boss_agent_cli.auth.manager.probe_cdp")
@patch("boss_agent_cli.auth.manager.extract_cookies")
def test_login_falls_back_to_browser_when_cdp_login_fails(
	mock_extract,
	mock_probe_cdp,
	mock_login_via_cdp,
	mock_login_via_browser,
	mock_store_cls,
	tmp_path,
):
	store = _make_store()
	mock_store_cls.return_value = store
	mock_extract.return_value = {"cookies": {"wt2": "stale-cookie"}, "stoken": "old"}
	mock_probe_cdp.return_value = True
	mock_login_via_cdp.side_effect = RuntimeError("cdp failed")
	mock_login_via_browser.return_value = {"cookies": {"wt2": "browser-cookie"}, "stoken": "browser-token"}

	manager = AuthManager(tmp_path)

	with patch.object(manager, "_verify_cookie", return_value=False):
		result = manager.login(timeout=30)

	mock_login_via_cdp.assert_called_once_with(cdp_url=None, timeout=30, platform="zhipin", reuse_existing=True)
	mock_login_via_browser.assert_called_once_with(timeout=30, platform="zhipin")
	store.save.assert_called_once_with({"cookies": {"wt2": "browser-cookie"}, "stoken": "browser-token"})
	assert result["_method"] == "扫码登录"


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("boss_agent_cli.auth.manager.login_via_browser")
@patch("boss_agent_cli.auth.manager.login_via_cdp")
@patch("boss_agent_cli.auth.manager.probe_cdp")
@patch("boss_agent_cli.auth.manager.extract_cookies")
def test_login_cdp_save_failure_is_not_downgraded_to_scan(
	mock_extract,
	mock_probe_cdp,
	mock_login_via_cdp,
	mock_login_via_browser,
	mock_store_cls,
	tmp_path,
):
	"""CDP 登录成功但落盘失败：直接上抛本地 IO 错误，不再启动第二次扫码（Issue #1480）。"""
	store = _make_store()
	save_error = PermissionError(13, "Permission denied", str(tmp_path / "auth" / "session.enc"))
	store.save.side_effect = save_error
	mock_store_cls.return_value = store
	mock_extract.return_value = None
	mock_probe_cdp.return_value = "ws://localhost/devtools/browser"
	mock_login_via_cdp.return_value = {"cookies": {"wt2": "cdp-cookie"}, "stoken": "cdp-token"}

	manager = AuthManager(tmp_path)

	with pytest.raises(PermissionError) as raised:
		manager.login(timeout=30)

	assert raised.value is save_error
	mock_login_via_cdp.assert_called_once_with(cdp_url=None, timeout=30, platform="zhipin", reuse_existing=True)
	mock_login_via_browser.assert_not_called()
	assert manager._token is None

	ctx = click.Context(click.Command("login"), obj={"data_dir": tmp_path})
	payload = classify_login_error(raised.value, ctx)
	assert payload["code"] == "LOCAL_IO_ERROR"
	assert payload["code"] != "LOGIN_TIMEOUT"
	assert payload["recoverable"] is False


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("boss_agent_cli.auth.manager.login_via_browser")
@patch("boss_agent_cli.auth.manager.login_via_cdp")
@patch("boss_agent_cli.auth.manager.probe_cdp")
@patch("boss_agent_cli.auth.manager.extract_cookies")
def test_login_still_falls_back_when_cdp_login_itself_raises_connection_error(
	mock_extract,
	mock_probe_cdp,
	mock_login_via_cdp,
	mock_login_via_browser,
	mock_store_cls,
	tmp_path,
):
	"""修复只豁免落盘失败：CDP 链路自身失败（含 OSError 子类 ConnectionError）仍要降级扫码。"""
	store = _make_store()
	mock_store_cls.return_value = store
	mock_extract.return_value = None
	mock_probe_cdp.return_value = True
	mock_login_via_cdp.side_effect = ConnectionError("CDP 不可用")
	mock_login_via_browser.return_value = {"cookies": {"wt2": "browser-cookie"}, "stoken": "browser-token"}

	result = AuthManager(tmp_path).login(timeout=30)

	mock_login_via_browser.assert_called_once_with(timeout=30, platform="zhipin")
	store.save.assert_called_once_with({"cookies": {"wt2": "browser-cookie"}, "stoken": "browser-token"})
	assert result["_method"] == "扫码登录"


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("boss_agent_cli.auth.manager.login_via_browser")
@patch("boss_agent_cli.auth.manager.probe_cdp")
@patch("boss_agent_cli.auth.manager.extract_cookies")
def test_login_goes_straight_to_browser_when_cdp_unavailable(
	mock_extract,
	mock_probe_cdp,
	mock_login_via_browser,
	mock_store_cls,
	tmp_path,
):
	"""Cookie 失败 + CDP 不可用时直接走浏览器扫码，之前不再发任何 httpx 登录请求。"""
	store = _make_store()
	mock_store_cls.return_value = store
	mock_extract.return_value = None
	mock_probe_cdp.return_value = False
	browser_token = {"cookies": {"wt2": "browser-cookie"}, "stoken": "browser-token"}
	mock_login_via_browser.return_value = browser_token

	with patch("httpx.Client") as http_client, patch("httpx.get") as http_get:
		result = AuthManager(tmp_path).login(timeout=30)

	http_client.assert_not_called()
	http_get.assert_not_called()
	mock_login_via_browser.assert_called_once_with(timeout=30, platform="zhipin")
	store.save.assert_called_once_with(browser_token)
	assert result["_method"] == "扫码登录"


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("boss_agent_cli.auth.manager.login_via_browser")
@patch("boss_agent_cli.auth.manager.extract_cookies")
@patch("httpx.get")
def test_login_falls_back_to_scan_when_cdp_probe_payload_not_object(
	mock_get, mock_extract, mock_login_via_browser, mock_store_cls, tmp_path
):
	"""CDP 探测拿到非对象 JSON 时按 CDP 不可用处理，继续降级到扫码（Issue #1421）。

	走真实 ``probe_cdp`` 实现（不 patch 掉它），只 mock 它的 httpx 响应与扫码降级函数：
	修复前 ``[]`` 会让 ``resp.json().get`` 抛 AttributeError，在降级链之外直接中止。
	"""
	store = _make_store()
	mock_store_cls.return_value = store
	mock_extract.return_value = None
	probe_response = MagicMock()
	probe_response.json.return_value = []
	mock_get.return_value = probe_response
	browser_token = {"cookies": {"wt2": "browser-cookie"}, "stoken": "browser-token"}
	mock_login_via_browser.return_value = browser_token

	result = AuthManager(tmp_path).login(timeout=30)

	mock_login_via_browser.assert_called_once_with(timeout=30, platform="zhipin")
	store.save.assert_called_once_with(browser_token)
	assert result["_method"] == "扫码登录"


@patch("boss_agent_cli.auth.manager.TokenStore")
def test_force_refresh_raises_when_no_token(mock_store_cls, tmp_path):
	store = _make_store(token=None)
	mock_store_cls.return_value = store
	manager = AuthManager(tmp_path)

	with pytest.raises(TokenRefreshFailed, match="无法刷新 Token，请重新登录"):
		manager.force_refresh()


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("boss_agent_cli.auth.manager.refresh_stoken", side_effect=ProxyConfigurationError("HTTPS_PROXY"))
@patch("boss_agent_cli.auth.manager.probe_cdp", return_value=False)
def test_force_refresh_preserves_proxy_configuration_error(
	mock_probe_cdp, mock_refresh_stoken, mock_store_cls, tmp_path
):
	"""代理配置错误不能被包装成可重试的 TOKEN_REFRESH_FAILED（Issue #1423）。"""
	current = {"cookies": {"wt2": "cookie"}, "stoken": "old-token", "user_agent": "ua"}
	mock_store_cls.return_value = _make_store(token=current.copy())
	manager = AuthManager(tmp_path)

	with pytest.raises(ProxyConfigurationError):
		manager.force_refresh()


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("boss_agent_cli.auth.manager.refresh_stoken")
@patch("boss_agent_cli.auth.manager.refresh_stoken_via_cdp")
@patch("boss_agent_cli.auth.manager.probe_cdp")
def test_force_refresh_prefers_cdp_and_persists_new_stoken(
	mock_probe_cdp,
	mock_refresh_cdp,
	mock_refresh_stoken,
	mock_store_cls,
	tmp_path,
):
	current = {"cookies": {"wt2": "cookie"}, "stoken": "old-token", "user_agent": "ua"}
	store = _make_store(token=current.copy())
	mock_store_cls.return_value = store
	mock_probe_cdp.return_value = True
	mock_refresh_cdp.return_value = "new-token"

	manager = AuthManager(tmp_path)

	manager.force_refresh(cdp_url="http://127.0.0.1:9222")

	mock_refresh_stoken.assert_not_called()
	assert mock_refresh_cdp.call_args.args == ("http://127.0.0.1:9222",)
	assert mock_refresh_cdp.call_args.kwargs["policy"].name == "auto"
	store.save.assert_called_once()
	saved_token = store.save.call_args.args[0]
	assert saved_token["stoken"] == "new-token"
	assert manager._token["stoken"] == "new-token"


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("boss_agent_cli.auth.manager.refresh_stoken")
@patch("boss_agent_cli.auth.manager.refresh_stoken_via_cdp")
@patch("boss_agent_cli.auth.manager.probe_cdp")
def test_force_refresh_propagates_save_failure_without_wrapping_as_token_refresh_failed(
	mock_probe_cdp,
	mock_refresh_cdp,
	mock_refresh_stoken,
	mock_store_cls,
	tmp_path,
):
	"""刷新成功但落盘失败：原始本地 IO 错误上抛，命令层报不可重试的 LOCAL_IO_ERROR（Issue #1479）。"""
	current = {"cookies": {"wt2": "cookie"}, "stoken": "old-token", "user_agent": "ua"}
	store = _make_store(token=current)
	save_error = PermissionError(13, "Permission denied", str(tmp_path / "auth" / "session.enc"))
	store.save.side_effect = save_error
	mock_store_cls.return_value = store
	mock_probe_cdp.return_value = False
	mock_refresh_stoken.return_value = "new-token"

	manager = AuthManager(tmp_path)

	with pytest.raises(PermissionError) as raised:
		manager.force_refresh()

	assert raised.value is save_error
	assert not isinstance(raised.value, TokenRefreshFailed)
	mock_refresh_cdp.assert_not_called()
	mock_refresh_stoken.assert_called_once_with({"wt2": "cookie"}, "ua")
	assert current["stoken"] == "old-token"
	assert manager._token is None

	# 命令层：确定性本地失败，不能提示「重新登录」或当成可重试网络错误。
	ctx = click.Context(click.Command("login"), obj={"data_dir": tmp_path})
	payload = classify_login_error(raised.value, ctx)
	assert payload["code"] == "LOCAL_IO_ERROR"
	assert payload["recoverable"] is False


# ── login(force_relogin=True)（Issue #424） ─────────────────


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("boss_agent_cli.auth.manager.login_via_cdp")
@patch("boss_agent_cli.auth.manager.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.manager.extract_cookies")
def test_login_force_relogin_skips_cookie_extraction_and_disables_reuse(
	mock_extract, mock_probe_cdp, mock_login_via_cdp, mock_store_cls, tmp_path
):
	store = _make_store()
	mock_store_cls.return_value = store
	mock_login_via_cdp.return_value = {"cookies": {"wt2": "fresh"}, "stoken": "fresh-token"}

	result = AuthManager(tmp_path).login(timeout=30, cdp_url="http://127.0.0.1:9222", force_relogin=True)

	mock_extract.assert_not_called()  # 本地 Cookie 正是要放弃的旧登录态
	mock_login_via_cdp.assert_called_once_with(
		cdp_url="http://127.0.0.1:9222", timeout=30, platform="zhipin", reuse_existing=False
	)
	store.save.assert_called_once_with({"cookies": {"wt2": "fresh"}, "stoken": "fresh-token"})
	assert result["_method"] == "CDP 扫码"


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("boss_agent_cli.auth.manager.login_via_cdp")
@patch("boss_agent_cli.auth.manager.extract_cookies")
def test_login_force_cdp_and_force_relogin_compose(mock_extract, mock_login_via_cdp, mock_store_cls, tmp_path):
	"""--cdp --force：CDP 必须可用，且不复用。"""
	mock_store_cls.return_value = _make_store()
	mock_login_via_cdp.return_value = {"cookies": {"wt2": "fresh"}, "stoken": "t"}

	AuthManager(tmp_path).login(timeout=45, cdp_url=None, force_cdp=True, force_relogin=True)

	mock_extract.assert_not_called()
	mock_login_via_cdp.assert_called_once_with(cdp_url=None, timeout=45, platform="zhipin", reuse_existing=False)


# ── 复用登录态失效 → 自动改走不复用路径；风控不降级（Issue #424 第二步） ─────


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("boss_agent_cli.auth.manager.login_via_browser")
@patch("boss_agent_cli.auth.manager.login_via_cdp")
@patch("boss_agent_cli.auth.manager.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.manager.extract_cookies", return_value=None)
def test_login_stale_reused_session_retries_without_reuse(
	mock_extract, mock_probe_cdp, mock_login_via_cdp, mock_login_via_browser, mock_store_cls, tmp_path
):
	from boss_agent_cli.auth.browser import ReusedSessionStaleError

	store = _make_store()
	mock_store_cls.return_value = store
	mock_login_via_cdp.side_effect = [
		ReusedSessionStaleError("stale"),
		{"cookies": {"wt2": "fresh"}, "stoken": "fresh-token"},
	]

	result = AuthManager(tmp_path).login(timeout=30, cdp_url="http://127.0.0.1:9222")

	assert [c.kwargs["reuse_existing"] for c in mock_login_via_cdp.call_args_list] == [True, False]
	store.save.assert_called_once_with({"cookies": {"wt2": "fresh"}, "stoken": "fresh-token"})
	assert result["_method"] == "CDP 扫码"
	mock_login_via_browser.assert_not_called()


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("boss_agent_cli.auth.manager.login_via_browser")
@patch("boss_agent_cli.auth.manager.login_via_cdp")
@patch("boss_agent_cli.auth.manager.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.manager.extract_cookies", return_value=None)
def test_login_platform_risk_during_cdp_reuse_is_not_downgraded(
	mock_extract, mock_probe_cdp, mock_login_via_cdp, mock_login_via_browser, mock_store_cls, tmp_path
):
	"""复用探测命中风控：立即上抛，不得降级到 patchright 再登一次。"""
	from boss_agent_cli.api.client import AccountRiskError

	store = _make_store()
	mock_store_cls.return_value = store
	mock_login_via_cdp.side_effect = AccountRiskError("风控", is_cdp=True)

	with pytest.raises(AccountRiskError):
		AuthManager(tmp_path).login(timeout=30, cdp_url="http://127.0.0.1:9222")

	mock_login_via_cdp.assert_called_once()
	mock_login_via_browser.assert_not_called()
	store.save.assert_not_called()
