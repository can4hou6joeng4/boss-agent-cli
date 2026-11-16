"""认证与 CDP 探测的响应解析必须吸收深嵌套 JSON 的 RecursionError（Issue #1378）。

``resp.json()`` 对极深嵌套正文会抛 ``RecursionError``；逃出后 Cookie 探测会退化成
通用网络错误、CDP 探测会冒泡成探测异常。两处都按「响应无法解析」处理：Cookie
校验视为失败并走既有降级/报错路径，CDP 探测视为拿不到调试端口。
"""

from unittest.mock import MagicMock, patch

import pytest

from boss_agent_cli.auth.browser import probe_cdp
from boss_agent_cli.auth.manager import AuthManager, AuthRequired


_CURL = "curl 'https://www.zhipin.com/wapi/test' -b 'wt2=test-session; __zp_stoken__=test-stoken'"


def _make_store() -> MagicMock:
	store = MagicMock()
	store.load.return_value = None
	lock = MagicMock()
	lock.__enter__.return_value = None
	lock.__exit__.return_value = None
	store.refresh_lock.return_value = lock
	return store


def _deep_nested_response() -> MagicMock:
	"""模拟 httpx 响应：解析深嵌套正文时 ``json()`` 抛 RecursionError。"""
	response = MagicMock()
	response.json.side_effect = RecursionError("maximum recursion depth exceeded while decoding")
	return response


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("httpx.get")
def test_verify_cookie_recursion_error_is_verification_failure(mock_get, mock_store_cls, tmp_path):
	"""Cookie 探测响应无法解析时按校验失败处理，不把 RecursionError 抛给调用方。"""
	mock_store_cls.return_value = _make_store()
	mock_get.return_value = _deep_nested_response()

	manager = AuthManager(tmp_path)

	assert manager._verify_cookie({"cookies": {"wt2": "x"}}) is False


@patch("httpx.get")
def test_probe_cdp_recursion_error_reports_unavailable(mock_get):
	"""CDP 探测响应无法解析时按拿不到调试端口处理，返回 None 而不是冒泡。"""
	mock_get.return_value = _deep_nested_response()

	assert probe_cdp() is None


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("httpx.get")
def test_import_curl_recursion_error_raises_auth_required(mock_get, mock_store_cls, tmp_path):
	"""校验失败走既有报错路径：cURL 导入得到 AuthRequired，而不是 RecursionError。"""
	mock_store_cls.return_value = _make_store()
	mock_get.return_value = _deep_nested_response()

	manager = AuthManager(tmp_path)

	with pytest.raises(AuthRequired):
		manager.import_curl(_CURL)


@patch("boss_agent_cli.auth.manager.TokenStore")
@patch("boss_agent_cli.auth.manager.login_via_cdp")
@patch("boss_agent_cli.auth.manager.probe_cdp")
@patch("boss_agent_cli.auth.manager.extract_cookies")
@patch("httpx.get")
def test_login_recursion_error_degrades_to_cdp(
	mock_get, mock_extract, mock_probe_cdp, mock_login_via_cdp, mock_store_cls, tmp_path
):
	"""Cookie 校验因深嵌套响应失败时走既有降级路径，改由 CDP 登录。"""
	store = _make_store()
	mock_store_cls.return_value = store
	mock_extract.return_value = {"cookies": {"wt2": "cookie-token"}, "stoken": "cookie-stoken"}
	mock_probe_cdp.return_value = "ws://127.0.0.1:9222/devtools/browser"
	mock_login_via_cdp.return_value = {"cookies": {"wt2": "cdp-cookie"}, "stoken": "cdp-token"}
	mock_get.return_value = _deep_nested_response()

	manager = AuthManager(tmp_path)

	result = manager.login()

	assert result["_method"] == "CDP 扫码"
	mock_login_via_cdp.assert_called_once()
