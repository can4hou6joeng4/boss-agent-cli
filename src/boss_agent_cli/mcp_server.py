"""MCP Server for boss-agent-cli — 让 Claude Desktop / Cursor 直接调用 BOSS 直聘求职工具。

工具目录（`TOOLS` 及其合规过滤）在 `mcp_tools`，工具名 → CLI 参数的映射在 `mcp_args`；
本模块只保留服务器实例、CLI 调用、传输层与入口。两者的公开符号在下方原样再导出，
`mcp-server/server.py` wrapper 与既有测试的导入路径因此保持不变。
"""

import argparse
import asyncio
import json
import subprocess
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from boss_agent_cli.compliance import (
	COMPLIANCE_BLOCKED_ACTION,
	LOW_RISK_MODE_DESCRIPTION,
)
from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.mcp_tools import _LOW_RISK_BLOCKED_TOOLS, TOOLS

# 向后兼容的再导出：这些符号在拆分前属于本模块，`mcp-server/server.py` wrapper
# 与既有测试仍按 `boss_agent_cli.mcp_server.<name>` 取用。本模块自身不再使用它们，
# 故显式标注 F401——删掉会静默破坏 wrapper 和测试的导入路径。
from boss_agent_cli.mcp_tools import (  # noqa: F401
	_MCP_TOOL_COMPLIANCE_COMMAND_OVERRIDES,
	_SCHEMA_WITH_AVAILABILITY,
	_availability_of,
	_build_schema_with_availability,
	_compliance_command_for_tool,
	_crawl_task_tools,
	_decorate_tool_descriptions,
	_is_low_risk_blocked_tool,
	_tool_availability,
)

__all__ = [
	"SERVER_INSTRUCTIONS",
	"TOOLS",
	"call_tool",
	"list_tools",
	"main",
	"run",
	"server",
]

if TYPE_CHECKING:
	# SSE / HTTP 传输是可选路径，starlette 与 session manager 只在对应工厂函数里
	# 惰性导入；这里仅为类型标注引入，不影响 stdio 传输的启动开销。
	from collections.abc import AsyncIterator

	from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
	from starlette.applications import Starlette
	from starlette.types import Receive, Scope, Send

SERVER_INSTRUCTIONS = (
	"boss-agent-cli over MCP: a local-assist BOSS Zhipin job-search toolset in assisted mode by default — "
	"read-only first and user-triggered. MCP remains assisted-only until a dedicated mode-aware exposure contract is "
	"implemented; it can only inspect or locally shortlist existing crawl runs by run_id. "
	"Sensitive actions (greet, batch-greet, apply, contact exchange, recruiter candidate data, replies) "
	"are not exposed and return COMPLIANCE_BLOCKED at the CLI layer; for those the user acts manually on "
	"the official BOSS Zhipin website. Every tool returns the same JSON envelope "
	"{ok, data, pagination, error, hints}; when ok is false, read error.code and error.recovery_action and "
	"act on it (for example AUTH_REQUIRED means the user runs boss login). boss schema is the capability "
	"source of truth — do not hardcode command tables."
)

server = Server(
	"boss-agent-cli",
	instructions=SERVER_INSTRUCTIONS,
)
DEFAULT_TRANSPORT = "stdio"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_HTTP_PATH = "/mcp"
DEFAULT_SSE_PATH = "/sse"
DEFAULT_MESSAGE_PATH = "/messages/"
DEFAULT_BOSS_BIN = "boss"
_BOSS_BIN = DEFAULT_BOSS_BIN
_BOSS_GLOBAL_ARGS: list[str] = []


def _configure_boss_invocation(
	*,
	boss_bin: str = DEFAULT_BOSS_BIN,
	data_dir: str | None = None,
	platform: str | None = None,
	role: str | None = None,
) -> None:
	"""Configure global flags passed from the MCP host to the underlying boss CLI."""
	global _BOSS_BIN, _BOSS_GLOBAL_ARGS
	_BOSS_BIN = boss_bin
	args: list[str] = []
	if data_dir:
		args.extend(["--data-dir", data_dir])
	if platform:
		args.extend(["--platform", platform])
	if role:
		args.extend(["--role", role])
	_BOSS_GLOBAL_ARGS = args


def _run_boss(*args: str) -> dict[str, Any]:
	"""调用 boss CLI 并返回解析后的 JSON。"""
	cmd = [_BOSS_BIN, "--json", *_BOSS_GLOBAL_ARGS, *args]
	result = subprocess.run(
		cmd,
		capture_output=True,
		text=True,
		timeout=120,
		stdin=subprocess.DEVNULL,
	)
	try:
		parsed = json.loads(result.stdout)
	except json.JSONDecodeError:
		parsed = None
	if isinstance(parsed, dict):
		return parsed
	# CLI 契约保证 stdout 是 JSON 信封对象；拿到别的形状（解析失败或非对象）
	# 一律按命令失败处理，而不是把非 dict 结果透传给调用方。
	return {
		"ok": False,
		"error": {"code": "CLI_ERROR", "message": result.stderr or "命令执行失败"},
	}


