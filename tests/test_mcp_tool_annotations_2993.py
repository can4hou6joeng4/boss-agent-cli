"""MCP 工具只读注解回归（Issue #2993）。

`readOnlyHint` 规范缺省为 false；不注明会让宿主对 `boss_schema`、`boss_search`
这类只读调用也逐个确认。注解必须保守：参数取决于 action（`boss_config` /
`boss_hr_jobs` / `boss_wizard`）或可能写本地文件（带 `output` / `output_file`
的工具）时不能标成只读，否则宿主自动放行会藏住真实的写入。
"""

from __future__ import annotations

import asyncio

from boss_agent_cli import mcp_server
from boss_agent_cli.mcp_tools import TOOLS


_OUTPUT_ARGUMENT_KEYS = frozenset({"output", "output_file"})


def _properties(tool) -> dict:
	return tool.input_schema.get("properties", {})


def _tool_by_name(name: str):
	return next(tool for tool in mcp_server._annotated_tools() if tool.name == name)


def test_every_tool_exposes_boolean_read_only_hint() -> None:
	tools = mcp_server._annotated_tools()
	assert [tool.name for tool in tools] == [tool.name for tool in TOOLS]
	for tool in tools:
		assert tool.annotations is not None, tool.name
		assert isinstance(tool.annotations.read_only_hint, bool), tool.name


def test_static_read_only_set_matches_documented_rules() -> None:
	expected = {
		tool.name
		for tool in TOOLS
		if tool.name in mcp_server._READ_ONLY_TOOL_NAMES
		and tool.name not in mcp_server._ACTION_DIMENSION_TOOL_NAMES
		and not (_OUTPUT_ARGUMENT_KEYS & set(_properties(tool)))
	}
	actual = {
		tool.name
		for tool in mcp_server._annotated_tools()
		if tool.annotations is not None and tool.annotations.read_only_hint
	}
	assert actual == expected


def test_read_only_tools_never_declare_action_or_local_output() -> None:
	for tool in mcp_server._annotated_tools():
		assert tool.annotations is not None
		if not tool.annotations.read_only_hint:
			continue
		assert "action" not in _properties(tool), tool.name
		assert not (_OUTPUT_ARGUMENT_KEYS & set(_properties(tool))), tool.name


def test_parameter_dependent_write_tools_are_not_marked_read_only() -> None:
	for name in (
		"boss_config",
		"boss_wizard",
		"boss_hr_jobs",
		"boss_chat",
		"boss_export",
		"boss_digest",
		"boss_stats",
		"boss_resume_export",
		"boss_resume_import",
		"boss_resume_edit",
		"boss_hr_download_resume",
	):
		tool = _tool_by_name(name)
		assert tool.annotations is not None
		assert tool.annotations.read_only_hint is False, name


def test_list_tools_returns_annotated_copies() -> None:
	result = asyncio.run(mcp_server.list_tools(None))
	assert len(result.tools) == len(TOOLS)
	assert all(tool.annotations is not None for tool in result.tools)
	# 返回副本而不是改动 TOOLS 本身：wrapper 与兼容导出仍按原对象取用。
	assert all(tool.annotations is None for tool in TOOLS)


def test_other_hints_stay_at_spec_defaults() -> None:
	for tool in mcp_server._annotated_tools():
		assert tool.annotations is not None
		assert tool.annotations.destructive_hint is None
		assert tool.annotations.idempotent_hint is None
		assert tool.annotations.open_world_hint is None
