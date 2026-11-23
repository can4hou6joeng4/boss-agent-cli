"""审计批次 144：MCP 参数接缝（Issue #2552 / #2553）。

覆盖四处「入口放行、下游语义改变」的接缝：

1. ``boss_wizard`` 的读写判定忽略 action，只读 status 被当成写操作。
2. 选项取值以 ``--`` 开头时 argv 扫描被提前截断。
3. 非有限浮点与超出 2^53 的整值浮点通过输入校验。
4. inputSchema 的 enum 未校验，``preset_add active=""`` 被静默丢弃。
"""

from __future__ import annotations

import asyncio
import json
import math
from types import SimpleNamespace

import click
import pytest

from boss_agent_cli import mcp_server
from boss_agent_cli.main import cli
from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.mcp_server import (
	_argument_type_errors,
	_cli_timeout,
	_estimated_requests,
	_int_option,
	_json_enum_allows,
	_normalize_integral_numbers,
	_option_region,
	_option_value,
	_tool_may_write,
)

# ── 1. boss_wizard 的读写判定（Issue #2552） ────────────────────────


@pytest.mark.parametrize(
	("arguments", "expected"),
	[
		({"action": "status", "run_id": "r1"}, False),
		({"action": "stop", "run_id": "r1"}, True),
		({"action": "run", "role": "candidate", "platform": "zhipin", "goal": "g"}, True),
		({"action": "resume", "run_id": "r1"}, True),
		# 省略 action 时映射层执行 run，判定必须与 schema 缺省一致。
		({"role": "candidate", "platform": "zhipin", "goal": "g"}, True),
		({"action": None, "role": "candidate", "platform": "zhipin", "goal": "g"}, True),
		# 非字符串 action 到不了映射层，按写处理（保守方向）。
		({"action": 123}, True),
	],
)
def test_wizard_may_write_depends_on_action(arguments, expected) -> None:
	assert _tool_may_write("boss_wizard", arguments) is expected


def test_wizard_status_readonly_survives_read_only_names_registration() -> None:
	assert "boss_wizard" in mcp_server._READ_ONLY_TOOL_NAMES
	assert mcp_server._TOOL_WRITE_MODES["boss_wizard"] == frozenset({"run", "resume", "stop"})
	assert mcp_server._DEFAULT_ACTION_BY_TOOL["boss_wizard"] == "run"


@pytest.mark.parametrize(("action", "unknown"), [("status", False), ("stop", True)])
def test_interrupted_wizard_control_reports_replay_contract(action, unknown) -> None:
	arguments = {"action": action, "run_id": "wrn_demo"}
	args = tuple(_build_args("boss_wizard", arguments))
	result = mcp_server._interrupted_failure(
		args, "WORKFLOW_TIMEOUT", b"", may_write=_tool_may_write("boss_wizard", arguments),
	)
	assert result["error"]["details"]["outcome_unknown"] is unknown
	assert result["error"]["details"]["replay_safe"] is (not unknown)
	assert "同一 run_id" in result["error"]["recovery_action"]
	assert ("不要自动重试" in result["error"]["recovery_action"]) is unknown


def test_existing_action_dimension_tools_keep_their_modes() -> None:
	assert _tool_may_write("boss_hr_jobs") is False
	assert _tool_may_write("boss_hr_jobs", {"action": "list"}) is False
	assert _tool_may_write("boss_hr_jobs", {"action": "online"}) is True
	assert _tool_may_write("boss_config", {"action": "get"}) is False
	assert _tool_may_write("boss_config", {"action": "set", "key": "k", "value": "v"}) is True


# ── 2. 选项取值以 ``--`` 开头（Issue #2553） ─────────────────────────


def test_double_dash_value_uses_single_token_form() -> None:
	args = _build_args("boss_search", {"url": "--", "page": 5})
	assert args == ["search", "--url=--", "--page", "5"]


def test_option_region_keeps_options_after_double_dash_value() -> None:
	args = tuple(_build_args("boss_search", {"url": "--", "page": 5}))
	assert _option_region(args) == args
	assert _int_option(args, "--page", 1) == 5


