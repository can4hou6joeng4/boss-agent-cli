"""Bridge 协议和客户端扩展测试 — 覆盖协议数据结构和客户端重试逻辑。"""

from unittest.mock import patch, MagicMock

import pytest

from boss_agent_cli.bridge.protocol import (
	BridgeCommand,
	BridgeResult,
	make_command_id,
	DEFAULT_PORT,
	BRIDGE_HOST,
)
from boss_agent_cli.bridge.client import BridgeClient


# ── BridgeCommand ────────────────────────────────────────────────────


def test_command_to_dict_minimal():
	"""最小命令只包含 id 和 action。"""
	cmd = BridgeCommand(id="cmd_abc", action="exec")
	d = cmd.to_dict()
	assert d["id"] == "cmd_abc"
	assert d["action"] == "exec"
	assert "code" not in d
	assert "url" not in d


def test_command_to_dict_with_code():
	"""exec 命令带 code 字段。"""
	cmd = BridgeCommand(id="cmd_abc", action="exec", code="return 1+1")
	d = cmd.to_dict()
	assert d["code"] == "return 1+1"


def test_command_to_dict_with_url():
	"""navigate 命令带 url 字段。"""
	cmd = BridgeCommand(id="cmd_abc", action="navigate", url="https://example.com")
	d = cmd.to_dict()
	assert d["url"] == "https://example.com"


def test_command_to_dict_with_tab_id():
	"""带 tab_id 时转为 tabId。"""
	cmd = BridgeCommand(id="cmd_abc", action="exec", tab_id=42)
	d = cmd.to_dict()
	assert d["tabId"] == 42


def test_command_to_dict_workspace():
	"""workspace 默认为 boss。"""
	cmd = BridgeCommand(id="cmd_abc", action="exec")
	d = cmd.to_dict()
	assert d["workspace"] == "boss"


# ── BridgeResult ─────────────────────────────────────────────────────


def test_result_from_dict_success():
	"""成功结果解析。"""
	d = {"id": "cmd_abc", "ok": True, "data": {"value": 42}}
	r = BridgeResult.from_dict(d)
	assert r.id == "cmd_abc"
	assert r.ok is True
	assert r.data == {"value": 42}
	assert r.error == ""


def test_result_from_dict_failure():
	"""失败结果解析。"""
	d = {"id": "cmd_abc", "ok": False, "error": "Extension disconnected"}
	r = BridgeResult.from_dict(d)
	assert r.ok is False
	assert r.error == "Extension disconnected"


def test_result_from_dict_defaults():
	"""缺失字段应使用默认值。"""
	r = BridgeResult.from_dict({})
	assert r.id == ""
	assert r.ok is False
	assert r.data is None
	assert r.error == ""


# ── make_command_id ──────────────────────────────────────────────────


def test_make_command_id_format():
	"""命令 ID 应以 cmd_ 开头。"""
	cid = make_command_id()
	assert cid.startswith("cmd_")
	assert len(cid) == 16  # cmd_ + 12 hex


def test_make_command_id_unique():
	"""连续生成的命令 ID 应不同。"""
	ids = {make_command_id() for _ in range(100)}
	assert len(ids) == 100


# ── 协议常量 ─────────────────────────────────────────────────────────


def test_default_port():
	"""默认端口应为 19826。"""
	assert DEFAULT_PORT == 19826


def test_bridge_host():
	"""主机应为 127.0.0.1。"""
	assert BRIDGE_HOST == "127.0.0.1"


# ── BridgeClient ─────────────────────────────────────────────────────


@patch("boss_agent_cli.bridge.client.httpx.get")
def test_client_is_running_true(mock_get):
	"""daemon 返回 200 时 is_running 应为 True。"""
	mock_get.return_value = MagicMock(status_code=200)
	client = BridgeClient()
	assert client.is_running() is True


@patch("boss_agent_cli.bridge.client.httpx.get")
def test_client_is_running_false_on_error(mock_get):
	"""连接失败时 is_running 应为 False。"""
	mock_get.side_effect = ConnectionError("refused")
	client = BridgeClient()
	assert client.is_running() is False


@patch("boss_agent_cli.bridge.client.httpx.get")
def test_client_status_returns_dict(mock_get):
	"""status 返回 200 时应解析为 dict。"""
	mock_get.return_value = MagicMock(status_code=200, json=lambda: {"extensionConnected": True})
	client = BridgeClient()
	st = client.status()
	assert st["extensionConnected"] is True


