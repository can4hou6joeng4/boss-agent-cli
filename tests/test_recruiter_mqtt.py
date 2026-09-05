"""MQTT bootstrap、回调与单连接生命周期，不连接真实平台。"""

import subprocess
import sys
import time
import re
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import paho.mqtt.client as mqtt
import pytest

from boss_agent_cli.api.recruiter_client import BossRecruiterClient
from boss_agent_cli.api.recruiter_mqtt import RecruiterMqttCredentials, RecruiterMqttError, encode_presence, mark_chat_read

_CREDENTIALS = RecruiterMqttCredentials("chat.zhipin.com", "user", "fresh-wt", 1, "uid")


class FakeMqttClient:
	def __init__(self, *, rejected=False, loop_error=False, publish_error=False, pending=False):
		self.rejected = rejected
		self.loop_error = loop_error
		self.publish_error = publish_error
		self.pending = pending
		self.disconnected = False
		self.on_connect = None
		self.packets = []
		self.connected = False

	def username_pw_set(self, *args):
		self.auth = args

	def tls_set(self):
		pass

	def ws_set_options(self, **kwargs):
		self.ws_options = kwargs

	def connect(self, *args, **kwargs):
		self.connect_args = args, kwargs

	def loop(self, **kwargs):
		if not self.connected:
			self.connected = True
			self.on_connect(self, None, None, SimpleNamespace(is_failure=self.rejected), None)
		if self.pending:
			raise TimeoutError("deadline")
		return mqtt.MQTT_ERR_CONN_LOST if self.loop_error else mqtt.MQTT_ERR_SUCCESS

	def publish(self, topic, payload, **kwargs):
		self.packets.append((topic, payload, kwargs))
		return SimpleNamespace(rc=mqtt.MQTT_ERR_NO_CONN if self.publish_error else mqtt.MQTT_ERR_SUCCESS, is_published=lambda: True)

	def disconnect(self):
		self.disconnected = True


def _send(client, credentials=_CREDENTIALS):
	with patch("paho.mqtt.client.Client", return_value=client) as factory:
		result = mark_chat_read(
			credentials, cookies={"tracking": "private", "wt2": "stale", "zp_at": "session"},
			user_agent="test", peer_uid=2, message_id=3, deadline=time.monotonic() + 5,
		)
		assert factory.call_args.kwargs["reconnect_on_failure"] is False
		assert factory.call_args.kwargs["clean_session"] is True
		assert re.fullmatch(r"ws-[0-9A-F]{16}", factory.call_args.kwargs["client_id"])
		return result


def test_mqtt_publishes_presence_before_read_and_preserves_cookie_values():
	client = FakeMqttClient()
	result = _send(client)
	assert result["published"] is True
	assert "cleared" not in result
	assert len(client.packets) == 2
	assert client.packets[0][1].startswith(bytes.fromhex("080222"))
	assert client.packets[1][1].startswith(bytes.fromhex("080642"))
	assert all(packet[0] == "chat" and packet[2] == {"qos": 1, "retain": True} for packet in client.packets)
	assert client.auth == ("user", "fresh-wt")
	assert client.ws_options["headers"]["Cookie"] == "tracking=private; wt2=stale; zp_at=session"
	assert client.disconnected


def test_mqtt_cookie_jar_uses_websocket_host_and_path():
	cookies = httpx.Cookies()
	cookies.set("wt2", "cookie-wt", domain=".zhipin.com", path="/")
	cookies.set("sid", "browser-model", domain=".zhipin.com", path="/")
	cookies.set("www_only", "excluded", domain="www.zhipin.com", path="/")
	cookies.set("page_only", "excluded", domain=".zhipin.com", path="/web")
	cookies.set("other_site", "excluded", domain="example.test", path="/")
	client = FakeMqttClient()
	with patch("paho.mqtt.client.Client", return_value=client):
		mark_chat_read(_CREDENTIALS, cookies=cookies, user_agent="native-ua", peer_uid=2, message_id=3)
	headers = client.ws_options["headers"]
	assert headers["Cookie"] == "wt2=cookie-wt; sid=browser-model"
	assert headers["Sec-Websocket-Protocol"] == "fresh-wt"
	assert headers["User-Agent"] == "native-ua"
	assert headers["Origin"] == "https://www.zhipin.com"
	assert cookies.get("wt2") == "cookie-wt"


@pytest.mark.parametrize("cookies,user_agent", [({"wt2": "bad\r\nInjected: value"}, "test"), ({}, "bad\nheader")])
def test_mqtt_rejects_injected_headers_before_connect(cookies, user_agent):
	with patch("paho.mqtt.client.Client") as client:
		with pytest.raises(RecruiterMqttError, match="headers"):
			mark_chat_read(_CREDENTIALS, cookies=cookies, user_agent=user_agent, peer_uid=2, message_id=3)
		client.assert_not_called()


@pytest.mark.parametrize("budget,keepalive", [(5, 5), (30, 25)])
def test_mqtt_preserves_heartbeat_budget_but_caps_connect_timeout(budget, keepalive):
	client = FakeMqttClient()
	with patch("paho.mqtt.client.Client", return_value=client), patch("boss_agent_cli.api.recruiter_mqtt.time.monotonic", return_value=100):
		mark_chat_read(_CREDENTIALS, cookies={}, user_agent="test", peer_uid=2, message_id=3, deadline=100 + budget)
	assert client.connect_args == (("chat.zhipin.com",), {"port": 443, "keepalive": keepalive})
	assert client.connect_timeout == min(10, budget)


