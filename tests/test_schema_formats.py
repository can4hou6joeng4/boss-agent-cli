"""Tests for boss schema --format 扩展（openai-tools / anthropic-tools）。"""

import json

from click.testing import CliRunner

from boss_agent_cli.schema.data import SCHEMA_DATA
from boss_agent_cli.schema.formats import (
	command_to_json_schema,
	format_anthropic_tools,
	format_mcp_tools,
	format_openai_tools,
)
from boss_agent_cli.schema.tool_catalog import build_tool_catalog
from boss_agent_cli.main import cli


def test_native_format_is_default():
	"""默认 --format 应等同于 native（原行为）。"""
	runner = CliRunner()
	result = runner.invoke(cli, ["schema"])
	assert result.exit_code == 0
	data = json.loads(result.output)["data"]
	assert "commands" in data
	assert len(data["commands"]) == len(SCHEMA_DATA["commands"])
	assert "error_codes" in data
	assert "format" not in data  # native 格式不带 format 字段


def test_openai_tools_format():
	"""--format openai-tools 输出符合 OpenAI Tools API 结构。"""
	runner = CliRunner()
	result = runner.invoke(cli, ["schema", "--format", "openai-tools"])
	assert result.exit_code == 0
	data = json.loads(result.output)["data"]
	assert data["format"] == "openai-tools"
	tools = data["tools"]
	assert len(tools) == len(build_tool_catalog())
	for tool in tools:
		assert tool["type"] == "function"
		assert "function" in tool
		fn = tool["function"]
		assert fn["name"].startswith("boss_")
		assert fn["description"]
		assert fn["parameters"]["type"] == "object"
		assert "properties" in fn["parameters"]


def test_anthropic_tools_format():
	"""--format anthropic-tools 输出符合 Claude Tool Use API 结构。"""
	runner = CliRunner()
	result = runner.invoke(cli, ["schema", "--format", "anthropic-tools"])
	assert result.exit_code == 0
	data = json.loads(result.output)["data"]
	assert data["format"] == "anthropic-tools"
	tools = data["tools"]
	assert len(tools) == len(build_tool_catalog())
	for tool in tools:
		assert tool["name"].startswith("boss_")
		assert tool["description"]
		assert tool["input_schema"]["type"] == "object"


def test_openai_and_anthropic_share_parameters_schema():
	"""两种格式参数 schema 应一致（只是外层包装不同）。"""
	oai = format_openai_tools(SCHEMA_DATA)
	anth = format_anthropic_tools(SCHEMA_DATA)

	oai_search = next(t["function"]["parameters"] for t in oai if t["function"]["name"] == "boss_search")
	anth_search = next(t["input_schema"] for t in anth if t["name"] == "boss_search")
	assert oai_search == anth_search


def test_tool_formats_keep_search_welfare_parameter():
	"""OpenAI / Anthropic tool schema 都必须暴露 search.welfare 参数。"""
	oai = format_openai_tools(SCHEMA_DATA)
	anth = format_anthropic_tools(SCHEMA_DATA)

	oai_search = next(t["function"]["parameters"] for t in oai if t["function"]["name"] == "boss_search")
	anth_search = next(t["input_schema"] for t in anth if t["name"] == "boss_search")
	assert "welfare" in oai_search["properties"]
	assert "福利筛选" in oai_search["properties"]["welfare"]["description"]
	assert "welfare" in anth_search["properties"]
	assert "福利筛选" in anth_search["properties"]["welfare"]["description"]


def test_tool_formats_describe_explicit_agent_resume():
	parameters = [
		next(tool["function"]["parameters"] for tool in format_openai_tools(SCHEMA_DATA) if tool["function"]["name"] == "boss_agent_run"),
		next(tool["input_schema"] for tool in format_anthropic_tools(SCHEMA_DATA) if tool["name"] == "boss_agent_run"),
		next(tool["inputSchema"] for tool in format_mcp_tools(SCHEMA_DATA) if tool["name"] == "boss_agent_run"),
	]
	for schema in parameters:
		resume = schema["properties"]["resume"]
		assert resume["type"] == "boolean"
		assert resume["default"] is False
		assert "官方验证" in resume["description"]
		assert "操作者明确确认" in resume["description"]


