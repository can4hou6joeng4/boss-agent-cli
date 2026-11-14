"""MCP 的显式 ``null`` 必须与缺键等价，不能先被类型检查拒绝（Issue #1108）。"""

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolRequestParams

from boss_agent_cli import mcp_server
from boss_agent_cli.mcp_tools import TOOLS


_PLACEHOLDERS = {
	"string": "synthetic",
	"integer": 7,
	"number": 1,
	"array": [],
	"object": {},
	"boolean": False,
}


def _placeholder(prop):
	if "default" in prop:
		return prop["default"]
	enum = prop.get("enum")
	if enum:
		return enum[0]
	return _PLACEHOLDERS.get(prop.get("type"))


def _base(tool) -> dict:
	properties = tool.input_schema["properties"]
	return {key: _placeholder(properties[key]) for key in tool.input_schema.get("required", [])}


def _optional_properties(tool):
	return [key for key in tool.input_schema.get("properties", {}) if key not in tool.input_schema.get("required", [])]


def _mappable(tool, arguments):
	try:
		return mcp_server._build_args(tool.name, arguments)
	except (KeyError, ValueError):
		return None


#: null 侧不做“能映射”过滤：映射层若开始对某个 ``None`` 抛错，这里要让用例
#: 失败而不是静默消失。基础参数映射不了的组合（缺必填等）本来就不是可选用例。
_OPTIONAL_CASES = [
	(tool, key)
	for tool in TOOLS
	if _mappable(tool, _base(tool)) is not None
	for key in _optional_properties(tool)
]
_REQUIRED_CASES = [(tool, key) for tool in TOOLS for key in tool.input_schema.get("required", [])]


def _call(tool_name: str, arguments: dict):
	return asyncio.run(mcp_server.call_tool(None, CallToolRequestParams(name=tool_name, arguments=arguments)))


@pytest.fixture
def fake_cli(monkeypatch):
	monkeypatch.setattr(mcp_server, "TextContent", SimpleNamespace)
	monkeypatch.setattr(mcp_server, "CallToolResult", SimpleNamespace)
	run = AsyncMock(return_value={"ok": True, "error": None})
	monkeypatch.setattr(mcp_server, "_run_boss_async", run)
	return run


@pytest.mark.parametrize(("tool", "key"), _OPTIONAL_CASES, ids=[f"{tool.name}-{key}" for tool, key in _OPTIONAL_CASES])
def test_explicit_null_matches_omitted_key(tool, key, fake_cli):
	"""可选参数显式传 null 时，生成的 argv 与写判定必须和完全不传一致。"""
	base = _base(tool)
	expected_args = mcp_server._build_args(tool.name, base)
	arguments = {**base, key: None}
	original = deepcopy(arguments)

	result = _call(tool.name, arguments)

	assert result.is_error is False, json.loads(result.content[0].text).get("error")
	fake_cli.assert_awaited_once_with(*expected_args, may_write=mcp_server._tool_may_write(tool.name, base))
	assert arguments == original


@pytest.mark.parametrize(("tool", "key"), _REQUIRED_CASES, ids=[f"{tool.name}-{key}" for tool, key in _REQUIRED_CASES])
def test_required_null_is_reported_as_missing(tool, key, fake_cli):
	"""必填参数传 null 时按缺参处理，不能把 str(None) 拼成 "None" 发给 CLI。"""
	arguments = {**_base(tool), key: None}

	result = _call(tool.name, arguments)

	assert result.is_error is True
	payload = json.loads(result.content[0].text)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert f"缺少必填参数：{key}" in payload["error"]["message"]
	fake_cli.assert_not_called()


def test_wizard_action_null_falls_back_to_run(fake_cli):
	"""文档承诺 `boss_wizard.action` 的 null 落回默认 run（Issue #1002）。"""
	arguments = {"role": "candidate", "goal": "search", "action": None}

	result = _call("boss_wizard", arguments)

	assert result.is_error is False
	expected = mcp_server._build_args("boss_wizard", {"role": "candidate", "goal": "search"})
	fake_cli.assert_awaited_once_with(*expected, may_write=True)


def test_all_null_arguments_are_ignored_like_an_empty_call(fake_cli):
	"""整份参数都是 null 时，等价于不传任何可选参数。"""
	tool = next(item for item in TOOLS if item.name == "boss_search")
	arguments = {"query": "synthetic", "city": None, "salary": None, "page": None, "sort": None}

	result = _call(tool.name, arguments)

	assert result.is_error is False
	expected = mcp_server._build_args(tool.name, {"query": "synthetic"})
	fake_cli.assert_awaited_once_with(*expected, may_write=False)


def test_null_for_an_unknown_argument_is_still_rejected(fake_cli):
	"""未知参数即使传 null 也不能被静默丢弃。"""
	result = _call("boss_search", {"query": "synthetic", "not_a_parameter": None})

	assert result.is_error is True
	assert "未知参数" in json.loads(result.content[0].text)["error"]["message"]
	fake_cli.assert_not_called()


def test_null_array_elements_remain_type_errors(fake_cli):
	"""数组内部的 null 元素仍按元素类型拒绝：null 只在顶层等价于省略。"""
	result = _call("boss_shortlist_annotate", {"security_id": "s", "job_id": "j", "add_tags": ["ok", None]})

	assert result.is_error is True
	assert "add_tags[1] 应为 string" in json.loads(result.content[0].text)["error"]["message"]
	fake_cli.assert_not_called()
