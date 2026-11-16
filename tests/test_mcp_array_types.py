"""MCP 必须按数组 items 类型拒绝错误元素，不把它们转成命令字符串（Issue #1105）。"""

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.types import CallToolRequestParams

from boss_agent_cli import mcp_server
from boss_agent_cli.mcp_tools import TOOLS


_ARRAY_PROPERTIES = [
	(tool, key, (prop.get("items") or {}).get("type"))
	for tool in TOOLS
	for key, prop in tool.input_schema.get("properties", {}).items()
	if prop.get("type") == "array"
]
_ARRAY_IDS = [f"{tool.name}-{key}" for tool, key, _ in _ARRAY_PROPERTIES]


def _min_items(tool, key) -> int:
	value = tool.input_schema["properties"][key].get("minItems")
	return value if isinstance(value, int) and not isinstance(value, bool) else 0


# 声明 minItems 的属性上，空数组会被当作「没传」并触达平台（Issue #1218），
# 因此「空数组原样保留」只覆盖没有下限的属性；有下限的另行断言被拒绝。
_KEEP_VALUE_CASES = [
	(tool, key, expected, empty)
	for tool, key, expected in _ARRAY_PROPERTIES
	for empty in (False, True)
	if not (empty and _min_items(tool, key))
]
_KEEP_VALUE_IDS = [
	f"{tool.name}-{key}-{'empty' if empty else 'values'}" for tool, key, _, empty in _KEEP_VALUE_CASES
]
_REQUIRED_ITEM_CASES = [
	(tool, key, minimum) for tool, key, _ in _ARRAY_PROPERTIES if (minimum := _min_items(tool, key))
]
_REQUIRED_ITEM_IDS = [f"{tool.name}-{key}" for tool, key, _ in _REQUIRED_ITEM_CASES]
_VALID_ELEMENTS = {"string": ["synthetic", "--help"], "integer": [7, 101]}
_INVALID_ELEMENTS = {
	"string": [None, False, True, 1, 1.5, {}, []],
	# 数字字符串也不是 integer：`str()` 拼装会让它在 CLI 侧被隐式接受。
	"integer": [None, False, True, 1.5, "synthetic", "101", {}, []],
}
_INVALID_CASES = [
	(tool, key, expected, bad)
	for tool, key, expected in _ARRAY_PROPERTIES
	for bad in _INVALID_ELEMENTS.get(expected, [])
]


def _arguments(tool) -> dict:
	properties = tool.input_schema["properties"]
	return {
		key: properties[key].get("default", properties[key].get("enum", ["synthetic"])[0])
		for key in tool.input_schema.get("required", [])
	}


def _call(tool, arguments):
	return asyncio.run(mcp_server.call_tool(None, CallToolRequestParams(name=tool.name, arguments=arguments)))


@pytest.fixture
def fake_cli(monkeypatch):
	# 其它测试文件可能已把 mcp 换成导入替身；用可读的轻量对象断言结果形状。
	monkeypatch.setattr(mcp_server, "TextContent", SimpleNamespace)
	monkeypatch.setattr(mcp_server, "CallToolResult", SimpleNamespace)
	run = AsyncMock(return_value={"ok": True, "error": None})
	monkeypatch.setattr(mcp_server, "_run_boss_async", run)
	return run


def test_every_array_argument_declares_a_checked_element_type():
	"""新增数组参数必须声明受支持的 items.type，否则元素校验会被静默跳过。"""
	assert _ARRAY_PROPERTIES, "工具目录里应至少有一个数组参数"
	for tool, key, expected in _ARRAY_PROPERTIES:
		assert expected in _INVALID_ELEMENTS, f"{tool.name}.{key} 的 items.type={expected!r} 没有元素校验用例"
		assert expected in mcp_server._JSON_TYPE_CHECKS, f"{tool.name}.{key}"


@pytest.mark.parametrize(
	("tool", "key", "expected", "bad"),
	_INVALID_CASES,
	ids=[f"{tool.name}-{key}-{type(bad).__name__}-{index}" for index, (tool, key, _, bad) in enumerate(_INVALID_CASES)],
)
def test_invalid_array_elements_fail_before_mapping(tool, key, expected, bad, fake_cli, monkeypatch):
	arguments = {**_arguments(tool), key: [_VALID_ELEMENTS[expected][0], bad]}
	original = deepcopy(arguments)
	mapping = MagicMock(side_effect=AssertionError("类型错误不应进入参数映射"))
	monkeypatch.setattr(mcp_server, "_build_args", mapping)

	result = _call(tool, arguments)

	assert result.is_error is True
	payload = json.loads(result.content[0].text)
	assert set(payload) == {"ok", "schema_version", "command", "data", "pagination", "error", "hints"}
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert f"{key}[1] 应为 {expected}，收到 {type(bad).__name__}" in payload["error"]["message"]
	assert f"{key}[0]" not in payload["error"]["message"]
	assert arguments == original
	mapping.assert_not_called()
	fake_cli.assert_not_called()


