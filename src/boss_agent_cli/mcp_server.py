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

from anyio import CancelScope
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
	CallToolRequestParams,
	CallToolResult,
	ListToolsResult,
	PaginatedRequestParams,
	TextContent,
	Tool,
	ToolAnnotations,
)

from boss_agent_cli.api.browser_source import POLICIES as BROWSER_SOURCES
from boss_agent_cli.display import RISK_ERROR_CONTRACTS
from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.mcp_tools import TOOLS
from boss_agent_cli.output import envelope_error, json_safe_value, serialization_failure_envelope
from boss_agent_cli.schema.tool_policy import READ_ONLY_TOOL_NAMES as _READ_ONLY_TOOL_NAMES
from boss_agent_cli.schema.tool_policy import static_read_only
from boss_agent_cli.services.contact_lookup import MAX_FRIEND_LOOKUP_PAGES
from boss_agent_cli.services.friend_list_pages import MAX_FRIEND_LIST_PAGES
from boss_agent_cli.services.recruiter_last_messages import max_last_messages_requests
from boss_agent_cli.search_filters import detail_filter_max_pages, resolve_active_level

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
#: pipeline / follow-up / digest 聚合好友列表的估算页数上限：与 collector
#: 的真实翻页上限一致；按 5 页估算会让 8 页以上的账号被提前掐断（Issue #2024）。
_FRIEND_LIST_ESTIMATE_PAGES = MAX_FRIEND_LIST_PAGES
#: ``boss me`` 未指定 ``--section`` 时依次请求的四段：用户信息、简历基本信息、
#: 求职期望、投递记录；相邻两次请求都要经过默认节流（Issue #2852）。
_ME_FULL_SECTION_REQUESTS = 4
#: batch-greet 的 CLI 默认值与上限（命令内会把 --count 截断到 10）。
_BATCH_GREET_DEFAULT_COUNT = 10
_BATCH_GREET_MAX_COUNT = 10
#: export 的 CLI 默认导出数量。
_EXPORT_DEFAULT_COUNT = 50
#: 招聘者聊天摘要与最近消息的 CLI 默认条数。
_HR_DEFAULT_LIMIT = 100
#: ``hr accept-resume``：只读预检（历史 + 好友详情）+ 一次同意 POST；``--dry-run``
#: 不请求平台。默认 120s 只够一次请求，健康调用会在预检阶段被宿主掐断
#: （Issue #3009）。
_HR_ACCEPT_RESUME_REQUESTS = 3
#: ``hr download-resume``：历史 + 好友详情 + 预览检查 + 附件下载各一次
#: （Issue #3009）。
_HR_DOWNLOAD_RESUME_REQUESTS = 4
#: 招聘自动化一轮先扫描的 tab 数上界（新招呼 / 未读）。
_AUTOMATION_SCAN_REQUESTS = 2
#: 自动化每个会话最多一次历史读取 + 一次动作派发；``--limit`` 是本轮全局
#: 会话上限，缺省时扫描量由本地 automation 配置决定，MCP 层无法预知
#: （Issue #2854）。
_AUTOMATION_REQUESTS_PER_CONVERSATION = 2


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


# 子进程超时 / 取消时，只有 `schema.tool_policy.READ_ONLY_TOOL_NAMES` 里的工具
# 才能声明「可重放」。白名单与静态只读注解共用同一份规则（Issue #2997）。

#: 工具名整体只读、但个别参数会把调用变成平台写操作。判定必须看参数：
#: `boss_hr_jobs` 的 `action=online|offline` 会 POST 上线/下线，超时后若按只读
#: 告诉 Agent「可直接重试」，就会在状态未知时重发一次状态变更（Issue #869）。
_TOOL_WRITE_MODES: dict[str, frozenset[str]] = {
	"boss_hr_jobs": frozenset({"online", "offline"}),
	# config 的 list / get 只读本地配置；set / reset 才写本地文件，按写处理。
	"boss_config": frozenset({"set", "reset"}),
	# stop 写本地停止标记；结果未知时无条件重放可能中断期间恢复的任务。
	"boss_wizard": frozenset({"run", "resume", "stop"}),
}

#: 带 action 维度但 schema 缺省值不是 ``list`` 的工具：`_tool_may_write` 的缺省
#: action 必须与 schema 的 ``default`` 一致，否则省略 action 会被误判成只读。
#: `boss_wizard` 省略 action 时映射层执行 run（Issue #2552）。
_DEFAULT_ACTION_BY_TOOL: dict[str, str] = {"boss_wizard": "run"}

