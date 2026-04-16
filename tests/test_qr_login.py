"""Tests for auth/qr_login.py — QR code httpx login flow."""
from unittest.mock import MagicMock, patch
import pytest


def _mock_httpx_client():
	"""Create a mock httpx client with cookie jar."""
	client = MagicMock()
	client.__enter__ = MagicMock(return_value=client)
	client.__exit__ = MagicMock(return_value=False)

	# Mock cookie jar
	mock_cookie = MagicMock()
	mock_cookie.domain = ".zhipin.com"
	mock_cookie.name = "wt2"
	mock_cookie.value = "test_wt2"
	client.cookies.jar = [mock_cookie]

	return client


def _mock_response(data):
	resp = MagicMock()
	resp.json.return_value = data
	resp.raise_for_status = MagicMock()
	return resp


@patch("boss_agent_cli.auth.qr_login.httpx.Client")
def test_qr_login_success(mock_client_cls):
	"""完整 QR 登录流程：randkey → getqrcode → scan(confirmed) → dispatcher → cookies。"""
	from boss_agent_cli.auth.qr_login import qr_login_httpx

	client = _mock_httpx_client()
	mock_client_cls.return_value = client

	# randkey 返回
	randkey_resp = _mock_response({"code": 0, "zpData": {"randKey": "rk123"}})
	# getqrcode 返回
	qrcode_resp = _mock_response({"code": 0, "zpData": {"qrId": "qr456"}})
	# scan 轮询：第一次未扫，第二次确认
	scan_not_yet = _mock_response({"code": 0})
	scan_confirmed = _mock_response({"code": 2})
	# dispatcher 返回
	dispatcher_resp = _mock_response({"code": 0, "zpData": {}})

	client.get.side_effect = [randkey_resp, qrcode_resp, scan_not_yet, scan_confirmed, dispatcher_resp]

	result = qr_login_httpx(timeout=30)
	assert result["cookies"]["wt2"] == "test_wt2"
	assert result["stoken"] == ""
	assert result["user_agent"] != ""


@patch("boss_agent_cli.auth.qr_login.httpx.Client")
def test_qr_login_randkey_fail(mock_client_cls):
	"""randkey 请求失败时应抛出 RuntimeError。"""
	from boss_agent_cli.auth.qr_login import qr_login_httpx

	client = _mock_httpx_client()
	mock_client_cls.return_value = client

	client.get.return_value = _mock_response({"code": 1, "message": "fail"})

	with pytest.raises(RuntimeError, match="randkey"):
		qr_login_httpx(timeout=5)


@patch("boss_agent_cli.auth.qr_login.httpx.Client")
def test_qr_login_expired(mock_client_cls):
	"""二维码过期应抛出 RuntimeError。"""
	from boss_agent_cli.auth.qr_login import qr_login_httpx

	client = _mock_httpx_client()
	mock_client_cls.return_value = client

	randkey_resp = _mock_response({"code": 0, "zpData": {"randKey": "rk123"}})
	qrcode_resp = _mock_response({"code": 0, "zpData": {"qrId": "qr456"}})
	scan_expired = _mock_response({"code": 3})

	client.get.side_effect = [randkey_resp, qrcode_resp, scan_expired]

	with pytest.raises(RuntimeError, match="过期"):
		qr_login_httpx(timeout=30)


@patch("boss_agent_cli.auth.qr_login.httpx.Client")
def test_qr_login_no_wt2(mock_client_cls):
	"""登录完成但无 wt2 Cookie 时应报错。"""
	from boss_agent_cli.auth.qr_login import qr_login_httpx

	client = _mock_httpx_client()
	mock_client_cls.return_value = client
	# 清空 cookie jar 中的 wt2
	empty_cookie = MagicMock()
	empty_cookie.domain = ".zhipin.com"
	empty_cookie.name = "other"
	empty_cookie.value = "val"
	client.cookies.jar = [empty_cookie]

	randkey_resp = _mock_response({"code": 0, "zpData": {"randKey": "rk123"}})
	qrcode_resp = _mock_response({"code": 0, "zpData": {"qrId": "qr456"}})
	scan_confirmed = _mock_response({"code": 2})
	dispatcher_resp = _mock_response({"code": 0, "zpData": {}})

	client.get.side_effect = [randkey_resp, qrcode_resp, scan_confirmed, dispatcher_resp]

	with pytest.raises(RuntimeError, match="wt2"):
		qr_login_httpx(timeout=30)
