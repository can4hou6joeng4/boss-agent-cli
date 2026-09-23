import click

from boss_agent_cli.compliance import compliance_mode_data
from boss_agent_cli.output import emit_success
from boss_agent_cli.platforms import list_platforms, list_recruiter_platforms
from boss_agent_cli.schema.availability import inject_availability
from boss_agent_cli.schema.data import SCHEMA_DATA
from boss_agent_cli.schema.formats import format_anthropic_tools, format_mcp_tools, format_openai_tools
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
	data = dict(SCHEMA_DATA)
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
		emit_success("schema", {"format": "openai-tools", "tools": format_openai_tools(data)})
		return
	if output_format == "anthropic-tools":
		emit_success("schema", {"format": "anthropic-tools", "tools": format_anthropic_tools(data)})
		return
	if output_format == "mcp-tools":
		emit_success("schema", {"format": "mcp-tools", "tools": format_mcp_tools(data)})
		return
	emit_success("schema", data)
