"""boss_watch_run / boss_crawl_shortlist 的 schema 互斥约束（Issue #2792 / #2794）。"""

from __future__ import annotations

from typing import Any

import pytest

from boss_agent_cli.schema.tool_catalog import build_tool_catalog


def _tool_schema(name: str) -> dict[str, Any]:
	for tool in build_tool_catalog():
		if tool.name == name:
			return tool.input_schema
	raise AssertionError(f"{name} 未注册")


def _same_json_value(value: Any, expected: Any) -> bool:
	# JSON Schema 的 const 区分 true 与 1；Python 的 bool 是 int 子类，必须显式分开。
	if isinstance(value, bool) or isinstance(expected, bool):
		return isinstance(value, bool) and isinstance(expected, bool) and value is expected
	return value == expected


def _valid(instance: dict[str, Any], schema: dict[str, Any]) -> bool:
	"""最小 JSON Schema 判定。

	只覆盖这些工具实际用到的 required / properties / anyOf / not / type /
	minLength / minItems，避免测试引入 jsonschema 这个非直接依赖。语义与
	Draft 2020-12 一致：``not`` 要求子 schema 校验失败。
	"""
	if not all(key in instance for key in schema.get("required", [])):
		return False
	for key, prop in schema.get("properties", {}).items():
		if key not in instance:
			continue
		value = instance[key]
		if "const" in prop and not _same_json_value(value, prop["const"]):
			return False
		expected = prop.get("type")
		if expected == "string" and not isinstance(value, str):
			return False
		if expected == "boolean" and not isinstance(value, bool):
			return False
		if expected == "array" and not isinstance(value, list):
			return False
		min_length = prop.get("minLength")
		if isinstance(min_length, int) and isinstance(value, str) and len(value) < min_length:
			return False
		min_items = prop.get("minItems")
		if isinstance(min_items, int) and isinstance(value, list) and len(value) < min_items:
			return False
	if "anyOf" in schema and not any(_valid(instance, branch) for branch in schema["anyOf"]):
		return False
	if "not" in schema and _valid(instance, schema["not"]):
		return False
	return True


_WATCH_RUN_VALID = [
	{"all": True},
	{"name": "晚班"},
	# all=false 等同省略，与 name 同时出现不算冲突。
	{"name": "晚班", "all": False},
]

_WATCH_RUN_INVALID = [
	{},
	{"all": False},
	{"name": ""},
	{"name": "晚班", "all": True},
]


@pytest.mark.parametrize("arguments", _WATCH_RUN_VALID)
def test_watch_run_schema_accepts_exactly_one_selector(arguments: dict[str, Any]) -> None:
	assert _valid(arguments, _tool_schema("boss_watch_run"))


@pytest.mark.parametrize("arguments", _WATCH_RUN_INVALID)
def test_watch_run_schema_rejects_missing_or_conflicting_selector(arguments: dict[str, Any]) -> None:
	assert not _valid(arguments, _tool_schema("boss_watch_run"))


_SHORTLIST_VALID = [
	{"run_id": "r", "selectors": ["s1"]},
	{"run_id": "r", "all": True},
	{"run_id": "r", "selectors": ["s1"], "all": False},
]

_SHORTLIST_INVALID = [
	{"run_id": "r"},
	{"run_id": "r", "selectors": []},
	{"run_id": "r", "selectors": ["s1"], "all": True},
]


@pytest.mark.parametrize("arguments", _SHORTLIST_VALID)
def test_crawl_shortlist_schema_accepts_selectors_or_all(arguments: dict[str, Any]) -> None:
	assert _valid(arguments, _tool_schema("boss_crawl_shortlist"))


@pytest.mark.parametrize("arguments", _SHORTLIST_INVALID)
def test_crawl_shortlist_schema_rejects_missing_or_conflicting_selection(arguments: dict[str, Any]) -> None:
	assert not _valid(arguments, _tool_schema("boss_crawl_shortlist"))


def test_not_subschemas_require_both_mutually_exclusive_keys() -> None:
	# not 只写一个键时，另一个键缺失会让子 schema 通过，从而误拒合法输入（#2794）。
	assert set(_tool_schema("boss_watch_run")["not"]["required"]) == {"name", "all"}
	assert set(_tool_schema("boss_crawl_shortlist")["not"]["required"]) == {"selectors", "all"}


def test_watch_run_schema_rejects_blank_name_at_entry() -> None:
	# name 进入条件必填后由 _conditionally_required_string_names 统一补 minLength，
	# 与 search 的 query / url / preset 同口径（空名不必进子进程才失败）。
	assert _tool_schema("boss_watch_run")["properties"]["name"].get("minLength") == 1
