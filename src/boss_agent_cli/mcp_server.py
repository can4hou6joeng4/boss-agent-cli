"""MCP Server for boss-agent-cli — 让 Claude Desktop / Cursor 直接调用 BOSS 直聘求职工具。

工具目录 `TOOLS` 在 `mcp_tools`，工具名 → CLI 参数的映射在 `mcp_args`；
本模块只保留服务器实例、CLI 调用、传输层与入口。两者的公开符号在下方原样再导出，
`mcp-server/server.py` wrapper 与既有测试的导入路径因此保持不变。
"""

import argparse
import asyncio
import errno
import json
import math
import os
import signal
import stat
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
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
from boss_agent_cli.output import envelope_error, json_safe_value, serialization_failure_envelope
from boss_agent_cli.services.friend_list_pages import MAX_FRIEND_LIST_PAGES
from boss_agent_cli.search_filters import detail_filter_max_pages

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
#: 初始值只是回退；模块加载末尾会用 `_default_boss_bin()` 解析成与 run() 相同的入口。
_BOSS_BIN = DEFAULT_BOSS_BIN
_BOSS_GLOBAL_ARGS: list[str] = []
_CLI_TIMEOUT_SECONDS = 120.0
_CHECKPOINT_GRACE_SECONDS = 5.0
_TERMINATE_GRACE_SECONDS = 1.0

#: 按参数估算子进程超时时的单次请求预算（秒）。默认节流
#: ``DEFAULT_REQUEST_DELAY = (30.0, 60.0)`` 的高斯均值是 45s、上界 60s；
#: 每次按 60s 节流上界 + 5s 抖动 / 请求开销估算。
_REQUEST_BUDGET_SECONDS = 65.0
#: 登录态、缓存初始化与信封输出等非请求开销的固定余量。
_REQUEST_MARGIN_SECONDS = 30.0
#: 估算超时的封顶：更长的参数应让调用方拆分，而不是让 MCP 调用挂到天荒地老。
_MAX_CLI_TIMEOUT_SECONDS = 900.0
#: 搜索结果页固定 15 条；详情筛选要逐条请求。
_SEARCH_PAGE_SIZE = 15
#: 招聘者聊天摘要 / 最近消息按每批 50 条请求估算。
_HR_BATCH_SIZE = 50
#: pipeline / follow-up / digest 聚合好友列表的估算页数上限：与 collector
#: 的真实翻页上限一致；按 5 页估算会让 8 页以上的账号被提前掐断（Issue #2024）。
_FRIEND_LIST_ESTIMATE_PAGES = MAX_FRIEND_LIST_PAGES
#: batch-greet 的 CLI 默认值与上限（命令内会把 --count 截断到 10）。
_BATCH_GREET_DEFAULT_COUNT = 10
_BATCH_GREET_MAX_COUNT = 10
#: export 的 CLI 默认导出数量。
_EXPORT_DEFAULT_COUNT = 50
#: 招聘者聊天摘要与最近消息的 CLI 默认条数。
_HR_DEFAULT_LIMIT = 100


def _default_boss_bin(*, executable: str | None = None, windows: bool | None = None) -> str:
	"""未显式传 ``--boss-bin`` 时使用的底层 CLI。

	``boss`` 与 ``boss-mcp`` 是同一次安装的两个入口，都在运行本 server 的解释器
	旁边。MCP 主机常用绝对路径启动 ``boss-mcp`` 且不继承登录 shell 的 PATH
	（``uv tool install`` / ``pipx install`` 的入口目录不在其中），只按 PATH 找
	``boss`` 会让全部工具调用失败；PATH 上若另有一份旧版 ``boss``，还会被静默
	驱动（Issue #1066）。同目录入口不存在时才回退到按 PATH 查找。
	"""
	interpreter = sys.executable if executable is None else executable
	if not interpreter:
		# 嵌入式运行时 sys.executable 可能为空；不能据此落到当前目录下的同名文件。
		return DEFAULT_BOSS_BIN
	bin_dir = Path(interpreter).parent
	on_windows = os.name == "nt" if windows is None else windows
	for name in (("boss.exe", "boss") if on_windows else ("boss",)):
		candidate = bin_dir / name
		try:
			mode = candidate.stat().st_mode
		except FileNotFoundError:
			continue
		# 只把「不存在」当未找到：3.14 起 is_file() 会吞 EACCES，静默回退
		# PATH 上另一份 boss 会驱动旧版本（Issue #1066 / #1864），这里让其它
		# OSError 显式暴露。stat 跟随符号链接，指向普通文件的入口仍然可用。
		if stat.S_ISREG(mode):
			return str(candidate)
	return DEFAULT_BOSS_BIN