#: schema 里带 action 维度的工具。只读白名单中的这类工具读写取决于参数，必须在
#: `_TOOL_WRITE_MODES` 显式登记（写模式可以是空集）；漏登记时按写处理，不让新增
#: 的写 action 静默回到「超时也告诉 Agent 可直接重试」的旧缺陷（Issue #968）。
_ACTION_DIMENSION_TOOL_NAMES = frozenset(
	tool.name for tool in TOOLS if "action" in tool.input_schema.get("properties", {})
)

def _static_read_only(tool: Tool) -> bool:
	"""返回工具在所有参数组合下是否都只读（供 MCP ``ToolAnnotations`` 使用）。

	规则与 ``boss schema --format mcp-tools`` 导出共用
	``schema.tool_policy.static_read_only``，避免两处注解漂移（Issue #2997）。
	动态写判定仍由 ``_tool_may_write`` 按参数负责，两者互不替代。
	"""
	return static_read_only(tool.name, tool.input_schema)


#: ``tools/list`` 返回带 ``ToolAnnotations`` 的副本：``readOnlyHint`` 规范缺省
#: 为 false，不注明会让宿主对 ``boss_schema``、``boss_search`` 这类只读调用也
#: 逐个确认。``destructiveHint`` / ``idempotentHint`` / ``openWorldHint`` 保持
#: 规范默认值，避免误标（Issue #2993）。延迟到请求时构造：mock 环境下的
#: ``mcp.types.Tool`` 替身没有 ``model_copy``，模块导入不能依赖它。
def _annotated_tools() -> list[Tool]:
	return [
		tool.model_copy(update={"annotations": ToolAnnotations(read_only_hint=_static_read_only(tool))})
		for tool in TOOLS
	]


#: schema 缺省 ``dry_run=true`` 的工具：省略该键时 ``mcp_args._build_args`` 仍会
#: 生成 ``--dry-run``，实际语义是只预览。不能按「未显式声明只读」归为写，否则
#: 超时 / 取消会误报 ``outcome_unknown`` 并让 Agent 去核对并不存在的平台写入
#: （Issue #2414）。守卫测试保证集合与 TOOLS 的 schema 默认值一致。
#: MCP 侧固定不产生平台写入的工具：schema 缺省 ``dry_run=true`` 的 ``boss_agent_run``，
#: 以及从不生成 ``--live`` 的 ``boss_agent_train``（CLI 的 ``agent train`` 不传
#: ``--live`` 即演练，MCP 工具没有 live 参数）。不能按「未显式声明只读」归为写，
#: 否则超时 / 取消会误报 ``outcome_unknown`` 并让 Agent 去核对并不存在的平台写入
#: （Issue #2414、#2454）。守卫测试保证 schema 默认 dry_run=true 的工具都登记在案。
_DRY_RUN_DEFAULT_TOOL_NAMES = frozenset({"boss_agent_run", "boss_agent_train"})

_WRITE_INTERRUPT_RECOVERY = "检查本地 checkpoint 与平台实际结果，再显式恢复；未确认的写操作不要直接重试"
_READ_INTERRUPT_RECOVERY = "只读命令已中断，不产生平台写入；如仍需结果，可按原参数重新调用"
_EXPORT_READ_INTERRUPT_RECOVERY = "导出已中断，不产生平台写入；可减少 count 或收窄筛选范围后重试"
_FILTER_READ_INTERRUPT_RECOVERY = (
	"只读命令已中断，不产生平台写入；福利 / 活跃度筛选可能逐条查详情，可缩小筛选范围后重试。"
	"如需避免查详情，可停用福利筛选，并仅用 active=online 筛选在线职位"
)
_PRESET_READ_INTERRUPT_RECOVERY = (
	"只读搜索已中断，不产生平台写入；若预设包含需查详情的福利 / 活跃度筛选，可缩小筛选范围；"
	"如需避免查详情，可停用福利筛选，并仅用 active=online 筛选在线职位后重试"
)
#: agent train 的 MCP 映射固定演练，没有 active 等筛选参数（Issue #2454）。
_DRY_RUN_INTERRUPT_RECOVERY = "演练命令已中断，不产生平台写入；可直接重试或降低 limit"
#: 只读保证只适用于原参数：未开启预览时，增大零上限会变成真实写入（Issue #2847）。
_BATCH_GREET_READ_INTERRUPT_RECOVERY = "只读调用已中断，未发出任何招呼；如仍需结果，请保留原来的预览模式或零上限，按原参数重试"
#: 本地维护工具只写本机文件：中断后不能按通用写文案让 Agent 去核对平台结果，
#: 那会把恢复引向不存在的平台写入（Issue #2975）。
_LOCAL_RESUME_INIT_INTERRUPT_RECOVERY = (
	"初始化已中断；本地简历文件可能已写入。先运行 boss_resume_list 确认名称，再决定是否重试；不产生平台写入"
)
_LOCAL_RESUME_DELETE_INTERRUPT_RECOVERY = (
	"删除已中断；本机删除可能已生效。先运行 boss_resume_list 确认，再决定是否重试；不产生平台写入"
)
_LOCAL_FAVORITES_SYNC_INTERRUPT_RECOVERY = (
	"同步已中断；本地候选池可能已部分更新，重新执行会按当前收藏继续 upsert；平台侧只读"
)
_LOCAL_RESUME_CLONE_INTERRUPT_RECOVERY = (
	"复制已中断；目标简历可能已写入。先运行 boss_resume_list 核对，再决定是否重试；不产生平台写入"
)
_LOCAL_RESUME_LINK_INTERRUPT_RECOVERY = (
	"职位关联已中断；本机关联表可能已写入。先用 boss_resume_applications 核对，再决定是否重试；不产生平台写入"
)
_LOCAL_RESUME_EXPORT_INTERRUPT_RECOVERY = (
	"导出已中断；目标文件可能已部分写入或尚未生成。先核对输出目录中的文件，再决定是否重试；不产生平台写入"
)
_LOCAL_RESUME_IMPORT_INTERRUPT_RECOVERY = (
	"导入已中断；本地简历文件可能已写入。先运行 boss_resume_list 核对，再决定是否重试；不产生平台写入"
)
_LOCAL_RESUME_EDIT_INTERRUPT_RECOVERY = (
	"编辑已中断；本地简历可能已保存新值。先用 boss_resume_show 核对，再决定是否重试；不产生平台写入"
)


