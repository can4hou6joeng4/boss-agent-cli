"""MCP schema 的数组元素约束必须与 CLI 校验同口径（Issue #2976）。

`boss_crawl_shortlist.selectors` 与 `boss_wizard.requested_steps` 的元素在
CLI 侧会按 ``INVALID_PARAM`` 拒绝空串与重复项，但 schema 没有声明
``items.minLength`` / ``uniqueItems``，宿主按 schema 校验通过后才在子进程失败。
入口校验（``_argument_type_errors``）必须按同一份 schema 提前拒绝。
"""

from __future__ import annotations

import pytest

from boss_agent_cli.mcp_server import _MAX_ELEMENT_TYPE_ERRORS, _argument_type_errors
from boss_agent_cli.mcp_tools import TOOLS


def _tool(name: str):
	return next(tool for tool in TOOLS if tool.name == name)


def test_crawl_shortlist_selectors_declare_element_bounds() -> None:
	selectors = _tool("boss_crawl_shortlist").input_schema["properties"]["selectors"]

	assert selectors["minItems"] == 1
	assert selectors["uniqueItems"] is True
	assert selectors["items"]["minLength"] == 1


def test_wizard_requested_steps_declare_element_bounds() -> None:
	steps = _tool("boss_wizard").input_schema["properties"]["requested_steps"]

	assert steps["minItems"] == 1
	assert steps["uniqueItems"] is True
	assert steps["items"]["minLength"] == 1


def test_crawl_shortlist_rejects_blank_and_duplicate_selectors() -> None:
	blank = _argument_type_errors("boss_crawl_shortlist", {"run_id": "run-1", "selectors": [""]})
	assert blank, "空 selector 必须在入口被拒绝"
	assert any("selectors[0]" in message and "字符" in message for message in blank)

	duplicate = _argument_type_errors(
		"boss_crawl_shortlist", {"run_id": "run-1", "selectors": ["csel_1", "csel_1"]}
	)
	assert duplicate, "重复 selector 必须在入口被拒绝"
	assert any("selectors[1]" in message and "重复" in message for message in duplicate)

	assert _argument_type_errors(
		"boss_crawl_shortlist", {"run_id": "run-1", "selectors": ["csel_1", "csel_2"]}
	) == []


def test_wizard_rejects_blank_and_duplicate_requested_steps() -> None:
	blank = _argument_type_errors(
		"boss_wizard",
		{"action": "run", "role": "candidate", "platform": "zhipin", "goal": "job_search", "requested_steps": [""]},
	)
	assert blank, "空步骤名必须在入口被拒绝"
	assert any("requested_steps[0]" in message and "字符" in message for message in blank)

	duplicate = _argument_type_errors(
		"boss_wizard",
		{
			"action": "run",
			"role": "candidate",
			"platform": "zhipin",
			"goal": "job_search",
			"requested_steps": ["candidate_search", "candidate_search"],
		},
	)
	assert duplicate, "重复步骤必须在入口被拒绝"
	assert any("requested_steps[1]" in message and "重复" in message for message in duplicate)
	assert _argument_type_errors(
		"boss_wizard", {"requested_steps": ["candidate_search", "candidate_recommend"]}
	) == []


@pytest.mark.parametrize(
	("tool_name", "key"),
	[("boss_crawl_shortlist", "selectors"), ("boss_wizard", "requested_steps")],
)
def test_element_type_errors_precede_duplicate_errors(tool_name: str, key: str) -> None:
	errors = _argument_type_errors(tool_name, {key: ["same", "same", {"nested": []}, ["invalid"]]})

	assert errors == [
		f"{key}[2] 应为 string，收到 dict",
		f"{key}[3] 应为 string，收到 list",
		f"{key}[1] 与前面的元素重复",
	]


@pytest.mark.parametrize(
	("tool_name", "key"),
	[("boss_crawl_shortlist", "selectors"), ("boss_wizard", "requested_steps")],
)
@pytest.mark.parametrize("item", ["same", "", None])
def test_large_array_errors_stay_bounded(tool_name: str, key: str, item: str | None) -> None:
	errors = _argument_type_errors(tool_name, {key: [item] * 10_000})

	if item is None:
		assert len(errors) == _MAX_ELEMENT_TYPE_ERRORS + 1
		assert all("重复" not in message for message in errors)
		assert errors[-1] == f"{key} 另有 {10_000 - _MAX_ELEMENT_TYPE_ERRORS} 个元素类型错误"
	else:
		assert sum("重复" in message for message in errors) == 1
		if item:
			assert len(errors) == 1
		else:
			assert len(errors) == _MAX_ELEMENT_TYPE_ERRORS + 2
			assert errors[-1] == f"{key} 另有 {10_000 - _MAX_ELEMENT_TYPE_ERRORS} 个元素取值错误"