@patch("boss_agent_cli.bridge.client.httpx.get")
def test_client_status_returns_none_for_non_object_payload(mock_get):
	"""status 返回非对象 JSON 时应视为 daemon 不可用。"""
	mock_get.return_value = MagicMock(status_code=200, json=lambda: ["unexpected"])
	client = BridgeClient()
	assert client.status() is None


@patch("boss_agent_cli.bridge.client.httpx.get")
def test_client_status_returns_none_on_error(mock_get):
	"""status 连接失败时应返回 None。"""
	mock_get.side_effect = ConnectionError("refused")
	client = BridgeClient()
	assert client.status() is None


@patch("boss_agent_cli.bridge.client.httpx.get")
def test_client_is_extension_connected(mock_get):
	"""扩展已连接时应返回 True。"""
	mock_get.return_value = MagicMock(status_code=200, json=lambda: {"extensionConnected": True})
	client = BridgeClient()
	assert client.is_extension_connected() is True


@patch("boss_agent_cli.bridge.client.httpx.get")
def test_client_is_extension_disconnected(mock_get):
	"""扩展未连接时应返回 False。"""
	mock_get.return_value = MagicMock(status_code=200, json=lambda: {"extensionConnected": False})
	client = BridgeClient()
	assert client.is_extension_connected() is False


@patch("boss_agent_cli.bridge.client.httpx.get")
def test_client_diagnose_reports_daemon_disconnected(mock_get):
	"""daemon 不可用时诊断应给出 daemon 和 extension 恢复动作。"""
	mock_get.side_effect = ConnectionError("refused")
	client = BridgeClient()
	checks = client.diagnose()
	names = {item["name"] for item in checks}
	assert {"bridge_daemon", "bridge_extension"} <= names
	assert next(item for item in checks if item["name"] == "bridge_daemon")["status"] == "warn"
	assert "19826" in next(item for item in checks if item["name"] == "bridge_daemon")["recovery_action"]


@patch("boss_agent_cli.bridge.client.httpx.post")
@patch("boss_agent_cli.bridge.client.httpx.get")
def test_client_diagnose_reports_connected_capabilities(mock_get, mock_post):
	"""扩展连接时诊断应检查协议、workspace、exec 和 fetch。"""
	mock_get.return_value = MagicMock(
		status_code=200,
		json=lambda: {
			"ok": True,
			"extensionConnected": True,
			"extensionVersion": "1.0.0",
			"pid": 123,
			"uptime": 7,
		},
	)
	mock_post.side_effect = [
		MagicMock(json=lambda: {
			"id": "cmd_x",
			"ok": True,
			"data": {"ok": True, "href": "https://www.zhipin.com/web/geek/job", "title": "BOSS"},
		}),
		MagicMock(json=lambda: {"id": "cmd_y", "ok": True, "data": {"ok": True}}),
		MagicMock(json=lambda: {"id": "cmd_z", "ok": True, "data": {"url": "http://127.0.0.1:19826/ping"}}),
	]
	client = BridgeClient(max_retries=1)
	checks = client.diagnose()
	by_name = {item["name"]: item for item in checks}
	assert by_name["bridge_daemon"]["status"] == "ok"
	assert by_name["bridge_extension"]["status"] == "ok"
	assert by_name["bridge_protocol"]["status"] == "ok"
	assert by_name["bridge_workspace"]["status"] == "ok"
	assert by_name["bridge_workspace"]["tab_url"] == "https://www.zhipin.com/web/geek/job"
	assert by_name["bridge_exec"]["status"] == "ok"
	assert by_name["bridge_fetch"]["status"] == "ok"
	assert by_name["bridge_navigate"]["status"] == "ok"


@patch("boss_agent_cli.bridge.client.httpx.get")
def test_client_diagnose_warns_on_protocol_mismatch(mock_get):
	"""扩展 major 版本不匹配时协议检查应为 warn。"""
	mock_get.return_value = MagicMock(
		status_code=200,
		json=lambda: {
			"ok": True,
			"extensionConnected": True,
			"extensionVersion": "2.0.0",
			"pid": 123,
			"uptime": 7,
		},
	)
	client = BridgeClient()
	checks = client.diagnose(run_probes=False)
	protocol = next(item for item in checks if item["name"] == "bridge_protocol")
	assert protocol["status"] == "warn"
	assert "重新加载" in protocol["recovery_action"]