def _write_interrupt_recovery(args: tuple[str, ...]) -> str:
	if args[:2] == ("resume", "init"):
		return _LOCAL_RESUME_INIT_INTERRUPT_RECOVERY
	if args[:2] == ("resume", "delete"):
		return _LOCAL_RESUME_DELETE_INTERRUPT_RECOVERY
	if args[:2] == ("resume", "clone"):
		return _LOCAL_RESUME_CLONE_INTERRUPT_RECOVERY
	if args[:2] == ("resume", "link"):
		return _LOCAL_RESUME_LINK_INTERRUPT_RECOVERY
	if args[:2] == ("resume", "export"):
		return _LOCAL_RESUME_EXPORT_INTERRUPT_RECOVERY
	if args[:2] == ("resume", "import"):
		return _LOCAL_RESUME_IMPORT_INTERRUPT_RECOVERY
	if args[:2] == ("resume", "edit"):
		return _LOCAL_RESUME_EDIT_INTERRUPT_RECOVERY
	if args[:2] == ("favorites", "sync"):
		return _LOCAL_FAVORITES_SYNC_INTERRUPT_RECOVERY
	if args and args[0] == "wizard" and _option_value(args, "--stop") is not None:
		return "先用 wizard --status 查询同一 run_id 的当前状态；确认仍需停止后再发出 stop，不要自动重试"
	return _WRITE_INTERRUPT_RECOVERY


def _has_explicit_detail_filters(args: tuple[str, ...]) -> bool:
	welfare = _option_value(args, "--welfare") or ""
	if any(label.strip() for label in welfare.split(",")):
		return True
	try:
		active = resolve_active_level(_option_value(args, "--active"))
	except ValueError:
		# 无效取值由 CLI 校验，不据此推断子进程已查详情。
		return False
	return active not in (None, "online")


