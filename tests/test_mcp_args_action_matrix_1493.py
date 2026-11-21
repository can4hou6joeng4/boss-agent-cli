"""action 条件参数不再被静默丢弃（Issue #1493）。

`_build_args` 按 action 分派的工具（wizard / config / hr jobs）原先只取「该 action
用得到」的键，其余键直接消失：``{"action": "run", ..., "run_id": "abc"}`` 会新建
run、``{"action": "list", "job_id": "abc"}`` 会变成无过滤列表，调用方却收到
``ok:true``。这里锁定三件事：越界组合返回 ``INVALID_PARAM``、合法组合照旧构造
argv、缺省 action 与显式 action 行为一致。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolRequestParams

from boss_agent_cli import mcp_server
from boss_agent_cli.mcp_args import _build_args


#: (工具, 参数, 错误消息里必须出现的片段)。
_INVALID_CASES = [
	("boss_wizard", {"action": "run", "goal": "g", "run_id": "abc"}, "run_id"),
	("boss_wizard", {"action": "resume", "run_id": "r", "goal": "g"}, "goal"),
	("boss_wizard", {"action": "resume", "run_id": "r", "inputs": {"x": 1}}, "inputs"),
	("boss_wizard", {"action": "status", "run_id": "r", "requested_steps": ["s"]}, "requested_steps"),
	("boss_wizard", {"action": "stop", "run_id": "r", "role": "candidate"}, "role"),
	("boss_config", {"action": "list", "key": "log_level"}, "key"),
	("boss_config", {"action": "get", "key": "k", "value": "x"}, "value"),
	("boss_config", {"action": "reset", "key": "k", "value": "x"}, "value"),
	("boss_hr_jobs", {"action": "list", "job_id": "abc"}, "job_id"),
	("boss_hr_jobs", {"action": "online", "job_id": "abc", "page": 3}, "page"),
	# page=1 是 schema 默认值，自动补齐默认值的客户端会原样回传，按缺省处理；
	# 非默认值仍拒绝（Issue #2339）。
	("boss_hr_jobs", {"action": "offline", "job_id": "abc", "page": 2}, "page"),
]


@pytest.mark.parametrize(
	("tool_name", "arguments", "shown"),
	_INVALID_CASES,
	ids=[f"{tool}-{shown}" for tool, _, shown in _INVALID_CASES],
)
def test_build_args_rejects_incompatible_action_arguments(tool_name, arguments, shown):
	"""越界键带非空值时映射层直接抛 ValueError，不落进正常分支。"""
	with pytest.raises(ValueError) as excinfo:
		_build_args(tool_name, arguments)

	message = str(excinfo.value)
	assert tool_name in message
	assert shown in message
	assert f"action={arguments['action']}" in message
	# 提示正确用法：每个越界键都点名它属于哪个 action。
	assert "仅用于 action=" in message


@pytest.mark.parametrize(
	("tool_name", "arguments", "shown"),
	_INVALID_CASES,
	ids=[f"{tool}-{shown}" for tool, _, shown in _INVALID_CASES],
)
def test_call_tool_turns_incompatible_action_arguments_into_invalid_param(
	monkeypatch, tool_name, arguments, shown,
):
	"""`call_tool` 把 ValueError 转成 INVALID_PARAM 信封，且不启动 CLI。"""
	monkeypatch.setattr(mcp_server, "TextContent", SimpleNamespace)
	monkeypatch.setattr(mcp_server, "CallToolResult", SimpleNamespace)
	run = AsyncMock(side_effect=AssertionError("不兼容参数不应触达 CLI"))
	monkeypatch.setattr(mcp_server, "_run_boss_async", run)

	result = asyncio.run(
		mcp_server.call_tool(None, CallToolRequestParams(name=tool_name, arguments=arguments))
	)

	assert result.is_error is True
	payload = json.loads(result.content[0].text)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert shown in payload["error"]["message"]
	assert tool_name in payload["error"]["message"]
	run.assert_not_called()


def test_wizard_run_id_without_action_is_rejected_with_explicit_action_hint():
	"""schema 没有承诺缺省 action 会按 run_id 恢复；缺省仍是 run，拒绝并提示显式传。"""
	with pytest.raises(ValueError) as excinfo:
		_build_args("boss_wizard", {"run_id": "abc"})

	message = str(excinfo.value)
	assert "缺少 action" in message
	assert "run_id" in message
	assert "action=resume/status/stop" in message
	assert "显式" in message


def test_hr_jobs_job_id_without_action_matches_explicit_list_rejection():
	"""缺省 action 与显式 action=list 一致：job_id 同样被拒绝。"""
	with pytest.raises(ValueError, match="action=list"):
		_build_args("boss_hr_jobs", {"job_id": "abc"})
	with pytest.raises(ValueError, match="action=list"):
		_build_args("boss_hr_jobs", {"action": "list", "job_id": "abc"})


def test_multiple_incompatible_keys_are_reported_together():
	"""一次调用里多个越界键要一起指出，Agent 才能一轮改完。"""
	with pytest.raises(ValueError) as excinfo:
		_build_args("boss_config", {"action": "list", "key": "k", "value": "v"})

	message = str(excinfo.value)
	assert "key 仅用于 action=get/set/reset" in message
	assert "value 仅用于 action=set" in message


def test_blank_values_for_unused_keys_stay_equivalent_to_omitted():
	"""只拒绝值非空的越界键：空串 / 空对象与没传等价（Issue #1493）。"""
	assert _build_args("boss_config", {"action": "list", "key": "", "value": ""}) == ["config", "list"]
	assert _build_args("boss_hr_jobs", {"action": "list", "job_id": ""}) == ["hr", "jobs", "list"]
	assert _build_args("boss_hr_jobs", {"action": "online", "job_id": "77", "page": None}) == [
		"hr", "jobs", "online", "77",
	]
	assert _build_args("boss_wizard", {"action": "resume", "run_id": "r", "goal": "", "inputs": {}}) == [
		"wizard", "--resume", "r",
	]