def test_value_equal_to_a_flag_is_not_scanned_as_that_flag() -> None:
	args = tuple(_build_args("boss_search", {"url": "--dry-run", "page": 1}))
	assert "--dry-run" not in _option_region(args)
	assert _option_value(args, "--url") == "--dry-run"


def test_flag_shaped_value_does_not_fake_dry_run_estimate() -> None:
	# batch-greet 的 --city 取值为 "--dry-run" 时不能把写操作估算成一次搜索。
	args = tuple(_build_args("boss_batch_greet", {"query": "go", "city": "--dry-run", "limit": 3}))
	assert "--dry-run" not in _option_region(args)
	assert _estimated_requests(args) == 1 + 3


def test_wizard_run_id_double_dash_keeps_timeout_visible() -> None:
	args = tuple(_build_args("boss_wizard", {"action": "resume", "run_id": "--", "timeout": 60}))
	assert args == ("wizard", "--resume=--", "--timeout", "60")
	assert _option_region(args)[-2:] == ("--timeout", "60")
	assert 60 < _cli_timeout(args) <= mcp_server._MAX_CLI_TIMEOUT_SECONDS


@pytest.mark.parametrize(("tool", "key", "output_param"), [
	("boss_export", "output_file", "output"),
	("boss_digest", "output", "output_path"),
])
@pytest.mark.parametrize("path", ["--", "--report.csv"])
def test_short_output_options_preserve_path(tool, key, output_param, path) -> None:
	name, *argv = _build_args(tool, {key: path})
	parent = click.Context(cli)
	command = cli.get_command(parent, name)
	assert command is not None
	with command.make_context(name, argv, parent=parent) as ctx:
		assert str(ctx.params[output_param]) == path


@pytest.mark.parametrize("action", ["status", "stop"])
def test_wizard_control_does_not_inject_timeout_for_attached_values(monkeypatch, action) -> None:
	cmd = []

	async def fake_spawn(*args, **kwargs):
		cmd.extend(args)
		raise FileNotFoundError("模拟子进程未启动")

	monkeypatch.setattr(mcp_server.asyncio, "create_subprocess_exec", fake_spawn)
	args = _build_args("boss_wizard", {"action": action, "run_id": "--"})
	asyncio.run(mcp_server._run_boss_async(*args))
	assert cmd[-2:] == ["wizard", f"--{action}=--"]


def test_single_dash_values_keep_existing_two_token_form() -> None:
	# 薪资区间的负数编码是合法取值（--salary -1,3），不受单 token 规则影响。
	args = _build_args(
		"boss_hr_candidates",
		{"query": "golang", "salary": "-1,3", "page": 2},
	)
	assert args[-4:] == ["--salary", "-1,3", "--page", "2"]


def test_ai_resume_path_double_dash_stays_a_value() -> None:
	args = tuple(_build_args("boss_ai_fit", {"resume": "--", "limit": 2}))
	assert args == ("ai", "fit", "--resume=--", "--limit", "2")
	assert _option_value(args, "--limit") == "2"


# ── 3. 非有限数与超大浮点（Issue #2553） ────────────────────────────


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
def test_non_finite_timeout_is_rejected(value) -> None:
	errors = _argument_type_errors(
		"boss_wizard",
		{"action": "run", "role": "candidate", "platform": "zhipin", "goal": "g", "timeout": value},
	)
	assert any("必须是有限数值" in error for error in errors)


def test_nan_bounds_comparison_cannot_slip_through() -> None:
	# nan 与 minimum / maximum 的比较恒为 False：number 类型的取值必须由有限性
	# 检查兜住（integer 类型会先被类型检查拒绝）。
	errors = _argument_type_errors(
		"boss_wizard",
		{"action": "run", "role": "candidate", "platform": "zhipin", "goal": "g", "timeout": math.nan},
	)
	assert any("必须是有限数值" in error for error in errors)


def test_unsafe_integral_float_is_not_normalized() -> None:
	arguments = {"number": float(2**53 + 1)}
	_normalize_integral_numbers("boss_show", arguments)
	assert isinstance(arguments["number"], float)