def _read_interrupt_recovery(args: tuple[str, ...]) -> str:
	if args and args[0] == "wizard" and _option_value(args, "--status") is not None:
		return "状态查询已中断，未更改任务；可重新查询同一 run_id"
	if args[:2] == ("agent", "train"):
		return _DRY_RUN_INTERRUPT_RECOVERY
	if args and args[0] == "search":
		if _has_explicit_detail_filters(args):
			return _FILTER_READ_INTERRUPT_RECOVERY
		if _option_value(args, "--preset"):
			# 恢复文案不读取本地预设，不把请求数估算的最坏情况当成已发生的事实。
			return _PRESET_READ_INTERRUPT_RECOVERY
	if args and args[0] == "batch-greet":
		# 能走到只读分支的 batch-greet 只有 --dry-run 与 --count 0：候选在发出
		# 招呼前被截断，没有写请求；通用文案里的 active=online 不是它的参数
		# （Issue #2839）。
		return _BATCH_GREET_READ_INTERRUPT_RECOVERY
	if args and args[0] == "export":
		# 与 search 共用归一化口径，让「在线」等别名不再误入详情筛选分支。
		if _has_explicit_detail_filters(args):
			return _FILTER_READ_INTERRUPT_RECOVERY
		return _EXPORT_READ_INTERRUPT_RECOVERY
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
	arguments = arguments or {}
	# --dry-run 的契约是只预览、不产生平台写入（batch-greet / clean / agent run /
	# hr greet / hr location / hr accept-resume）。按工具名整体归为写会让超时 /
	# 取消返回 outcome_unknown 并要求核对并不存在的平台写入（Issue #2177）。
	if arguments.get("dry_run") is True:
		return False
	if tool_name in _DRY_RUN_DEFAULT_TOOL_NAMES and "dry_run" not in arguments:
		# schema 默认 dry_run=true：省略键与显式 true 的 argv 完全相同。
		return False
	if tool_name == "boss_batch_greet":
		# limit=0：CLI 在收集候选之前判断上限，只做一次搜索、不发出任何招呼
		# （commands/greet.py）。按 --dry-run 同口径归为只读，否则超时 / 取消会报
		# outcome_unknown 并要求核对并不存在的平台写入（Issue #2839）。
		limit = arguments.get("limit")
		if isinstance(limit, int) and not isinstance(limit, bool) and limit <= 0:
			return False
	if tool_name not in _READ_ONLY_TOOL_NAMES:
		return True
	write_modes = _TOOL_WRITE_MODES.get(tool_name)
	if write_modes is None:
		# 带 action 维度却没有登记写模式：无法判断哪个动作会产生平台写入，按写处理
		# （与「未列出的工具按写」同一保守原则）。守卫测试保证现有工具不会走到这里。
		return tool_name in _ACTION_DIMENSION_TOOL_NAMES
	# 缺省 action 与 schema 默认值一致：登记过的工具缺省是 list，wizard 缺省是 run
	# （`_DEFAULT_ACTION_BY_TOOL`）。只有显式声明写模式才算写。
	raw_action = arguments.get("action")
	action = raw_action if isinstance(raw_action, str) else _DEFAULT_ACTION_BY_TOOL.get(tool_name, "list")
	return action in write_modes


def _option_region(args: tuple[str, ...]) -> tuple[str, ...]:
	"""返回 ``--`` 之前的选项区；``--`` 之后一律是位置参数（Issue #2176）。

	位置参数可以合法地以连字符开头（`_literal_positionals` 把它们放到 ``--``
	之后）；把整段 argv 当选项扫描会把关键词误读成开关或选项取值。
	"""
	try:
		return args[: args.index("--")]
	except ValueError:
		return args


