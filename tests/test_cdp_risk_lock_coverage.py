"""CDP code 37 风控锁覆盖到聊天页动作与 login --cdp（全部离线：CDP、浏览器、平台都是替身）。"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.api import browser_client, cdp_risk_lock
from boss_agent_cli.api.browser_client import _PageRiskResponseTracker, read_cdp_stoken_hash
from boss_agent_cli.api.client import EnvironmentRiskError, EnvironmentRiskLockedError
from boss_agent_cli.api.recruiter_client import BossRecruiterClient
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.main import cli
from boss_agent_cli.platforms.zhipin_recruiter import BossRecruiterPlatform

OLD_HASH = cdp_risk_lock.hash_stoken("blocked-stoken")
NEW_HASH = cdp_risk_lock.hash_stoken("fresh-stoken")
FRIEND_DETAIL = {
	"code": 0,
	"zpData": {"friendList": [{"uid": 1, "encryptUid": "u", "encryptJobId": "j", "securityId": "s", "name": "T", "friendSource": 0}]},
}
HISTORY = {"code": 0, "zpData": {"messages": [{"mid": 1, "time": 1, "from": {"uid": 9}, "body": {"text": "hi"}}]}}


class FakeAuth:
	def __init__(self, data_dir: Path) -> None:
		self.data_dir = data_dir


def _lock(tmp_path: Path) -> None:
	cdp_risk_lock.write_lock(tmp_path, OLD_HASH)


def _client(tmp_path: Path, *, events: list[dict[str, Any]] | None = None, page: dict[str, Any] | None = None):
	client = BossRecruiterClient(FakeAuth(tmp_path), cdp_url="http://127.0.0.1:9222")  # type: ignore[arg-type]
	client.friend_detail = MagicMock(return_value=FRIEND_DETAIL)  # type: ignore[method-assign]
	client.chat_history = MagicMock(return_value=HISTORY)  # type: ignore[method-assign]
	browser = MagicMock()
	browser.evaluate_js_with_chat_events.return_value = {
		"value": page or {"ok": True, "componentName": "ExchangeWx", "confirmed": True, "log": []},
		"events": events or [],
	}
	client._get_browser = MagicMock(return_value=browser)  # type: ignore[method-assign]
	return client, browser


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
	from boss_agent_cli.api import recruiter_client

	monkeypatch.setattr(recruiter_client.time, "sleep", lambda _s: None)


# ── 页面响应风控跟踪 ─────────────────────────────────────────────


def _feed(tracker: _PageRiskResponseTracker, url: str, body: str, *, mime="application/json", b64=False):
	assert tracker.on_message({"method": "Network.responseReceived", "params": {"requestId": "r1", "response": {"url": url, "mimeType": mime}}}) is None
	command = tracker.on_message({"method": "Network.loadingFinished", "params": {"requestId": "r1"}})
	if command is None:
		return None
	assert command["method"] == "Network.getResponseBody"
	assert tracker.handles({"id": command["id"]})
	payload = base64.b64encode(body.encode()).decode() if b64 else body
	tracker.on_message({"id": command["id"], "result": {"body": payload, "base64Encoded": b64}})
	return command


def test_tracker_keeps_only_risk_codes_and_minimal_fields():
	tracker = _PageRiskResponseTracker()
	_feed(tracker, "https://www.zhipin.com/wapi/zpchat/exchange/test?x=secret", json.dumps({"code": 37, "message": "您的访问环境存在异常"}))
	_feed(tracker, "https://www.zhipin.com/wapi/zpchat/ok", json.dumps({"code": 0, "zpData": {"phone": "138"}}))
	_feed(tracker, "https://www.zhipin.com/wapi/zpchat/b64", json.dumps({"code": 36, "message": "账户异常"}), b64=True)
	assert tracker.events == [
		{"kind": "http_risk", "path": "/wapi/zpchat/exchange/test", "code": 37, "message": "您的访问环境存在异常"},
		{"kind": "http_risk", "path": "/wapi/zpchat/b64", "code": 36, "message": "账户异常"},
	]


def test_tracker_ignores_non_wapi_and_non_json():
	tracker = _PageRiskResponseTracker()
	assert _feed(tracker, "https://www.zhipin.com/web/chat/index", "{}") is None
	assert _feed(tracker, "https://www.zhipin.com/wapi/x", "{}", mime="text/html") is None
	assert tracker.events == []


def test_read_cdp_stoken_hash_uses_storage_get_cookies_and_fails_closed():
	class FakeWs:
		def __init__(self):
			self.sent = []

		def __enter__(self):
			return self

		def __exit__(self, *a):
			return None

		def send(self, raw):
			self.sent.append(json.loads(raw))

		def recv(self, timeout=None):
			return json.dumps({"id": 1, "result": {"cookies": [{"name": "__zp_stoken__", "value": "fresh-stoken", "domain": ".zhipin.com"}]}})

	ws = FakeWs()
	response = MagicMock()
	response.__enter__ = lambda self: self
	response.__exit__ = lambda self, *a: None
	response.read.return_value = json.dumps({"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/x"}).encode()
	with patch("urllib.request.urlopen", return_value=response), \
		patch("websockets.sync.client.connect", return_value=ws):
		assert read_cdp_stoken_hash("http://127.0.0.1:9222") == NEW_HASH
	assert ws.sent == [{"id": 1, "method": "Storage.getCookies"}]
	with patch("urllib.request.urlopen", side_effect=OSError("down")):
		assert read_cdp_stoken_hash("http://127.0.0.1:9222") is None


# ── 聊天页动作 ───────────────────────────────────────────────────


@pytest.mark.parametrize("action", ["exchange", "reply"])
def test_locked_chat_page_action_is_refused_before_page_runs(tmp_path, action):
	_lock(tmp_path)
	client, browser = _client(tmp_path)
	with patch.object(browser_client, "read_cdp_stoken_hash", return_value=OLD_HASH), pytest.raises(EnvironmentRiskLockedError):
		if action == "exchange":
			client.exchange_request_by_friend(1, exchange_type=2)
		else:
			client.send_message_by_friend(1, "你好")
	browser.evaluate_js_with_chat_events.assert_not_called()
	assert (tmp_path / cdp_risk_lock.LOCK_FILENAME).exists()


def test_unreadable_stoken_keeps_chat_page_locked(tmp_path):
	_lock(tmp_path)
	client, browser = _client(tmp_path)
	with patch.object(browser_client, "read_cdp_stoken_hash", return_value=None), pytest.raises(EnvironmentRiskLockedError):
		client.send_message_by_friend(1, "你好")
	browser.evaluate_js_with_chat_events.assert_not_called()


def test_changed_stoken_unlocks_chat_page_action(tmp_path):
	_lock(tmp_path)
	client, browser = _client(tmp_path, events=[{"kind": "ws_send", "bytes": 194, "utf8_bits": ["你好呀"]}])
	with patch.object(browser_client, "read_cdp_stoken_hash", return_value=NEW_HASH):
		assert client.send_message_by_friend(1, "你好呀")["code"] == 0
	assert not (tmp_path / cdp_risk_lock.LOCK_FILENAME).exists()
	browser.evaluate_js_with_chat_events.assert_called_once()


def test_page_code37_during_exchange_records_lock_and_maps_to_environment_risk(tmp_path):
	events = [{"kind": "http_risk", "path": "/wapi/zpchat/exchange/test", "code": 37, "message": "您的访问环境存在异常"}]
	client, _ = _client(tmp_path, events=events)
	with patch.object(browser_client, "read_cdp_stoken_hash", return_value=OLD_HASH):
		result = client.exchange_request_by_friend(1, exchange_type=2)
	assert result["code"] == 37
	assert result["__cli_error_details__"]["page_risk"] == {"path": "/wapi/zpchat/exchange/test", "code": 37}
	assert BossRecruiterPlatform(client).parse_error(result)[0] == "ENVIRONMENT_RISK"
	lock = cdp_risk_lock.read_lock(tmp_path)
	assert lock is not None and lock.stoken_sha256 == OLD_HASH
	# 之后同一 stoken 的任何聊天页动作都会被拦下
	with patch.object(browser_client, "read_cdp_stoken_hash", return_value=OLD_HASH), pytest.raises(EnvironmentRiskLockedError):
		client.send_message_by_friend(1, "x")


def test_page_code36_maps_to_account_risk_without_lock(tmp_path):
	events = [{"kind": "http_risk", "path": "/wapi/zpchat/x", "code": 36, "message": "账户存在异常行为"}]
	client, _ = _client(tmp_path, events=events)
	result = client.send_message_by_friend(1, "你好")
	assert result["code"] == 36
	assert BossRecruiterPlatform(client).parse_error(result)[0] == "ACCOUNT_RISK"
	assert not (tmp_path / cdp_risk_lock.LOCK_FILENAME).exists()


def test_no_lock_means_no_cdp_cookie_read(tmp_path):
	client, _ = _client(tmp_path, events=[{"kind": "ws_send", "bytes": 194, "utf8_bits": ["你好呀"]}])
	with patch.object(browser_client, "read_cdp_stoken_hash", side_effect=AssertionError("不应读取")):
		assert client.send_message_by_friend(1, "你好呀")["code"] == 0


# ── login --cdp ──────────────────────────────────────────────────


def _auth(tmp_path: Path) -> AuthManager:
	return AuthManager(tmp_path, platform="zhipin")


TOKEN = {"cookies": {"wt2": "w"}, "stoken": "s", "user_agent": "ua"}


def test_login_cdp_blocked_while_locked(tmp_path):
	_lock(tmp_path)
	with patch("boss_agent_cli.auth.manager.probe_cdp", return_value="ws://x"), \
		patch.object(browser_client, "read_cdp_stoken_hash", return_value=OLD_HASH), \
		patch("boss_agent_cli.auth.manager.login_via_cdp") as login_via_cdp:
		with pytest.raises(EnvironmentRiskLockedError) as exc_info:
			_auth(tmp_path).login(force_cdp=True)
	login_via_cdp.assert_not_called()
	assert "boss clean --risk-lock" in str(exc_info.value)
	assert "--ignore-risk-lock" in str(exc_info.value)


def test_login_auto_path_blocked_before_cdp_login(tmp_path):
	_lock(tmp_path)
	with patch("boss_agent_cli.auth.manager.extract_cookies", return_value=None), \
		patch("boss_agent_cli.auth.manager.probe_cdp", return_value="ws://x"), \
		patch.object(browser_client, "read_cdp_stoken_hash", return_value=OLD_HASH), \
		patch("boss_agent_cli.auth.manager.login_via_cdp") as login_via_cdp, \
		patch("boss_agent_cli.auth.manager.qr_login_httpx") as qr:
		with pytest.raises(EnvironmentRiskLockedError):
			_auth(tmp_path).login()
	login_via_cdp.assert_not_called()
	qr.assert_not_called()


def test_login_cdp_unlocks_when_stoken_changed(tmp_path):
	_lock(tmp_path)
	with patch("boss_agent_cli.auth.manager.probe_cdp", return_value="ws://x"), \
		patch.object(browser_client, "read_cdp_stoken_hash", return_value=NEW_HASH), \
		patch("boss_agent_cli.auth.manager.login_via_cdp", return_value=dict(TOKEN)):
		_auth(tmp_path).login(force_cdp=True)
	assert not (tmp_path / cdp_risk_lock.LOCK_FILENAME).exists()


def test_login_cdp_ignore_flag_proceeds_and_keeps_lock(tmp_path):
	_lock(tmp_path)
	with patch("boss_agent_cli.auth.manager.probe_cdp", return_value="ws://x"), \
		patch.object(browser_client, "read_cdp_stoken_hash", side_effect=AssertionError("不应读取")), \
		patch("boss_agent_cli.auth.manager.login_via_cdp", return_value=dict(TOKEN)) as login_via_cdp:
		_auth(tmp_path).login(force_cdp=True, ignore_risk_lock=True)
	login_via_cdp.assert_called_once()
	assert (tmp_path / cdp_risk_lock.LOCK_FILENAME).exists()


def test_login_probe_code37_records_lock(tmp_path):
	with patch("boss_agent_cli.auth.manager.probe_cdp", return_value="ws://x"), \
		patch.object(browser_client, "read_cdp_stoken_hash", return_value=OLD_HASH), \
		patch("boss_agent_cli.auth.manager.login_via_cdp", side_effect=EnvironmentRiskError("code 37", is_cdp=True)):
		with pytest.raises(EnvironmentRiskError):
			_auth(tmp_path).login(force_cdp=True)
	lock = cdp_risk_lock.read_lock(tmp_path)
	assert lock is not None and lock.stoken_sha256 == OLD_HASH


def test_login_cli_envelope_while_locked(tmp_path):
	_lock(tmp_path)
	with patch("boss_agent_cli.auth.manager.probe_cdp", return_value="ws://x"), \
		patch.object(browser_client, "read_cdp_stoken_hash", return_value=OLD_HASH), \
		patch("boss_agent_cli.auth.manager.login_via_cdp") as login_via_cdp:
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "login", "--cdp"])
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "ENVIRONMENT_RISK_LOCKED"
	assert payload["error"]["recoverable"] is False
	login_via_cdp.assert_not_called()