def test_unsafe_integral_float_is_rejected_with_precision_reason() -> None:
	errors = _argument_type_errors("boss_show", {"number": float(2**53 + 1)})
	assert any("超出 JSON 安全整数范围" in error for error in errors)


def test_safe_integral_float_still_normalizes() -> None:
	arguments = {"number": 2.0}
	_normalize_integral_numbers("boss_show", arguments)
	assert arguments["number"] == 2
	assert _argument_type_errors("boss_show", arguments) == []


def test_integer_array_skips_unsafe_elements() -> None:
	arguments = {"friend_ids": [float(2**53 + 1), 2.0]}
	_normalize_integral_numbers("boss_hr_last_messages", arguments)
	assert arguments["friend_ids"][0] == float(2**53 + 1)
	assert arguments["friend_ids"][1] == 2
	errors = _argument_type_errors("boss_hr_last_messages", arguments)
	assert any("friend_ids[0] 应为 integer" in error for error in errors)


def test_call_tool_rejects_non_finite_timeout(monkeypatch) -> None:
	from mcp.types import CallToolRequestParams

	monkeypatch.setattr(mcp_server, "TextContent", SimpleNamespace)
	monkeypatch.setattr(mcp_server, "CallToolResult", SimpleNamespace)

	async def fake_run(*args, **kwargs):
		raise AssertionError("非法 timeout 不应触达 CLI")

	monkeypatch.setattr(mcp_server, "_run_boss_async", fake_run)

	result = asyncio.run(
		mcp_server.call_tool(
			None,
			CallToolRequestParams(
				name="boss_wizard",
				arguments={
					"action": "run",
					"role": "candidate",
					"platform": "zhipin",
					"goal": "g",
					"timeout": math.inf,
				},
			),
		)
	)

	assert result.is_error is True
	payload = json.loads(result.content[0].text)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "必须是有限数值" in payload["error"]["message"]


# ── 4. enum 校验（Issue #2553） ────────────────────────────────────


@pytest.mark.parametrize("active", ["3day", "3日", " TODAY ", ""])
def test_search_keeps_active_aliases_and_explicit_clear(active) -> None:
	arguments = {"query": "go", "active": active}
	assert _argument_type_errors("boss_search", arguments) == []
	assert _build_args("boss_search", arguments)[-2:] == ["--active", active]


def test_blank_preset_active_value_is_rejected_at_entry() -> None:
	errors = _argument_type_errors("boss_preset_add", {"name": "n", "query": "q", "active": ""})
	assert "active 至少需要 1 个字符" in errors


def test_unknown_enum_value_lists_allowed_values() -> None:
	errors = _argument_type_errors("boss_search", {"query": "q", "sort": "tomorrow"})
	message = "\n".join(errors)
	assert "sort=tomorrow 不是允许的取值" in message
	assert "relevance" in message and "score" in message


@pytest.mark.parametrize("active", ["online", "3day", "3日"])
def test_preset_active_keeps_cli_aliases(active) -> None:
	arguments = {"name": "n", "query": "q", "active": active}
	assert _argument_type_errors("boss_preset_add", arguments) == []
	assert _build_args("boss_preset_add", arguments)[-2:] == ["--active", active]


def test_long_enum_error_stays_bounded() -> None:
	errors = _argument_type_errors("boss_preset_add", {"name": "n", "query": "q", "industry": "不存在"})
	message = "\n".join(errors)
	assert "不是允许的取值" in message
	assert "等 " in message and " 个取值" in message


@pytest.mark.parametrize(
	("value", "expected"),
	[
		("online", True),
		("today", True),
		("", False),
		(1, False),
		(True, False),
	],
)
def test_json_enum_allows_semantics(value, expected) -> None:
	enum = ["online", "today"]
	assert _json_enum_allows(enum, value) is expected


def test_json_enum_numeric_values_compare_by_value() -> None:
	assert _json_enum_allows([1, 2], 1.0) is True
	assert _json_enum_allows([1, 2], True) is False
	assert _json_enum_allows([True], 1) is False
