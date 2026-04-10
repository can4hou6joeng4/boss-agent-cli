"""Tests for bridge module — protocol, client, daemon utilities."""

from unittest.mock import patch

from boss_agent_cli.bridge.protocol import (
	BridgeCommand, BridgeResult, make_command_id,
	DEFAULT_PORT, BRIDGE_HOST,
)


# ── protocol tests ─────────────────────────────────────────────────


class TestBridgeCommand:
	def test_to_dict_minimal(self):
		cmd = BridgeCommand(id="cmd_1", action="exec")
		d = cmd.to_dict()
		assert d == {"id": "cmd_1", "action": "exec", "workspace": "boss"}

	def test_to_dict_with_code(self):
		cmd = BridgeCommand(id="cmd_2", action="exec", code="return 1+1")
		d = cmd.to_dict()
		assert d["code"] == "return 1+1"

	def test_to_dict_with_url(self):
		cmd = BridgeCommand(id="cmd_3", action="navigate", url="https://example.com")
		d = cmd.to_dict()
		assert d["url"] == "https://example.com"

	def test_to_dict_with_tab_id(self):
		cmd = BridgeCommand(id="cmd_4", action="exec", tab_id=42)
		d = cmd.to_dict()
		assert d["tabId"] == 42

	def test_to_dict_omits_empty_fields(self):
		cmd = BridgeCommand(id="cmd_5", action="exec", code="", url="", domain="")
		d = cmd.to_dict()
		assert "code" not in d
		assert "url" not in d
		assert "domain" not in d

	def test_to_dict_with_domain(self):
		cmd = BridgeCommand(id="cmd_6", action="cookies", domain="zhipin.com")
		d = cmd.to_dict()
		assert d["domain"] == "zhipin.com"


class TestBridgeResult:
	def test_from_dict_success(self):
		r = BridgeResult.from_dict({"id": "r1", "ok": True, "data": {"key": "val"}})
		assert r.ok is True
		assert r.data == {"key": "val"}
		assert r.error == ""

	def test_from_dict_error(self):
		r = BridgeResult.from_dict({"id": "r2", "ok": False, "error": "timeout"})
		assert r.ok is False
		assert r.error == "timeout"

	def test_from_dict_missing_fields(self):
		r = BridgeResult.from_dict({})
		assert r.id == ""
		assert r.ok is False
		assert r.data is None
		assert r.error == ""


class TestMakeCommandId:
	def test_format(self):
		cid = make_command_id()
		assert cid.startswith("cmd_")
		assert len(cid) == 16  # cmd_ + 12 hex chars

	def test_unique(self):
		ids = {make_command_id() for _ in range(100)}
		assert len(ids) == 100


class TestConstants:
	def test_default_port(self):
		assert DEFAULT_PORT == 19826

	def test_bridge_host(self):
		assert BRIDGE_HOST == "127.0.0.1"


# ── client tests ───────────────────────────────────────────────────


class TestBridgeClient:
	def test_is_running_false_on_connection_error(self):
		from boss_agent_cli.bridge.client import BridgeClient
		client = BridgeClient()
		# No daemon running on test port
		with patch("httpx.get", side_effect=ConnectionError("refused")):
			assert client.is_running() is False

	def test_status_returns_none_on_error(self):
		from boss_agent_cli.bridge.client import BridgeClient
		client = BridgeClient()
		with patch("httpx.get", side_effect=ConnectionError("refused")):
			assert client.status() is None

	def test_is_extension_connected_false_when_no_status(self):
		from boss_agent_cli.bridge.client import BridgeClient
		client = BridgeClient()
		with patch.object(client, "status", return_value=None):
			assert client.is_extension_connected() is False

	def test_is_extension_connected_false_when_disconnected(self):
		from boss_agent_cli.bridge.client import BridgeClient
		client = BridgeClient()
		with patch.object(client, "status", return_value={"extensionConnected": False}):
			assert client.is_extension_connected() is False

	def test_is_extension_connected_true(self):
		from boss_agent_cli.bridge.client import BridgeClient
		client = BridgeClient()
		with patch.object(client, "status", return_value={"extensionConnected": True}):
			assert client.is_extension_connected() is True

	def test_send_command_returns_error_on_connection_failure(self):
		from boss_agent_cli.bridge.client import BridgeClient
		import httpx
		client = BridgeClient(max_retries=1)
		with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
			result = client.send_command("exec", code="1+1")
			assert result.ok is False

	def test_evaluate_raises_on_failure(self):
		from boss_agent_cli.bridge.client import BridgeClient
		client = BridgeClient()
		fail_result = BridgeResult(id="x", ok=False, error="boom")
		with patch.object(client, "send_command", return_value=fail_result):
			try:
				client.evaluate("1+1")
				assert False, "Should have raised"
			except RuntimeError as e:
				assert "boom" in str(e)

	def test_evaluate_returns_data(self):
		from boss_agent_cli.bridge.client import BridgeClient
		client = BridgeClient()
		ok_result = BridgeResult(id="x", ok=True, data={"result": 42})
		with patch.object(client, "send_command", return_value=ok_result):
			data = client.evaluate("1+1")
			assert data == {"result": 42}

	def test_navigate_returns_data(self):
		from boss_agent_cli.bridge.client import BridgeClient
		client = BridgeClient()
		ok_result = BridgeResult(id="x", ok=True, data={"url": "https://example.com"})
		with patch.object(client, "send_command", return_value=ok_result):
			data = client.navigate("https://example.com")
			assert data == {"url": "https://example.com"}

	def test_get_cookies_returns_list(self):
		from boss_agent_cli.bridge.client import BridgeClient
		client = BridgeClient()
		ok_result = BridgeResult(id="x", ok=True, data=[{"name": "wt2", "value": "abc"}])
		with patch.object(client, "send_command", return_value=ok_result):
			cookies = client.get_cookies("zhipin.com")
			assert len(cookies) == 1
			assert cookies[0]["name"] == "wt2"

	def test_close_window_no_error(self):
		from boss_agent_cli.bridge.client import BridgeClient
		client = BridgeClient()
		ok_result = BridgeResult(id="x", ok=True)
		with patch.object(client, "send_command", return_value=ok_result):
			client.close_window()  # should not raise


# ── daemon utility tests ───────────────────────────────────────────


class TestDaemonUtils:
	def test_is_daemon_running_no_pid_file(self, tmp_path):
		from boss_agent_cli.bridge import daemon
		with patch.object(daemon, "_PID_FILE", tmp_path / "nonexistent.pid"):
			assert daemon.is_daemon_running() is False

	def test_is_daemon_running_stale_pid(self, tmp_path):
		from boss_agent_cli.bridge import daemon
		pid_file = tmp_path / "daemon.pid"
		pid_file.write_text("99999999")  # unlikely real PID
		with patch.object(daemon, "_PID_FILE", pid_file):
			assert daemon.is_daemon_running() is False
			# Stale PID file should be cleaned up
			assert not pid_file.exists()

	def test_get_daemon_pid_none_when_no_file(self, tmp_path):
		from boss_agent_cli.bridge import daemon
		with patch.object(daemon, "_PID_FILE", tmp_path / "nonexistent.pid"):
			assert daemon.get_daemon_pid() is None

	def test_stop_daemon_no_process(self, tmp_path):
		from boss_agent_cli.bridge import daemon
		with patch.object(daemon, "_PID_FILE", tmp_path / "nonexistent.pid"):
			assert daemon.stop_daemon() is False