async def list_tools() -> list[Tool]:
	return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
	if name in _LOW_RISK_BLOCKED_TOOLS:
		result = {
			"ok": False,
			"schema_version": "1.0",
			"command": name.removeprefix("boss_"),
			"data": None,
			"pagination": None,
			"error": {
				"code": "COMPLIANCE_BLOCKED",
				"message": LOW_RISK_MODE_DESCRIPTION,
				"recoverable": False,
				"recovery_action": COMPLIANCE_BLOCKED_ACTION,
			},
			"hints": {
				"next_actions": [
					"使用只读或本地辅助工具",
					"需要写操作或候选人个人信息处理时，请回到平台官网由用户手动完成",
				],
			},
		}
		return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
	args = _build_args(name, arguments)
	result = _run_boss(*args)
	return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]


# ── 入口 ──────────────────────────────────────────────────────────


async def main() -> None:
	async with stdio_server() as (read_stream, write_stream):
		await server.run(read_stream, write_stream, server.create_initialization_options())


def _normalize_path(path: str) -> str:
	if not path.startswith("/"):
		return f"/{path}"
	return path


def _create_sse_app(*, sse_path: str = DEFAULT_SSE_PATH, message_path: str = DEFAULT_MESSAGE_PATH) -> "Starlette":
	from mcp.server.sse import SseServerTransport
	from starlette.applications import Starlette
	from starlette.requests import Request
	from starlette.responses import Response
	from starlette.routing import Mount, Route

	sse_path = _normalize_path(sse_path)
	message_path = _normalize_path(message_path)
	sse = SseServerTransport(message_path)

	async def handle_sse(scope: "Scope", receive: "Receive", send: "Send") -> Response:
		async with sse.connect_sse(scope, receive, send) as streams:
			await server.run(
				streams[0],
				streams[1],
				server.create_initialization_options(),
			)
		return Response()

	async def sse_endpoint(request: Request) -> Response:
		return await handle_sse(request.scope, request.receive, request._send)

	return Starlette(
		routes=[
			Route(sse_path, endpoint=sse_endpoint, methods=["GET"]),
			Mount(message_path, app=sse.handle_post_message),
		]
	)


class _StreamableHTTPASGIApp:
	def __init__(self, session_manager: "StreamableHTTPSessionManager") -> None:
		self.session_manager = session_manager

	async def __call__(self, scope: "Scope", receive: "Receive", send: "Send") -> None:
		await self.session_manager.handle_request(scope, receive, send)


def _create_streamable_http_app(*, path: str = DEFAULT_HTTP_PATH) -> "Starlette":
	from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
	from starlette.applications import Starlette
	from starlette.routing import Route

	path = _normalize_path(path)
	session_manager = StreamableHTTPSessionManager(app=server)
	http_app = _StreamableHTTPASGIApp(session_manager)

	# 必须是 asynccontextmanager：Starlette 对裸 async generator lifespan 会走
	# 弃用路径并发 DeprecationWarning。
	@asynccontextmanager
	async def lifespan(app: "Starlette") -> "AsyncIterator[None]":
		async with session_manager.run():
			yield

	return Starlette(
		routes=[Route(path, endpoint=http_app)],
		lifespan=lifespan,
	)


def _serve_asgi_app(app: "Starlette", *, host: str, port: int) -> None:
	import uvicorn

	uvicorn.run(app, host=host, port=port, log_level="info")


def _run_sse_server(*, host: str, port: int, sse_path: str, message_path: str) -> None:
	app = _create_sse_app(sse_path=sse_path, message_path=message_path)
	_serve_asgi_app(app, host=host, port=port)


def _run_http_server(*, host: str, port: int, path: str) -> None:
	app = _create_streamable_http_app(path=path)
	_serve_asgi_app(app, host=host, port=port)


def _parse_cli_args(argv: list[str] | None = None) -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Run boss-agent-cli MCP server")
	parser.add_argument(
		"--transport",
		choices=("stdio", "sse", "http"),
		default=DEFAULT_TRANSPORT,
		help="MCP 传输模式（默认 stdio）",
	)
	parser.add_argument("--host", default=DEFAULT_HOST, help="HTTP/SSE 监听地址")
	parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="HTTP/SSE 监听端口")
	parser.add_argument("--path", default=DEFAULT_HTTP_PATH, help="HTTP streaming 路径")
	parser.add_argument("--sse-path", default=DEFAULT_SSE_PATH, help="SSE 建链路径")
	parser.add_argument("--message-path", default=DEFAULT_MESSAGE_PATH, help="SSE 消息回传路径")
	parser.add_argument("--boss-bin", default=DEFAULT_BOSS_BIN, help="底层 boss CLI 可执行文件路径")
	parser.add_argument("--data-dir", default=None, help="传给 boss CLI 的数据目录，用于项目级状态隔离")
	parser.add_argument("--platform", default=None, help="传给 boss CLI 的默认平台，如 zhilian 或 zhipin")
	parser.add_argument("--role", choices=("candidate", "recruiter"), default=None, help="传给 boss CLI 的默认角色")
	return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> None:
	args = _parse_cli_args(argv)
	_configure_boss_invocation(
		boss_bin=args.boss_bin,
		data_dir=args.data_dir,
		platform=args.platform,
		role=args.role,
	)
	if args.transport == "stdio":
		asyncio.run(main())
		return
	if args.transport == "sse":
		_run_sse_server(host=args.host, port=args.port, sse_path=args.sse_path, message_path=args.message_path)
		return
	_run_http_server(host=args.host, port=args.port, path=args.path)


if __name__ == "__main__":
	run()
