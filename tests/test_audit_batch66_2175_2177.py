"""MCP 入口参数校验、映射与只读分类的边界收口（Issue #2175 / #2176 / #2177）。

- action 是 schema enum 之外的值时不能原样拼进 argv（`--` / `--help`）；
- `config set` 缺 key、`crawl_shortlist` 的 selectors+all 互斥在映射层拒绝；
- JSON 数字 ``1.0`` 按 schema 语义归一成整数；
- 显式空 resume 原样下发，让 CLI 的 INVALID_PARAM 诊断生效；
- 超时估算只看 ``--`` 之前的选项，不把位置参数误读成开关；
- search.active 空串、annotate 空数组与 dry-run / config list 的只读分类。
"""

from __future__ import annotations

import pytest

from boss_agent_cli import mcp_server
from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.mcp_tools import TOOLS


def _tool(name: str):
	return next(tool for tool in TOOLS if tool.name == name)


# ── Issue #2175：入口参数校验 ─────────────────────────────────────


@pytest.mark.parametrize("action", ["--", "--help", "drop"])
def test_config_rejects_unknown_action(action: str) -> None:
	with pytest.raises(ValueError, match="action 应为"):
		_build_args("boss_config", {"action": action})


@pytest.mark.parametrize("action", ["--help", "bogus"])
def test_hr_jobs_rejects_unknown_action(action: str) -> None:
	with pytest.raises(ValueError, match="action 应为"):
		_build_args("boss_hr_jobs", {"action": action})


def test_config_set_requires_key() -> None:
	# 缺 key 时 value 会被补进 KEY 位置，CLI 报的是缺少 VALUE。
	with pytest.raises(KeyError, match="key"):
		_build_args("boss_config", {"action": "set", "value": "x"})


def test_config_set_builds_key_and_value() -> None:
	assert _build_args("boss_config", {"action": "set", "key": "log_level", "value": "debug"}) == [
		"config",
		"set",
		"log_level",
		"debug",
	]


@pytest.mark.parametrize("all_value", [False, True])
def test_crawl_shortlist_rejects_selectors_with_all(all_value: bool) -> None:
	with pytest.raises(ValueError, match="selectors 与 all"):
		_build_args("boss_crawl_shortlist", {"run_id": "run-1", "selectors": ["1"], "all": all_value})


def test_crawl_shortlist_single_mode_still_builds() -> None:
	assert _build_args("boss_crawl_shortlist", {"run_id": "run-1", "all": True}) == [
		"crawl",
		"shortlist",
		"run-1",
		"--all",
	]
	assert _build_args("boss_crawl_shortlist", {"run_id": "run-1", "selectors": ["1"]}) == [
		"crawl",
		"shortlist",
		"run-1",
		"--selector",
		"1",
	]


def test_integral_float_normalized_for_integer_fields() -> None:
	arguments: dict[str, object] = {"page": 1.0}

	mcp_server._normalize_integral_numbers("boss_recommend", arguments)

	assert arguments == {"page": 1}
	assert mcp_server._argument_type_errors("boss_recommend", arguments) == []


def test_integral_float_normalized_in_integer_arrays() -> None:
	arguments: dict[str, object] = {"friend_ids": [1.0, 2]}

	mcp_server._normalize_integral_numbers("boss_hr_last_messages", arguments)

	assert arguments == {"friend_ids": [1, 2]}
	assert mcp_server._argument_type_errors("boss_hr_last_messages", arguments) == []


def test_non_integral_float_still_rejected() -> None:
	arguments: dict[str, object] = {"page": 1.5}

	mcp_server._normalize_integral_numbers("boss_recommend", arguments)

	assert mcp_server._argument_type_errors("boss_recommend", arguments)


# ── Issue #2176：映射与超时估算 ─────────────────────────────────────


def test_ai_tools_forward_explicit_empty_resume() -> None:
	assert _build_args("boss_ai_reply", {"recruiter_message": "消息", "resume": ""}) == [
		"ai",
		"reply",
		"消息",
		"--resume",
		"",
	]
	assert _build_args("boss_ai_interview_prep", {"jd_text": "jd", "resume": ""}) == [
		"ai",
		"interview-prep",
		"jd",
		"--resume",
		"",
	]
	assert _build_args("boss_ai_chat_coach", {"chat_text": "聊天", "resume": ""}) == [
		"ai",
		"chat-coach",
		"聊天",
		"--resume",
		"",
	]


def test_estimated_requests_ignores_positional_double_dash_values() -> None:
	args = _build_args("boss_batch_greet", {"query": "--dry-run"})
	assert "--" in args
	# 映射层总会显式下发 --count（MCP 默认 limit=5），估算按它计算而不是 dry-run。
	assert mcp_server._estimated_requests(tuple(args)) == 1 + 5

	args = _build_args("boss_export", {"query": "--count=1"})
	pages = -(-mcp_server._EXPORT_DEFAULT_COUNT // mcp_server._SEARCH_PAGE_SIZE)
	assert mcp_server._estimated_requests(tuple(args)) == pages


def test_estimated_requests_still_honors_real_dry_run() -> None:
	args = _build_args("boss_batch_greet", {"query": "Python", "dry_run": True})

	assert mcp_server._estimated_requests(tuple(args)) == 1


# ── Issue #2177：schema 与只读分类 ─────────────────────────────────


def test_search_active_schema_accepts_empty_clear_value() -> None:
	assert "" in _tool("boss_search").input_schema["properties"]["active"]["enum"]


def test_shortlist_annotate_schema_requires_non_empty_tag_arrays() -> None:
	properties = _tool("boss_shortlist_annotate").input_schema["properties"]

	assert properties["add_tags"]["minItems"] == 1
	assert properties["remove_tags"]["minItems"] == 1
	errors = mcp_server._argument_type_errors(
		"boss_shortlist_annotate",
		{"security_id": "sid", "job_id": "jid", "add_tags": []},
	)
	assert errors and "至少需要 1 个元素" in errors[0]


def test_dry_run_invocations_are_read_only() -> None:
	assert mcp_server._tool_may_write("boss_batch_greet", {"query": "Python", "dry_run": True}) is False
	assert mcp_server._tool_may_write("boss_batch_greet", {"query": "Python"}) is True


def test_config_read_actions_are_read_only() -> None:
	assert mcp_server._tool_may_write("boss_config", {"action": "list"}) is False
	assert mcp_server._tool_may_write("boss_config", {"action": "get", "key": "k"}) is False
	assert mcp_server._tool_may_write("boss_config", {"action": "set", "key": "k", "value": "v"}) is True
	assert mcp_server._tool_may_write("boss_config", {"action": "reset", "key": "k"}) is True
