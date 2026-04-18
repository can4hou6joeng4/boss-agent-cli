"""auth/manager.py 覆盖率补齐测试。

覆盖 get_token 缓存、AuthRequired、_verify_cookie、force_refresh 所有分支、check_status、logout。
"""

from unittest.mock import MagicMock, patch

import pytest

from boss_agent_cli.auth.manager import AuthManager, AuthRequired, TokenRefreshFailed


def _make_store(token: dict | None = None) -> MagicMock:
	store = MagicMock()
	store.load.return_value = token
	lock = MagicMock()
	lock.__enter__.return_value = None
	lock.__exit__.return_value = None
	store.refresh_lock.return_value = lock
	return store


# ── get_token ─────────────────────────────────────────────


@patch("boss_agent_cli.auth.manager.TokenStore")
def test_get_token_raises_auth_required_when_no_session(mock_store_cls, tmp_path):
	store = _make_store(token=None)
	mock_store_cls.return_value = store
	manager = AuthManager(tmp_path)

	with pytest.raises(AuthRequired, match="未登录"):
		manager.get_token()


@patch("boss_agent_cli.auth.manager.TokenStore")
def test_get_token_loads_from_store_and_caches(mock_store_cls, tmp_path):
	token = {"cookies": {"wt2": "c1"}, "stoken": "s1"}
	store = _make_store(token=token)
	mock_store_cls.return_value = store
	manager = AuthManager(tmp_path)

	first = manager.get_token()
	second = manager.get_token()

	assert first == token
	assert second == token
	# load 应只被调用 1 次（缓存生效）
	assert store.load.call_count == 1


# ── login 降级链路 ─────────────────────────────────────────


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("boss_agent_cli.auth.manager.login_via_browser")
@patch("boss_agent_cli.auth.manager.qr_login_httpx")
@patch("boss_agent_cli.auth.manager.login_via_cdp")
@patch("boss_agent_cli.auth.manager.probe_cdp")
@patch("boss_agent_cli.auth.manager.extract_cookies")
def test_login_falls_back_to_qr_when_cdp_login_raises(
	mock_extract,
	mock_probe_cdp,
	mock_login_via_cdp,
	mock_qr_login,
	mock_login_via_browser,
	mock_store_cls,
	tmp_path,
):
	store = _make_store()
	mock_store_cls.return_value = store
	mock_extract.return_value = None
	mock_probe_cdp.return_value = True
	mock_login_via_cdp.side_effect = RuntimeError("cdp dead")
	mock_qr_login.return_value = {"cookies": {"wt2": "qr"}, "stoken": "qr-token"}

	manager = AuthManager(tmp_path)
	result = manager.login(timeout=30)

	mock_login_via_cdp.assert_called_once()
	mock_qr_login.assert_called_once_with(timeout=30)
	mock_login_via_browser.assert_not_called()
	assert result["_method"] == "QR httpx 登录"


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("boss_agent_cli.auth.manager.login_via_browser")
@patch("boss_agent_cli.auth.manager.qr_login_httpx")
@patch("boss_agent_cli.auth.manager.probe_cdp")
@patch("boss_agent_cli.auth.manager.extract_cookies")
def test_login_falls_back_to_patchright_when_qr_fails(
	mock_extract,
	mock_probe_cdp,
	mock_qr_login,
	mock_login_via_browser,
	mock_store_cls,
	tmp_path,
):
	"""CDP 不可用 + QR httpx 失败 → 兜底 patchright 扫码。"""
	store = _make_store()
	mock_store_cls.return_value = store
	mock_extract.return_value = None
	mock_probe_cdp.return_value = False
	mock_qr_login.side_effect = TimeoutError("qr timeout")
	mock_login_via_browser.return_value = {"cookies": {"wt2": "b"}, "stoken": "bt"}

	manager = AuthManager(tmp_path)
	result = manager.login(timeout=20)

	mock_qr_login.assert_called_once()
	mock_login_via_browser.assert_called_once_with(timeout=20)
	assert result["_method"] == "扫码登录"
	assert manager._token["stoken"] == "bt"


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("boss_agent_cli.auth.manager.login_via_browser")
@patch("boss_agent_cli.auth.manager.qr_login_httpx")
@patch("boss_agent_cli.auth.manager.probe_cdp")
@patch("boss_agent_cli.auth.manager.extract_cookies")
def test_login_falls_back_to_patchright_when_cookie_invalid_and_cdp_down_and_qr_fails(
	mock_extract,
	mock_probe_cdp,
	mock_qr_login,
	mock_login_via_browser,
	mock_store_cls,
	tmp_path,
):
	"""Cookie 提取有 wt2 但验证失效 → CDP 不可用 → QR 失败 → patchright 兜底。"""
	store = _make_store()
	mock_store_cls.return_value = store
	# 返回一个带 wt2 的 cookie，但 verify 返回 False
	mock_extract.return_value = {"cookies": {"wt2": "stale"}, "stoken": ""}
	mock_probe_cdp.return_value = False
	mock_qr_login.side_effect = RuntimeError("qr died")
	mock_login_via_browser.return_value = {"cookies": {"wt2": "good"}, "stoken": "good-t"}

	manager = AuthManager(tmp_path)
	with patch.object(manager, "_verify_cookie", return_value=False):
		result = manager.login(timeout=10)

	mock_login_via_browser.assert_called_once()
	assert result["_method"] == "扫码登录"