# 直接调用 `_run_boss()` / handler 而不经 run() 的嵌入方与测试也走同安装入口：
# 默认值停留在裸名字会从 PATH 找 boss，PATH 上另一份旧安装（或坏 shim）会被
# 静默驱动（Issue #1066 / #2088）。run() 的显式 --boss-bin 仍会覆盖这里。
_BOSS_BIN = _default_boss_bin()


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
	# 空串是显式取值，不是「未提供」：真值判断会在映射层丢弃它，子进程静默回落
	# 到默认数据目录 / 平台（Issue #1471）。原样传给 CLI，由 CLI 参数校验报
	# INVALID_PARAM，调用方才能看到自己传错了。
	if data_dir is not None:
		args.extend(["--data-dir", data_dir])
	if platform is not None:
		args.extend(["--platform", platform])
	if role is not None:
		args.extend(["--role", role])
	if browser_source is not None:
		args.extend(["--browser-source", browser_source])
	_BOSS_GLOBAL_ARGS = args


# 子进程超时 / 取消时，只有明确只读的工具才能声明「可重放」。未列出的工具
# （包括未来新增的）一律按「可能已产生平台副作用」处理：漏判只读只是保守，
# 漏判写操作会让 Agent 直接重试一个可能已经生效的动作。
_READ_ONLY_TOOL_NAMES = frozenset({
	"boss_status",
	"boss_doctor",
	"boss_schema",
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
	"boss_chat_summary",
	"boss_agent_stats",
	"boss_agent_pending",
	"boss_agent_review",
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
_READ_INTERRUPT_RECOVERY = "只读命令已中断，不产生平台写入；先降低 limit/count 或改用 active=online 再重试"
#: search / watch 没有 limit / count 参数，恢复文案不能提示不存在的选项（Issue #1860）。
_FILTER_READ_INTERRUPT_RECOVERY = (
	"只读命令已中断，不产生平台写入；该命令按福利 / 活跃度筛选逐条查详情，"
	"改用 active=online 或缩小筛选范围后重试"
)


def _read_interrupt_recovery(args: tuple[str, ...]) -> str:
	if args and args[0] in {"search", "watch"}:
		return _FILTER_READ_INTERRUPT_RECOVERY
	return _READ_INTERRUPT_RECOVERY
#: 子进程没能启动：从未运行过，不可能有平台副作用。真正要处理的是安装 / 路径，
#: 让 Agent 去核对 checkpoint 与平台结果只会把人引向不存在的现场（Issue #962）。
_SPAWN_FAILURE_RECOVERY = (
	"底层 boss CLI 未能启动，未产生任何平台操作；确认 boss-agent-cli 已安装且 boss 在 "
	"MCP server 进程的 PATH 中，或用 boss-mcp --boss-bin 指定可执行文件路径后重新调用"
)
_ARGV_TOO_LONG_RECOVERY = (
	"底层 boss CLI 未启动，未产生任何平台操作；缩短文本参数或拆分成多次调用后重试"
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


def _option_value(args: tuple[str, ...], flag: str) -> str | None:
	"""取 ``--flag value`` / ``--flag=value`` 的取值；未出现时返回 None。"""
	prefix = f"{flag}="
	for index, arg in enumerate(args):
		if arg == flag:
			return args[index + 1] if index + 1 < len(args) else None
		if arg.startswith(prefix):
			return arg[len(prefix):]
	return None


def _int_option(args: tuple[str, ...], flag: str, default: int) -> int | None:
	"""解析整数选项：缺省取 ``default``，显式值非法时返回 None（回退默认超时）。"""
	raw = _option_value(args, flag)
	if raw is None:
		return default
	try:
		return int(raw)
	except (TypeError, ValueError):
		return None


def _estimated_requests(args: tuple[str, ...]) -> int | None:
	"""按 argv 估算一次调用最坏情况下的平台请求数。

	返回 None 表示这条命令不做参数估算（保持默认 120s 超时）。
	"""
	command = args[0]
	if command == "batch-greet":
		if "--dry-run" in args:
			# 只做一次搜索，没有写请求。
			return 1
		count = _int_option(args, "--count", _BATCH_GREET_DEFAULT_COUNT)
		if count is None:
			return None
		# 1 次搜索 + 每个候选人 1 次招呼；CLI 会把 --count 截断到 10（Issue #1491）。
		return 1 + min(max(count, 0), _BATCH_GREET_MAX_COUNT)
	if command == "search":
		welfare = _option_value(args, "--welfare") or ""
		active = _option_value(args, "--active") or ""
		if _option_value(args, "--preset") is not None:
			# 预设可能保存 welfare / active，展开后同样逐条查详情：1 次请求的
			# 普通搜索估算只适用于无筛选调用（Issue #1860）。
			pages = detail_filter_max_pages("welfare", "today")
			return pages * (_SEARCH_PAGE_SIZE + 1)
		if not welfare and active in ("", "online"):
			# 普通搜索 / active=online 只看列表：每页 1 次请求。
			return 1
		# 其余档位逐条查职位详情：每页 15 条详情 + 该页列表请求。
		# 翻页上限与 search / watch / wizard 共用 detail_filter_max_pages。
		pages = detail_filter_max_pages(welfare, active or None)
		return pages * (_SEARCH_PAGE_SIZE + 1)
	if command == "export":
		count = _int_option(args, "--count", _EXPORT_DEFAULT_COUNT)
		if count is None or count < 1:
			return None
		# 整数除法避免超大 JSON 整数在浮点除法时抛 OverflowError（Issue #1609）。
		# 超时最终按 900 秒封顶，这里没必要保留比封顶更大的精确页数。
		pages = -(-count // _SEARCH_PAGE_SIZE)
		return min(pages, _MAX_ESTIMATED_REQUESTS)
	if command == "hr":
		subcommand = args[1] if len(args) > 1 else ""
		if subcommand == "chat":
			summary_limit = _int_option(args, "--summary-limit", _HR_DEFAULT_LIMIT)
			if summary_limit is None:
				return None
			# 1 次好友列表 + 每 50 条摘要一批。整数除法避免超大整数在
			# 浮点除法时抛 OverflowError（Issue #1612）。
			batches = -(-max(summary_limit, 0) // _HR_BATCH_SIZE)
			return min(1 + batches, _MAX_ESTIMATED_REQUESTS)
		if subcommand == "last-messages":
			limit = _int_option(args, "--limit", _HR_DEFAULT_LIMIT)
			if limit is None or limit < 1:
				return None
			# 与 summary-limit 分支同口径：整数分页并封顶（Issue #1612）。
			batches = -(-limit // _HR_BATCH_SIZE)
			# 无 --friend-id 时命令先取一页沟通列表再分批拉消息（Issue #2025）。
			base = 0 if "--friend-id" in args else 1
			return min(base + batches, _MAX_ESTIMATED_REQUESTS)
		return None
	if command == "watch" and len(args) > 1 and args[1] == "run":
		# watch run 对保存的搜索应用福利 / 活跃度筛选，逐条查详情；
		# 最坏情况与 search --welfare 共用同一翻页上限（Issue #1860）。
		pages = detail_filter_max_pages("welfare", "today")
		return pages * (_SEARCH_PAGE_SIZE + 1)
	if command in {"pipeline", "follow-up", "digest"}:
		# 好友列表逐页聚合，再追加一次面试数据请求（Issue #2024）。
		return _FRIEND_LIST_ESTIMATE_PAGES + 1
	return None


# 任何单命令的超时估算都不会超过这个上限；同时作为大整数分页的封顶值。
_MAX_ESTIMATED_REQUESTS = int(_MAX_CLI_TIMEOUT_SECONDS // _REQUEST_BUDGET_SECONDS) + 1


def _cli_timeout(args: tuple[str, ...]) -> float:
	# 超时按最坏情况下的平台请求数估算（Issue #1491 / #1492）：
	#   默认节流 DEFAULT_REQUEST_DELAY = (30.0, 60.0)，高斯均值 45s、上界 60s；
	#   RequestThrottle.wait() 首次请求不等待，之后的每次请求最多等 60s。
	#   单次请求按 60s 节流上界 + 5s 抖动 / 请求开销 = 65s 计，另加 30s 固定余量
	#   （登录态、缓存初始化、信封输出），并以 900s 封顶。
	#   例：batch-greet --count 5 = 1 次搜索 + 5 次招呼 → 6×65+30 = 420s；
	#       search --welfare = 5 页 ×（15 条详情 + 1 次列表）→ 80×65+30 = 5230s，
	#       按 900s 封顶；普通 search 只有 1 次列表请求 → 95s，仍取默认 120s。
	# A wizard deadline belongs to the child. The host allows bounded extra
	# time for that child to commit its result/checkpoint and emit the envelope.
	if args and args[0] == "wizard" and "--timeout" in args:
		try:
			requested = float(args[args.index("--timeout") + 1])
		except (IndexError, ValueError):
			return _CLI_TIMEOUT_SECONDS
		if math.isfinite(requested) and requested > 0:
			# wizard 子进程自己的 --timeout 没有上限：宿主若按任意大值等待，
			# 客户端一次超时调用会把 MCP server 挂到近似无界（Issue #1594）。
			return min(requested + _CHECKPOINT_GRACE_SECONDS, _MAX_CLI_TIMEOUT_SECONDS)
		return _CLI_TIMEOUT_SECONDS
	requests = _estimated_requests(args) if args else None
	if requests is None:
		return _CLI_TIMEOUT_SECONDS
	estimate = requests * _REQUEST_BUDGET_SECONDS + _REQUEST_MARGIN_SECONDS
	return min(max(estimate, _CLI_TIMEOUT_SECONDS), _MAX_CLI_TIMEOUT_SECONDS)


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
	if exc.errno == errno.E2BIG:
		# 参数超过系统 argv 上限：子进程从未启动，问题在入参而不是安装 / PATH
		# （Issue #1943）。
		return _cli_failure(
			args,
			"INVALID_PARAM",
			"工具参数过长，超过系统命令行长度上限，底层 boss CLI 未启动",
			recovery_action=_ARGV_TOO_LONG_RECOVERY,
			outcome_unknown=False,
		)
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
	except (ValueError, RecursionError):
		# 超长整数抛裸 ValueError、超深嵌套抛 RecursionError，都不是
		# JSONDecodeError；逃逸出去会让 handler 抛异常而不是返回信封（Issue #1942）。
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


def _dumps_tool_result(payload: dict[str, Any]) -> str:
	"""把工具结果序列化成可直接放进 ``TextContent`` 的文本。

	平台字段或本地坏文件里可能出现 JSON 合法的孤立代理转义（``\\udcff`` /
	``\\ud800``）：``json.loads`` 会把它还原成孤立代理，``json.dumps(...,
	ensure_ascii=False)`` 又原样写回。MCP SDK 用 UTF-8 序列化 ``TextContent`` 时
	抛 ``PydanticSerializationError``，异常穿出 ``stdio_server()`` 让进程退出，
	写工具的成功响应就此丢失，客户端还可能重试（Issue #1375）。
	与 CLI 信封同口径用 ``backslashreplace`` 转成字面 ``\\udcff`` 转义，解析出的
	值不变，响应始终可编码。

	``allow_nan=False`` 与 CLI 出口同口径（Issue #1391）：NaN / ±Infinity / 超长
	整数先经 :func:`json_safe_value` 归一为 ``null``；归一后仍无法严格序列化时返回
	固定 ``INTERNAL_ERROR`` 信封，异常不会穿出工具 handler。
	"""
	try:
		# 快路径：正常结果与修复前逐字节一致。
		text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
	except (TypeError, ValueError, RecursionError):
		try:
			text = json.dumps(json_safe_value(payload), ensure_ascii=False, indent=2, allow_nan=False)
		except (TypeError, ValueError, RecursionError):
			command = payload.get("command") if isinstance(payload, dict) else None
			text = json.dumps(
				serialization_failure_envelope(command), ensure_ascii=False, indent=2, allow_nan=False
			)
	return text.encode("utf-8", "backslashreplace").decode("utf-8")


def _run_boss(*args: str, may_write: bool = True) -> dict[str, Any]:
	"""Synchronous compatibility entrypoint; MCP handlers use the async path."""
	cmd = [_BOSS_BIN, "--json", *_BOSS_GLOBAL_ARGS, *args]
	unsafe_reason = _unsafe_argv_reason(cmd)
	if unsafe_reason is not None:
		# 子进程从未启动：不能套用「核对 checkpoint」的写中断模板，写工具本次
		# 同样没有平台写入（Issue #1863）。
		return _cli_failure(
			args,
			"INVALID_PARAM",
			unsafe_reason,
			outcome_unknown=False,
			recovery_action="参数未通过本地校验，子进程未启动，修正参数后重试；本次没有平台写入",
		)
	# 超时估算同样必须遵守 schema 的数值边界；本地参数错误不能伪装成 CLI 失败。
	try:
		timeout = _cli_timeout(args)
	except (ArithmeticError, ValueError) as exc:
		estimate_error = _cli_failure(
			args,
			"INVALID_PARAM",
			f"参数超出本地估算范围：{exc}",
			outcome_unknown=False,
		)
		# envelope_error 会从登记表补齐 recovery_action；本地参数错误未启动子进程，
		# 不能沿用其它 INVALID_PARAM 来源的「修正参数」笼统文案。
		estimate_error["error"]["recovery_action"] = (
			"减小 --summary-limit / --limit / --count 后重试；本次未启动子进程。"
			"参数提示由 --help 给出，输入未进入 CLI"
		)
		return estimate_error
	try:
		result = subprocess.run(
			cmd,
			capture_output=True,
			text=True,
			timeout=timeout,
			stdin=subprocess.DEVNULL,
		)
	except subprocess.TimeoutExpired:
		return _cli_failure(
			args,
			"WORKFLOW_TIMEOUT",
			"底层 CLI 超时",
			outcome_unknown=may_write,
			recovery_action=_WRITE_INTERRUPT_RECOVERY if may_write else _read_interrupt_recovery(args),
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
	except (ValueError, RecursionError):
		# ValueError 已覆盖 JSONDecodeError / UnicodeDecodeError 与超长整数。
		checkpoint_result = None
	return _cli_failure(
		args,
		code,
		"MCP 调用已取消" if code == "WORKFLOW_STOPPED" else "底层 CLI 超时",
		outcome_unknown=may_write,
		recovery_action=_WRITE_INTERRUPT_RECOVERY if may_write else _read_interrupt_recovery(args),
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
	# 超时估算必须在 spawn 之后仍可安全求值：异常先停子进程并回标准失败信封，
	# 不能把 OverflowError 漏成 MCP 内部错误（Issue #1609）。
	try:
		timeout = _cli_timeout(args)
	except (ArithmeticError, ValueError) as exc:
		stdout, _ = await asyncio.shield(_stop_cli_process(process, output))
		return _cli_failure(
			args,
			"INVALID_PARAM",
			f"参数超出本地估算范围：{exc}",
			outcome_unknown=False,
			recovery_action="减小 count / summary-limit 后重试；本次子进程已停止，未返回结果",
		)
	try:
		stdout, stderr = await asyncio.wait_for(asyncio.shield(output), timeout)
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


def _unsafe_argv_reason(args: "list[str] | tuple[str, ...]") -> str | None:
	"""argv 里的 NUL / 孤立代理项返回原因（Issue #1236）。

	两者都能编码在 JSON-RPC 字符串里，但传给子进程时
	``create_subprocess_exec`` 会抛 ``ValueError``、UTF-8 编码会抛
	``UnicodeEncodeError``；异常逃出 handler 会被 MCP SDK 变成
	"Internal server error"，调用方拿不到错误信封与修复指引。
	"""
	for arg in args:
		if "\x00" in arg:
			return "参数中含 NUL 字符，无法传给底层 boss CLI；请去掉该字符后重试"
		if any(0xD800 <= ord(char) <= 0xDFFF for char in arg):
			return "参数中含孤立代理项字符，无法编码为 UTF-8；请修正文本后重试"
	return None


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

#: 单个数组最多逐条列出的元素错误数，其余只报数量：错误信封不随输入规模膨胀。
_MAX_ELEMENT_TYPE_ERRORS = 3
# 超过这个位数的整数不直接转十进制文本：Python 的 int→str 位数上限会让
# 错误信息本身抛 ValueError，进而把 tools/call 变成内部错误（Issue #1620）。
_MAX_BRIEF_NUMBER_BITS = 256


def _brief_numeric(value: object) -> str:
	if isinstance(value, int) and not isinstance(value, bool) and value.bit_length() > _MAX_BRIEF_NUMBER_BITS:
		return f"超大整数（{value.bit_length()} bit）"
	return str(value)


def _matches_json_type(value: Any, expected: str) -> bool:
	# Python 里 bool 是 int 子类；JSON 里 true/false 不是数字，反向也一样。
	return isinstance(value, _JSON_TYPE_CHECKS[expected]) and (expected == "boolean" or not isinstance(value, bool))


def _argument_type_errors(tool_name: str, arguments: dict[str, Any]) -> list[str]:
	"""按 inputSchema 校验参数类型，返回人类可读的错误列表。

	`_build_args` 用 `is True` / truthiness 判断布尔参数：客户端把 `dry_run` /
	`yes` 传成字符串时，标志位会被静默丢弃或反向生效（`"false"` 是 truthy），
	写命令的预览 / 确认语义随之改变。类型错误必须在进入 CLI 之前拒绝。

	数组还要按 `items.type` 逐元素校验（Issue #1105）：`_build_args` 用 `str()`
	拼装元素，布尔值或对象会变成 `False`、`{'k': 'v'}` 这样的文本写进本地标签，
	整数数组里的错误元素则要到 CLI 才得到一条不指明位置的参数错误。
	数值上下界与数组元素的 minLength 也按 inputSchema 校验（Issue #1594 / #1595），
	避免越界值到 CLI 后被静默截断、空标签写进本地候选池。
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
		if not _matches_json_type(value, expected):
			errors.append(f"{key} 应为 {expected}，收到 {type(value).__name__}")
			continue
		if expected == "string":
			# schema 声明了 minLength 时，入口按同一约束拒绝（Issue #1533）：
			# 空串在 CLI 上大多只能得到参数错误，客户端本可以在发送前拦下。
			min_length = prop.get("minLength")
			if isinstance(min_length, int) and not isinstance(min_length, bool) and len(value) < min_length:
				errors.append(f"{key} 至少需要 {min_length} 个字符")
		if expected in {"integer", "number"}:
			minimum = prop.get("minimum")
			maximum = prop.get("maximum")
			if isinstance(minimum, (int, float)) and not isinstance(minimum, bool) and value < minimum:
				errors.append(f"{key}={_brief_numeric(value)} 不能小于 {minimum}")
			if isinstance(maximum, (int, float)) and not isinstance(maximum, bool) and value > maximum:
				errors.append(f"{key}={_brief_numeric(value)} 不能大于 {maximum}")
		if expected == "array":
			# 空数组与「不传」在 mapping 层不可区分：例如 last-messages 的
			# friend_ids=[] 会退化成抓取当前页候选人并发出平台请求（Issue #1218）。
			min_items = prop.get("minItems")
			if isinstance(min_items, int) and not isinstance(min_items, bool) and len(value) < min_items:
				errors.append(f"{key} 至少需要 {min_items} 个元素")
		items = prop.get("items")
		item_type = items.get("type") if isinstance(items, dict) else None
		if expected != "array" or not isinstance(item_type, str) or item_type not in _JSON_TYPE_CHECKS:
			continue
		invalid = [index for index, item in enumerate(value) if not _matches_json_type(item, item_type)]
		errors.extend(
			f"{key}[{index}] 应为 {item_type}，收到 {type(value[index]).__name__}"
			for index in invalid[:_MAX_ELEMENT_TYPE_ERRORS]
		)
		if len(invalid) > _MAX_ELEMENT_TYPE_ERRORS:
			errors.append(f"{key} 另有 {len(invalid) - _MAX_ELEMENT_TYPE_ERRORS} 个元素类型错误")
		if not isinstance(items, dict):
			return errors
		item_min_length = items.get("minLength")
		item_minimum = items.get("minimum")
		item_maximum = items.get("maximum")
		for index, item in enumerate(value):
			if isinstance(item_min_length, int) and not isinstance(item_min_length, bool):
				# schema 的 items.minLength 是客户端与入口共同的契约：空标签不会
				# 走到 CLI，也不会在 mapping 层被拼成 `--add-tag ""`（Issue #1595）。
				if isinstance(item, str) and len(item) < item_min_length:
					errors.append(f"{key}[{index}] 至少需要 {item_min_length} 个字符")
			if isinstance(item_minimum, (int, float)) and not isinstance(item_minimum, bool):
				if isinstance(item, (int, float)) and not isinstance(item, bool) and item < item_minimum:
					errors.append(f"{key}[{index}]={_brief_numeric(item)} 不能小于 {item_minimum}")
			if isinstance(item_maximum, (int, float)) and not isinstance(item_maximum, bool):
				if isinstance(item, (int, float)) and not isinstance(item, bool) and item > item_maximum:
					errors.append(f"{key}[{index}]={_brief_numeric(item)} 不能大于 {item_maximum}")
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
	# 显式 ``null`` 与缺键等价于省略（Issue #958 / #982 / #1002 的契约）：映射层所有
	# 可选参数判断都为 ``None`` 留了位置，文档也承诺 ``null`` 落回默认值，但类型检查
	# 会先把 ``null`` 判成类型错误，导致 `boss_wizard.action: null` 到不了默认的 run。
	# 在类型检查与映射之前剔除；必填参数传 null 因此走缺参分支，而不是把
	# ``str(None)`` 拼成 "None" 发给 CLI（Issue #1108）。
	arguments = {key: value for key, value in arguments.items() if value is not None}
	try:
		type_errors = _argument_type_errors(params.name, arguments)
	except (ArithmeticError, TypeError, ValueError) as exc:
		# 本地校验不能把不可格式化的超大整数等异常漏成 MCP 内部错误（Issue #1620）。
		return _tool_argument_error(params.name, f"参数校验失败：{type(exc).__name__}")
	if type_errors:
		# 类型错误同样不能交给 `_build_args` 的 truthiness 判断：`"false"` 是 truthy，
		# `is True` 对 `"true"` 为假，`dry_run` / `yes` 会因此静默丢失或反向生效。
		return _tool_argument_error(
			params.name,
			"参数无效：" + "；".join(type_errors) + "；请按 tools/list 的 inputSchema 传参",
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
		# 超大整数的十进制转换也可能在这里抛 ValueError（Issue #1620），
		# 此时不能把 Python 的位数上限原文回显给调用方。
		if "integer string conversion" in str(exc):
			return _tool_argument_error(params.name, "参数取值非法：存在超出本地数值表示范围的参数")
		return _tool_argument_error(params.name, f"参数取值非法：{exc}")
	unsafe_reason = _unsafe_argv_reason(args)
	if unsafe_reason is not None:
		return _tool_argument_error(params.name, unsafe_reason)
	result = await _run_boss_async(*args, may_write=_tool_may_write(params.name, arguments))
	# 信封的 ok 是唯一真源，但 MCP 客户端还需要 is_error 才能把失败标成工具错误；
	# 两者不一致时，ok:false 会被当成一次成功的工具调用。
	return CallToolResult(
		content=[TextContent(type="text", text=_dumps_tool_result(result))],
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
	parser.add_argument(
		"--boss-bin",
		default=None,
		help="底层 boss CLI 可执行文件路径（默认使用与 boss-mcp 同一安装的 boss 入口，找不到时按 PATH 查找）",
	)
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
		boss_bin=args.boss_bin if args.boss_bin is not None else _default_boss_bin(),
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