def _option_value(args: tuple[str, ...], flag: str) -> str | None:
	"""取 ``--flag value`` / ``--flag=value`` 的取值；未出现时返回 None。"""
	prefix = f"{flag}="
	region = _option_region(args)
	for index, arg in enumerate(region):
		if arg == flag:
			return region[index + 1] if index + 1 < len(region) else None
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
	if command in {"chatmsg", "chat-summary", "mark", "exchange"}:
		if command == "chatmsg" and _option_value(args, "--friend-uid") is not None:
			# 显式稳定 uid 直接读取历史，不扫描联系人列表。
			return 1
		# 先逐页找联系人，再读历史或派发一次操作。漏算查找会在默认节流下
		# 提前杀掉健康调用；页数与查找服务共用常量，仍受 900s 封顶（Issue #3001）。
		return min(MAX_FRIEND_LOOKUP_PAGES + 1, _MAX_ESTIMATED_REQUESTS)
	if command == "favorites" and len(args) > 1 and args[1] == "sync":
		# 收藏同步逐页拉取且没有可拆分的分页参数：只按默认 120s 估算，收藏较多的
		# 账号必然在同步中途被掐断（本地可能已部分 upsert）。直接给满估算封顶，
		# 超时仍受 _MAX_CLI_TIMEOUT_SECONDS 约束。
		return _MAX_ESTIMATED_REQUESTS
	if command == "batch-greet":
		# 只看 ``--`` 之前的选项：关键词恰好是 "--dry-run" 时不能把预算当成预览
		# （Issue #2176）。
		if "--dry-run" in _option_region(args):
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
		welfare = _option_value(args, "--welfare") or ""
		active = _option_value(args, "--active") or ""
		if welfare or active not in ("", "online"):
			# 带福利 / 活跃度时 export 与 search 走同一条详情管线：逐条查
			# job_card，页数上限同为 detail_filter_max_pages。只按 --count
			# 数列表页会把 80 次请求估成 4 次，宿主在约 290s 就以
			# WORKFLOW_TIMEOUT 中断只读导出（Issue #2725）。
			pages = detail_filter_max_pages(welfare, active or None)
			return min(pages * (_SEARCH_PAGE_SIZE + 1), _MAX_ESTIMATED_REQUESTS)
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
			# 好友列表带 friendSource，分组后各自分批，不能只按总数分页（Issue #3006）。
			batches = max_last_messages_requests(summary_limit, grouped_by_source=True)
			return min(1 + batches, _MAX_ESTIMATED_REQUESTS)
		if subcommand == "last-messages":
			limit = _int_option(args, "--limit", _HR_DEFAULT_LIMIT)
			if limit is None or limit < 1:
				return None
			# 无 --friend-id 时先取列表及来源（Issue #2025），之后可能分成两组。
			# 显式 ID 没有来源信息，仍是单组；估算共用服务的默认批次大小。
			from_list = "--friend-id" not in _option_region(args)
			batches = max_last_messages_requests(limit, grouped_by_source=from_list)
			return min(int(from_list) + batches, _MAX_ESTIMATED_REQUESTS)
		if subcommand == "accept-resume":
			# --dry-run 只预览本地参数，不请求平台（Issue #3009）。
			if "--dry-run" in _option_region(args):
				return None
			return _HR_ACCEPT_RESUME_REQUESTS
		if subcommand == "download-resume":
			return _HR_DOWNLOAD_RESUME_REQUESTS
		return None
	if command == "watch" and len(args) > 1 and args[1] == "run":
		# watch run 对保存的搜索应用福利 / 活跃度筛选，逐条查详情；
		# 最坏情况与 search --welfare 共用同一翻页上限（Issue #1860）。
		pages = detail_filter_max_pages("welfare", "today")
		return pages * (_SEARCH_PAGE_SIZE + 1)
	if command == "me":
		# 不指定 --section 时四段信息各发一次请求；单段调用只有一次。
		# 缺省 120s 只够一次请求加一次节流，四段全量会被宿主提前掐断
		# （Issue #2852）。
		return 1 if _option_value(args, "--section") is not None else _ME_FULL_SECTION_REQUESTS
	if command == "agent" and len(args) > 1 and args[1] in {"run", "train"}:
		# 一轮自动化先扫描 tab，再逐会话读取历史并在需要时派发一次动作；
		# --limit 是本轮全局会话上限。缺省时扫描范围由本地 automation 配置
		# 决定，MCP 层无法预知，按估算封顶值给满，避免默认 120s 杀掉一次正常
		# 演练（Issue #2854）。
		raw_limit = _option_value(args, "--limit")
		if raw_limit is None:
			return _MAX_ESTIMATED_REQUESTS
		try:
			limit = int(raw_limit)
		except (TypeError, ValueError):
			# 显式非法取值：CLI 会在发出任何请求前以 INVALID_PARAM 拒绝。
			return None
		if limit < 1:
			return None
		return min(
			_AUTOMATION_SCAN_REQUESTS + _AUTOMATION_REQUESTS_PER_CONVERSATION * limit,
			_MAX_ESTIMATED_REQUESTS,
		)
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
	requested_timeout = _option_value(args, "--timeout") if args and args[0] == "wizard" else None
	if requested_timeout is not None:
		try:
			requested = float(requested_timeout)
		except ValueError:
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


_CLI_ENVELOPE_KEYS = frozenset({"ok", "schema_version", "command", "data", "pagination", "error", "hints"})


def _is_cli_envelope(payload: dict[str, Any], returncode: int | None) -> bool:
	if payload.keys() != _CLI_ENVELOPE_KEYS or type(payload["ok"]) is not bool:
		return False
	if not isinstance(payload["schema_version"], str) or not isinstance(payload["command"], str):
		return False
	ok = payload["ok"]
	if returncode is not None and returncode != (0 if ok else 1):
		return False
	error = payload["error"]
	if ok:
		return error is None
	return (
		isinstance(error, dict)
		and isinstance(error.get("code"), str)
		and type(error.get("recoverable")) is bool
		and "recovery_action" in error
	)


