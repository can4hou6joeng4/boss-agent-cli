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
@patch("boss_agent_cli.auth.manager.login_via_cdp")
@patch("boss_agent_cli.auth.manager.extract_cookies")
def test_login_force_cdp_skips_cookie_extraction(mock_extract, mock_login_via_cdp, mock_store_cls, tmp_path):
	store = _make_store()
	mock_store_cls.return_value = store
	mock_login_via_cdp.return_value = {"cookies": {"wt2": "cdp-cookie"}, "stoken": "cdp-token"}

	manager = AuthManager(tmp_path)

	result = manager.login(timeout=45, cdp_url="http://127.0.0.1:9222", force_cdp=True)

	mock_extract.assert_not_called()
	mock_login_via_cdp.assert_called_once_with(cdp_url="http://127.0.0.1:9222", timeout=45, platform="zhipin")
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

	with patch.object(manager, "_verify_cookie", return_value=False), \
		patch("boss_agent_cli.auth.manager.qr_login_httpx", side_effect=RuntimeError("qr failed")):
		result = manager.login(timeout=30)

	mock_login_via_cdp.assert_called_once_with(cdp_url=None, timeout=30, platform="zhipin")
	mock_login_via_browser.assert_called_once_with(timeout=30, platform="zhipin")
	store.save.assert_called_once_with({"cookies": {"wt2": "browser-cookie"}, "stoken": "browser-token"})
	assert result["_method"] == "扫码登录"


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("boss_agent_cli.auth.manager.login_via_browser")
@patch("boss_agent_cli.auth.manager.probe_cdp")
@patch("boss_agent_cli.auth.manager.extract_cookies")
def test_login_uses_qr_httpx_when_cdp_unavailable(
	mock_extract,
	mock_probe_cdp,
	mock_login_via_browser,
	mock_store_cls,
	tmp_path,
):
	"""Cookie 失败 + CDP 不可用时，应尝试 QR httpx 登录。"""
	store = _make_store()
	mock_store_cls.return_value = store
	mock_extract.return_value = None
	mock_probe_cdp.return_value = False

	qr_token = {"cookies": {"wt2": "qr-cookie"}, "stoken": "", "user_agent": "ua"}

	manager = AuthManager(tmp_path)

	with patch("boss_agent_cli.auth.manager.qr_login_httpx", return_value=qr_token) as mock_qr:
		result = manager.login(timeout=30)

	mock_qr.assert_called_once_with(timeout=30)
	mock_login_via_browser.assert_not_called()
	store.save.assert_called_once_with(qr_token)
	assert result["_method"] == "QR httpx 登录"


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("boss_agent_cli.auth.manager.login_via_browser")
@patch("boss_agent_cli.auth.manager.login_via_cdp")
@patch("boss_agent_cli.auth.manager.probe_cdp")
@patch("boss_agent_cli.auth.manager.extract_cookies")
def test_zhilian_login_uses_valid_cookie_without_qr_or_browser(
	mock_extract,
	mock_probe_cdp,
	mock_login_via_cdp,
	mock_login_via_browser,
	mock_store_cls,
	tmp_path,
):
	store = _make_store()
	mock_store_cls.return_value = store
	cookie_token = {"cookies": {"zp_token": "cookie-token"}, "user_agent": "ua", "x_zp_client_id": "cid"}
	mock_extract.return_value = cookie_token

	manager = AuthManager(tmp_path, platform="zhilian")

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
def test_zhilian_login_falls_back_to_browser_when_cookie_and_cdp_unavailable(
	mock_extract,
	mock_probe_cdp,
	mock_login_via_cdp,
	mock_login_via_browser,
	mock_store_cls,
	tmp_path,
):
	store = _make_store()
	mock_store_cls.return_value = store
	mock_extract.return_value = None
	mock_probe_cdp.return_value = False
	mock_login_via_browser.return_value = {"cookies": {"zp_token": "browser-cookie"}, "user_agent": "ua"}

	manager = AuthManager(tmp_path, platform="zhilian")
	result = manager.login(timeout=25)

	mock_login_via_cdp.assert_not_called()
	mock_login_via_browser.assert_called_once_with(timeout=25, platform="zhilian")
	assert result["_method"] == "扫码登录"


