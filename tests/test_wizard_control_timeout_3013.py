"""wizard 控制动作的 timeout 口径与宿主等待上限（Issue #3013）。

status / stop 在进入 runner 前返回，--timeout 从未被执行；MCP 宿主却把它当作
等待子进程的硬预算。同一输入在 CLI 与 MCP 下语义相反，stop 还可能被提前杀掉
并报成 outcome_unknown=true。这里验证两侧入口一致拒绝该组合，以及超上限取值
在启动子进程前被拒绝。
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from mcp.types import CallToolRequestParams

from boss_agent_cli import mcp_server
from boss_agent_cli.main import cli
from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.mcp_tools import TOOLS
from boss_agent_cli.wizard.models import WorkflowPlan
from boss_agent_cli.wizard.store import WorkflowStore


def _create_run(data_dir: Path, run_id: str) -> None:
	plan = WorkflowPlan(
		role="candidate",
		platform="zhipin",
		goal="ai_assist",
		inputs={"prompt": "优化"},
		requested_steps=("ai_assist",),
		mode="headless",
	)
	with WorkflowStore(data_dir) as store:
		store.create(run_id, plan)


@pytest.mark.parametrize("flag", ["--status", "--stop"])
def test_cli_control_actions_reject_timeout(tmp_path: Path, flag: str) -> None:
	"""--timeout 只对 run / resume 生效；控制动作用户可见地拒绝（Issue #3013）。"""
	_create_run(tmp_path, "run-1")
	result = CliRunner().invoke(
		cli, ["--json", "--data-dir", str(tmp_path), "wizard", flag, "run-1", "--timeout", "30"]
	)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "--timeout" in payload["error"]["message"]
	assert flag in payload["error"]["message"]


@pytest.mark.parametrize("flag", ["--status", "--stop"])
def test_cli_control_actions_still_work_without_timeout(tmp_path: Path, flag: str) -> None:
	_create_run(tmp_path, "run-1")
	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "wizard", flag, "run-1"])

	assert result.exit_code == 0, result.output
	assert json.loads(result.stdout)["ok"] is True


@pytest.mark.parametrize("action", ["status", "stop"])
def test_mapping_rejects_timeout_for_control_actions(action: str) -> None:
	with pytest.raises(ValueError, match="timeout"):
		_build_args("boss_wizard", {"action": action, "run_id": "run-1", "timeout": 30})


@pytest.mark.parametrize("action", ["resume", "run"])
def test_mapping_keeps_timeout_for_run_and_resume(action: str) -> None:
	arguments = (
		{"action": "resume", "run_id": "run-1", "timeout": 30}
		if action == "resume"
		else {"action": "run", "role": "candidate", "platform": "zhipin", "goal": "shortlist", "inputs": {}, "timeout": 30}
	)
	args = _build_args("boss_wizard", arguments)
	assert args[-2:] == ["--timeout", "30"]


def test_control_timeout_does_not_shorten_host_budget() -> None:
	"""宿主预算按默认值等待，让 CLI 有机会返回确定性的 INVALID_PARAM。"""
	assert mcp_server._cli_timeout(("wizard", "--status", "run-1", "--timeout", "0.001")) == 120.0
	assert mcp_server._cli_timeout(("wizard", "--stop", "run-1", "--timeout", "0.001")) == 120.0
	# run / resume 的既有语义不变：60 秒截止加落盘宽限。
	assert mcp_server._cli_timeout(("wizard", "--resume", "run-1", "--timeout", "60")) == 65.0


def test_timeout_over_host_cap_is_rejected_before_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
	async def fail_spawn(*args, **kwargs):
		raise AssertionError("越界 timeout 不应启动子进程")

	monkeypatch.setattr(mcp_server.asyncio, "create_subprocess_exec", fail_spawn)
	result = asyncio.run(
		mcp_server._run_boss_async(
			"wizard", "--input-json", '{"role": "candidate"}', "--timeout", "1200"
		)
	)

	assert result["ok"] is False
	assert result["error"]["code"] == "INVALID_PARAM"
	assert "timeout" in result["error"]["message"]
	assert "900" in result["error"]["message"]
	assert result["error"]["details"]["outcome_unknown"] is False
	assert "timeout" in result["error"]["recovery_action"]


def test_wizard_schema_declares_maximum_and_control_exclusion() -> None:
	tool = next(item for item in TOOLS if item.name == "boss_wizard")
	timeout = tool.input_schema["properties"]["timeout"]
	assert timeout["maximum"] == 900
	assert "status" in timeout["description"] and "stop" in timeout["description"]
	then = tool.input_schema["then"]
	# resume 允许 timeout；status / stop 落入 else 分支被排除。
	assert then["if"]["properties"]["action"]["const"] == "resume"
	assert then["else"]["not"]["required"] == ["timeout"]


def test_call_tool_control_timeout_returns_invalid_param(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setattr(mcp_server, "TextContent", SimpleNamespace)
	monkeypatch.setattr(mcp_server, "CallToolResult", SimpleNamespace)

	async def fake_run(*args, **kwargs):
		raise AssertionError("映射层拒绝不应触达 CLI")

	monkeypatch.setattr(mcp_server, "_run_boss_async", fake_run)
	result = asyncio.run(
		mcp_server.call_tool(
			None,
			CallToolRequestParams(
				name="boss_wizard",
				arguments={"action": "stop", "run_id": "run-1", "timeout": 30},
			),
		)
	)

	payload = json.loads(result.content[0].text)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "timeout" in payload["error"]["message"]
