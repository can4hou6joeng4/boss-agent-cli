"""MCP schema 编码 action→参数矩阵（Issue #2795）。"""

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
	# JSON Schema 的 const / enum 区分 true 与 1；Python 的 bool 是 int 子类。
	if isinstance(value, bool) or isinstance(expected, bool):
		return isinstance(value, bool) and isinstance(expected, bool) and value is expected
	return value == expected


def _valid(instance: dict[str, Any], schema: dict[str, Any]) -> bool:
	"""最小 JSON Schema 判定。

	覆盖这些工具用到的 required / properties / anyOf / allOf / not / if-then-else /
	type / const / enum / minLength / minItems，避免测试引入非直接依赖 jsonschema。
	"""
	if not all(key in instance for key in schema.get("required", [])):
		return False
	for key, prop in schema.get("properties", {}).items():
		if key not in instance:
			continue
		value = instance[key]
		if "const" in prop and not _same_json_value(value, prop["const"]):
			return False
		enum = prop.get("enum")
		if isinstance(enum, list) and not any(_same_json_value(value, item) for item in enum):
			return False
		expected = prop.get("type")
		if expected == "string" and not isinstance(value, str):
			return False
		if expected == "boolean" and not isinstance(value, bool):
			return False
		if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
			return False
		if expected == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
			return False
		if expected == "array" and not isinstance(value, list):
			return False
		if expected == "object" and not isinstance(value, dict):
			return False
		min_length = prop.get("minLength")
		if isinstance(min_length, int) and isinstance(value, str) and len(value) < min_length:
			return False
		min_items = prop.get("minItems")
		if isinstance(min_items, int) and isinstance(value, list) and len(value) < min_items:
			return False
	if "anyOf" in schema and not any(_valid(instance, branch) for branch in schema["anyOf"]):
		return False
	if "allOf" in schema and not all(_valid(instance, branch) for branch in schema["allOf"]):
		return False
	if "not" in schema and _valid(instance, schema["not"]):
		return False
	if "if" in schema:
		branch = schema.get("then") if _valid(instance, schema["if"]) else schema.get("else")
		if branch is not None and not _valid(instance, branch):
			return False
	return True


_HR_JOBS_VALID = [
	{},
	{"action": "list"},
	{"action": "list", "page": 2},
	{"action": "online", "job_id": "x"},
	# page=1 是默认值，映射层按 #2339 容忍显式默认值。
	{"action": "online", "job_id": "x", "page": 1},
	{"action": "offline", "job_id": "x"},
]

_HR_JOBS_INVALID = [
	{"action": "online"},
	{"action": "list", "job_id": "x"},
	{"action": "online", "job_id": "x", "page": 2},
	{"action": "offline", "job_id": "x", "page": 2},
]


@pytest.mark.parametrize("arguments", _HR_JOBS_VALID)
def test_hr_jobs_schema_accepts_action_applicable_params(arguments: dict[str, Any]) -> None:
	assert _valid(arguments, _tool_schema("boss_hr_jobs"))


@pytest.mark.parametrize("arguments", _HR_JOBS_INVALID)
def test_hr_jobs_schema_rejects_action_inapplicable_params(arguments: dict[str, Any]) -> None:
	assert not _valid(arguments, _tool_schema("boss_hr_jobs"))


_CONFIG_VALID = [
	{"action": "list"},
	{"action": "get", "key": "k"},
	{"action": "set", "key": "k", "value": "v"},
	# 空串是显式清空配置值（Issue #1640），不能因 value 条件必填被 minLength 拒绝。
	{"action": "set", "key": "k", "value": ""},
]

_CONFIG_INVALID = [
	{"action": "list", "value": "v"},
	{"action": "get", "key": "k", "value": "v"},
	{"action": "reset", "key": "k", "value": "v"},
	{"action": "get"},
	{"action": "set", "key": "k"},
]


@pytest.mark.parametrize("arguments", _CONFIG_VALID)
def test_config_schema_accepts_action_applicable_params(arguments: dict[str, Any]) -> None:
	assert _valid(arguments, _tool_schema("boss_config"))


@pytest.mark.parametrize("arguments", _CONFIG_INVALID)
def test_config_schema_rejects_action_inapplicable_params(arguments: dict[str, Any]) -> None:
	assert not _valid(arguments, _tool_schema("boss_config"))


_WIZARD_VALID = [
	{"action": "run", "role": "candidate", "platform": "zhipin", "goal": "search"},
	{"action": "run", "role": "candidate", "platform": "zhipin", "goal": "search", "inputs": {"a": 1}, "requested_steps": ["s"]},
	{"action": "status", "run_id": "r"},
	{"action": "resume", "run_id": "r", "timeout": 5},
	# timeout 只对 run / resume 生效；max_retries 对四种 action 都可用（Issue #3013）。
	{"action": "stop", "run_id": "r", "max_retries": 1},
]

_WIZARD_INVALID = [
	{"action": "status"},
	{"action": "status", "run_id": "r", "role": "candidate"},
	{"action": "stop", "run_id": "r", "inputs": {}},
	{"action": "status", "run_id": "r", "requested_steps": ["s"]},
	# status / stop 不执行 --timeout，schema 与 CLI / 映射层一致拒绝（Issue #3013）。
	{"action": "stop", "run_id": "r", "timeout": 5},
	{"action": "status", "run_id": "r", "timeout": 5, "max_retries": 1},
	{"action": "run", "role": "candidate", "platform": "zhipin", "goal": "search", "run_id": "r"},
]


@pytest.mark.parametrize("arguments", _WIZARD_VALID)
def test_wizard_schema_accepts_action_applicable_params(arguments: dict[str, Any]) -> None:
	assert _valid(arguments, _tool_schema("boss_wizard"))


@pytest.mark.parametrize("arguments", _WIZARD_INVALID)
def test_wizard_schema_rejects_action_inapplicable_params(arguments: dict[str, Any]) -> None:
	assert not _valid(arguments, _tool_schema("boss_wizard"))
