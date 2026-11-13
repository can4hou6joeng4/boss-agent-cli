"""MCP Server for boss-agent-cli — 让 Claude Desktop / Cursor 直接调用 BOSS 直聘求职工具。

工具目录 `TOOLS` 在 `mcp_tools`，工具名 → CLI 参数的映射在 `mcp_args`；
本模块只保留服务器实例、CLI 调用、传输层与入口。两者的公开符号在下方原样再导出，
`mcp-server/server.py` wrapper 与既有测试的导入路径因此保持不变。
"""

import argparse
import asyncio
import json
import math
import signal
import subprocess
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, cast

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
	CallToolRequestParams,
	CallToolResult,
	ListToolsResult,
	PaginatedRequestParams,
	TextContent,
)

from boss_agent_cli.api.browser_source import POLICIES as BROWSER_SOURCES
from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.mcp_tools import TOOLS
from boss_agent_cli.output import envelope_error

# 向后兼容的再导出：这些符号在拆分前属于本模块，`mcp-server/server.py` wrapper
# 与既有测试仍按 `boss_agent_cli.mcp_server.<name>` 取用。本模块自身不再使用它们，
# 故显式标注 F401——删掉会静默破坏 wrapper 和测试的导入路径。
from boss_agent_cli.mcp_tools import (  # noqa: F401
	_LOW_RISK_BLOCKED_TOOLS,
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
	"boss-agent-cli over MCP exposes all implemented candidate, recruiter, communication and crawl tools. "
	"Historical assisted and research configurations have identical capability access; platform adapters may still "
	"return NOT_SUPPORTED when an operation is not implemented. Every tool returns the same JSON envelope "
	"{ok, data, pagination, error, hints}; when ok is false, read error.code and error.recovery_action and "
	"act on it (for example AUTH_REQUIRED means the user runs boss login). boss schema is the capability "
	"source of truth — do not hardcode command tables."
	" For hr_greet, preview with dry_run and obtain explicit operator approval for that candidate and message "
	"before setting yes=true. Never infer approval or retry a confirmation failure with yes=true on your own. "
	"An uncertain send result requires conversation verification, not another send. "
	"After a post-send error, inspect error.details.sent and stop; a sent greeting must not be repeated. "
	"For hr_location, preview with dry_run and obtain explicit operator approval before setting yes=true. "
	"Do not resend a location card to the same friend, and do not call it while the automation stop switch is open."
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
_CLI_TIMEOUT_SECONDS = 120.0
_CHECKPOINT_GRACE_SECONDS = 5.0
_TERMINATE_GRACE_SECONDS = 1.0


def _configure_boss_invocation(
	*,
	boss_bin: str = DEFAULT_BOSS_BIN,
	data_dir: str | None = None,
	platform: str | None = None,
	role: str | None = None,
	browser_source: str | None = None,
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
	if browser_source:
		args.extend(["--browser-source", browser_source])
	_BOSS_GLOBAL_ARGS = args


# 子进程超时 / 取消时，只有明确只读的工具才能声明「可重放」。未列出的工具
# （包括未来新增的）一律按「可能已产生平台副作用」处理：漏判只读只是保守，
# 漏判写操作会让 Agent 直接重试一个可能已经生效的动作。
_READ_ONLY_TOOL_NAMES = frozenset({
	"boss_status",
	"boss_doctor",
	"boss_search",
	"boss_recommend",
	"boss_detail",
	"boss_show",
	"boss_history",
	"boss_chat",
	"boss_chatmsg",
	"boss_me",
	"boss_interviews",
	"boss_cities",
	"boss_pipeline",
	"boss_follow_up",
	"boss_digest",
	"boss_stats",
	"boss_export",
	"boss_resume_list",
	"boss_resume_show",
	"boss_watch_list",
	"boss_preset_list",
	"boss_shortlist_list",
	"boss_shortlist_compare",
	"boss_favorites_list",
	"boss_crawl_status",
	"boss_crawl_results",
	"boss_hr_whoami",
	"boss_hr_applications",
	"boss_hr_candidates",
	"boss_hr_recommendations",
	"boss_hr_chat",
	"boss_hr_chatmsg",
	"boss_hr_last_messages",
	"boss_hr_resume",
	"boss_hr_jobs",
	"boss_hr_jobs_detail",
})

#: 工具名整体只读、但个别参数会把调用变成平台写操作。判定必须看参数：
#: `boss_hr_jobs` 的 `action=online|offline` 会 POST 上线/下线，超时后若按只读
#: 告诉 Agent「可直接重试」，就会在状态未知时重发一次状态变更（Issue #869）。
_TOOL_WRITE_MODES: dict[str, frozenset[str]] = {
	"boss_hr_jobs": frozenset({"online", "offline"}),
}

#: schema 里带 action 维度的工具。只读白名单中的这类工具读写取决于参数，必须在
#: `_TOOL_WRITE_MODES` 显式登记（写模式可以是空集）；漏登记时按写处理，不让新增
#: 的写 action 静默回到「超时也告诉 Agent 可直接重试」的旧缺陷（Issue #968）。
_ACTION_DIMENSION_TOOL_NAMES = frozenset(
	tool.name for tool in TOOLS if "action" in tool.input_schema.get("properties", {})
)

_WRITE_INTERRUPT_RECOVERY = "检查本地 checkpoint 与平台实际结果，再显式恢复；未确认的写操作不要直接重试"
_READ_INTERRUPT_RECOVERY = "只读命令已中断，不产生平台写入；确认子进程已退出后可直接重新调用"
#: 子进程没能启动：从未运行过，不可能有平台副作用。真正要处理的是安装 / 路径，
#: 让 Agent 去核对 checkpoint 与平台结果只会把人引向不存在的现场（Issue #962）。
_SPAWN_FAILURE_RECOVERY = (
	"底层 boss CLI 未能启动，未产生任何平台操作；确认 boss-agent-cli 已安装且 boss 在 "
	"MCP server 进程的 PATH 中，或用 boss-mcp --boss-bin 指定可执行文件路径后重新调用"
)
_READ_UNPARSEABLE_RECOVERY = (
	"只读命令未返回 JSON 信封，不产生平台写入；按 error.message 排查，"
	"升级 boss-agent-cli 后重新调用，仍出现时附上 error.message 提交 issue"
)


def _tool_may_write(tool_name: str, arguments: dict[str, Any] | None = None) -> bool:
	if tool_name not in _READ_ONLY_TOOL_NAMES:
		return True
	write_modes = _TOOL_WRITE_MODES.get(tool_name)
	if write_modes is None:
		# 带 action 维度却没有登记写模式：无法判断哪个动作会产生平台写入，按写处理
		# （与「未列出的工具按写」同一保守原则）。守卫测试保证现有工具不会走到这里。
		return tool_name in _ACTION_DIMENSION_TOOL_NAMES
	# 缺省 action 与 schema 默认值一致（list）：只有显式声明写模式才算写。
	action = (arguments or {}).get("action", "list")
	return isinstance(action, str) and action in write_modes


def _cli_timeout(args: tuple[str, ...]) -> float:
	# A wizard deadline belongs to the child. The host allows bounded extra
	# time for that child to commit its result/checkpoint and emit the envelope.
	if args and args[0] == "wizard" and "--timeout" in args:
		try:
			requested = float(args[args.index("--timeout") + 1])
		except (IndexError, ValueError):
			return _CLI_TIMEOUT_SECONDS
		if math.isfinite(requested) and requested > 0:
			return requested + _CHECKPOINT_GRACE_SECONDS
	return _CLI_TIMEOUT_SECONDS


def _cli_failure(
	args: tuple[str, ...],
	code: str,
	message: str,
	*,
	recovery_action: str | None = None,
	**details: Any,
) -> dict[str, Any]:
	command = args[0] if args else "mcp"
	if command in {"agent", "hr", "ai", "resume", "watch", "crawl"} and len(args) > 1:
		command = f"{command}.{args[1]}"
	return cast(
		"dict[str, Any]",
		json.loads(
			envelope_error(
				command,
				code=code,
				message=message,
				recoverable=code != "CLI_ERROR" and not details.get("outcome_unknown", False),
				recovery_action=recovery_action or _WRITE_INTERRUPT_RECOVERY,
				details=details or None,
			)
		),
	)


def _spawn_failure(args: tuple[str, ...], exc: OSError) -> dict[str, Any]:
	return _cli_failure(
		args,
		"CLI_ERROR",
		f"无法启动底层 boss CLI：{exc}",
		recovery_action=_SPAWN_FAILURE_RECOVERY,
		outcome_unknown=False,
	)


def _parse_cli_output(
	args: tuple[str, ...], stdout: str, stderr: str, *, may_write: bool = True,
) -> dict[str, Any]:
	try:
		parsed = json.loads(stdout)
	except json.JSONDecodeError:
		parsed = None
	if isinstance(parsed, dict):
		return parsed
	# CLI 契约保证 stdout 是 JSON 信封对象；拿到别的形状（解析失败或非对象）
	# 一律按命令失败处理，而不是把非 dict 结果透传给调用方。进程已经运行过：
	# 写工具的结果此时未知，与超时 / 取消同样标 outcome_unknown；只读工具没有
	# 平台写入，不能套用「未确认的写操作不要直接重试」（Issue #962）。
	return _cli_failure(
		args,
		"CLI_ERROR",
		stderr or "命令执行失败",
		recovery_action=_WRITE_INTERRUPT_RECOVERY if may_write else _READ_UNPARSEABLE_RECOVERY,
		outcome_unknown=may_write,
	)


def _run_boss(*args: str, may_write: bool = True) -> dict[str, Any]:
	"""Synchronous compatibility entrypoint; MCP handlers use the async path."""
	cmd = [_BOSS_BIN, "--json", *_BOSS_GLOBAL_ARGS, *args]
	try:
		result = subprocess.run(
			cmd,
			capture_output=True,
			text=True,
			timeout=_cli_timeout(args),
			stdin=subprocess.DEVNULL,
		)
	except subprocess.TimeoutExpired:
		return _cli_failure(
			args,
			"WORKFLOW_TIMEOUT",
			"底层 CLI 超时",
			outcome_unknown=may_write,
			recovery_action=_WRITE_INTERRUPT_RECOVERY if may_write else _READ_INTERRUPT_RECOVERY,
		)
	except OSError as exc:
		return _spawn_failure(args, exc)
	return _parse_cli_output(args, result.stdout, result.stderr, may_write=may_write)


async def _stop_cli_process(
	process: asyncio.subprocess.Process,
	output: "asyncio.Task[tuple[bytes, bytes]]",
) -> tuple[bytes, bytes]:
	if process.returncode is None:
		# SIGINT gives wizard/automation their normal interruption checkpoint
		# path. Never signal the user's attached browser process.
		try:
			process.send_signal(signal.SIGINT)
		except ProcessLookupError:
			pass
		except ValueError:
			try:
				process.terminate()
			except ProcessLookupError:
				pass
	try:
		return await asyncio.wait_for(asyncio.shield(output), _CHECKPOINT_GRACE_SECONDS)
	except asyncio.TimeoutError:
		if process.returncode is None:
			try:
				process.terminate()
			except ProcessLookupError:
				pass
	try:
		return await asyncio.wait_for(asyncio.shield(output), _TERMINATE_GRACE_SECONDS)
	except asyncio.TimeoutError:
		if process.returncode is None:
			try:
				process.kill()
			except ProcessLookupError:
				pass
		try:
			return await asyncio.wait_for(asyncio.shield(output), _TERMINATE_GRACE_SECONDS)
		except asyncio.TimeoutError:
			# An inherited pipe held by another process must not turn cleanup into
			# an unbounded wait after the owned CLI has been killed.
			output.cancel()
			await asyncio.gather(output, return_exceptions=True)
			return b"", b""


def _interrupted_failure(
	args: tuple[str, ...],
	code: str,
	stdout: bytes,
	*,
	may_write: bool,
) -> dict[str, Any]:
	# A child envelope can carry the committed checkpoint/run id. Killing a child
	# never proves an action had no effect; both the spawn-time cancel path and the
	# running-child path must surface that envelope (Issue #998).
	try:
		checkpoint_result = json.loads(stdout)
	except (json.JSONDecodeError, UnicodeDecodeError):
		checkpoint_result = None
	return _cli_failure(
		args,
		code,
		"MCP 调用已取消" if code == "WORKFLOW_STOPPED" else "底层 CLI 超时",
		outcome_unknown=may_write,
		recovery_action=_WRITE_INTERRUPT_RECOVERY if may_write else _READ_INTERRUPT_RECOVERY,
		checkpoint_result=checkpoint_result if isinstance(checkpoint_result, dict) else None,
	)


async def _run_boss_async(*args: str, may_write: bool = True) -> dict[str, Any]:
	if args and args[0] == "wizard" and not any(flag in args for flag in ("--timeout", "--status", "--stop")):
		args = (*args, "--timeout", str(_CLI_TIMEOUT_SECONDS))
	cmd = [_BOSS_BIN, "--json", *_BOSS_GLOBAL_ARGS, *args]
	spawn = asyncio.create_task(
		asyncio.create_subprocess_exec(
			*cmd,
			stdout=asyncio.subprocess.PIPE,
			stderr=asyncio.subprocess.PIPE,
			stdin=asyncio.subprocess.DEVNULL,
		)
	)
	try:
		process = await asyncio.shield(spawn)
	except OSError as exc:
		return _spawn_failure(args, exc)
	except asyncio.CancelledError:
		try:
			process = await asyncio.shield(spawn)
		except OSError as exc:
			# The child never ran, so the interrupted call left no platform effect.
			# Point at installation/path, not at a checkpoint that cannot exist
			# (Issue #1000).
			return _cli_failure(
				args,
				"WORKFLOW_STOPPED",
				f"无法启动底层 boss CLI：{exc}",
				outcome_unknown=False,
				recovery_action=_SPAWN_FAILURE_RECOVERY,
			)
		output = asyncio.create_task(process.communicate())
		stdout, _ = await asyncio.shield(_stop_cli_process(process, output))
		return _interrupted_failure(args, "WORKFLOW_STOPPED", stdout, may_write=may_write)
	output = asyncio.create_task(process.communicate())
	try:
		stdout, stderr = await asyncio.wait_for(asyncio.shield(output), _cli_timeout(args))
	except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
		stdout, _ = await asyncio.shield(_stop_cli_process(process, output))
		code = "WORKFLOW_STOPPED" if isinstance(exc, asyncio.CancelledError) else "WORKFLOW_TIMEOUT"
		return _interrupted_failure(args, code, stdout, may_write=may_write)
	return _parse_cli_output(
		args,
		stdout.decode("utf-8", errors="replace"),
		stderr.decode("utf-8", errors="replace"),
		may_write=may_write,
	)


# ── Handler 注册（mcp 2.x） ────────────────────────────────────────
#
# 2.0 移除了 `@server.list_tools()` / `@server.call_tool()` 装饰器，改为
# `add_request_handler(method, params_type, handler)`。除注册方式外还有两处变化：
#   1. handler 签名从「按需取参」变成固定的 `(ctx, params)`；
#   2. 返回值从裸 list 变成 Result 对象（`ListToolsResult` / `CallToolResult`）。
# 本段的形状照抄 SDK 自身在 `mcp/server/mcpserver/server.py` 的注册与 handler 写法，
# 不是从文档推测的。


async def list_tools(ctx: Any, params: "PaginatedRequestParams | None" = None) -> ListToolsResult:
	return ListToolsResult(tools=TOOLS)


def _tool_argument_error(tool_name: str, message: str) -> CallToolResult:
	return CallToolResult(
		content=[
			TextContent(
				type="text",
				text=envelope_error(tool_name, code="INVALID_PARAM", message=message),
			)
		],
		is_error=True,
	)


def _declared_arguments(tool_name: str) -> set[str] | None:
	tool = next((item for item in TOOLS if item.name == tool_name), None)
	if tool is None:
		return None
	return set(tool.input_schema.get("properties", {}))


_JSON_TYPE_CHECKS: dict[str, tuple[type, ...]] = {
	"boolean": (bool,),
	"integer": (int,),
	"number": (int, float),
	"string": (str,),
	"array": (list,),
	"object": (dict,),
}


def _argument_type_errors(tool_name: str, arguments: dict[str, Any]) -> list[str]:
	"""按 inputSchema 校验参数类型，返回人类可读的错误列表。

	`_build_args` 用 `is True` / truthiness 判断布尔参数：客户端把 `dry_run` /
	`yes` 传成字符串时，标志位会被静默丢弃或反向生效（`"false"` 是 truthy），
	写命令的预览 / 确认语义随之改变。类型错误必须在进入 CLI 之前拒绝。
	"""
	tool = next((item for item in TOOLS if item.name == tool_name), None)
	if tool is None:
		return []
	properties = tool.input_schema.get("properties", {})
	errors: list[str] = []
	for key, value in arguments.items():
		prop = properties.get(key)
		if not isinstance(prop, dict):
			continue
		expected = prop.get("type")
		if not isinstance(expected, str) or expected not in _JSON_TYPE_CHECKS:
			continue
		allowed = _JSON_TYPE_CHECKS[expected]
		# Python 里 bool 是 int 子类；JSON 里 true/false 不是数字，反向也一样。
		valid = isinstance(value, allowed) and (expected in {"boolean"} or not isinstance(value, bool))
		if not valid:
			errors.append(f"{key} 应为 {expected}，收到 {type(value).__name__}")
	return errors


async def call_tool(ctx: Any, params: CallToolRequestParams) -> CallToolResult:
	arguments = dict(params.arguments or {})
	declared = _declared_arguments(params.name)
	if declared is None:
		# 未知工具名不能落到 `boss <name>`：那只会得到 Click 的 "No such command"，
		# Agent 看不到「工具不存在」与可用工具列表的指引。
		return _tool_argument_error(
			params.name,
			f"未知工具：{params.name}；请调用 tools/list 获取可用工具",
		)
	unknown = sorted(set(arguments) - declared)
	if unknown:
		# 未知参数不能静默丢弃：给写工具多传一个 dry_run 也会被忽略，然后真实执行
		# 投递/打招呼。与 CLI 对未知选项的处理一致，直接拒绝并指出参数名。
		return _tool_argument_error(
			params.name,
			f"未知参数：{', '.join(unknown)}；请按 tools/list 的 inputSchema 传参",
		)
	type_errors = _argument_type_errors(params.name, arguments)
	if type_errors:
		# 类型错误同样不能交给 `_build_args` 的 truthiness 判断：`"false"` 是 truthy，
		# `is True` 对 `"true"` 为假，`dry_run` / `yes` 会因此静默丢失或反向生效。
		return _tool_argument_error(
			params.name,
			"参数类型错误：" + "；".join(type_errors) + "；请按 tools/list 的 inputSchema 传参",
		)
	try:
		args = _build_args(params.name, arguments)
	except KeyError as exc:
		# 参数映射按 schema 直接取值；客户端漏传条件必填参数（如 resume/status 的
		# run_id、online/offline 的 job_id）时会抛 KeyError。交给 SDK 只会变成
		# JSON-RPC 的 "Internal server error"，Agent 拿不到任何可执行信息。这里按
		# CLI 的错误信封返回，至少带上 INVALID_PARAM、缺失参数名与恢复指引。
		missing = str(exc.args[0]) if exc.args else "参数"
		return _tool_argument_error(params.name, f"缺少必填参数：{missing}")
	except ValueError as exc:
		# 映射层自己拒绝的非法枚举值（Issue #1002）：不能落进默认分支。
		return _tool_argument_error(params.name, f"参数取值非法：{exc}")
	result = await _run_boss_async(*args, may_write=_tool_may_write(params.name, arguments))
	# 信封的 ok 是唯一真源，但 MCP 客户端还需要 is_error 才能把失败标成工具错误；
	# 两者不一致时，ok:false 会被当成一次成功的工具调用。
	return CallToolResult(
		content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))],
		is_error=result.get("ok") is False,
	)


server.add_request_handler("tools/list", PaginatedRequestParams, list_tools)
server.add_request_handler("tools/call", CallToolRequestParams, call_tool)


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
	parser.add_argument("--platform", default=None, help="传给 boss CLI 的默认平台，如 zhipin")
	parser.add_argument("--role", choices=("candidate", "recruiter"), default=None, help="传给 boss CLI 的默认角色")
	parser.add_argument(
		"--browser-source",
		# 取值域只从策略表推导，与 main.py / config_cmd.py 一致；新增来源不必再改这里。
		choices=tuple(BROWSER_SOURCES),
		default=None,
		help="传给 boss CLI 的浏览器通道来源（默认 auto）",
	)
	return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> None:
	args = _parse_cli_args(argv)
	_configure_boss_invocation(
		boss_bin=args.boss_bin,
		data_dir=args.data_dir,
		platform=args.platform,
		role=args.role,
		browser_source=args.browser_source,
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
