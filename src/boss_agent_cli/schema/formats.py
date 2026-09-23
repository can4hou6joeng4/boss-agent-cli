"""把能力描述转换成 JSON Schema 与各家 tool-use 格式（OpenAI / Anthropic / MCP）。"""

from typing import Any

from boss_agent_cli.schema.availability import availability_note


# 类型转换：native schema → JSON Schema 基础类型
_JSON_SCHEMA_TYPE_MAP = {
	"string": "string",
	"int": "integer",
	"integer": "integer",
	"bool": "boolean",
	"boolean": "boolean",
	"float": "number",
	"number": "number",
}


def _option_to_json_schema_property(opt_spec: dict[str, Any]) -> dict[str, Any]:
	"""把 native option 转成单个 JSON Schema 属性。"""
	native_type = opt_spec.get("type", "string")
	prop: dict[str, Any] = {"type": _JSON_SCHEMA_TYPE_MAP.get(native_type, "string")}
	desc = opt_spec.get("description")
	if desc:
		prop["description"] = desc
	default = opt_spec.get("default")
	if default is not None:
		prop["default"] = default
	return prop


def command_to_json_schema(cmd_name: str, cmd_spec: dict[str, Any]) -> dict[str, Any]:
	"""把 native 命令描述转成 OpenAI Tools / Anthropic Tool Use 共用的 JSON Schema。"""
	properties: dict[str, Any] = {}
	required: list[str] = []

	for arg in cmd_spec.get("args", []):
		arg_name = arg["name"]
		properties[arg_name] = {
			"type": "string",
			"description": arg.get("description", ""),
		}
		if arg.get("required"):
			required.append(arg_name)

	for opt_key, opt_spec in cmd_spec.get("options", {}).items():
		if not opt_key.startswith("-"):
			continue
		# 去掉短/长选项前缀，保留长选项作为参数名
		primary_name = opt_key.split(",")[-1].strip().lstrip("-").replace("-", "_")
		properties[primary_name] = _option_to_json_schema_property(opt_spec)

	schema: dict[str, Any] = {
		"type": "object",
		"properties": properties,
	}
	if required:
		schema["required"] = required
	return schema


def format_openai_tools(data: dict[str, Any]) -> list[dict[str, Any]]:
	"""OpenAI Functions / Tools API 格式。"""
	tools = []
	for cmd_name, cmd_spec in data["commands"].items():
		description = cmd_spec.get("description", "")
		if availability := cmd_spec.get("availability"):
			description = f"{description} [{availability_note(availability)}]"
		tools.append(
			{
				"type": "function",
				"function": {
					"name": f"boss_{cmd_name.replace('-', '_')}",
					"description": description,
					"parameters": command_to_json_schema(cmd_name, cmd_spec),
				},
			}
		)
	return tools


def format_anthropic_tools(data: dict[str, Any]) -> list[dict[str, Any]]:
	"""Anthropic Tool Use 格式。"""
	tools = []
	for cmd_name, cmd_spec in data["commands"].items():
		description = cmd_spec.get("description", "")
		if availability := cmd_spec.get("availability"):
			description = f"{description} [{availability_note(availability)}]"
		tools.append(
			{
				"name": f"boss_{cmd_name.replace('-', '_')}",
				"description": description,
				"input_schema": command_to_json_schema(cmd_name, cmd_spec),
			}
		)
	return tools


def format_mcp_tools(data: dict[str, Any]) -> list[dict[str, Any]]:
	"""Model Context Protocol Tools 格式（与 Anthropic 同结构，键名 inputSchema）。"""
	tools = []
	for cmd_name, cmd_spec in data["commands"].items():
		if mcp_tools := cmd_spec.get("mcp_tools"):
			availability = cmd_spec.get("availability")
			for tool in mcp_tools:
				description = tool["description"]
				if availability:
					description = f"{description} [{availability_note(availability)}]"
				tools.append({
					"name": tool["name"],
					"description": description,
					"inputSchema": tool["inputSchema"],
				})
			continue
		if cmd_spec.get("mcp_exposed") is False:
			continue
		description = cmd_spec.get("description", "")
		if availability := cmd_spec.get("availability"):
			description = f"{description} [{availability_note(availability)}]"
		tools.append(
			{
				"name": f"boss_{cmd_name.replace('-', '_')}",
				"description": description,
				"inputSchema": command_to_json_schema(cmd_name, cmd_spec),
			}
		)
	return tools
