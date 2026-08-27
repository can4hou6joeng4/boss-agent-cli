"""MCP stdio 协议回归测试。"""

import json
import subprocess
import sys


def test_tools_list_is_registered_over_stdio() -> None:
	probe = """
import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
	server = StdioServerParameters(command=sys.executable, args=["-m", "boss_agent_cli.mcp_server"])
	async with stdio_client(server) as (read_stream, write_stream):
		async with ClientSession(read_stream, write_stream) as session:
			initialization = await session.initialize()
			tool_list = await session.list_tools()
			print(json.dumps({
				"server_name": initialization.serverInfo.name,
				"tool_names": [tool.name for tool in tool_list.tools],
			}))


asyncio.run(main())
"""
	result = subprocess.run(
		[sys.executable, "-c", probe],
		capture_output=True,
		text=True,
		encoding="utf-8",
		timeout=10,
	)

	assert result.returncode == 0, result.stderr
	response = json.loads(result.stdout)
	assert response["server_name"] == "boss-agent-cli"
	assert response["tool_names"]
	assert "boss_status" in response["tool_names"]
