"""Tests for boss schema --format 扩展（openai-tools / anthropic-tools）。"""

import json

from click.testing import CliRunner

from boss_agent_cli.commands.schema import (
	_command_to_json_schema,
	_format_anthropic_tools,
	_format_openai_tools,
	SCHEMA_DATA,
)
from boss_agent_cli.main import cli


def test_native_format_is_default():
	"""默认 --format 应等同于 native（原行为）。"""
	runner = CliRunner()
	result = runner.invoke(cli, ["schema"])
	assert result.exit_code == 0
	data = json.loads(result.output)["data"]
	assert "commands" in data
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
	assert len(tools) == len(SCHEMA_DATA["commands"])
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
	assert len(tools) == len(SCHEMA_DATA["commands"])
	for tool in tools:
		assert tool["name"].startswith("boss_")
		assert tool["description"]
		assert tool["input_schema"]["type"] == "object"


def test_openai_and_anthropic_share_parameters_schema():
	"""两种格式参数 schema 应一致（只是外层包装不同）。"""
	oai = _format_openai_tools(SCHEMA_DATA)
	anth = _format_anthropic_tools(SCHEMA_DATA)

	oai_search = next(t["function"]["parameters"] for t in oai if t["function"]["name"] == "boss_search")
	anth_search = next(t["input_schema"] for t in anth if t["name"] == "boss_search")
	assert oai_search == anth_search


def test_command_to_json_schema_required_args():
	"""required=True 的参数应出现在 required 数组中。"""
	cmd_spec = {
		"args": [
			{"name": "query", "required": True, "description": "关键词"},
			{"name": "opt", "required": False, "description": "可选"},
		],
		"options": {},
	}
	schema = _command_to_json_schema("test", cmd_spec)
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
	schema = _command_to_json_schema("test", cmd_spec)
	assert schema["properties"]["days"]["type"] == "integer"
	assert schema["properties"]["days"]["default"] == 30
	assert schema["properties"]["dry_run"]["type"] == "boolean"
	assert schema["properties"]["name"]["type"] == "string"


def test_command_name_dash_to_underscore():
	"""带横线的命令名（如 batch-greet）应转成下划线。"""
	oai = _format_openai_tools(SCHEMA_DATA)
	names = {t["function"]["name"] for t in oai}
	assert "boss_batch_greet" in names
	assert "boss_follow_up" in names
	assert "boss_chat_summary" in names


def test_invalid_format_raises():
	"""非法 --format 应被 Click 拦截。"""
	runner = CliRunner()
	result = runner.invoke(cli, ["schema", "--format", "xml"])
	assert result.exit_code != 0


def test_openai_tools_output_ready_for_openai_sdk():
	"""OpenAI Tools 输出应能直接传给 openai SDK 的 tools 参数（结构校验）。"""
	oai = _format_openai_tools(SCHEMA_DATA)
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
