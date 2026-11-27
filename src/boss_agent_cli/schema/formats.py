"""把能力描述转换成 JSON Schema 与各家 tool-use 格式（OpenAI / Anthropic / MCP）。"""

import json
from typing import Any

from boss_agent_cli.output import SchemaParameterPaths, SchemaPath
from boss_agent_cli.schema.tool_catalog import build_tool_catalog
from boss_agent_cli.schema.tool_policy import static_read_only


#: OpenAI 与 Anthropic 的工具参数 schema 顶层只接受普通 object：带这些组合 /
#: 条件关键字的定义会被接口以 400 拒绝，一个工具被拒整次请求失败（Issue #1774）。
_TOP_LEVEL_CONSTRAINT_KEYWORDS = ("anyOf", "oneOf", "allOf", "not", "if", "then", "else")


def _provider_tool_schema(description: str, input_schema: dict[str, Any]) -> tuple[str, dict[str, Any]]:
	"""把顶层组合 / 条件约束从参数 schema 挪进描述，返回（描述, schema）。

	约束本身由 CLI 校验，模型侧只需要读到它：原样附上 JSON Schema 片段，不做
	有损的自然语言改写。`mcp-tools` 与运行时 `tools/list` 仍保留完整 schema。
	"""
	constraints = {key: input_schema[key] for key in _TOP_LEVEL_CONSTRAINT_KEYWORDS if key in input_schema}
	if not constraints:
		return description, input_schema
	plain = {key: value for key, value in input_schema.items() if key not in constraints}
	note = json.dumps(constraints, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
	return f"{description}\n参数约束（JSON Schema 片段，由 CLI 校验）：{note}", plain


def format_openai_tools(data: dict[str, Any]) -> list[dict[str, Any]]:
	"""把实际可调用的共享工具目录包装为 OpenAI Tools API 格式。"""
	return [
		{
			"type": "function",
			"function": {
				"name": tool.name,
				"description": description,
				"parameters": parameters,
			},
		}
		for tool in build_tool_catalog(data)
		for description, parameters in (_provider_tool_schema(tool.description, tool.input_schema),)
	]


def format_anthropic_tools(data: dict[str, Any]) -> list[dict[str, Any]]:
	"""把实际可调用的共享工具目录包装为 Anthropic Tool Use 格式。"""
	return [
		{
			"name": tool.name,
			"description": description,
			"input_schema": input_schema,
		}
		for tool in build_tool_catalog(data)
		for description, input_schema in (_provider_tool_schema(tool.description, tool.input_schema),)
	]


def format_mcp_tools(data: dict[str, Any]) -> list[dict[str, Any]]:
	"""与运行时 tools/list 共用目录与只读判定，键名遵循 Model Context Protocol。

	``annotations.readOnlyHint`` 必须与服务器返回逐工具一致：按导出的工具定义
	离线注册宿主时，同样能拿到静态只读提示（Issue #2997）。
	"""
	return [
		{
			"name": tool.name,
			"description": tool.description,
			"inputSchema": tool.input_schema,
			"annotations": {"readOnlyHint": static_read_only(tool.name, tool.input_schema)},
		}
		for tool in build_tool_catalog(data)
	]


def _json_schema_parameter_paths(
	schema: dict[str, Any], path: SchemaPath, parameter_name: str = "",
) -> SchemaParameterPaths:
	paths: SchemaParameterPaths = {}
	if parameter_name:
		paths[path] = parameter_name
	for name, spec in schema.get("properties", {}).items():
		if isinstance(spec, dict):
			parameter_path = (*path, "properties", name)
			nested_name = f"{parameter_name}.{name}" if parameter_name else name
			paths.update(_json_schema_parameter_paths(spec, parameter_path, nested_name))
	for field in ("items", "additionalProperties"):
		if isinstance(spec := schema.get(field), dict):
			paths.update(_json_schema_parameter_paths(spec, (*path, field), parameter_name))
	for field in ("allOf", "anyOf", "oneOf", "prefixItems"):
		for index, spec in enumerate(schema.get(field, [])):
			if isinstance(spec, dict):
				paths.update(_json_schema_parameter_paths(spec, (*path, field, index), parameter_name))
	for field in ("$defs", "definitions"):
		for name, spec in schema.get(field, {}).items():
			if isinstance(spec, dict):
				paths.update(_json_schema_parameter_paths(spec, (*path, field, name), parameter_name))
	return paths


def _native_option_paths(options: dict[str, Any], path: SchemaPath) -> SchemaParameterPaths:
	paths: SchemaParameterPaths = {}
	for name, spec in options.items():
		if not isinstance(spec, dict):
			continue
		option_path = (*path, name)
		if name.startswith("-"):
			paths[option_path] = name
		else:
			paths.update(_native_option_paths(spec, option_path))
	return paths


def schema_parameter_paths(data: dict[str, Any]) -> SchemaParameterPaths:
	"""仅由 schema 命令为内置目录构造参数路径；不检查或提升任意业务数据。"""
	paths: SchemaParameterPaths = {}
	output_format = data.get("format")
	if output_format in {"openai-tools", "anthropic-tools", "mcp-tools"}:
		schema_key = {"openai-tools": "parameters", "anthropic-tools": "input_schema", "mcp-tools": "inputSchema"}[output_format]
		for index, tool in enumerate(data["tools"]):
			path: SchemaPath = ("tools", index)
			if output_format == "openai-tools":
				tool = tool["function"]
				path = (*path, "function")
			paths.update(_json_schema_parameter_paths(tool[schema_key], (*path, schema_key)))
		return paths
	for command, spec in data["commands"].items():
		command_path: SchemaPath = ("commands", command)
		paths.update(_native_option_paths(spec.get("options", {}), (*command_path, "options")))
		for index, arg in enumerate(spec.get("args", [])):
			paths[command_path + ("args", index)] = arg["name"]
		for index, tool in enumerate(spec.get("mcp_tools", [])):
			paths.update(_json_schema_parameter_paths(tool["inputSchema"], (*command_path, "mcp_tools", index, "inputSchema")))
	paths.update(_native_option_paths(data.get("global_options", {}), ("global_options",)))
	return paths
