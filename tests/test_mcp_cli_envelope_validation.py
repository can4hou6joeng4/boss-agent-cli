"""MCP 只接受结构和退出状态一致的 CLI 信封。"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from boss_agent_cli import mcp_server
from mcp.types import CallToolRequestParams


def _success_wire(**overrides):
	return json.dumps({
		"ok": True, "schema_version": "1.0", "command": "wizard", "data": {"state": "complete"},
		"pagination": None, "error": None, "hints": None, **overrides,
	})


@pytest.mark.parametrize(("stdout", "returncode"), [
	("{}", 0),
	(_success_wire(ok=0), 0),
	(_success_wire(ok=False), 1),
	(_success_wire(), 1),
	(_success_wire(extra=None), 0),
	(_success_wire(ok=False, error={"code": "INVALID_PARAM"}), 1),
])
@pytest.mark.parametrize("may_write", [False, True])
def test_invalid_cli_envelope_retains_write_uncertainty(stdout, returncode, may_write):
	result = mcp_server._parse_cli_output(
		("wizard",), stdout, "", returncode=returncode, may_write=may_write,
	)
	assert result["ok"] is False
	assert result["error"]["code"] == "CLI_ERROR"
	details = result["error"]["details"]
	assert details["outcome_unknown"] is may_write
	assert details["replay_safe"] is (not may_write)


@pytest.mark.parametrize("exit_code", [0, 1])
def test_async_host_checks_exit_code(exit_code, monkeypatch):
	async def communicate():
		return _success_wire().encode(), b""

	async def spawn(*args, **kwargs):
		return SimpleNamespace(communicate=communicate, returncode=exit_code)

	monkeypatch.setattr(mcp_server.asyncio, "create_subprocess_exec", spawn)
	result = asyncio.run(mcp_server._run_boss_async("wizard", "--resume", "wrn_demo"))
	assert result["ok"] is (exit_code == 0)
	if exit_code:
		assert result["error"]["code"] == "CLI_ERROR"
		assert result["error"]["details"]["replay_safe"] is False


def test_sync_host_checks_exit_code(monkeypatch):
	def run(*args, **kwargs):
		return SimpleNamespace(stdout=_success_wire(), stderr="", returncode=1)

	monkeypatch.setattr(mcp_server.subprocess, "run", run)
	assert mcp_server._run_boss("wizard")["error"]["code"] == "CLI_ERROR"


def test_valid_error_envelope_retains_recovery_fields():
	error = {"code": "INVALID_PARAM", "recoverable": False, "recovery_action": "修正参数"}
	wire = _success_wire(ok=False, error=error)
	assert mcp_server._parse_cli_output(("wizard",), wire, "", returncode=1) == json.loads(wire)


@pytest.mark.parametrize(("value", "problem"), [("bad\0path", "NUL"), ("bad\ud800path", "代理项")])
@pytest.mark.parametrize("source", ["executable", "global_option"])
def test_invalid_host_arguments_do_not_spawn_cli(value, problem, source, monkeypatch):
	async def unexpected_spawn(*args, **kwargs):
		pytest.fail("无效的宿主参数不应启动子进程")

	monkeypatch.setattr(mcp_server.asyncio, "create_subprocess_exec", unexpected_spawn)
	if source == "executable":
		monkeypatch.setattr(mcp_server, "_BOSS_BIN", value)
	else:
		monkeypatch.setattr(mcp_server, "_BOSS_GLOBAL_ARGS", ["--data-dir", value])
	result = asyncio.run(mcp_server.call_tool(None, CallToolRequestParams(name="boss_schema")))
	assert result.is_error is True
	error = json.loads(result.content[0].text)["error"]
	assert error["code"] == "INVALID_PARAM"
	assert error["details"]["outcome_unknown"] is False
	assert problem in error["message"]
	assert "子进程未启动" in error["recovery_action"]
