"""第五轮审计批次：Issue #1591–#1595 的回归测试。

全部测试离线运行：不调用平台 API，不启动浏览器。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from mcp.types import CallToolRequestParams

from boss_agent_cli import mcp_server
from boss_agent_cli.automation.storage import AutomationStore
from boss_agent_cli.main import cli
from boss_agent_cli.mcp_tools import TOOLS
from boss_agent_cli.wizard.models import WorkflowPlan
from boss_agent_cli.wizard.store import WorkflowStore

_ENVELOPE_KEYS = {"ok", "schema_version", "command", "data", "pagination", "error", "hints"}
_RUN_ID = "run-error-contract"


def _invoke(data_dir: Path, *args: str):
	return CliRunner().invoke(cli, ["--json", "--data-dir", str(data_dir), *args])


def _seed_run(data_dir: Path, *, error: dict, step_error: dict | None = None) -> None:
	plan = WorkflowPlan(
		role="recruiter",
		platform="zhipin",
		goal="reply",
		inputs={"friend_id": 42, "message": "离线消息"},
		requested_steps=("recruiter_reply",),
		mode="headless",
	)
	with WorkflowStore(data_dir) as store:
		store.create(_RUN_ID, plan)
		store.update_run(_RUN_ID, status="failed", current_step="recruiter_reply", error=error)
		store.update_step(
			_RUN_ID,
			"recruiter_reply",
			status="failed",
			error=error if step_error is None else step_error,
		)


def _snapshot(data_dir: Path):
	with sqlite3.connect(data_dir / "cache" / "boss_agent.db") as connection:
		return (
			connection.execute("SELECT * FROM workflow_runs WHERE run_id = ?", (_RUN_ID,)).fetchone(),
			connection.execute("SELECT * FROM workflow_steps WHERE run_id = ?", (_RUN_ID,)).fetchone(),
		)


# Issue #1591 ---------------------------------------------------------------


def test_dry_run_with_resume_is_rejected_before_touching_stop_state(tmp_path: Path) -> None:
	"""预览 + 恢复必须拒绝；dry-run 不得解除操作者的停止闩锁（Issue #1591）。"""
	store = AutomationStore(tmp_path)
	store.request_stop("manual-stop")
	before = (tmp_path / "automation" / "stop.json").read_bytes()

	result = _invoke(tmp_path, "--role", "recruiter", "agent", "run", "--dry-run", "--resume")

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert set(payload) == _ENVELOPE_KEYS
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "--dry-run" in payload["error"]["message"]
	assert "--resume" in payload["error"]["message"]
	assert (tmp_path / "automation" / "stop.json").read_bytes() == before
	assert store.read_stop()["open"] is True
	assert not (tmp_path / "automation" / "pending-actions.jsonl").exists()


def test_mcp_agent_run_rejects_dry_run_resume_before_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
	"""MCP 的默认 dry_run=true 与 resume=true 组合同样不能落进 CLI。"""
	monkeypatch.setattr(mcp_server, "TextContent", SimpleNamespace)
	monkeypatch.setattr(mcp_server, "CallToolResult", SimpleNamespace)

	async def fake_run(*args, **kwargs):
		pytest.fail(f"dry-run resume 不应触达 CLI：{args}")

	monkeypatch.setattr(mcp_server, "_run_boss_async", fake_run)
	import asyncio

	result = asyncio.run(
		mcp_server.call_tool(
			None,
			CallToolRequestParams(name="boss_agent_run", arguments={"resume": True}),
		)
	)
	payload = json.loads(result.content[0].text)
	assert result.is_error is True
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "--dry-run" in payload["error"]["message"]


# Issue #1592 ---------------------------------------------------------------


@pytest.mark.parametrize(
	("field", "value", "expected"),
	[
		("recoverable", "false", "error.recoverable"),
		("recoverable", 0, "error.recoverable"),
		("recovery_action", 123, "error.recovery_action"),
		("recovery_action", ["重试"], "error.recovery_action"),
	],
)
@pytest.mark.parametrize("table", ["workflow_runs", "workflow_steps"])
def test_wizard_rejects_drifted_recovery_metadata(
	tmp_path: Path, field: str, value: object, expected: str, table: str
) -> None:
	"""持久化的错误元数据必须是 bool / str|None；不能 bool("false") 后给恢复指引。"""
	error = {
		"code": "RATE_LIMITED",
		"message": "离线断点",
		"recoverable": False,
		"recovery_action": "核对后重试",
	}
	error[field] = value
	run_error = error if table == "workflow_runs" else {**error, "replay_safe": True}
	step_error = error if table == "workflow_steps" else {**error, "replay_safe": True}
	_seed_run(tmp_path, error=run_error, step_error=step_error)
	before = _snapshot(tmp_path)

	result = _invoke(tmp_path, "wizard", "--status", _RUN_ID)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert set(payload) == _ENVELOPE_KEYS
	assert payload["ok"] is False
	assert payload["error"]["code"] == "CACHE_RECORD_INVALID"
	assert expected in payload["error"]["message"]
	assert _snapshot(tmp_path) == before


