"""boss_agent_run 的 resume 需要显式 dry_run=false（Issue #2798）。"""

from __future__ import annotations

from typing import Any

import pytest

from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.schema.tool_catalog import build_tool_catalog


def _schema() -> dict[str, Any]:
	for tool in build_tool_catalog():
		if tool.name == "boss_agent_run":
			return tool.input_schema
	raise AssertionError("boss_agent_run 未注册")


def _same_json_value(value: Any, expected: Any) -> bool:
	if isinstance(value, bool) or isinstance(expected, bool):
		return isinstance(value, bool) and isinstance(expected, bool) and value is expected
	return value == expected


def _valid(instance: dict[str, Any], schema: dict[str, Any]) -> bool:
	"""最小 JSON Schema 判定：required / properties(const,type) / if-then-else。"""
	if not all(key in instance for key in schema.get("required", [])):
		return False
	for key, prop in schema.get("properties", {}).items():
		if key not in instance:
			continue
		value = instance[key]
		if "const" in prop and not _same_json_value(value, prop["const"]):
			return False
		expected = prop.get("type")
		if expected == "boolean" and not isinstance(value, bool):
			return False
		if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
			return False
	if "if" in schema:
		branch = schema.get("then") if _valid(instance, schema["if"]) else schema.get("else")
		if branch is not None and not _valid(instance, branch):
			return False
	return True


_VALID = [
	{},
	{"dry_run": True},
	{"dry_run": False},
	{"resume": False},
	{"resume": False, "dry_run": True},
	{"resume": True, "dry_run": False},
	{"limit": 5},
	{"resume": True, "dry_run": False, "limit": 5},
]

_INVALID = [
	# 缺省 dry_run=true 时 resume 必然被映射层拒绝（Issue #1591）。
	{"resume": True},
	{"resume": True, "dry_run": True},
]


@pytest.mark.parametrize("arguments", _VALID)
def test_agent_run_schema_accepts_mapping_supported_combinations(arguments: dict[str, Any]) -> None:
	assert _valid(arguments, _schema())
	_build_args("boss_agent_run", dict(arguments))


@pytest.mark.parametrize("arguments", _INVALID)
def test_agent_run_schema_rejects_dry_run_with_resume(arguments: dict[str, Any]) -> None:
	assert not _valid(arguments, _schema())
