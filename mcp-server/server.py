"""Compatibility wrapper for the packaged MCP server entrypoint."""

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
	sys.path.insert(0, str(SRC))

_impl = importlib.import_module("boss_agent_cli.mcp_server")

TOOLS = _impl.TOOLS
_build_args = _impl._build_args
_create_sse_app = _impl._create_sse_app
_create_streamable_http_app = _impl._create_streamable_http_app
_parse_cli_args = _impl._parse_cli_args
_run_http_server = _impl._run_http_server
_run_boss = _impl._run_boss
_run_sse_server = _impl._run_sse_server
_serve_asgi_app = _impl._serve_asgi_app
call_tool = _impl.call_tool
list_tools = _impl.list_tools
main = _impl.main
run = _impl.run
server = _impl.server
subprocess = _impl.subprocess


if __name__ == "__main__":
	run()