@patch("boss_agent_cli.auth.manager.TokenStore")
def test_force_refresh_raises_when_no_token(mock_store_cls, tmp_path):
	store = _make_store(token=None)
	mock_store_cls.return_value = store
	manager = AuthManager(tmp_path)

	with pytest.raises(TokenRefreshFailed, match="无法刷新 Token，请重新登录"):
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
	mock_refresh_cdp.assert_called_once_with("http://127.0.0.1:9222")
	store.save.assert_called_once()
	saved_token = store.save.call_args.args[0]
	assert saved_token["stoken"] == "new-token"
	assert manager._token["stoken"] == "new-token"


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("boss_agent_cli.auth.manager.refresh_stoken")
@patch("boss_agent_cli.auth.manager.refresh_stoken_via_cdp")
@patch("boss_agent_cli.auth.manager.probe_cdp")
def test_force_refresh_does_not_mutate_loaded_token_when_save_fails(
	mock_probe_cdp,
	mock_refresh_cdp,
	mock_refresh_stoken,
	mock_store_cls,
	tmp_path,
):
	current = {"cookies": {"wt2": "cookie"}, "stoken": "old-token", "user_agent": "ua"}
	store = _make_store(token=current)
	store.save.side_effect = OSError("disk full")
	mock_store_cls.return_value = store
	mock_probe_cdp.return_value = False
	mock_refresh_stoken.return_value = "new-token"

	manager = AuthManager(tmp_path)

	with pytest.raises(TokenRefreshFailed, match="Token 刷新失败"):
		manager.force_refresh()

	mock_refresh_cdp.assert_not_called()
	mock_refresh_stoken.assert_called_once_with({"wt2": "cookie"}, "ua")
	assert current["stoken"] == "old-token"
	assert manager._token is None


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("boss_agent_cli.auth.manager.extract_cookies")
def test_zhilian_force_refresh_prefers_browser_cookie_extract(mock_extract, mock_store_cls, tmp_path):
	current = {"cookies": {"zp_token": "old"}, "user_agent": "ua"}
	store = _make_store(token=current.copy())
	mock_store_cls.return_value = store
	mock_extract.return_value = {"cookies": {"zp_token": "new"}, "user_agent": "ua", "x_zp_client_id": "cid"}

	manager = AuthManager(tmp_path, platform="zhilian")
	with patch.object(manager, "_verify_cookie", return_value=True):
		manager.force_refresh()

	mock_extract.assert_called_once_with(None, platform="zhilian")
	store.save.assert_called_once_with({"cookies": {"zp_token": "new"}, "user_agent": "ua", "x_zp_client_id": "cid"})


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("boss_agent_cli.auth.manager.login_via_cdp")
@patch("boss_agent_cli.auth.manager.extract_cookies")
def test_zhilian_force_refresh_falls_back_to_cdp(mock_extract, mock_login_via_cdp, mock_store_cls, tmp_path):
	current = {"cookies": {"zp_token": "old"}, "user_agent": "ua"}
	store = _make_store(token=current.copy())
	mock_store_cls.return_value = store
	mock_extract.return_value = None
	mock_login_via_cdp.return_value = {"cookies": {"zp_token": "fresh"}, "user_agent": "ua", "x_zp_client_id": "cid"}

	manager = AuthManager(tmp_path, platform="zhilian")
	with patch.object(manager, "_verify_cookie", return_value=True):
		manager.force_refresh(cdp_url="http://127.0.0.1:9222")

	mock_login_via_cdp.assert_called_once_with(cdp_url="http://127.0.0.1:9222", timeout=30, platform="zhilian")
