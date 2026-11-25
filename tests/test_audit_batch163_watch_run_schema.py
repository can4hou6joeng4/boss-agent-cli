"""boss_watch_run 的 schema 编码 name / all 互斥（Issue #2792）。"""

from __future__ import annotations

from typing import Any

from boss_agent_cli.schema.tool_catalog import build_tool_catalog


def _watch_run_schema() -> dict[str, Any]:
	for tool in build_tool_catalog():
		if tool.name == "boss_watch_run":
			return tool.input_schema
	raise AssertionError("boss_watch_run 未注册")


def test_watch_run_schema_requires_exactly_one_of_name_and_all() -> None:
	schema = _watch_run_schema()
	assert schema.get("required") == []
	assert {"required": ["name"]} in schema.get("anyOf", [])
	assert {"properties": {"all": {"const": True}}, "required": ["all"]} in schema.get("anyOf", [])
	# 同时给出 name 与 all=true 必须被拒绝；all=false 等同省略，不算冲突。
	assert schema.get("not") == {"required": ["name"], "properties": {"all": {"const": True}}}


def test_watch_run_schema_rejects_blank_name_at_entry() -> None:
	# name 进入条件必填后由 _conditionally_required_string_names 统一补 minLength，
	# 与 search 的 query / url / preset 同口径（空名不必进子进程才失败）。
	schema = _watch_run_schema()
	assert schema["properties"]["name"].get("minLength") == 1
