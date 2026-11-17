"""批次十九回归测试：Issue #1604–#1609。

全部测试离线运行；不调用平台 API，不启动浏览器。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.api.client import AccountRiskError
from boss_agent_cli.api.request_budget import RequestBudgetExceeded
from boss_agent_cli.main import cli
from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.mcp_server import _cli_timeout, _estimated_requests
from boss_agent_cli.schema.tool_catalog import build_tool_catalog
from boss_agent_cli.wizard.actions import ActionContext
from boss_agent_cli.wizard.models import WorkflowPlan
from boss_agent_cli.wizard.runner import WorkflowRunner
from boss_agent_cli.wizard.store import WorkflowStore

_ENVELOPE_KEYS = {"ok", "schema_version", "command", "data", "pagination", "error", "hints"}
_CHECK_COMMAND = "boss hr chatmsg 42"


def _plan(*steps: str) -> WorkflowPlan:
	return WorkflowPlan(role="recruiter", platform="zhipin", goal="offline", inputs={}, requested_steps=steps, mode="headless")


def _context(data_dir: Path) -> ActionContext:
	return ActionContext(data_dir=data_dir, platform="zhipin", role="recruiter", logger=MagicMock())


def _tools() -> dict:
	return {tool.name: tool for tool in build_tool_catalog()}


# ── #1604：风控 / 预算异常不得覆盖在途写标记 ────────────────────────


@pytest.mark.parametrize(
	"exc",
	[
		AccountRiskError("平台风控"),
		RequestBudgetExceeded("预算耗尽"),
	],
)
def test_control_exceptions_preserve_unconfirmed_write_marker(tmp_path: Path, exc: Exception) -> None:
	called = []

	def write_then_raise(context, inputs, prior):
		called.append("write")
		context.workflow_control.begin_write(_CHECK_COMMAND)
		raise exc

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, {"recruiter_reply": write_then_raise})
		run = runner.run(_plan("recruiter_reply"), _context(tmp_path), run_id="run-control-exception")

	assert run["status"] == "failed"
	assert run["error"]["code"] == exc.code
	assert run["error"]["replay_safe"] is False
	assert _CHECK_COMMAND in run["error"]["recovery_action"]

	def must_not_replay(context, inputs, prior):
		called.append("replay")
		raise AssertionError("结果未知的写操作被恢复重放")

	with WorkflowStore(tmp_path) as store:
		resumed = WorkflowRunner(store, {"recruiter_reply": must_not_replay}).run(
			_plan("recruiter_reply"), _context(tmp_path), run_id="run-control-exception"
		)

	assert resumed["status"] == "failed"
	assert resumed["error"]["code"] == exc.code
	assert "replay" not in called


def test_unsent_budget_stop_keeps_retryable_contract(tmp_path: Path) -> None:
	"""显式 request_sent=false 的控制停止没有副作用，不能继承在途写标记。"""
	exc = RequestBudgetExceeded("预算耗尽")
	exc.details["request_sent"] = False

	def reply(context, inputs, prior):
		context.workflow_control.begin_write(_CHECK_COMMAND)
		raise exc

	with WorkflowStore(tmp_path) as store:
		run = WorkflowRunner(store, {"recruiter_reply": reply}).run(
			_plan("recruiter_reply"), _context(tmp_path), run_id="run-unsent-budget"
		)

	assert run["error"]["code"] == "REQUEST_BUDGET_EXHAUSTED"
	# 与 Ctrl-C 的保护口径一致：一旦 begin_write 落盘，本执行过程不再自行
	# 判定「请求尚未发出」；恢复由操作者按核对命令确认，不能自动重放。
	assert run["error"]["replay_safe"] is False
	assert _CHECK_COMMAND in run["error"]["recovery_action"]


# ── #1605：hr resume --exchange 是写操作 ────────────────────────────


@patch("boss_agent_cli.commands.recruiter.resume.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.resume.AuthManager")
def test_resume_exchange_unknown_is_not_retryable(mock_auth_cls, mock_platform_cls) -> None:
	instance = mock_platform_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.exchange_request_by_friend.return_value = {"code": -1, "message": "未知失败"}
	instance.is_success.return_value = False
	instance.parse_error.return_value = ("UNKNOWN", "未知失败")

	result = CliRunner().invoke(cli, ["--role", "recruiter", "hr", "resume", "--exchange", "--friend-id", "42"])

	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert set(payload) == _ENVELOPE_KEYS
	assert payload["error"]["code"] == "UNKNOWN"
	assert payload["error"]["recoverable"] is False
	assert "不要自动重试" in payload["error"]["recovery_action"]


@patch("boss_agent_cli.commands.recruiter.resume.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.resume.AuthManager")
def test_resume_read_unknown_remains_retryable(mock_auth_cls, mock_platform_cls) -> None:
	instance = mock_platform_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.view_geek.return_value = {"code": -1, "message": "未知失败"}
	instance.is_success.return_value = False
	instance.parse_error.return_value = ("UNKNOWN", "未知失败")

	result = CliRunner().invoke(
		cli, ["--role", "recruiter", "hr", "resume", "geek-1", "--job-id", "job-1", "--security-id", "sec-1"]
	)

	payload = json.loads(result.output)
	assert payload["error"]["code"] == "NETWORK_ERROR"
	assert payload["error"]["recoverable"] is True


# ── #1606–#1608：MCP schema 与实际入口一致 ──────────────────────────


def test_chat_schema_requires_format_with_output_file() -> None:
	schema = _tools()["boss_chat"].input_schema
	assert schema["if"] == {"required": ["output_file"]}
	assert schema["then"] == {"required": ["format"]}


def test_annotate_schema_requires_one_modification() -> None:
	schema = _tools()["boss_shortlist_annotate"].input_schema
	assert {"required": ["add_tags"]} in schema["anyOf"]
	assert {"required": ["remove_tags"]} in schema["anyOf"]
	assert {"required": ["note"]} in schema["anyOf"]
	# 空 note 是显式清空备注，仍不得声明 minLength。
	assert "minLength" not in schema["properties"]["note"]


@pytest.mark.parametrize(
	"name, keys",
	[
		("boss_export", ("query", "url")),
		("boss_ai_cover_letter", ("jd_text", "job_id")),
		("boss_ai_resume_optimize", ("jd_text", "job_id")),
	],
)
def test_any_of_required_strings_have_min_length(name: str, keys: tuple[str, ...]) -> None:
	properties = _tools()[name].input_schema["properties"]
	for key in keys:
		assert properties[key]["minLength"] == 1


# ── #1609：超大 count 必须在估算处收口 ─────────────────────────────


def test_export_huge_count_uses_bounded_estimate() -> None:
	args = tuple(_build_args("boss_export", {"query": "x", "count": 10**400}))
	assert _estimated_requests(args) <= 15
	assert _cli_timeout(args) == 900.0


def test_export_normal_count_estimate_unchanged() -> None:
	args = tuple(_build_args("boss_export", {"query": "x", "count": 50}))
	assert _estimated_requests(args) == 4
	assert _cli_timeout(args) == 290.0
