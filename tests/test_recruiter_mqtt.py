"""MQTT bootstrap、回调与单连接生命周期，不连接真实平台。"""

import subprocess
import sys
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import paho.mqtt.client as mqtt
import pytest

from boss_agent_cli.api.recruiter_client import BossRecruiterClient
from boss_agent_cli.api.recruiter_mqtt import RecruiterMqttCredentials, RecruiterMqttError, mark_chat_read

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
		return result


def test_mqtt_publishes_once_and_filters_handshake_cookies():
	client = FakeMqttClient()
	result = _send(client)
	assert result["published"] is True
	assert "cleared" not in result
	assert len(client.packets) == 2
	assert client.auth == ("user", "fresh-wt")
	assert client.ws_options["headers"]["Cookie"] == "wt2=fresh-wt; zp_at=session"
	assert client.disconnected


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
	client._get_client = MagicMock(return_value=SimpleNamespace(cookies=httpx.Cookies({"zp_at": "session"})))
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
		result = client.mark_read(peer_uid=2, message_id=3, deadline=deadline)
	assert result["zpData"] == {"published": True}
	assert all(call.kwargs["deadline"] == deadline for call in client._request.call_args_list)
	assert send.call_args.kwargs["deadline"] == deadline
	assert send.call_args.args[0].password == "fresh-wt"


@pytest.mark.parametrize("stage", ["batch", "subrequest", "config"])
def test_bootstrap_risk_response_is_not_flattened(stage):
	risk = {"code": 36, "message": "risk"}
	batch = _batch()
	if stage == "subrequest":
		batch["zpData"]["/wapi/zppassport/get/wt"] = risk
	responses = [risk] if stage == "batch" else [batch, risk]
	client = _bootstrap_client(responses)
	with patch("boss_agent_cli.api.recruiter_mqtt.mark_chat_read") as send:
		assert client.mark_read(peer_uid=2, message_id=3) == risk
		send.assert_not_called()


def test_candidate_cli_import_does_not_load_paho():
	result = subprocess.run(
		[sys.executable, "-c", "import sys; import boss_agent_cli.main; assert not any(n.startswith('paho') for n in sys.modules)"],
		capture_output=True, text=True, timeout=20,
	)
	assert result.returncode == 0, result.stderr