@pytest.mark.parametrize(("tool", "key", "expected", "empty"), _KEEP_VALUE_CASES, ids=_KEEP_VALUE_IDS)
def test_valid_and_empty_arrays_keep_their_values(tool, key, expected, empty, fake_cli):
	arguments = {**_arguments(tool), key: [] if empty else list(_VALID_ELEMENTS[expected])}
	original = deepcopy(arguments)
	expected_args = mcp_server._build_args(tool.name, arguments)

	result = _call(tool, arguments)

	assert result.is_error is False
	assert json.loads(result.content[0].text)["ok"] is True
	assert arguments == original
	fake_cli.assert_awaited_once_with(*expected_args, may_write=mcp_server._tool_may_write(tool.name, arguments))


@pytest.mark.parametrize(("tool", "key", "minimum"), _REQUIRED_ITEM_CASES, ids=_REQUIRED_ITEM_IDS)
def test_empty_array_is_rejected_when_schema_declares_min_items(tool, key, minimum, fake_cli, monkeypatch):
	"""空数组与缺失参数不可区分：声明了 minItems 就必须在映射前拒绝（Issue #1218）。"""
	arguments = {**_arguments(tool), key: []}
	original = deepcopy(arguments)
	mapping = MagicMock(side_effect=AssertionError("空数组不应进入参数映射"))
	monkeypatch.setattr(mcp_server, "_build_args", mapping)

	result = _call(tool, arguments)

	assert result.is_error is True
	payload = json.loads(result.content[0].text)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert f"{key} 至少需要 {minimum} 个元素" in payload["error"]["message"]
	assert arguments == original
	mapping.assert_not_called()
	fake_cli.assert_not_called()


def test_min_items_declarations_are_positive_integers():
	"""minItems=0 等于没有约束，应直接省略；布尔值会绕过校验。"""
	assert _REQUIRED_ITEM_CASES, "friend_ids 应声明 minItems，空数组用例不能被静默跳过"
	for tool, key, _ in _ARRAY_PROPERTIES:
		raw = tool.input_schema["properties"][key].get("minItems")
		if raw is None:
			continue
		assert isinstance(raw, int) and not isinstance(raw, bool) and raw >= 1, f"{tool.name}.{key} 的 minItems={raw!r}"


@pytest.mark.parametrize(("tool", "key", "_expected"), _ARRAY_PROPERTIES, ids=_ARRAY_IDS)
def test_array_container_type_still_fails_before_cli(tool, key, _expected, fake_cli):
	result = _call(tool, {**_arguments(tool), key: "synthetic"})

	assert result.is_error is True
	payload = json.loads(result.content[0].text)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert f"{key} 应为 array，收到 str" in payload["error"]["message"]
	assert f"{key}[" not in payload["error"]["message"]
	fake_cli.assert_not_called()


@pytest.mark.parametrize(("tool", "key", "expected"), _ARRAY_PROPERTIES, ids=_ARRAY_IDS)
def test_element_errors_stay_bounded_for_large_arrays(tool, key, expected, fake_cli):
	"""错误信封不能随输入规模膨胀：只逐条列出前几处，其余报数量。"""
	limit = mcp_server._MAX_ELEMENT_TYPE_ERRORS
	bad = [_VALID_ELEMENTS[expected][0], *([None] * 5000)]

	result = _call(tool, {**_arguments(tool), key: bad})

	assert result.is_error is True
	message = json.loads(result.content[0].text)["error"]["message"]
	for index in range(1, limit + 1):
		assert f"{key}[{index}] 应为 {expected}，收到 NoneType" in message
	assert f"{key}[{limit + 1}]" not in message
	assert f"{key} 另有 {5000 - limit} 个元素类型错误" in message
	assert len(message) < 400
	fake_cli.assert_not_called()


def test_errors_in_several_arguments_are_reported_together(fake_cli):
	tool = next(item for item in TOOLS if item.name == "boss_shortlist_annotate")
	arguments = {**_arguments(tool), "add_tags": [False], "remove_tags": ["synthetic", {"unexpected": "object"}]}

	result = _call(tool, arguments)

	assert result.is_error is True
	message = json.loads(result.content[0].text)["error"]["message"]
	assert "add_tags[0] 应为 string，收到 bool" in message
	assert "remove_tags[1] 应为 string，收到 dict" in message
	assert "另有" not in message
	fake_cli.assert_not_called()