# Issue #1593–#1595 ---------------------------------------------------------


@pytest.mark.parametrize(
	("tool_name", "arguments", "field"),
	[
		("boss_wizard", {"action": "status", "run_id": ""}, "run_id"),
		("boss_hr_jobs", {"action": "online", "job_id": ""}, "job_id"),
		("boss_config", {"action": "set", "key": "", "value": "zhipin"}, "key"),
	],
)
def test_mcp_rejects_empty_conditional_required_strings(
	monkeypatch: pytest.MonkeyPatch, tool_name: str, arguments: dict, field: str
) -> None:
	"""条件必填字符串获得 minLength 声明，入口按同一约束拒绝（Issue #1593）。"""
	tool = next(item for item in TOOLS if item.name == tool_name)
	assert tool.input_schema["properties"][field].get("minLength") == 1

	monkeypatch.setattr(mcp_server, "TextContent", SimpleNamespace)
	monkeypatch.setattr(mcp_server, "CallToolResult", SimpleNamespace)

	async def fake_run(*args, **kwargs):
		pytest.fail(f"空 {field} 不应触达 CLI：{args}")

	monkeypatch.setattr(mcp_server, "_run_boss_async", fake_run)
	import asyncio

	result = asyncio.run(mcp_server.call_tool(None, CallToolRequestParams(name=tool_name, arguments=arguments)))
	payload = json.loads(result.content[0].text)
	assert result.is_error is True
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert field in payload["error"]["message"]


@pytest.mark.parametrize(
	("tool_name", "arguments", "field"),
	[
		("boss_wizard", {"action": "run", "timeout": -5}, "timeout"),
		("boss_wizard", {"action": "run", "max_retries": 100}, "max_retries"),
		("boss_batch_greet", {"query": "AI", "limit": -3}, "limit"),
		("boss_clean", {"days": -1}, "days"),
	],
)
def test_mcp_rejects_out_of_range_numbers(
	monkeypatch: pytest.MonkeyPatch, tool_name: str, arguments: dict, field: str
) -> None:
	"""schema 声明的 minimum / maximum 必须在 MCP 入口生效（Issue #1594）。"""
	monkeypatch.setattr(mcp_server, "TextContent", SimpleNamespace)
	monkeypatch.setattr(mcp_server, "CallToolResult", SimpleNamespace)

	async def fake_run(*args, **kwargs):
		pytest.fail(f"越界 {field} 不应触达 CLI：{args}")

	monkeypatch.setattr(mcp_server, "_run_boss_async", fake_run)
	import asyncio

	result = asyncio.run(mcp_server.call_tool(None, CallToolRequestParams(name=tool_name, arguments=arguments)))
	payload = json.loads(result.content[0].text)
	assert result.is_error is True
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert field in payload["error"]["message"]


@pytest.mark.parametrize("requested", ["1e308", "3600"])
def test_mcp_caps_wizard_host_wait(requested: str) -> None:
	"""宿主等待最多 900s；wizard 自身超时再长也不能让 MCP 近似无界（Issue #1594）。

	超过上限的请求现在直接拒绝而不是静默截断（Issue #3013）：CLI 仍会按请求值
	运行，宿主却只等 900s，截断会让调用方拿到与请求不符的终止时间。
	"""
	with pytest.raises(ValueError, match="900"):
		mcp_server._cli_timeout(("wizard", "--timeout", requested))
	assert mcp_server._cli_timeout(("wizard", "--timeout", "60")) == 65.0


@pytest.mark.parametrize("field", ["add_tags", "remove_tags"])
def test_mcp_rejects_empty_tag_array_elements(monkeypatch: pytest.MonkeyPatch, field: str) -> None:
	"""add_tags / remove_tags 的每个元素都要按 items.minLength 校验（Issue #1595）。"""
	arguments = {"security_id": "sec_1", "job_id": "job_1", field: ["有效", ""]}
	monkeypatch.setattr(mcp_server, "TextContent", SimpleNamespace)
	monkeypatch.setattr(mcp_server, "CallToolResult", SimpleNamespace)

	async def fake_run(*args, **kwargs):
		pytest.fail(f"空标签不应触达 CLI：{args}")

	monkeypatch.setattr(mcp_server, "_run_boss_async", fake_run)
	import asyncio

	result = asyncio.run(
		mcp_server.call_tool(None, CallToolRequestParams(name="boss_shortlist_annotate", arguments=arguments))
	)
	payload = json.loads(result.content[0].text)
	assert result.is_error is True
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert f"{field}[1]" in payload["error"]["message"]
