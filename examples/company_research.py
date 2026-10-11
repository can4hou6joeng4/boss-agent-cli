"""Read a local shortlist and research an explicitly supplied public company query.

Run from a source checkout after `uv sync --all-extras`:
    uv run python examples/company_research.py "Mozilla company products careers"
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import anyio
import httpx2
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client


PARALLEL_URL = "https://search.parallel.ai/mcp"
USER_AGENT = "boss-agent-cli/company-research"
REQUEST_TIMEOUT = 60


def checked_result(name, result):
	if result.is_error:
		raise RuntimeError(f"{name} returned an MCP tool error")
	return result.model_dump(mode="json", by_alias=True, exclude_none=True)


async def read_shortlist(data_dir):
	"""Use the installed boss MCP server; never contact the recruitment platform."""
	parameters = StdioServerParameters(
		command=sys.executable,
		args=["-m", "boss_agent_cli.mcp_server", "--data-dir", str(data_dir)],
	)
	async with stdio_client(parameters) as (read, write):
		async with ClientSession(read, write, read_timeout_seconds=REQUEST_TIMEOUT) as session:
			await session.initialize()
			tools = await session.list_tools()
			if "boss_shortlist_list" not in {tool.name for tool in tools.tools}:
				raise RuntimeError("boss_shortlist_list is unavailable")
			result = await session.call_tool("boss_shortlist_list", {})
			checked_result("boss_shortlist_list", result)
			text = "".join(block.text for block in result.content if block.type == "text")
			envelope = json.loads(text)
			if not envelope.get("ok"):
				raise RuntimeError("Local shortlist could not be read")
			return envelope["data"]


async def research(query, data_dir, url=None):
	"""Direct MCP tool calls, without a model or an autonomous agent loop."""
	with anyio.fail_after(180):
		shortlist = await read_shortlist(data_dir)
		# A fresh HTTP client has no saved credentials or API-key lookup.
		async with httpx2.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT) as http:
			async with streamable_http_client(PARALLEL_URL, http_client=http) as (read, write):
				async with ClientSession(read, write, read_timeout_seconds=REQUEST_TIMEOUT) as session:
					await session.initialize()
					tools = {tool.name for tool in (await session.list_tools()).tools}
					required = {"web_search", "web_fetch"} if url else {"web_search"}
					if not required <= tools:
						raise RuntimeError("Required Parallel tools are unavailable")
					search = await session.call_tool(
						"web_search",
						{
							"objective": "Find public company information for interview preparation.",
							"search_queries": [query],
						},
					)
					data = {"shortlist": shortlist, "search": checked_result("web_search", search)}
					if url:
						fetch = await session.call_tool("web_fetch", {"urls": [url]})
						data["fetch"] = checked_result("web_fetch", fetch)
					return data


def main(argv=None):
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("query", help="Public company search query sent to Parallel")
	parser.add_argument("--url", help="Optional public HTTP(S) page URL sent to Parallel for fetching")
	parser.add_argument("--data-dir", type=Path, default=Path.home() / ".boss-agent")
	args = parser.parse_args(argv)
	if not args.query.strip():
		parser.error("query must not be empty")
	if args.url and not args.url.startswith(("https://", "http://")):
		parser.error("--url must be an HTTP(S) URL")
	envelope = {
		"ok": False,
		"schema_version": "1.0",
		"command": "company-research",
		"data": None,
		"pagination": None,
		"error": None,
		"hints": None,
	}
	try:
		envelope["data"] = asyncio.run(research(args.query, args.data_dir, args.url))
		envelope["ok"] = True
	except Exception as exc:
		print(str(exc), file=sys.stderr)
		envelope["error"] = {
			"code": "CLI_ERROR",
			"message": "Company research failed",
			"recoverable": True,
			"recovery_action": "Check the local data directory and MCP connection; retry later.",
		}
	print(json.dumps(envelope, ensure_ascii=False))
	return 0 if envelope["ok"] else 1


if __name__ == "__main__":
	raise SystemExit(main())