# ── _verify_cookie 三个分支 ───────────────────────────────


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("httpx.get")
def test_verify_cookie_returns_true_when_code_zero(mock_get, mock_store_cls, tmp_path):
	mock_store_cls.return_value = _make_store()
	mock_resp = MagicMock()
	mock_resp.json.return_value = {"code": 0, "zpData": {"name": "tester"}}
	mock_get.return_value = mock_resp

	manager = AuthManager(tmp_path)
	result = manager._verify_cookie({"cookies": {"wt2": "abc"}, "user_agent": "UA"})
	assert result is True
	# 应使用 Cookie + UA
	call = mock_get.call_args
	assert call.kwargs["cookies"] == {"wt2": "abc"}
	assert "UA" in call.kwargs["headers"]["User-Agent"]


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("httpx.get")
def test_verify_cookie_returns_false_when_code_nonzero(mock_get, mock_store_cls, tmp_path):
	mock_store_cls.return_value = _make_store()
	mock_resp = MagicMock()
	mock_resp.json.return_value = {"code": 1001, "message": "need login"}
	mock_get.return_value = mock_resp

	manager = AuthManager(tmp_path)
	result = manager._verify_cookie({"cookies": {"wt2": "stale"}})
	assert result is False


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("httpx.get")
def test_verify_cookie_returns_false_on_http_error(mock_get, mock_store_cls, tmp_path):
	import httpx
	mock_store_cls.return_value = _make_store()
	mock_get.side_effect = httpx.ConnectError("no network")

	manager = AuthManager(tmp_path)
	result = manager._verify_cookie({"cookies": {"wt2": "x"}})
	assert result is False


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("httpx.get")
def test_verify_cookie_returns_false_on_value_error(mock_get, mock_store_cls, tmp_path):
	"""JSON 解析失败也要优雅返回 False。"""
	mock_store_cls.return_value = _make_store()
	mock_resp = MagicMock()
	mock_resp.json.side_effect = ValueError("not json")
	mock_get.return_value = mock_resp

	manager = AuthManager(tmp_path)
	assert manager._verify_cookie({"cookies": {"wt2": "x"}}) is False


# ── force_refresh 剩余分支 ────────────────────────────────


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("boss_agent_cli.auth.manager.refresh_stoken")
@patch("boss_agent_cli.auth.manager.refresh_stoken_via_cdp")
@patch("boss_agent_cli.auth.manager.probe_cdp")
def test_force_refresh_uses_headless_when_cdp_unavailable(
	mock_probe_cdp,
	mock_refresh_cdp,
	mock_refresh_stoken,
	mock_store_cls,
	tmp_path,
):
	"""CDP 不可用时降级到 headless refresh_stoken。"""
	current = {"cookies": {"wt2": "c"}, "stoken": "old", "user_agent": "UA"}
	store = _make_store(token=current.copy())
	mock_store_cls.return_value = store
	mock_probe_cdp.return_value = False
	mock_refresh_stoken.return_value = "fresh-token"

	manager = AuthManager(tmp_path)
	manager.force_refresh()

	mock_refresh_cdp.assert_not_called()
	mock_refresh_stoken.assert_called_once_with({"wt2": "c"}, "UA")
	assert manager._token["stoken"] == "fresh-token"


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("boss_agent_cli.auth.manager.refresh_stoken")
@patch("boss_agent_cli.auth.manager.probe_cdp")
def test_force_refresh_wraps_exception_in_tokenrefreshfailed(
	mock_probe_cdp,
	mock_refresh_stoken,
	mock_store_cls,
	tmp_path,
):
	current = {"cookies": {"wt2": "c"}, "stoken": "old", "user_agent": "UA"}
	store = _make_store(token=current.copy())
	mock_store_cls.return_value = store
	mock_probe_cdp.return_value = False
	mock_refresh_stoken.side_effect = RuntimeError("upstream 500")

	manager = AuthManager(tmp_path)
	with pytest.raises(TokenRefreshFailed, match="upstream 500"):
		manager.force_refresh()


# ── check_status / logout ─────────────────────────────────


@patch("boss_agent_cli.auth.manager.TokenStore")
def test_check_status_returns_loaded_token(mock_store_cls, tmp_path):
	token = {"cookies": {"wt2": "x"}, "stoken": "y"}
	store = _make_store(token=token)
	mock_store_cls.return_value = store

	manager = AuthManager(tmp_path)
	assert manager.check_status() == token


@patch("boss_agent_cli.auth.manager.TokenStore")
def test_check_status_returns_none_when_no_token(mock_store_cls, tmp_path):
	store = _make_store(token=None)
	mock_store_cls.return_value = store

	manager = AuthManager(tmp_path)
	assert manager.check_status() is None


@patch("boss_agent_cli.auth.manager.TokenStore")
def test_logout_clears_store_and_cached_token(mock_store_cls, tmp_path):
	token = {"cookies": {"wt2": "x"}, "stoken": "y"}
	store = _make_store(token=token)
	mock_store_cls.return_value = store

	manager = AuthManager(tmp_path)
	manager.get_token()  # populate cache
	assert manager._token is not None

	manager.logout()

	store.clear.assert_called_once()
	assert manager._token is None