def _parse_cli_output(
	args: tuple[str, ...], stdout: str, stderr: str, *, returncode: int | None = None, may_write: bool = True,
) -> dict[str, Any]:
	try:
		parsed = json.loads(stdout)
	except (ValueError, RecursionError):
		# 超长整数抛裸 ValueError、超深嵌套抛 RecursionError，都不是
		# JSONDecodeError；逃逸出去会让 handler 抛异常而不是返回信封（Issue #1942）。
		parsed = None
	if isinstance(parsed, dict) and _is_cli_envelope(parsed, returncode):
		return parsed
	# 可解码的对象也可能不是信封；退出码与 ok 矛盾时同样不能确认结果。
	# 进程已经运行过，不能把缺失或失真的写结果当作未发出（Issue #962）。
	return _cli_failure(
		args,
		"CLI_ERROR",
		stderr or "底层 CLI 未返回与退出码一致的 JSON 信封",
		recovery_action=_write_interrupt_recovery(args) if may_write else _READ_UNPARSEABLE_RECOVERY,
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


def _invalid_launch_args(args: tuple[str, ...], reason: str) -> dict[str, Any]:
	# 子进程从未启动，不应引导调用方核对不存在的写入（Issue #1863）。
	return _cli_failure(
		args,
		"INVALID_PARAM",
		reason,
		outcome_unknown=False,
		recovery_action="参数未通过本地校验，子进程未启动，修正参数后重试；本次没有平台写入",
	)


def _run_boss(*args: str, may_write: bool = True) -> dict[str, Any]:
	"""Synchronous compatibility entrypoint; MCP handlers use the async path."""
	cmd = [_BOSS_BIN, "--json", *_BOSS_GLOBAL_ARGS, *args]
	if unsafe_reason := _unsafe_argv_reason(cmd):
		return _invalid_launch_args(args, unsafe_reason)
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
			recovery_action=_write_interrupt_recovery(args) if may_write else _read_interrupt_recovery(args),
		)
	except OSError as exc:
		return _spawn_failure(args, exc)
	return _parse_cli_output(args, result.stdout, result.stderr, returncode=result.returncode, may_write=may_write)


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
	if isinstance(checkpoint_result, dict) and _is_cli_envelope(checkpoint_result, None):
		child_error = checkpoint_result.get("error")
		if isinstance(child_error, dict) and child_error.get("code") in RISK_ERROR_CONTRACTS:
			# 清理阶段已经收到风控终止信封：保留人工恢复动作、hints 与断点，
			# 不能用可重试的超时 / 取消覆盖平台硬边界（Issue #3000）。
			return checkpoint_result
	return _cli_failure(
		args,
		code,
		"MCP 调用已取消" if code == "WORKFLOW_STOPPED" else "底层 CLI 超时",
		outcome_unknown=may_write,
		recovery_action=_write_interrupt_recovery(args) if may_write else _read_interrupt_recovery(args),
		checkpoint_result=checkpoint_result if isinstance(checkpoint_result, dict) else None,
	)


