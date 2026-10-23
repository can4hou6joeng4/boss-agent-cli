"""MCP SDK 适配与历史工具目录导出。

工具名称与参数由 schema.tool_catalog 提供；该模块仅构造 MCP Tool 对象，
保留 mcp_server / mcp-server/server.py wrapper 使用的历史导入路径。
"""

from typing import Any

from mcp.types import Tool

from boss_agent_cli.compliance import restricted_commands
from boss_agent_cli.schema.availability import availability_note
from boss_agent_cli.schema.tool_catalog import (
	availability_of as _availability_of,  # noqa: F401 - 历史再导出
	build_schema_with_availability as _build_schema_with_availability,
	build_tool_catalog,
	crawl_tool_definitions,
	tool_availability,
)


_SCHEMA_WITH_AVAILABILITY = _build_schema_with_availability()


def _tool_availability(tool_name: str) -> dict[str, Any] | None:
	return tool_availability(tool_name, _SCHEMA_WITH_AVAILABILITY)


def _decorate_tool_descriptions() -> None:
	"""保留旧 wrapper 的入口；初始目录由共享 builder 装饰。"""
	for tool in TOOLS:
		availability = _tool_availability(tool.name)
		if availability:
			tool.description = f"{tool.description} [可用性: {availability_note(availability)}]"


def _crawl_task_tools() -> list[Tool]:
	return [
		Tool(name=tool.name, description=tool.description, input_schema=tool.input_schema)
		for tool in crawl_tool_definitions(_SCHEMA_WITH_AVAILABILITY)
	]


_MCP_TOOL_COMPLIANCE_COMMAND_OVERRIDES = {
	"boss_batch_greet": "batch-greet",
	"boss_chat_summary": "chat-summary",
	"boss_follow_up": "follow-up",
	"boss_watch_run": "watch-run",
	"boss_hr_whoami": "recruiter-whoami",
	"boss_hr_applications": "recruiter-applications",
	"boss_hr_candidates": "recruiter-candidates",
	"boss_hr_chat": "recruiter-chat",
	"boss_hr_chatmsg": "recruiter-chatmsg",
	"boss_hr_last_messages": "recruiter-last-messages",
	"boss_hr_resume": "recruiter-resume",
	"boss_hr_exchange": "recruiter-resume",
	"boss_hr_reply": "recruiter-reply",
	"boss_hr_request_resume": "recruiter-request-resume",
	"boss_hr_accept_resume": "recruiter-accept-resume",
	"boss_hr_download_resume": "recruiter-download-resume",
	"boss_hr_recommendations": "recruiter-recommendations",
	"boss_hr_greet": "recruiter-greet",
}


def _compliance_command_for_tool(tool_name: str) -> str:
	"""Map an MCP tool name to the CLI compliance command identifier."""
	if override := _MCP_TOOL_COMPLIANCE_COMMAND_OVERRIDES.get(tool_name):
		return override
	return tool_name.removeprefix("boss_").replace("_", "-")


def _is_low_risk_blocked_tool(tool_name: str) -> bool:
	command = _compliance_command_for_tool(tool_name)
	return command in restricted_commands("assisted")


TOOLS = [
	Tool(name=tool.name, description=tool.description, input_schema=tool.input_schema)
	for tool in build_tool_catalog(_SCHEMA_WITH_AVAILABILITY)
]

_LOW_RISK_BLOCKED_TOOLS = {
	tool.name
	for tool in TOOLS
	if _is_low_risk_blocked_tool(tool.name)
}
# 历史导出符号保留给外部 wrapper；开放策略下集合恒为空且不再过滤工具。