def test_tool_formats_expose_callable_shortlist_subcommands():
	"""导出实际可调用的子命令，不生成运行时不存在的分组工具。"""
	oai = format_openai_tools(SCHEMA_DATA)
	tools = {tool["function"]["name"]: tool["function"]["parameters"] for tool in oai}
	assert "boss_shortlist" not in tools
	assert {
		"boss_shortlist_list", "boss_shortlist_add", "boss_shortlist_remove",
		"boss_shortlist_annotate", "boss_shortlist_compare",
	} <= tools.keys()
	assert tools["boss_shortlist_add"]["required"] == ["security_id", "job_id"]
	assert "tag" in tools["boss_shortlist_compare"]["properties"]
	for name, schema in tools.items():
		if name.startswith("boss_shortlist_"):
			assert {"add", "annotate", "compare"}.isdisjoint(schema["properties"])


def test_tool_formats_have_single_availability_prefix():
	"""可用性说明只拼接一次：availability_note 自带「可用性: 」前缀。

	修复前 build_tool_catalog 又包了一层，导出给 Agent 的描述是
	「可用性: 可用性: roles=...」。
	"""
	oai = format_openai_tools(SCHEMA_DATA)
	anth = format_anthropic_tools(SCHEMA_DATA)
	mcp = format_mcp_tools(SCHEMA_DATA)
	descriptions = [
		*(tool["function"]["description"] for tool in oai),
		*(tool["description"] for tool in anth),
		*(tool["description"] for tool in mcp),
	]
	assert all("可用性: 可用性:" not in description for description in descriptions)
	assert all(description.count("可用性:") <= 1 for description in descriptions)


def test_command_to_json_schema_required_args():
	"""required=True 的参数应出现在 required 数组中。"""
	cmd_spec = {
		"args": [
			{"name": "query", "required": True, "description": "关键词"},
			{"name": "opt", "required": False, "description": "可选"},
		],
		"options": {},
	}
	schema = command_to_json_schema("test", cmd_spec)
	assert "query" in schema["properties"]
	assert "opt" in schema["properties"]
	assert schema["required"] == ["query"]


def test_command_to_json_schema_type_mapping():
	"""native 类型应映射到 JSON Schema 类型（int → integer, bool → boolean）。"""
	cmd_spec = {
		"args": [],
		"options": {
			"--days": {"type": "int", "default": 30, "description": "天数"},
			"--dry-run": {"type": "bool", "default": False, "description": "预览"},
			"--name": {"type": "string", "default": None, "description": "名称"},
		},
	}
	schema = command_to_json_schema("test", cmd_spec)
	assert schema["properties"]["days"]["type"] == "integer"
	assert schema["properties"]["days"]["default"] == 30
	assert schema["properties"]["dry_run"]["type"] == "boolean"
	assert schema["properties"]["name"]["type"] == "string"


def test_command_name_dash_to_underscore():
	"""带横线的命令名（如 batch-greet）应转成下划线。"""
	oai = format_openai_tools(SCHEMA_DATA)
	names = {t["function"]["name"] for t in oai}
	assert "boss_batch_greet" in names
	assert "boss_follow_up" in names
	assert "boss_chat_summary" in names


def test_invalid_format_raises():
	"""非法 --format 应转换为标准 JSON 错误信封。"""
	runner = CliRunner()
	result = runner.invoke(cli, ["schema", "--format", "xml"])
	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert payload["ok"] is False
	assert payload["command"] == "schema"
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert payload["error"]["recoverable"] is False
	assert payload["error"]["recovery_action"] == "修正参数"
	assert result.stderr == ""


def test_openai_tools_output_ready_for_openai_sdk():
	"""OpenAI Tools 输出应能直接传给 openai SDK 的 tools 参数（结构校验）。"""
	oai = format_openai_tools(SCHEMA_DATA)
	for tool in oai:
		# 必需字段：type, function.name, function.description, function.parameters
		assert set(tool.keys()) == {"type", "function"}
		fn = tool["function"]
		assert "name" in fn and "description" in fn and "parameters" in fn
		# parameters 必须是合法 JSON Schema
		params = fn["parameters"]
		assert params["type"] == "object"
		for prop_name, prop_spec in params.get("properties", {}).items():
			assert "type" in prop_spec
			assert prop_spec["type"] in {"string", "integer", "boolean", "number", "array", "object"}