def test_blank_run_id_without_action_keeps_default_run():
	"""空 run_id 不算越界：缺省 action 仍落回 run。"""
	assert _build_args("boss_wizard", {"run_id": ""}) == _build_args("boss_wizard", {})


def test_valid_wizard_actions_still_build_expected_argv():
	assert _build_args("boss_wizard", {"action": "resume", "run_id": "run-1"}) == [
		"wizard", "--resume", "run-1",
	]
	assert _build_args("boss_wizard", {"action": "status", "run_id": "run-1"}) == [
		"wizard", "--status", "run-1",
	]
	assert _build_args("boss_wizard", {"action": "stop", "run_id": "run-1"}) == [
		"wizard", "--stop", "run-1",
	]
	args = _build_args("boss_wizard", {
		"action": "run",
		"role": "candidate",
		"goal": "search",
		"inputs": {"query": "Python"},
		"requested_steps": ["search"],
		"timeout": 30,
		"max_retries": 2,
	})
	assert args[:2] == ["wizard", "--input-json"]
	assert json.loads(args[2]) == {
		"role": "candidate",
		"goal": "search",
		"inputs": {"query": "Python"},
		"requested_steps": ["search"],
	}
	assert args[3:] == ["--timeout", "30", "--max-retries", "2"]


def test_valid_config_actions_still_build_expected_argv():
	assert _build_args("boss_config", {"action": "list"}) == ["config", "list"]
	assert _build_args("boss_config", {"action": "get", "key": "log_level"}) == ["config", "get", "log_level"]
	assert _build_args("boss_config", {"action": "set", "key": "log_level", "value": "debug"}) == [
		"config", "set", "log_level", "debug",
	]
	assert _build_args("boss_config", {"action": "reset", "key": "log_level"}) == ["config", "reset", "log_level"]


def test_valid_hr_jobs_actions_still_build_expected_argv():
	assert _build_args("boss_hr_jobs", {"action": "list"}) == ["hr", "jobs", "list"]
	assert _build_args("boss_hr_jobs", {"action": "list", "page": 2}) == ["hr", "jobs", "list", "--page", "2"]
	assert _build_args("boss_hr_jobs", {"action": "online", "job_id": "77"}) == ["hr", "jobs", "online", "77"]
	assert _build_args("boss_hr_jobs", {"action": "offline", "job_id": "77"}) == ["hr", "jobs", "offline", "77"]


def test_default_action_matches_explicit_action_for_valid_arguments():
	"""缺省 action 与显式默认 action 生成相同 argv。"""
	assert _build_args("boss_hr_jobs", {"page": 2}) == _build_args(
		"boss_hr_jobs", {"action": "list", "page": 2},
	)
	assert _build_args("boss_wizard", {"goal": "g", "inputs": {}}) == _build_args(
		"boss_wizard", {"action": "run", "goal": "g", "inputs": {}},
	)
