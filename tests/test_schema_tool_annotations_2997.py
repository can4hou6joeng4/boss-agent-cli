"""schema mcp-tools 导出与运行时 tools/list 的只读注解一致（Issue #2997）。

`boss schema` 是能力真源：按 `--format mcp-tools` 离线注册工具的宿主，必须拿到
与服务器 `tools/list` 相同的 `readOnlyHint`；否则同一批工具在导出层看不到只读
提示，只能把全部调用按写处理。
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from boss_agent_cli import mcp_server
from boss_agent_cli.main import cli
from boss_agent_cli.mcp_tools import TOOLS
from boss_agent_cli.schema.data import SCHEMA_DATA
from boss_agent_cli.schema.formats import format_mcp_tools


def _exported_by_name() -> dict[str, dict]:
	return {tool["name"]: tool for tool in format_mcp_tools(SCHEMA_DATA)}


def test_schema_export_declares_boolean_read_only_hint_for_every_tool() -> None:
	exported = format_mcp_tools(SCHEMA_DATA)
	assert [tool["name"] for tool in exported] == [tool.name for tool in TOOLS]
	for tool in exported:
		annotations = tool.get("annotations")
		assert isinstance(annotations, dict), tool["name"]
		assert isinstance(annotations.get("readOnlyHint"), bool), tool["name"]
		# 其他 hint 维持规范默认值：不写明比误标安全。
		assert set(annotations) == {"readOnlyHint"}, tool["name"]


def test_schema_export_matches_runtime_tools_list_annotations() -> None:
	exported = _exported_by_name()
	runtime = {tool.name: tool for tool in mcp_server._annotated_tools()}
	assert set(exported) == set(runtime)
	for name, tool in runtime.items():
		assert tool.annotations is not None, name
		assert exported[name]["annotations"]["readOnlyHint"] is tool.annotations.read_only_hint, name


def test_static_read_only_rules_reject_parameter_dependent_writes() -> None:
	from boss_agent_cli.schema.tool_policy import static_read_only

	exported = _exported_by_name()

	def read_only(name: str) -> bool:
		return bool(exported[name]["annotations"]["readOnlyHint"])

	for name in ("boss_schema", "boss_search", "boss_resume_show", "boss_crawl_results"):
		assert read_only(name) is True, name
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
	):
		assert read_only(name) is False, name
	# 未列入白名单的工具（含未来新增）一律保守按写处理。
	assert static_read_only("boss_unknown_tool", {"type": "object", "properties": {}}) is False


def test_schema_mcp_tools_cli_output_carries_annotations(tmp_path) -> None:
	result = CliRunner().invoke(
		cli,
		["--data-dir", str(tmp_path), "schema", "--format", "mcp-tools"],
	)
	assert result.exit_code == 0, result.output
	tools = json.loads(result.output)["data"]["tools"]
	assert len(tools) == len(TOOLS)
	assert all(tool["annotations"]["readOnlyHint"] is False for tool in tools if tool["name"] == "boss_config")
	assert all(tool["annotations"]["readOnlyHint"] is True for tool in tools if tool["name"] == "boss_search")
