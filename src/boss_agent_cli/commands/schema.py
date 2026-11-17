import click
from typing import Any

from boss_agent_cli.compliance import compliance_mode_data
from boss_agent_cli.output import emit_success
from boss_agent_cli.platforms import list_platforms, list_recruiter_platforms
from boss_agent_cli.schema.availability import inject_availability
from boss_agent_cli.schema.data import SCHEMA_DATA
from boss_agent_cli.schema.formats import format_anthropic_tools, format_mcp_tools, format_openai_tools, schema_parameter_paths
from boss_agent_cli.schema.tool_catalog import native_crawl_mcp_tools
from boss_agent_cli.wizard.catalog import catalog_data


@click.command("schema")
@click.option(
	"--format",
	"output_format",
	type=click.Choice(["native", "openai-tools", "anthropic-tools", "mcp-tools"]),
	default="native",
	help="输出格式：native（本项目信封）/ openai-tools（OpenAI Functions & Tools API）/ anthropic-tools（Claude Tool Use API）/ mcp-tools（Model Context Protocol Tools）",
)
@click.pass_context
def schema_cmd(ctx: click.Context, output_format: str) -> None:
	"""返回工具完整能力描述的 JSON"""
	# 动态注入当前会话的平台信息（Issue #129 Week 1b）
	data: dict[str, Any] = dict(SCHEMA_DATA)
	current = (ctx.obj or {}).get("platform") or "zhipin"
	data["current_platform"] = current
	data["current_role"] = (ctx.obj or {}).get("role") or "candidate"
	data["current_browser_source"] = (ctx.obj or {}).get("browser_source") or "auto"
	data["supported_platforms"] = list_platforms()
	data["supported_recruiter_platforms"] = list_recruiter_platforms()
	data["wizard_catalog"] = catalog_data()
	data["compliance"] = compliance_mode_data(ctx)
	data = inject_availability(data)

	if output_format == "openai-tools":
		data = {"format": "openai-tools", "tools": format_openai_tools(data)}
		emit_success("schema", data, schema_parameter_paths=schema_parameter_paths(data))
		return
	if output_format == "anthropic-tools":
		data = {"format": "anthropic-tools", "tools": format_anthropic_tools(data)}
		emit_success("schema", data, schema_parameter_paths=schema_parameter_paths(data))
		return
	if output_format == "mcp-tools":
		data = {"format": "mcp-tools", "tools": format_mcp_tools(data)}
		emit_success("schema", data, schema_parameter_paths=schema_parameter_paths(data))
		return
	# native 内嵌的 crawl 工具与导出的工具目录共用同一份规范化结果（Issue #1536）。
	# 只替换内嵌副本：导出路径直接读 data 的 mcp_tools，先改写会让可用性说明
	# 被 append 两次。
	commands = data.get("commands")
	if isinstance(commands, dict) and isinstance(commands.get("crawl"), dict):
		data["commands"] = {**commands, "crawl": {**commands["crawl"], "mcp_tools": native_crawl_mcp_tools(data)}}
	emit_success("schema", data, schema_parameter_paths=schema_parameter_paths(data))