@patch("boss_agent_cli.bridge.client.httpx.post")
def test_client_send_command_success(mock_post):
	"""send_command 成功时返回 ok 结果。"""
	mock_post.return_value = MagicMock(json=lambda: {"id": "cmd_x", "ok": True, "data": {"value": 1}})
	client = BridgeClient()
	result = client.send_command("exec", code="return 1")
	assert result.ok is True
	assert result.data == {"value": 1}


@patch("boss_agent_cli.bridge.client.httpx.post")
def test_client_send_command_non_transient_error(mock_post):
	"""非临时性错误应立即返回，不重试。"""
	mock_post.return_value = MagicMock(json=lambda: {"id": "cmd_x", "ok": False, "error": "Invalid action"})
	client = BridgeClient(max_retries=3)
	result = client.send_command("bad-action")
	assert result.ok is False
	assert "Invalid action" in result.error
	assert mock_post.call_count == 1


@patch("boss_agent_cli.bridge.client.httpx.post")
@patch("boss_agent_cli.bridge.client.time.sleep")
def test_client_send_command_retries_on_transient(mock_sleep, mock_post):
	"""临时性错误应重试。"""
	fail_resp = MagicMock(json=lambda: {"id": "cmd_x", "ok": False, "error": "Extension disconnected"})
	ok_resp = MagicMock(json=lambda: {"id": "cmd_x", "ok": True, "data": None})
	mock_post.side_effect = [fail_resp, ok_resp]
	client = BridgeClient(max_retries=3)
	result = client.send_command("exec", code="return 1")
	assert result.ok is True
	assert mock_post.call_count == 2


@patch("boss_agent_cli.bridge.client.httpx.post")
@patch("boss_agent_cli.bridge.client.time.sleep")
def test_client_send_command_retries_on_connect_error(mock_sleep, mock_post):
	"""连接错误应重试。"""
	import httpx
	mock_post.side_effect = [httpx.ConnectError("refused"), MagicMock(json=lambda: {"id": "x", "ok": True, "data": None})]
	client = BridgeClient(max_retries=3)
	result = client.send_command("exec", code="1")
	assert result.ok is True
	assert mock_post.call_count == 2


@patch("boss_agent_cli.bridge.client.httpx.post")
def test_client_evaluate_raises_on_failure(mock_post):
	"""evaluate 失败时应抛异常。"""
	mock_post.return_value = MagicMock(json=lambda: {"id": "x", "ok": False, "error": "JS error"})
	client = BridgeClient(max_retries=1)
	with pytest.raises(RuntimeError, match="JS error"):
		client.evaluate("bad code")


@patch("boss_agent_cli.bridge.client.httpx.post")
def test_client_navigate_returns_data(mock_post):
	"""navigate 成功应返回数据。"""
	mock_post.return_value = MagicMock(json=lambda: {"id": "x", "ok": True, "data": {"url": "https://x.com"}})
	client = BridgeClient()
	data = client.navigate("https://x.com")
	assert data["url"] == "https://x.com"


@patch("boss_agent_cli.bridge.client.httpx.post")
def test_client_get_cookies_returns_list(mock_post):
	"""get_cookies 应返回 cookie 列表。"""
	mock_post.return_value = MagicMock(json=lambda: {"id": "x", "ok": True, "data": [{"name": "wt2", "value": "abc"}]})
	client = BridgeClient()
	cookies = client.get_cookies("zhipin.com")
	assert len(cookies) == 1
	assert cookies[0]["name"] == "wt2"


@patch("boss_agent_cli.bridge.client.httpx.post")
def test_client_fetch_json_get(mock_post):
	"""fetch_json GET 请求应正确构造 JS。"""
	mock_post.return_value = MagicMock(json=lambda: {"id": "x", "ok": True, "data": {"code": 0}})
	client = BridgeClient()
	result = client.fetch_json("https://api.example.com/data")
	assert result == {"code": 0}
	# 验证发送的 JS 包含 fetch 和 GET
	call_args = mock_post.call_args[1]["json"]
	assert "GET" in call_args["code"]


@patch("boss_agent_cli.bridge.client.httpx.post")
def test_client_fetch_json_post(mock_post):
	"""fetch_json POST 请求应正确构造 form data。"""
	mock_post.return_value = MagicMock(json=lambda: {"id": "x", "ok": True, "data": {"code": 0}})
	client = BridgeClient()
	result = client.fetch_json("https://api.example.com/data", method="POST", data={"key": "val"})
	assert result == {"code": 0}
	call_args = mock_post.call_args[1]["json"]
	assert "POST" in call_args["code"]
	assert "key" in call_args["code"]