async def _run_boss_async(*args: str, may_write: bool = True) -> dict[str, Any]:
	if args and args[0] == "wizard" and not any(
		_option_value(args, flag) is not None for flag in ("--timeout", "--status", "--stop")
	):
		args = (*args, "--timeout", str(_CLI_TIMEOUT_SECONDS))
	# 估算在启动前完成，参数错误不会启动可能产生写入的子进程。
	try:
		timeout = _cli_timeout(args)
	except (ArithmeticError, ValueError) as exc:
		return _cli_failure(
			args,
			"INVALID_PARAM",
			f"参数超出本地估算范围：{exc}",
			outcome_unknown=False,
			recovery_action="减小 count / summary-limit 后重试；本次子进程未启动",
		)
	cmd = [_BOSS_BIN, "--json", *_BOSS_GLOBAL_ARGS, *args]
	# handler 校验工具参数；实际启动前还要覆盖宿主注入的全局选项和可执行文件名。
	if unsafe_reason := _unsafe_argv_reason(cmd):
		return _invalid_launch_args(args, unsafe_reason)
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
		# asyncio.shield 保护启动任务，却不保护等待者。MCP SDK 的 AnyIO 取消域
		# 会在之后每次 await 再抛取消；必须保护取回句柄及清理，不能遗留仍运行的 CLI。
		with CancelScope(shield=True):
			try:
				process = await asyncio.shield(spawn)
			except OSError as exc:
				# 启动失败未产生平台操作，应指向安装或路径（Issue #1000）。
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
		stdout, stderr = await asyncio.wait_for(asyncio.shield(output), timeout)
	except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
		with CancelScope(shield=True):
			stdout, _ = await asyncio.shield(_stop_cli_process(process, output))
		code = "WORKFLOW_STOPPED" if isinstance(exc, asyncio.CancelledError) else "WORKFLOW_TIMEOUT"
		return _interrupted_failure(args, code, stdout, may_write=may_write)
	return _parse_cli_output(
		args,
		stdout.decode("utf-8", errors="replace"),
		stderr.decode("utf-8", errors="replace"),
		returncode=process.returncode,
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
	return ListToolsResult(tools=_annotated_tools())


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
#: 超过这个位数的整数不直接转十进制文本：Python 的 int→str 位数上限会让
#: 错误信息本身抛 ValueError，进而把 tools/call 变成内部错误（Issue #1620）。
_MAX_BRIEF_NUMBER_BITS = 256
#: JSON 客户端（JavaScript 等）能精确表示的最大整数（``Number.MAX_SAFE_INTEGER``
#: = ``2**53 - 1``）。浮点输入超过这个量级时相邻整数已经无法区分，归一化成 int
#: 只会把丢失精度的值当精确整数使用（Issue #2553）。
_JSON_SAFE_INTEGER = 2**53 - 1
#: enum 校验失败时最多列出的合法取值数：行业等枚举有几十项，错误信封不随
#: schema 规模膨胀。
_MAX_ENUM_VALUES_IN_ERROR = 8


def _brief_numeric(value: object) -> str:
	if isinstance(value, int) and not isinstance(value, bool) and value.bit_length() > _MAX_BRIEF_NUMBER_BITS:
		return f"超大整数（{value.bit_length()} bit）"
	return str(value)


def _matches_json_type(value: Any, expected: str) -> bool:
	# Python 里 bool 是 int 子类；JSON 里 true/false 不是数字，反向也一样。
	return isinstance(value, _JSON_TYPE_CHECKS[expected]) and (expected == "boolean" or not isinstance(value, bool))


def _is_integral_float(value: Any) -> bool:
	"""JSON Schema 的 ``integer`` 包含小数部分为零的浮点数（如 1.0）。

	``float('inf')`` / ``float('nan')`` 的 ``is_integer()`` 为 False，不会进入
	归一化；``bool`` 已被 ``isinstance(value, float)`` 排除。
	"""
	return isinstance(value, float) and value.is_integer()


def _normalize_integral_numbers(tool_name: str, arguments: dict[str, Any]) -> None:
	"""把参数里小数部分为零的浮点整数就地归一成 ``int``（Issue #2175）。

	JSON 数字 ``1.0`` 按 schema 语义是合法 ``integer``，Python 解码成 ``float``
	后会被类型检查拒绝；即使放行，``str(1.0)`` 拼进 argv 也会变成选项值
	``"1.0"``。这里在类型检查与映射之前统一归一，两处共用同一份数据。

	超出 ``2**53 - 1`` 的整值浮点不归一（Issue #2553）：IEEE 754 在该量级无法表示
	相邻整数，调用方看到的 ``9007199254740993.0`` 实际已经是 ``...992``，
	再转成 ``int`` 只会把已丢失精度的值当成精确整数发出去。
	"""
	tool = next((item for item in TOOLS if item.name == tool_name), None)
	if tool is None:
		return
	properties = tool.input_schema.get("properties", {})
	for key, value in list(arguments.items()):
		prop = properties.get(key)
		if not isinstance(prop, dict):
			continue
		if prop.get("type") == "integer" and _is_integral_float(value):
			if abs(value) <= _JSON_SAFE_INTEGER:
				arguments[key] = int(value)
			continue
		items = prop.get("items")
		if (
			prop.get("type") == "array"
			and isinstance(items, dict)
			and items.get("type") == "integer"
			and isinstance(value, list)
		):
			arguments[key] = [
				int(item) if _is_integral_float(item) and abs(item) <= _JSON_SAFE_INTEGER else item
				for item in value
			]


# 预设 / 订阅 / 批量打招呼的四种筛选在 Click 中显式不区分大小写。schema 仍发布
# 标准写法，入口不能因新增枚举检查而拒绝此前合法的大小写变体。工具名单必须与
# CLI 的 click.Choice(..., case_sensitive=False) 对齐（Issue #1180、#2839）。
_CASE_INSENSITIVE_FILTER_ENUMS = frozenset({"industry", "scale", "stage", "job_type"})
_CASE_INSENSITIVE_ENUM_TOOLS = frozenset({"boss_preset_add", "boss_watch_add", "boss_batch_greet"})


def _json_enum_allows(enum: list[Any], value: Any, *, case_insensitive: bool = False) -> bool:
	"""按 JSON Schema 的 enum 语义判断取值是否合法。

	数值按值相等（``1`` 与 ``1.0`` 同值）；``bool`` 不与 ``0`` / ``1`` 混同。
	"""
	if isinstance(value, bool):
		return any(item is value for item in enum)
	if case_insensitive and isinstance(value, str):
		folded = value.casefold()
		return any(isinstance(item, str) and item.casefold() == folded for item in enum)
	for item in enum:
		if isinstance(item, bool):
			continue
		if isinstance(value, (int, float)) and isinstance(item, (int, float)):
			if value == item:
				return True
			continue
		if type(item) is type(value) and item == value:
			return True
	return False


def _enum_error(key: str, value: Any, enum: list[Any]) -> str:
	shown = [item if isinstance(item, str) else _brief_numeric(item) for item in enum[:_MAX_ENUM_VALUES_IN_ERROR]]
	allowed = "、".join(shown)
	if len(enum) > _MAX_ENUM_VALUES_IN_ERROR:
		allowed += f" 等 {len(enum)} 个取值"
	if isinstance(value, str):
		# 错误只展示一段可读预览，避免反射整段误传文本或内嵌控制字符。
		preview = value[:80]
		rendered = preview if preview.isprintable() and preview else repr(preview)
		if len(value) > len(preview):
			rendered += "…"
	else:
		rendered = _brief_numeric(value)
	return f"{key}={rendered} 不是允许的取值：{allowed}"


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
		if (
			expected == "integer"
			and isinstance(value, float)
			and value.is_integer()
			and abs(value) > _JSON_SAFE_INTEGER
		):
			# 超范围的整值浮点已在归一化时跳过（Issue #2553）：报精度问题比
			# 「应为 integer，收到 float」更接近调用方需要修的东西。
			errors.append(
				f"{key}={_brief_numeric(value)} 超出 JSON 安全整数范围（2^53-1）："
				"请用 JSON 整数字面量传入原值，不要使用小数点或指数写法"
			)
			continue
		if not _matches_json_type(value, expected):
			errors.append(f"{key} 应为 {expected}，收到 {type(value).__name__}")
			continue
		enum_values = prop.get("enum")
		if isinstance(enum_values, list) and enum_values:
			case_insensitive = tool_name in _CASE_INSENSITIVE_ENUM_TOOLS and key in _CASE_INSENSITIVE_FILTER_ENUMS
			if not _json_enum_allows(enum_values, value, case_insensitive=case_insensitive):
				errors.append(_enum_error(key, value, enum_values))
				continue
		if expected == "string":
			# schema 声明了 minLength 时，入口按同一约束拒绝（Issue #1533）：
			# 空串在 CLI 上大多只能得到参数错误，客户端本可以在发送前拦下。
			min_length = prop.get("minLength")
			if isinstance(min_length, int) and not isinstance(min_length, bool) and len(value) < min_length:
				errors.append(f"{key} 至少需要 {min_length} 个字符")
		if expected in {"integer", "number"}:
			if isinstance(value, float) and not math.isfinite(value):
				# JSON 的 1e400 解码成 inf：上下界比较对 inf / nan 恒为 False，
				# 入口放行后会被拼成 `--timeout inf` 之类的取值（Issue #2553）。
				errors.append(f"{key} 必须是有限数值，收到 {_brief_numeric(value)}")
				continue
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
		if item_type == "string" and prop.get("uniqueItems") is True:
			# 目前仅 selectors / requested_steps 声明字符串去重（Issue #2976）。
			# 类型错误先报；重复项最多报一条，避免错误数随数组大小增长。
			seen: set[str] = set()
			for index, item in enumerate(value):
				if not isinstance(item, str):
					continue
				if item in seen:
					errors.append(f"{key}[{index}] 与前面的元素重复")
					break
				seen.add(item)
		item_min_length = items.get("minLength")
		item_minimum = items.get("minimum")
		item_maximum = items.get("maximum")
		# 取值错误与类型错误共用上限：十万个空标签逐个拼进 message 会让错误信封
		# 膨胀到数 MB，与 `_MAX_ELEMENT_TYPE_ERRORS` 声明的「不随输入规模膨胀」
		# 相悖（Issue #2399）。
		value_errors: list[str] = []
		value_error_count = 0

		def _record_value_error(message: str) -> None:
			nonlocal value_error_count
			if len(value_errors) < _MAX_ELEMENT_TYPE_ERRORS:
				value_errors.append(message)
			else:
				value_error_count += 1

		for index, item in enumerate(value):
			if isinstance(item_min_length, int) and not isinstance(item_min_length, bool):
				# schema 的 items.minLength 是客户端与入口共同的契约：空标签不会
				# 走到 CLI，也不会在 mapping 层被拼成 `--add-tag ""`（Issue #1595）。
				if isinstance(item, str) and len(item) < item_min_length:
					_record_value_error(f"{key}[{index}] 至少需要 {item_min_length} 个字符")
			if isinstance(item_minimum, (int, float)) and not isinstance(item_minimum, bool):
				if isinstance(item, (int, float)) and not isinstance(item, bool) and item < item_minimum:
					_record_value_error(f"{key}[{index}]={_brief_numeric(item)} 不能小于 {item_minimum}")
			if isinstance(item_maximum, (int, float)) and not isinstance(item_maximum, bool):
				if isinstance(item, (int, float)) and not isinstance(item, bool) and item > item_maximum:
					_record_value_error(f"{key}[{index}]={_brief_numeric(item)} 不能大于 {item_maximum}")
		errors.extend(value_errors)
		if value_error_count:
			errors.append(f"{key} 另有 {value_error_count} 个元素取值错误")
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
	_normalize_integral_numbers(params.name, arguments)
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
