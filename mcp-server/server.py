"""MCP Server for boss-agent-cli — 让 Claude Desktop / Cursor 直接调用 BOSS 直聘求职工具。"""

import json
import subprocess
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

server = Server("boss-agent-cli")

# ── Tool 定义 ──────────────────────────────────────────────────────

TOOLS = [
	Tool(
		name="boss_status",
		description="检查 BOSS 直聘登录态",
		inputSchema={"type": "object", "properties": {}, "required": []},
	),
	Tool(
		name="boss_doctor",
		description="诊断本地运行环境、依赖、登录态和网络连通性",
		inputSchema={"type": "object", "properties": {}, "required": []},
	),
	Tool(
		name="boss_search",
		description="按关键词和筛选条件搜索 BOSS 直聘职位列表。支持城市、薪资、经验、学历、福利等多维度筛选。",
		inputSchema={
			"type": "object",
			"properties": {
				"query": {"type": "string", "description": "搜索关键词（如 Golang、Python 后端）"},
				"city": {"type": "string", "description": "城市名称（如 北京、广州）"},
				"salary": {"type": "string", "description": "薪资范围（如 20-50K）"},
				"experience": {"type": "string", "description": "经验要求（如 3-5年）"},
				"education": {"type": "string", "description": "学历要求（如 本科）"},
				"welfare": {"type": "string", "description": "福利筛选，逗号分隔 AND 逻辑（如 双休,五险一金）"},
				"page": {"type": "integer", "description": "页码", "default": 1},
			},
			"required": ["query"],
		},
	),
	Tool(
		name="boss_recommend",
		description="获取基于简历的个性化职位推荐",
		inputSchema={
			"type": "object",
			"properties": {
				"page": {"type": "integer", "description": "页码", "default": 1},
			},
			"required": [],
		},
	),
	Tool(
		name="boss_detail",
		description="查看职位详情。参数为 security_id（从 search/recommend 结果获取）。",
		inputSchema={
			"type": "object",
			"properties": {
				"security_id": {"type": "string", "description": "职位的 security_id"},
				"job_id": {"type": "string", "description": "encrypt_job_id，传入可走快速通道"},
			},
			"required": ["security_id"],
		},
	),
	Tool(
		name="boss_greet",
		description="向招聘者打招呼。需要 security_id 和 job_id。",
		inputSchema={
			"type": "object",
			"properties": {
				"security_id": {"type": "string", "description": "职位的 security_id"},
				"job_id": {"type": "string", "description": "职位的 encrypt_job_id"},
			},
			"required": ["security_id", "job_id"],
		},
	),
	Tool(
		name="boss_chat",
		description="查看沟通列表，支持按发起方和时间筛选",
		inputSchema={
			"type": "object",
			"properties": {
				"from_who": {"type": "string", "enum": ["boss", "me"], "description": "筛选发起方"},
				"days": {"type": "integer", "description": "只显示最近 N 天的记录"},
				"page": {"type": "integer", "description": "页码", "default": 1},
			},
			"required": [],
		},
	),
	Tool(
		name="boss_me",
		description="获取当前登录用户信息（基本信息、简历、求职期望、投递记录）",
		inputSchema={
			"type": "object",
			"properties": {
				"section": {
					"type": "string",
					"enum": ["info", "resume", "expect", "deliver"],
					"description": "指定查看的部分",
				},
			},
			"required": [],
		},
	),
	Tool(
		name="boss_cities",
		description="列出支持的城市列表（约 40 个）",
		inputSchema={"type": "object", "properties": {}, "required": []},
	),
	Tool(
		name="boss_interviews",
		description="查看面试邀请列表",
		inputSchema={"type": "object", "properties": {}, "required": []},
	),
	Tool(
		name="boss_history",
		description="查看浏览历史",
		inputSchema={"type": "object", "properties": {}, "required": []},
	),
]


# ── Tool 调用逻辑 ──────────────────────────────────────────────────


def _run_boss(*args: str) -> dict[str, Any]:
	"""调用 boss CLI 并返回解析后的 JSON。"""
	cmd = ["boss", "--json", *args]
	result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
	try:
		return json.loads(result.stdout)
	except json.JSONDecodeError:
		return {
			"ok": False,
			"error": {"code": "CLI_ERROR", "message": result.stderr or "命令执行失败"},
		}


def _build_args(tool_name: str, arguments: dict) -> list[str]:
	"""根据 tool name 和参数构建 CLI 参数列表。"""
	name = tool_name.replace("boss_", "")

	if name == "search":
		args = [name, arguments["query"]]
		for opt in ("city", "salary", "experience", "education", "welfare"):
			if opt in arguments and arguments[opt]:
				args.extend([f"--{opt}", str(arguments[opt])])
		if "page" in arguments:
			args.extend(["--page", str(arguments["page"])])
		return args

	if name == "recommend":
		args = [name]
		if "page" in arguments:
			args.extend(["--page", str(arguments["page"])])
		return args

	if name == "detail":
		args = [name, arguments["security_id"]]
		if "job_id" in arguments and arguments["job_id"]:
			args.extend(["--job-id", arguments["job_id"]])
		return args

	if name == "greet":
		return [name, arguments["security_id"], arguments["job_id"]]

	if name == "chat":
		args = [name]
		if "from_who" in arguments and arguments["from_who"]:
			args.extend(["--from", arguments["from_who"]])
		if "days" in arguments:
			args.extend(["--days", str(arguments["days"])])
		if "page" in arguments:
			args.extend(["--page", str(arguments["page"])])
		return args

	if name == "me":
		args = [name]
		if "section" in arguments and arguments["section"]:
			args.extend(["--section", arguments["section"]])
		return args

	# 无参数命令：status, doctor, cities, interviews, history
	return [name]


# ── MCP Handlers ───────────────────────────────────────────────────


@server.list_tools()
async def list_tools() -> list[Tool]:
	return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
	args = _build_args(name, arguments)
	result = _run_boss(*args)
	return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]


# ── 入口 ──────────────────────────────────────────────────────────


async def main():
	async with stdio_server() as (read_stream, write_stream):
		await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
	import asyncio
	asyncio.run(main())