def test_presence_encodes_web_client_fields_including_empty_strings():
	payload = encode_presence(user_id=1, uniqid="ba", client_ip="ip", model="pc")
	assert payload == bytes.fromhex(
		"08022240080110011a38"
		"0a04342e393212001a00220270632a0262613202697038ba4642037765624a022d3152005a00"
		"6100000000000000006900000000000000002800"
	)


def test_mqtt_handshake_uses_wt_as_single_websocket_subprotocol():
	client = FakeMqttClient()
	_send(client)
	socket = MagicMock()
	socket.recv.return_value = b""
	# 使用真实 Paho 握手编码；假 socket 只记录请求，不连接任何服务。
	with pytest.raises(mqtt.WebsocketConnectionError):
		mqtt._WebsocketWrapper(socket, "chat.zhipin.com", 443, True, **{
			"path": client.ws_options["path"], "extra_headers": client.ws_options["headers"],
		})
	headers = socket.send.call_args.args[0].decode().split("\r\n")
	protocols = [line.split(":", 1)[1].strip() for line in headers if line.lower().startswith("sec-websocket-protocol:")]
	assert protocols == ["fresh-wt"]


@pytest.mark.parametrize("password", ["", "bad\r\nInjected: value", "two tokens", "two,tokens", "non-ascii-中"])
def test_mqtt_rejects_invalid_websocket_subprotocol_before_connect(password):
	with patch("paho.mqtt.client.Client") as client:
		with pytest.raises(RecruiterMqttError, match="subprotocol"):
			mark_chat_read(replace(_CREDENTIALS, password=password), cookies={}, user_agent="test", peer_uid=2, message_id=3)
		client.assert_not_called()


@pytest.mark.parametrize("options", [{"rejected": True}, {"loop_error": True}, {"publish_error": True}])
def test_mqtt_failure_disconnects_without_retry(options):
	client = FakeMqttClient(**options)
	with pytest.raises(RecruiterMqttError):
		_send(client)
	assert client.disconnected
	assert len(client.packets) <= 1


def test_mqtt_timeout_disconnects_without_publish():
	client = FakeMqttClient(pending=True)
	with pytest.raises(TimeoutError):
		_send(client)
	assert client.disconnected
	assert not client.packets


@pytest.mark.parametrize("host", ["zhipin.com.evil.test", "evilzhipin.com", "127.0.0.1", "https://chat.zhipin.com", "chat.zhipin.com:443", "x..zhipin.com"])
def test_mqtt_rejects_untrusted_hosts_before_connect(host):
	with patch("paho.mqtt.client.Client") as client:
		with pytest.raises(RecruiterMqttError, match="subdomain"):
			_send(FakeMqttClient(), replace(_CREDENTIALS, server=host))
		client.assert_not_called()


def _bootstrap_client(responses):
	client = object.__new__(BossRecruiterClient)
	client._request = MagicMock(side_effect=responses)
	client._auth = MagicMock()
	client._auth.get_token.return_value = {"user_agent": "test"}
	client._get_client = MagicMock(return_value=SimpleNamespace(cookies=httpx.Cookies({"zp_at": "session", "__a": "123.456.789", "sid": "browser-model"})))
	return client


def _batch():
	return {"code": 0, "zpData": {
		"/wapi/zppassport/get/wt": {"code": 0, "zpData": {"wt2": "fresh-wt"}},
		"/wapi/zpuser/wap/getUserInfo.json": {"code": 0, "zpData": {"token": "user", "userId": 1}},
	}}


def test_bootstrap_preserves_deadline_and_authentication():
	client = _bootstrap_client([_batch(), {"code": 0, "zpData": {"result": ["chat.zhipin.com"]}}])
	deadline = time.monotonic() + 10
	with patch("boss_agent_cli.api.recruiter_mqtt.mark_chat_read", return_value={"published": True}) as send:
		result = client.mark_read(peer_uid=2, message_id=3, deadline=deadline, allow_mqtt_session=True)
	assert result["zpData"] == {"published": True}
	assert all(call.kwargs["deadline"] == deadline for call in client._request.call_args_list)
	assert send.call_args.kwargs["deadline"] == deadline
	assert send.call_args.args[0].password == "fresh-wt"
	assert send.call_args.args[0].uniqid == "456123"
	assert send.call_args.args[0].model == "browser-model"
	assert send.call_args.kwargs["cookies"] is client._get_client().cookies


def test_bootstrap_missing_presence_cookies_does_not_use_user_id():
	client = _bootstrap_client([_batch(), {"code": 0, "zpData": {"result": ["chat.zhipin.com"]}}])
	client._get_client().cookies.clear()
	with patch("boss_agent_cli.api.recruiter_mqtt.mark_chat_read", return_value={"published": True}) as send:
		client.mark_read(peer_uid=2, message_id=3, allow_mqtt_session=True)
	assert send.call_args.args[0].uniqid == ""
	assert send.call_args.args[0].model == ""


@pytest.mark.parametrize("stage", ["batch", "subrequest", "config"])
def test_bootstrap_risk_response_is_not_flattened(stage):
	risk = {"code": 36, "message": "risk"}
	batch = _batch()
	if stage == "subrequest":
		batch["zpData"]["/wapi/zppassport/get/wt"] = risk
	responses = [risk] if stage == "batch" else [batch, risk]
	client = _bootstrap_client(responses)
	with patch("boss_agent_cli.api.recruiter_mqtt.mark_chat_read") as send:
		assert client.mark_read(peer_uid=2, message_id=3, allow_mqtt_session=True) == risk
		send.assert_not_called()


def test_candidate_cli_import_does_not_load_paho():
	result = subprocess.run(
		[sys.executable, "-c", "import sys; import boss_agent_cli.main; assert not any(n.startswith('paho') for n in sys.modules)"],
		capture_output=True, text=True, timeout=20,
	)
	assert result.returncode == 0, result.stderr
