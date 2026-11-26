"""Rich terminal rendering + TTY-aware output routing.

TTY mode: Rich tables/panels to stderr, nothing to stdout.
Pipe mode (Agent): JSON envelope to stdout.
--json flag: Force JSON to stdout even in TTY.
"""

import datetime
import json
import math
import re
import sqlite3
import sys
import threading
from collections.abc import Iterable, Iterator, Mapping, Sequence
from itertools import islice
from pathlib import Path
from typing import Any, Callable

import click
from rich.cells import cell_len
from rich.console import Console, ConsoleOptions, RenderableType
from rich.errors import MarkupError
from rich.panel import Panel
from rich.segment import Segment
from rich.table import Table
from rich.text import Text

from boss_agent_cli.api._base_client import (
	PlatformResponseInvalid,
	PlatformStatusRejected,
	RequestTextEncodingError,
	parameter_status_code,
)
from boss_agent_cli.api.models import safe_text
from boss_agent_cli.cache.store import (
	CacheBusyError,
	CacheDatabaseInvalid,
	CacheRecordInvalid,
	CacheWriteRejected,
	is_cache_busy_error,
)
from boss_agent_cli.output import (
	emit_success,
	exit_code_for_error,
	is_local_io_error,
	redact_sensitive,
	redact_sensitive_text,
)
from boss_agent_cli.schema.error_codes import ERROR_CODES
from boss_agent_cli.services.browser_runtime import (
	is_browser_kernel_missing,
	patchright_headless_shell_command,
	patchright_install_command,
)
from boss_agent_cli.services.saved_searches import configured_search_params
from boss_agent_cli.services.text_files import sanitize_surrogates

#: C0（保留 ``\t`` / ``\n``）与 C1 控制字符。Rich 只处理 markup，不会剥离原始
#: 转义字节：平台文本里的 ESC / OSC / CSI 会原样写进终端，可改写标题、伪造
#: 可点击链接或清屏（Issue #2245）。
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

#: 与 ``rich.markup.RE_TAGS`` 同形：Rich 认作标签的片段，连同紧邻的前导反斜杠。
_MARKUP_TAG = re.compile(r"(\\*)(\[[a-z#/@][^[]*?])")
#: 标签之外的 ``\[``：Rich 渲染时会还原成 ``[``，吃掉一个反斜杠。
_BACKSLASHED_BRACKET = re.compile(r"(\\+)\[")


class _SanitizingConsole(Console):
	"""在渲染出口按段剥离控制字符的 Console（Issue #2252）。

	``escape_markup`` 只覆盖经它转义的值；``Text.append``、表格单元格等不走
	markup 的路径曾经把平台文本里的 ESC 原样写进终端。所有内容最终都以
	``Segment`` 形式经过 ``render``，在这里过滤一次即可覆盖现有与以后的
	调用点。Rich 自己的光标 / 清屏指令走控制段（``segment.control`` 非空），
	样式的 ANSI 序列在写出阶段由 style 生成，都不受影响。
	"""

	def render(
		self,
		renderable: RenderableType,
		options: ConsoleOptions | None = None,
	) -> Iterable[Segment]:
		for segment in super().render(renderable, options):
			if not segment.control and _CONTROL_CHARS.search(segment.text):
				segment = Segment(_CONTROL_CHARS.sub("", segment.text), segment.style)
			yield segment


# Rich output goes to stderr so stdout stays clean for Agent JSON.
# emoji=False：Rich 默认把文本里的 ``:name:`` 短码替换成 emoji，平台文本必须按字面显示。
console: Console = _SanitizingConsole(stderr=True, emoji=False)


def strip_control(value: str) -> str:
	"""剥离终端控制字符，保留制表符与换行（描述类字段需要多行渲染）。

	不经 Rich markup 的出口（``click.echo`` 菜单等）也用它清洗外部文本。
	"""
	return _CONTROL_CHARS.sub("", value)


def _keep_backslashes(segment: str) -> str:
	"""标签之外的文本：给每处「反斜杠 + ``[``」多补一个反斜杠，抵消 Rich 的还原。"""
	return _BACKSLASHED_BRACKET.sub(lambda match: f"{match.group(1)}\\[", segment)


def label_items(value: Any) -> list[Any]:
	"""把技能 / 标签这类「应当是列表」的外部字段归一成列表。

	平台字段漂移：字符串按单条处理（否则会被按字符拆开），其余非列表 / 元组
	按空处理，不让切片或 join 抛 TypeError（Issue #2247；wizard 同口径见 #2252）。
	"""
	if isinstance(value, str):
		return [value] if value else []
	if isinstance(value, (list, tuple)):
		return list(value)
	return []


def _is_plain_scalar(value: Any) -> bool:
	"""字符串或数值（不含布尔）：能直接拼进「a, b, c」的列表元素。"""
	return isinstance(value, str) or (isinstance(value, (int, float)) and not isinstance(value, bool))


def _plain_scalar_text(value: Any) -> str:
	"""纯标量列表元素：非有限浮点显示 ``-``，与 ``cell_text`` 的标量口径一致（Issue #2835）。"""
	if isinstance(value, float) and not math.isfinite(value):
		return "-"
	return value if isinstance(value, str) else safe_text(value)


def cell_text(value: Any) -> str:
	"""把表格单元格的值转成给真人看的文本；未做 markup 转义，交给 ``escape_markup``。

	``item.get(key, "-")`` 的默认值只在键缺失时生效：键存在而值是 ``None`` /
	列表 / 对象时，``str()`` 会把 ``None``、``[]``、``['远程', '双休']`` 这类
	Python repr 打进终端表格（Issue #2755）。

	- ``None`` 与空容器显示 ``-``，与键缺失同一写法；
	- 元素全是字符串 / 数值的列表用 ``, `` 连接（与职位详情的 skills 同写法）；
	- 其余容器输出 JSON 文本，与 ``--json`` 看到的一致；
	- 非有限浮点（``NaN`` / ``Infinity``，平台 JSON 的 ``1e400`` / ``NaN``
	  会解析成这类值）显示 ``-``，与 ``field_text`` / 通用面板同一缺失口径
	  （Issue #2817、#2835）；
	- 其余标量保持原样，空字符串仍是空单元格。
	"""
	if value is None:
		return "-"
	if isinstance(value, str):
		return value
	if isinstance(value, float) and not math.isfinite(value):
		return "-"
	if isinstance(value, (list, tuple, dict)):
		if not value:
			return "-"
		if not isinstance(value, dict) and all(_is_plain_scalar(element) for element in value):
			return ", ".join(_plain_scalar_text(element) for element in value)
		try:
			return json.dumps(value, ensure_ascii=False, default=str)
		except (TypeError, ValueError, RecursionError):
			# 循环引用、超长整数、深嵌套：都不是 JSON 数据能产生的形状，按原样兜底。
			return safe_text(value) or "-"
	return safe_text(value)


def escape_markup(value: Any) -> str:
	"""把平台 / 用户外部字符串转成 Rich 按字面渲染的文本。

	Rich 默认把字符串当 markup 解析：未配对的闭合标签（``[/bold]``）直接抛
	MarkupError 让整条 TTY 输出崩掉，``[link=...]`` 则会把终端渲染成可点击
	超链接（OSC 8）。公司名、职位名、职位描述、聊天内容、昵称都是平台外部
	输入，进入 Rich 前一律经过这里；代码自身的样式标签保持不变。

	同时剥离 ESC / OSC / CSI 等 C0 / C1 控制字符（Issue #2245）：``escape``
	只挡 markup，挡不住原始转义字节。

	返回值是自包含的（Issue #2251）：``rich.markup.escape`` 只转义值内部已经
	成对的标签，两个各自转义的值拼进同一段 markup 时，前值留下的 ``[a`` 与
	后值的 ``]`` 仍会配对成标签（吞掉中间文本或抛 MarkupError），结尾的
	反斜杠还会吃掉代码紧跟着写的闭合标签。含 ``[`` 或以反斜杠结尾的值因此
	包进空样式标签 ``[none]…[/none]``：Rich 的标签正则不能跨越 ``[``，值内
	悬空的 ``[`` 够不到值外的 ``]``；结尾反斜杠加倍后恰好还原成原数量，
	闭合标签保持有效。

	``str()`` 走 ``safe_text`` 同口径（Issue #2826）：调用点常把平台 / AI
	返回的原始标量直接传进来，合法 JSON 的超大整数会让 ``str()`` 抛
	``ValueError``、深嵌套容器抛 ``RecursionError``，异常发生在构造 markup
	之前，渲染出口的 ``_SanitizingConsole`` 兜不住。这两种形状按缺失处理。
	"""
	text = strip_control(sanitize_surrogates(safe_text(value)))
	if "[" not in text and not text.endswith("\\"):
		return text
	parts: list[str] = []
	position = 0
	for match in _MARKUP_TAG.finditer(text):
		parts.append(_keep_backslashes(text[position : match.start()]))
		backslashes, tag = match.groups()
		# 2n+1 个反斜杠：Rich 还原成 n 个字面反斜杠，并把标签当字面量。
		parts.append(f"{backslashes}{backslashes}\\{tag}")
		position = match.end()
	tail = text[position:]
	body = tail.rstrip("\\")
	parts.append(_keep_backslashes(body))
	# 紧贴闭合标签的 n 个反斜杠写成 2n 个：还原成 n 个，标签本身不被转义。
	parts.append("\\" * ((len(tail) - len(body)) * 2))
	return f"[none]{''.join(parts)}[/none]"


_SHELL_METACHARACTERS = frozenset("'\"`$\\!*?[]{}()<>|&;#")


def open_file_command(path: str) -> str:
	"""返回按当前平台打开本地文件的命令（Issue #1441）。

	``hints.next_actions`` 是给 Agent 直接执行的通道；``open`` 只在 macOS 存在，
	Windows / Linux 上会 command not found。Windows 用 ``cmd /c start``（cmd 与
	PowerShell 都能调用），其余 Unix 用 ``xdg-open``。
	"""
	if sys.platform.startswith("win"):
		# cmd.exe 不认 POSIX 单引号；含空格路径必须用双引号，并中和 %VAR% 展开
		# 与 cmd 元字符。start 的第一个参数是窗口标题，保留空串占位。
		windows_path = path.replace("^", "^^").replace("%", "%%").replace('"', '""')
		return f'cmd /c start "" "{windows_path}"'
	if path.startswith("-"):
		# `open -report.md` / `xdg-open -report.md` 会把文件名当选项；相对路径
		# 前置 `./` 后仍指向同一个文件，且不会被 getopt 识别为选项（Issue #1767）。
		path = f"./{path}"
	quoted = shell_argument(path)
	if sys.platform == "darwin":
		return f"open {quoted}"
	return f"xdg-open {quoted}"


def shell_argument(value: str) -> str:
	"""Quote *value* so it survives being copied into a runnable hint verbatim.

	只在需要时加引号：shlex.quote 用 ASCII 安全字符集，会把中文名也包上引号。
	除空白外还要挡 shell 元字符——简历名、watch 名、预设名都是自由文本：

	- 空白会把一个名字拆成多个参数；
	- ``its'fine`` 这种落单的单引号会让整条提示变成非法 shell（引号没闭合）；
	- ``*`` / ``$HOME`` / 开头的 ``~`` 会在终端里被展开成别的东西。

	以上情况统一加 POSIX 单引号（内部的单引号按 ``'\\''`` 转义）。
	"""
	if not any(ch.isspace() or ch in _SHELL_METACHARACTERS for ch in value) and not value.startswith("~"):
		return value
	return "'" + value.replace("'", "'\\''") + "'"


def positional_hint(value: str) -> str:
	"""把位置参数值安全地拼进后继命令（Issue #1783 #1797）。

	加 shell 引用，避免空白 / 元字符拆开或改写命令；以 ``-`` 开头的值前置
	``--``，否则重放时会被 click 当成选项。
	"""
	return positional_args(value)


def positional_args(*values: str) -> str:
	"""把多个位置参数值安全地拼进同一条后继命令（Issue #1848）。

	任一值以 ``-`` 开头时，只在最前面加一次 ``--``：逐参数各加会让第二个
	``--`` 变成普通参数，提示不可重放。
	"""
	quoted = " ".join(shell_argument(value) for value in values)
	if any(value.startswith("-") for value in values):
		return f"-- {quoted}"
	return quoted


def boss_command(
	command: str, *, platform_name: str = "zhipin", data_dir: Path | None = None, role: str | None = None
) -> str:
	"""构造后继命令；依赖本地状态时显式保留数据目录。"""
	prefix = "boss" if platform_name == "zhipin" else f"boss --platform {platform_name}"
	if data_dir is not None:
		prefix += f" --data-dir {shell_argument(str(data_dir.resolve()))}"
	if role:
		# 部分命令组（如 agent）不在 hr 组下，重放时必须显式带角色。
		prefix += f" --role {role}"
	return f"{prefix} {command}".strip()


def boss_command_for_ctx(
	ctx: Any,
	command: str,
	*,
	include_data_dir: bool = False,
	role: str | None = None,
) -> str:
	"""构造平台感知命令，可选择保留当前数据目录与角色。"""
	platform_name = "zhipin"
	data_dir = None
	if ctx and getattr(ctx, "obj", None):
		platform_name = ctx.obj.get("platform") or "zhipin"
		if include_data_dir:
			data_dir = ctx.obj.get("data_dir")
	return boss_command(command, platform_name=platform_name, data_dir=data_dir, role=role)


def show_command_for_ctx(ctx: Any, *, index_saved: bool) -> str | None:
	"""编号查看命令：只有本次列表写入了编号索引时才可用。

	``boss show <#>`` 读的是索引里最近一次写入的列表，不是屏幕上这张表；
	没写索引时给出编号会让用户打开另一条职位（Issue #925）。
	"""
	if not index_saved:
		return None
	return boss_command_for_ctx(ctx, "show <#>", include_data_dir=True)


def login_action_for_ctx(ctx: Any) -> str:
	"""登录恢复使用原平台与数据目录，避免登录后原命令仍无会话。"""
	return boss_command_for_ctx(ctx, "login", include_data_dir=True)


def contact_refresh_command_for_ctx(ctx: Any) -> str:
	"""CONTACT_NOT_FOUND 的后继命令：招聘者刷新 hr chat，求职者刷新 chat。"""
	command = "hr chat" if ctx and getattr(ctx, "obj", None) and ctx.obj.get("role") == "recruiter" else "chat"
	return boss_command_for_ctx(ctx, command, include_data_dir=True)


def unconfirmed_write_contract_for_ctx(ctx: Any) -> tuple[str, dict[str, list[str]]]:
	"""ACTION_UNCONFIRMED 的恢复指引：按角色给出可执行的核对命令。

	登记表只保留角色中立文案；求职者只能核对自己的会话 / 投递，招聘者才用
	``hr chatmsg <friend_id>``。共享错误出口在回填或改写登记表文案时都走这里，
	避免未来新增的写命令漏掉角色覆盖（Issue #2921）。
	"""
	role = ctx.obj.get("role") if ctx and getattr(ctx, "obj", None) else None
	command = "hr chatmsg <friend_id>" if role == "recruiter" else "chat"
	check_command = boss_command_for_ctx(ctx, command, include_data_dir=True)
	return (
		f"动作可能已生效：先用 {check_command} 核对平台记录，不要自动重试",
		{"next_actions": [check_command]},
	)


def auth_required_hints(ctx: Any) -> dict[str, list[str]]:
	"""登录类错误附带的下一步：命令给 Agent 执行，说明给真人扫码。"""
	login_action = login_action_for_ctx(ctx)
	return {
		"next_actions": [login_action],
		"operator_actions": [f"在终端执行 {login_action}，按提示用 BOSS 直聘 App 扫码完成登录后重试"],
	}


#: 错误码登记表恢复文案里的示例命令；有运行上下文时改写成带 ``--data-dir``
#: 的真实命令（Issue #1092）。登记表是纯数据、被 `boss schema` 与文档引用，
#: 不能写死某个用户的数据目录；改写只发生在错误信封出口。
#:
#: 键必须按长度倒序匹配：``boss hr chat`` 是 ``boss hr chatmsg`` 的前缀，
#: ``boss wizard`` 是 ``boss wizard --status`` 的前缀，先短后长会切坏命令。
_REGISTRY_COMMANDS: dict[str, str] = {
	"boss agent pending list": "agent pending list",
	"boss agent review list": "agent review list",
	"boss agent run": "agent run",
	"boss agent stats": "agent stats",
	"boss ai config": "ai config",
	"boss chat": "chat",
	"boss clean --risk-lock": "clean --risk-lock",
	"boss config reset": "config reset",
	"boss crawl configure": "crawl configure",
	"boss crawl results": "crawl results",
	"boss crawl resume": "crawl resume",
	"boss crawl start": "crawl start",
	"boss crawl status": "crawl status",
	"boss doctor": "doctor",
	"boss hr chat": "hr chat",
	"boss hr chatmsg": "hr chatmsg",
	"boss hr reply": "hr reply",
	"boss hr request-resume": "hr request-resume",
	"boss login": "login",
	"boss logout": "logout",
	"boss me --section deliver": "me --section deliver",
	"boss preset list": "preset list",
	"boss preset remove": "preset remove",
	"boss recommend": "recommend",
	"boss resume delete": "resume delete",
	"boss resume list": "resume list",
	"boss search": "search",
	"boss shortlist add": "shortlist add",
	"boss shortlist list": "shortlist list",
	"boss watch list": "watch list",
	"boss watch remove": "watch remove",
	"boss wizard --input-json": "wizard --input-json",
	"boss wizard --resume": "wizard --resume",
	"boss wizard --status": "wizard --status",
	"boss wizard": "wizard",
	"boss --json wizard": "--json wizard",
}


def rewrite_command_examples(
	text: str,
	commands: Mapping[str, str],
	command_for: Callable[[str], str],
) -> str:
	"""一次扫描替换原文命令，保留引号内的路径和名称（Issue #1100）。

	不能逐项 ``str.replace``：插入的目录可能含另一条命令的字面量，下一轮
	替换会切坏路径。已构造的命令里的带引号参数也必须原样保留；Markdown
	反引号包裹的命令示例仍需改写。词内的撇号不作为引号边界，否则前文
	的 ``it's`` 或 ``'sample'unit'`` 会打乱后续路径的引号配对。
	"""
	if not commands:
		return text
	literals = "|".join(re.escape(literal) for literal in sorted(commands, key=len, reverse=True))
	single_quoted = r"(?<![A-Za-z0-9])'(?:[^']|(?<=[A-Za-z0-9])'(?=[A-Za-z0-9]))*'"
	pattern = re.compile(r"""\\.|""" + single_quoted + r"""|"(?:\\.|[^"\\])*"|(?P<command>""" + literals + ")")
	return pattern.sub(
		lambda match: command_for(commands[match["command"]]) if match["command"] else match[0],
		text,
	)


def registry_recovery_for_ctx(ctx: Any, text: str) -> str:
	"""把登记表恢复文案里的示例命令换成本次运行的命令（Issue #1092）。

	``boss doctor`` / ``boss resume list`` / ``boss crawl status`` 等读的都是
	``<data_dir>`` 里的登录态与本地记录；自定义 ``--data-dir`` 下照抄裸命令会
	诊断或查询默认目录，让真人与 Agent 以为任务丢了。无运行上下文（纯函数
	调用、``boss schema`` 文档）时原样返回。
	"""
	if ctx is None:
		return text

	def command_for(command: str) -> str:
		# agent 命令不在 hr 组下：恢复命令必须显式带角色，否则会读另一套本地
		# 状态，与同信封的 next_actions 也不一致（Issue #1524）。
		role = "recruiter" if command.startswith("agent ") else None
		return boss_command_for_ctx(ctx, command, include_data_dir=True, role=role)

	return rewrite_command_examples(text, _REGISTRY_COMMANDS, command_for)


def error_contract_for_code(
	code: str,
	*,
	fallback_recoverable: bool = False,
	fallback_recovery_action: str | None = None,
) -> tuple[bool, str | None]:
	"""Return recoverability metadata for a known error code."""
	spec = ERROR_CODES.get(code, {})
	return (
		bool(spec.get("recoverable", fallback_recoverable)),
		spec.get("recovery_action", fallback_recovery_action),
	)


def platform_error_contract(
	code: str,
	*,
	ctx: Any = None,
	clean_command: str | None = None,
	doctor_command: str | None = None,
	read_only: bool = False,
) -> tuple[bool, str | None, dict[str, Any] | None]:
	"""Return ``(recoverable, recovery_action, hints)`` for a platform error code.

	``read_only`` 标记产生该响应的平台调用不产生写入：未识别的响应只说明
	读取失败，按 ``NETWORK_ERROR`` 让 Agent 安全重试，而不是沿用写操作的
	「先核对官方页面、勿重试」契约（Issue #1519）。未知命令默认按写处理。

	风控码的恢复指引与 ``next_actions`` 一律以 ``RISK_ERROR_CONTRACTS`` 为准，
	避免各命令手工拼装出互相矛盾的停止指令。有运行上下文的调用方传入 ``ctx``：
	风险锁写在 ``<data_dir>/.cdp_risk_lock``，裸 ``boss clean --risk-lock`` 会去
	默认目录解锁，自定义目录的锁原样保留且命令不报错（Issue #978）。

	CONTACT_NOT_FOUND 是确定性结果：``next_actions`` 指向同一数据目录刷新沟通
	列表（招聘者 ``hr chat`` / 求职者 ``chat``），不重放原命令（Issue #1485）。
	"""
	if code == "UNKNOWN" and read_only:
		code = "NETWORK_ERROR"
	recoverable, recovery_action = error_contract_for_code(code)
	if code in RISK_ERROR_CONTRACTS:
		recovery_action, hints = risk_error_contract(
			code, ctx=ctx, clean_command=clean_command, doctor_command=doctor_command
		)
		return recoverable, recovery_action, hints
	if code == "CONTACT_NOT_FOUND":
		# friend_id 写错或联系人已变化是确定性结果：给出去同一数据目录刷新沟通
		# 列表的下一步，而不是让 Agent 重放同一条命令或让用户提 issue（#1485）。
		chat_command = contact_refresh_command_for_ctx(ctx)
		return recoverable, f"用 {chat_command} 核对联系人标识后重试", {"next_actions": [chat_command]}
	if code == "ACTION_UNCONFIRMED":
		# 写请求结果未知：登记表文案不区分角色，出口必须按当前角色注入核对命令。
		recovery_action, hints = unconfirmed_write_contract_for_ctx(ctx)
		return recoverable, recovery_action, hints
	if recovery_action:
		# 登记表是通用示例；诊断类命令（doctor / crawl status / resume list）读的
		# 是本次运行的数据目录（Issue #1092）。
		recovery_action = registry_recovery_for_ctx(ctx, recovery_action)
	return recoverable, recovery_action, None


def risk_error_message(code: str) -> str:
	"""Return a stop-oriented message for a risk code.

	风控信封不透传异常或平台响应文本（可能带临时凭据），只暴露稳定错误码与
	登记表文案；具体停止指引由 ``recovery_action`` / ``hints`` 承载。
	"""
	label = str(ERROR_CODES.get(code, {}).get("message") or "命中平台风控")
	return f"{label}；停止自动化访问并在官方页面处理"


def handle_browser_kernel_missing(ctx: Any, command: str, exc: Exception) -> None:
	"""内核缺失的统一出口：BROWSER_KERNEL_MISSING + 可执行的安装指引。

	Playwright 报 ``Executable doesn't exist`` / ``Playwright was just installed``
	是确定性失败：patchright 已升级但对应修订版的 Chromium 还没装，重试同一条
	命令（含 wizard 的自动重试）永远不会成功。此前命令层把它兜成
	``NETWORK_ERROR`` +「重试」，Agent 会拿同一条命令反复打平台。登录链路
	（``commands/_login_errors.py``）早已识别这条错误，命令层与 wizard 现在共用
	同一份契约与文案。异常文本可能带本机路径，只作为 ``details.cause`` 保留
	首行，不进 message。
	"""
	recoverable, _ = error_contract_for_code("BROWSER_KERNEL_MISSING")
	install_command = patchright_install_command()
	headless_command = patchright_headless_shell_command()
	doctor = boss_command_for_ctx(ctx, "doctor", include_data_dir=True)
	cause = (str(exc) or type(exc).__name__).splitlines()[0][:300]
	handle_error_output(
		ctx,
		command,
		code="BROWSER_KERNEL_MISSING",
		message="patchright 浏览器内核缺失或与所需修订版不匹配",
		recoverable=recoverable,
		# 恢复动作必须是**当前安装形态下可执行**的命令：`uv tool install` 只把
		# boss / boss-mcp 放进 PATH，裸 `patchright install chromium` 会 command
		# not found（Issue #1060）。用运行本 CLI 的解释器调模块入口，版本也必然
		# 与启动浏览器时一致。
		recovery_action=install_command,
		details={"cause": cause},
		hints={
			"next_actions": [install_command, doctor],
			"operator_actions": [
				f"浏览器内核安装完成后重新执行原命令；缺的是 headless shell 时运行 {headless_command}",
				f"仍失败时先跑 {doctor} 核对所需修订版与本机已安装版本",
			],
		},
	)


def platform_error_message(code: str, message: str | None, fallback_message: str) -> str:
	"""Return the envelope message for a platform error code.

	风控码只输出登记表文案：平台响应原文可能带临时凭据，不进信封。
	"""
	if code in RISK_ERROR_CONTRACTS:
		return risk_error_message(code)
	return message or fallback_message


def json_forced(ctx: Any) -> bool:
	"""Only the explicit ``--json`` flag, not merely piped stdout.

	``digest --format md`` 与 ``stats --format html`` 刻意把正文直出到管道，
	方便用户 ``> 文件``；那是人工路径，不能改成信封。但 ``--json`` 是硬契约
	（MCP 子进程总是带 ``--json`` 调用 CLI），这两条直出路径必须让位给信封，
	否则 stdout 不是 JSON，MCP 侧只能报 CLI_ERROR。
	"""
	return bool(ctx.obj.get("json_output", False)) if ctx and ctx.obj else False


def is_json_mode(ctx: Any) -> bool:
	"""Check if --json flag is set or stdout is piped (non-TTY)."""
	return json_forced(ctx) or not sys.stdout.isatty()


def render_operator_actions(hints: dict[str, Any] | None) -> None:
	"""把「人该做什么」渲染到 stderr。

	只渲染 hints.operator_actions（面向真人操作者的自然语言指引）。
	hints.next_actions 是面向 Agent 的命令通道，TTY 下刻意不渲染——避免给
	每条命令的输出加噪音。无 operator_actions 时零输出，因此对既有命令的
	TTY 行为没有影响。
	"""
	if not hints:
		return
	actions = hints.get("operator_actions")
	if not actions or not isinstance(actions, (list, tuple)):
		return
	console.print()
	for index, action in enumerate(actions):
		prefix = "↓ 你需要：" if index == 0 else "　　　　　"
		console.print(f"[yellow]{prefix}[/yellow]{escape_markup(redact_sensitive_text(str(action)))}", soft_wrap=True)


def handle_output(
	ctx: Any,
	command: str,
	data: Any,
	*,
	render: Callable[[Any], None] | None = None,
	pagination: dict[str, Any] | None = None,
	hints: dict[str, Any] | None = None,
) -> None:
	"""Smart output: TTY -> rich render, pipe -> JSON envelope."""
	if is_json_mode(ctx):
		emit_success(command, data, pagination=pagination, hints=hints)
	elif render:
		# 与信封共用脱敏规则。TTY 输出也会出现在日志和截图里（Issue #2765）。
		render(redact_sensitive(data))
		render_operator_actions(hints)
	else:
		# Fallback: no render function, emit JSON even in TTY
		emit_success(command, data, pagination=pagination, hints=hints)


def handle_error_output(
	ctx: Any,
	command: str,
	*,
	code: str,
	message: str,
	recoverable: bool | None = None,
	recovery_action: str | None = None,
	details: dict[str, Any] | None = None,
	hints: dict[str, Any] | None = None,
	data: Any = None,
	json_mode: bool | None = None,
) -> None:
	"""Smart error output: TTY -> rich error, pipe -> JSON error envelope.

	``data`` 只用于「部分完成」的失败：批量删除等操作需要把已完成项和失败项
	带回给调用方，而不是只留 ``data=null``（Issue #1497）。默认仍是 None。
	``json_mode`` 供入口层显式传入：启动期错误没有完整 ctx，但可能带了
	``--json``，不能仅凭 stdout 是否 TTY 决定通道（Issue #2786）。
	"""
	from boss_agent_cli.output import emit_error

	# 调用方未显式声明时按登记表补齐，TTY 与 JSON 两条路径看到同一份恢复契约。
	registered_recoverable, registered_recovery_action = error_contract_for_code(code)
	if recoverable is None:
		recoverable = registered_recoverable
	if recovery_action is None:
		if code == "ACTION_UNCONFIRMED":
			# 持久化失败记录的错误码回填也要按当前角色生成核对命令，不能把
			# 招聘者专属命令交给求职者（Issue #2921）。
			recovery_action, role_hints = unconfirmed_write_contract_for_ctx(ctx)
			hints = {**role_hints, **(hints or {})}
		else:
			recovery_action = registered_recovery_action
	# 调用方可能已经通过 platform_error_contract 之类把登记表原文改写成本次目录；
	# 两种形态都算「仍是登记表契约」，认证分支不能因此丢掉扫码提示。
	registered_variants = {registered_recovery_action}
	if registered_recovery_action:
		registered_variants.add(registry_recovery_for_ctx(ctx, registered_recovery_action))
	if (
		code in {"AUTH_REQUIRED", "AUTH_EXPIRED", "TOKEN_REFRESH_FAILED"}
		and recoverable
		and recovery_action in registered_variants
	):
		# 登记表是无运行上下文的示例；只补齐通用登录动作，不覆盖写操作的人工核对要求。
		recovery_action = login_action_for_ctx(ctx)
		hints = {**auth_required_hints(ctx), **(hints or {})}
	elif code == "ACTION_UNCONFIRMED" and recovery_action in registered_variants:
		recovery_action, role_hints = unconfirmed_write_contract_for_ctx(ctx)
		hints = {**role_hints, **(hints or {})}
	elif recovery_action and recovery_action == registered_recovery_action:
		# 仍是登记表原文：doctor / resume list / crawl status 读的是本次运行的数据
		# 目录，透传出参会让真人与 Agent 去默认目录找（Issue #1092）。
		recovery_action = registry_recovery_for_ctx(ctx, recovery_action)
	# error.message 里也可能嵌着采集层 / 招聘者层构造的裸命令（「由用户执行
	# boss clean --risk-lock」「请先 boss hr chatmsg <id> 核实」）。这些文案在
	# api/ 与 crawler/ 层没有 ctx，只能在出口按本次运行改写（Issue #1097）。
	message = registry_recovery_for_ctx(ctx, message)

	use_json = is_json_mode(ctx) if json_mode is None else json_mode
	if use_json:
		emit_error(
			command,
			code=code,
			message=message,
			recoverable=recoverable,
			recovery_action=recovery_action,
			details=details,
			hints=hints,
			data=data,
		)
	else:
		# JSON 路径由 output.py 脱敏；TTY 分支同样不能把异常文本里的凭据
		# 明文打到终端（Issue #880）。
		safe_message = redact_sensitive_text(message)
		safe_recovery = redact_sensitive_text(recovery_action) if recovery_action else recovery_action
		# 命令文本里可能带数据目录：方括号内容会被 Rich 当作样式标签吞掉，
		# 未配对的闭合标签还会直接抛 MarkupError，恢复指引必须按字面显示。
		# 错误码同样按字面渲染（Issue #2246）：未注册的错误码可能是任意字符串，
		# 且它会落在我们自己加的方括号里——markup 转义在标签内部不生效（`[X\\[bold]`
		# 仍会被解析），所以整行改用 Text 组装，不经过 markup 解析。
		error_line = Text("error", style="red")
		error_line.append(f" [{strip_control(str(code))}] {strip_control(safe_message)}")
		console.print(error_line)
		if safe_recovery:
			console.print(f"  [dim]recovery: {escape_markup(safe_recovery)}[/dim]", soft_wrap=True)
		render_operator_actions(hints)
		# TTY 与 JSON 出口同一退出码契约：--exit-codes sysexits 在终端下同样生效
		# （此前硬编码 1，认证 / 限流 / 风控失败全部退 1，Issue #2051）。
		raise SystemExit(exit_code_for_error(code))


def handle_not_supported(ctx: Any, command: str, exc: Exception, *, fallback_message: str) -> None:
	"""命令因平台不支持而抛 NotImplementedError 时的统一错误信封。"""
	_, recovery_action = error_contract_for_code("NOT_SUPPORTED")
	handle_error_output(
		ctx,
		command,
		code="NOT_SUPPORTED",
		message=str(exc) or fallback_message,
		recoverable=True,
		recovery_action=recovery_action,
	)


#: 不产生平台写入的命令（``handle_platform_error_output`` 的 ``command`` 取值）。
#: 只用于区分未识别响应的恢复语义：在集合内按 ``NETWORK_ERROR`` 允许重试；
#: 不在集合内（包括未来新增命令）一律按写处理，绝不告诉 Agent 重放可能已生效的写操作。
_READ_ONLY_COMMANDS = frozenset(
	{
		"chat",
		"chat-summary",
		"chatmsg",
		"detail",
		"digest",
		"export",
		"favorites-list",
		"follow-up",
		"history",
		"interviews",
		"me",
		"pipeline",
		"recommend",
		"recruiter-applications",
		"recruiter-candidates",
		"recruiter-chat",
		"recruiter-chatmsg",
		"recruiter-jobs-detail",
		"recruiter-jobs-list",
		"recruiter-last-messages",
		"recruiter-recommendations",
		"recruiter-resume",
		"recruiter-whoami",
		"search",
		"show",
		"status",
		"watch",
	}
)


def read_only_platform_code(command: str, code: str) -> str:
	"""只读命令的未识别响应按 ``NETWORK_ERROR`` 归一（Issue #1519）。

	写命令与未知命令保持 ``UNKNOWN``：响应无法证明动作没有生效，重试就是重放。
	"""
	if code == "UNKNOWN" and command in _READ_ONLY_COMMANDS:
		return "NETWORK_ERROR"
	return code


def handle_platform_error_output(
	ctx: Any,
	command: str,
	platform: Any,
	response: Any,
	*,
	fallback_message: str,
	extra_hints: dict[str, Any] | None = None,
	read_only_operation: bool | None = None,
) -> None:
	"""Emit a schema-backed error envelope for an unsuccessful platform response.

	``extra_hints`` 承载命令自身的诊断字段（如 status 的 ``auth_health``）；
	风控码的 ``next_actions`` 由 ``RISK_ERROR_CONTRACTS`` 覆盖，调用方不能改写。
	``UNKNOWN`` 沿用登记表的「先核对、勿自动重试」契约：写命令曾把它改写成
	可重试的 ``NETWORK_ERROR``，批量与 wizard 因此重放可能已生效的写操作
	（Issue #990）。
	``read_only_operation`` 供同一命令同时包含读写分支时覆盖按命令名的默认值：
	`hr resume --exchange` 是写操作，不能被只读命令名误判成可重试（Issue #1605）。
	"""
	code, message = platform.parse_error(response)
	read_only = command in _READ_ONLY_COMMANDS if read_only_operation is None else read_only_operation
	# 显式 read_only_operation=True 同样参与 UNKNOWN 归一：只按命令名查表会漏掉
	# 「同一命令的只读分支」这类声明，产出 code=UNKNOWN + recoverable=true 的
	# 自相矛盾信封（Issue #1900）。
	if read_only and code == "UNKNOWN":
		code = "NETWORK_ERROR"
	recoverable, recovery_action, risk_hints = platform_error_contract(code, ctx=ctx, read_only=read_only)
	hints = {**(extra_hints or {}), **(risk_hints or {})} or None
	details = None
	if isinstance(response, dict):
		error = response.get("error")
		if isinstance(error, dict):
			raw_details = error.get("details")
			if isinstance(raw_details, dict):
				details = raw_details
		cli_details = response.get("__cli_error_details__")
		if details is None and isinstance(cli_details, dict) and cli_details:
			details = cli_details
		if code in RISK_ERROR_CONTRACTS and response.get("code") in (
			31,
			"31",
			32,
			"32",
			35,
			"35",
			36,
			"36",
			37,
			"37",
			38,
			"38",
		):
			details = {**(details or {}), "platform_code": response.get("__cli_platform_code__", int(response["code"]))}
	handle_error_output(
		ctx,
		command,
		code=code,
		message=platform_error_message(code, message, fallback_message),
		recoverable=recoverable,
		recovery_action=recovery_action,
		details=details,
		hints=hints,
	)


# ── Table builders ──────────────────────────────────────────────────


def _detail_scalar(value: Any) -> str:
	"""职位详情展示用安全标量：容器 / 布尔 / 非有限浮点 / 超大整数按缺失处理。

	与向导职位详情同口径（Issue #2812 / #2815）：``str()`` 遇超大整数抛
	``ValueError``，容器与布尔会把 Python repr / ``True`` 打进面板。
	"""
	if value is None or isinstance(value, bool):
		return ""
	if isinstance(value, str):
		return value.strip()
	if isinstance(value, (int, float)):
		if isinstance(value, float) and not math.isfinite(value):
			return ""
		return safe_text(value)
	return ""


def job_field_text(data: dict[str, Any], *keys: str) -> str:
	"""职位字段的终端文本：缺失 / 空串 / 纯空白都显示 ``-``（Issue #2767）。

	``data.get(key, "-")`` 的默认值只挡键缺失，而上游 ``build_job_from_card``
	用 ``field_text`` 把缺失归一成空串，于是详情里出现 ``exp:  | edu:  |``、
	``boss:  ()``，表格里空单元格与其它列的 ``-`` 不一致。容器字段按
	``cell_text`` 的口径转文本，不把 Python repr 打给用户。

	布尔与非有限浮点按缺失处理：``cell_text`` 会把布尔原样给出 ``True``；
	非有限浮点已由 ``cell_text`` 统一显示 ``-``（Issue #2835），这里仍显式
	跳过这两类值，保持职位字段的入口契约（Issue #2815）。
	"""
	for key in keys:
		if key not in data:
			continue
		value = data[key]
		if isinstance(value, str):
			if value.strip():
				return escape_markup(value)
			continue
		if isinstance(value, bool) or (isinstance(value, float) and not math.isfinite(value)):
			continue
		text = cell_text(value)
		if text and text != "-":
			return escape_markup(text)
	return "-"


def render_job_table(
	items: list[dict[str, Any]],
	title: str,
	*,
	page: int = 1,
	hint_next: str = "",
	show_command: str | None = None,
) -> None:
	"""渲染职位表。

	``show_command`` 只在调用方已把这批职位写进编号索引时传入：`boss show <#>`
	读的是索引而不是屏幕上的表格，没写索引的列表给出编号提示会打开另一条职位。
	"""
	# 条目形状漂移（缓存损坏 / 平台字段变化）不能把 TTY 渲染兜成裸 traceback，
	# 非对象条目直接跳过，与导出服务入口同口径（Issue #2162 / #2247）。
	items = [job for job in items if isinstance(job, dict)]
	if not items:
		console.print("[yellow]no results[/yellow]")
		return

	# title 可能含外部输入（搜索词等）：整个标题按字面渲染，后缀是代码自己的文本。
	table = Table(title=f"{escape_markup(title)} ({len(items)} results)", show_lines=True)
	table.add_column("#", style="dim", width=3)
	# 列宽上限保持表格紧凑；超宽内容按字符折行而不是截成「…」（Issue #2761）。
	table.add_column("title", style="bold cyan", max_width=30, overflow="fold")
	table.add_column("company", style="green", max_width=20, overflow="fold")
	table.add_column("salary", style="yellow", max_width=12, overflow="fold")
	table.add_column("type", max_width=8, overflow="fold")
	table.add_column("exp", max_width=10, overflow="fold")
	table.add_column("edu", max_width=8, overflow="fold")
	table.add_column("city", style="blue", max_width=12, overflow="fold")

	for i, job in enumerate(items, 1):
		table.add_row(
			str(i),
			job_field_text(job, "title", "jobName"),
			job_field_text(job, "company", "brandName"),
			job_field_text(job, "salary", "salaryDesc"),
			job_field_text(job, "employment_type"),
			job_field_text(job, "experience", "jobExperience"),
			job_field_text(job, "education", "jobDegree"),
			job_field_text(job, "city", "cityName"),
		)

	console.print(table)
	if show_command:
		console.print(f"  [dim]use: {escape_markup(show_command)} to view details[/dim]", soft_wrap=True)
	if hint_next:
		console.print(f"  [dim]{escape_markup(hint_next)}[/dim]", soft_wrap=True)


def unix_time_label(value: Any) -> str:
	"""把 Unix 秒渲染成本地时间；未知或损坏的时间显示为 "-"。

	``index_cache._saved_at`` 用 ``0`` 表示「保存时间未知」（旧版本写入的缓存、
	被改坏的 ``saved_at``）。把这个哨兵交给 ``fromtimestamp`` 会显示成
	``1970-01-01``，真人据此误判编号快照的新旧（Issue #2756）；非正数与 NaN
	一律按未知处理。
	"""
	if isinstance(value, bool) or not isinstance(value, (int, float)):
		return "-"
	if not value > 0:
		return "-"
	try:
		return datetime.datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")
	except (OverflowError, OSError, ValueError):
		return "-"


def iso_time_label(value: Any) -> str:
	"""简历的 ISO 时间按本地时区显示到分钟。"""
	if not isinstance(value, str):
		return "-"
	try:
		stamp = datetime.datetime.fromisoformat(value)
		if stamp.tzinfo is not None:
			stamp = stamp.astimezone()
		return stamp.strftime("%Y-%m-%d %H:%M")
	except (OverflowError, OSError, ValueError):
		return "-"


def render_job_detail(data: dict[str, Any], *, greet_command: str | None = None) -> None:
	"""Render job detail as a rich panel."""
	title = job_field_text(data, "title")
	salary = job_field_text(data, "salary")
	exp = job_field_text(data, "experience")
	edu = job_field_text(data, "education")
	city = job_field_text(data, "city")
	company = job_field_text(data, "company")
	boss = job_field_text(data, "boss_name")
	boss_title = job_field_text(data, "boss_title")
	desc = _detail_scalar(data.get("description"))
	if boss == "-":
		boss_line = boss_title if boss_title != "-" else "-"
	elif boss_title == "-":
		boss_line = boss
	else:
		boss_line = f"{boss} ({boss_title})"

	skills = [text for item in label_items(data.get("skills")) if (text := _detail_scalar(item))]
	skill_str = ", ".join(escape_markup(skill) for skill in skills) if skills else "-"

	text = (
		f"[bold cyan]{title}[/bold cyan]  [yellow]{salary}[/yellow]\n"
		f"exp: {exp} | edu: {edu} | city: {city}\n"
		f"skills: {skill_str}\n"
		f"\n"
		f"[bold green]company:[/bold green] {company}\n"
		f"\n"
		f"[bold magenta]boss:[/bold magenta] {boss_line}\n"
	)

	if desc:
		omitted = ""
		if len(desc) > 500:
			# 只留一个 "..." 会让真人以为那就是全文；明确写出省略了多少字以及
			# 去哪里看完整内容（Issue #2768）。
			omitted = f"\n[dim]…… 已省略 {len(desc) - 500} 字，完整描述用 --json 查看[/dim]"
			desc = desc[:500]
		# 先截断再转义：截断可能落在转义序列中间。
		text += f"\n[bold]description:[/bold]\n{escape_markup(desc)}{omitted}"

	panel = Panel(text, title="job detail", border_style="cyan")
	console.print(panel)

	# 编号绑定的是索引快照，不是职位本身：把来源和保存时间放在详情下方，
	# 重搜之后一眼就能看出这条编号是不是已经指向另一次搜索。
	snapshot = data.get("index_snapshot")
	if isinstance(snapshot, dict):
		source = escape_markup(_detail_scalar(snapshot.get("source")) or "-")
		count_label = escape_markup(_detail_scalar(snapshot.get("count")) or "-")
		page_text = _detail_scalar(snapshot.get("page"))
		page_label = f"第 {escape_markup(page_text)} 页 · " if page_text else ""
		index = escape_markup(_detail_scalar(data.get("index")) or "-")
		console.print(
			f"  [dim]#{index} · {page_label}{source} · 共 {count_label} 条 · 保存于 {unix_time_label(snapshot.get('saved_at'))}[/dim]"
		)

	sid = _detail_scalar(data.get("security_id"))
	jid = _detail_scalar(data.get("job_id"))
	if sid and jid:
		greet_command = greet_command or "boss greet <security_id> <job_id>"
		console.print(f"  [dim]next: {escape_markup(greet_command)}[/dim]", soft_wrap=True)


def _status_lock_text(value: Any, default: str) -> str:
	"""风控锁字段的安全文本：容器 / 布尔 / 非有限浮点 / 超大整数回退默认值。

	``lock.get(key) or default`` 只挡假值：对象会显示 repr，超大整数在
	``escape_markup`` 内 ``str()`` 时抛 ``ValueError``（Issue #2817）。
	"""
	if isinstance(value, str):
		text = value.strip()
	elif isinstance(value, (int, float)) and not isinstance(value, bool):
		if isinstance(value, float) and not math.isfinite(value):
			return default
		text = safe_text(value)
	else:
		return default
	return escape_markup(text) if text else default


def render_status(
	data: dict[str, Any],
	*,
	login_action: str = "boss login",
	clean_action: str = "boss clean --risk-lock",
) -> None:
	"""Render login status.

	``clean_action`` 由命令层传入带 ``--data-dir`` / ``--platform`` 的真实解锁
	命令：风险锁写在 ``<data_dir>/.cdp_risk_lock``，裸命令会去默认目录空转
	（Issue #1087）。

	只有 ``user_name`` 是非空白字符串时才拼 ``as <name>``：没做在线验证的
	``boss status`` 按契约写 ``user_name: null``，``data.get("user_name", "unknown")``
	的默认值在键存在时不生效，终端每次都显示 ``logged in as None``（Issue #2754）。
	"""
	if data.get("logged_in"):
		user_name = data.get("user_name")
		if isinstance(user_name, str) and user_name.strip():
			console.print(f"[green]logged in[/green] as [bold]{escape_markup(user_name.strip())}[/bold]")
		else:
			console.print("[green]logged in[/green]")
	else:
		# 登录命令可能带自定义 --data-dir：方括号目录名不能被当作样式吞掉。
		console.print(f"[yellow]not logged in[/yellow] - run: {escape_markup(login_action)}", soft_wrap=True)
	lock = data.get("cdp_risk_lock")
	if isinstance(lock, dict) and lock.get("locked"):
		since = _status_lock_text(lock.get("locked_at"), "unknown")
		reason = _status_lock_text(lock.get("reason"), "ENVIRONMENT_RISK")
		console.print(
			f"[yellow]BOSS 本地风控锁定中[/yellow]（{since} 起，{reason}）：确认官方目标业务页已恢复后，"
			f"由用户执行 {escape_markup(clean_action)} 再显式恢复任务；令牌变化不会自动解锁"
		)


def render_simple_list(
	items: list[dict[str, Any]],
	title: str,
	columns: list[tuple[str, str, str]],
) -> None:
	"""Render a generic list as a rich table.

	columns: list of (header, dict_key, style)
	"""
	# 与 render_job_table 同口径：非对象条目跳过，计数按过滤后条目（Issue #2247）。
	items = [item for item in items if isinstance(item, dict)]
	if not items:
		console.print(f"[yellow]no {escape_markup(title)}[/yellow]")
		return

	# title 可能含外部输入（联系人名、配置路径等）：整个标题按字面渲染。
	table = Table(title=f"{escape_markup(title)} ({len(items)})", show_lines=True)
	table.add_column("#", style="dim", width=3)
	for header, _, style in columns:
		# 不设列宽上限：Rich 按终端宽度分配，超宽内容按字符折行（Issue #2761）。
		table.add_column(escape_markup(header), style=style, overflow="fold")

	for i, item in enumerate(items, 1):
		row = [str(i)]
		for _, key, _ in columns:
			# 键存在而值为 None / 列表 / 对象时不能把 Python repr 打给用户（Issue #2755）。
			row.append(escape_markup(cell_text(item.get(key))))
		table.add_row(*row)

	console.print(table)


# ── Additional renderers ────────────────────────────────────────────


_PANEL_INLINE_LIST_WIDTH = 60
_PANEL_MAX_DEPTH = 4
_PANEL_FIELD_MAX_LINES = 12
_PANEL_NESTED_TEXT_LIMIT = 200


def _panel_scalar_text(value: Any) -> str:
	"""通用键值面板的安全标量：非有限浮点按缺失处理。

	``safe_text`` 会把 ``nan`` / ``inf`` 原样打给真人，既不是 JSON 口径也不是
	可用值（Issue #2817）。布尔仍按既有 TTY 契约显示 ``True`` / ``False``，
	大量既有命令的输出与测试依赖该写法。
	"""
	if value is None:
		return "-"
	if isinstance(value, float) and not math.isfinite(value):
		return "-"
	return safe_text(value) or "-"


def _panel_inline_text(value: Any, depth: int) -> str | None:
	"""返回能在字段名后直接显示的值；需要逐项展开时返回 ``None``。"""
	if not isinstance(value, (list, tuple, dict)):
		return _panel_scalar_text(value)
	if not value:
		return "-"
	if depth >= _PANEL_MAX_DEPTH:
		return cell_text(value)
	if isinstance(value, dict) or len(value) > _PANEL_INLINE_LIST_WIDTH:
		return None
	if all(isinstance(item, (str, int, float)) or item is None for item in value):
		parts = [_panel_scalar_text(item) for item in value]
		if all("," not in part and "\n" not in part for part in parts):
			text = ", ".join(parts)
			if cell_len(text) <= _PANEL_INLINE_LIST_WIDTH:
				return text
	return None


def _panel_field_lines(key: Any, value: Any, *, bounded: bool = False) -> list[str]:
	"""保留标量，逐项展开容器。分段面板对嵌套字段保留有界预览（#2762）。"""
	label = f"[bold]{escape_markup(safe_text(key))}:[/bold]"
	inline = _panel_inline_text(value, 0)
	if inline is not None:
		return [f"{label} {escape_markup(inline)}"]
	clipped = False

	def block(container: Any, depth: int) -> Iterator[str]:
		nonlocal clipped
		pad = "  " * depth
		entries = container.items() if isinstance(container, dict) else ((None, item) for item in container)
		for child_key, child in entries:
			if isinstance(container, dict):
				prefix = f"{pad}[bold]{escape_markup(safe_text(child_key))}:[/bold]"
			else:
				prefix = f"{pad}·"
			text = _panel_inline_text(child, depth)
			if text is None:
				yield prefix
				yield from block(child, depth + 1)
				continue
			if bounded and len(text) > _PANEL_NESTED_TEXT_LIMIT:
				text = f"{text[:_PANEL_NESTED_TEXT_LIMIT]}…"
				clipped = True
			yield f"{prefix} {escape_markup(text)}"

	body = block(value, 1)
	if not bounded:
		return [label, *body]
	preview = list(islice(body, _PANEL_FIELD_MAX_LINES + 1))
	if len(preview) > _PANEL_FIELD_MAX_LINES:
		preview.pop()
		clipped = True
	if clipped:
		preview.append("  [dim]… 完整内容用 --json 查看[/dim]")
	return [label, *preview]


def render_message_panel(data: dict[str, Any], *, title: str = "result") -> None:
	"""Render a simple key-value result as a panel."""
	lines: list[str] = []
	for k, v in data.items():
		lines.extend(_panel_field_lines(k, v))
	# 标题按外部值转义：公共 helper 的调用方可能传平台 / 记录里的原始文本（Issue #2828）。
	panel = Panel("\n".join(lines), title=escape_markup(title), border_style="green")
	console.print(panel)


def _print_render_title(title: Any) -> None:
	"""面板标题统一走安全文本与 markup 转义，外部值不能把 TTY 打崩。"""
	text = escape_markup(safe_text(title)) or "info"
	console.print(f"[bold cyan]{text}[/bold cyan]")


def render_batch_operation_summary(data: dict[str, Any], *, title: str = "batch result") -> None:
	"""Render batch operation summary (greeted/failed counts + items)."""
	_print_render_title(title)
	greeted = data.get("greeted", [])
	failed = data.get("failed", [])
	dry_run = data.get("dry_run", False)

	if dry_run:
		candidates = data.get("candidates", [])
		console.print(f"[yellow]dry run[/yellow] — {len(candidates)} candidates")
		if candidates:
			render_job_table(candidates, "dry run candidates")
		return

	console.print(f"[green]success: {len(greeted)}[/green]  [red]failed: {len(failed)}[/red]")
	if greeted:
		table = Table(title="greeted", show_lines=True)
		table.add_column("title", style="cyan", max_width=25, overflow="fold")
		table.add_column("company", style="green", max_width=20, overflow="fold")
		for item in greeted:
			table.add_row(escape_markup(item.get("title", "-")), escape_markup(item.get("company", "-")))
		console.print(table)
	if data.get("stopped_reason"):
		console.print(f"  [yellow]stopped: {escape_markup(data['stopped_reason'])}[/yellow]")


def render_sectioned_record(data: dict[str, Any], *, title: str = "info") -> None:
	"""Render multi-section record (e.g., me command) as panels."""
	# 分段名只说明记录结构，标题才是命令标识；外部 title 必须转义后再交给 Rich。
	_print_render_title(title)
	for section, content in data.items():
		if isinstance(content, dict):
			lines: list[str] = []
			for k, v in content.items():
				# 嵌套的列表 / 对象有界展开，避免一条记录刷屏；0 / False 不再被当成缺失。
				lines.extend(_panel_field_lines(k, v, bounded=True))
			# 分段名来自 data 的键（招聘者侧直接是平台响应），按外部值转义（Issue #2828）。
			panel = Panel(
				"\n".join(lines) if lines else "[dim]empty[/dim]",
				title=escape_markup(section),
				border_style="cyan",
			)
			console.print(panel)
		else:
			# 顶层段落（resume show 的 modules 等）本身就是这条记录的正文，完整展开。
			for line in _panel_field_lines(section, content):
				console.print(line)


def render_string_grid(items: list[str], title: str, *, columns: int = 4) -> None:
	"""Render a list of strings as a multi-column grid."""
	if not items:
		console.print(f"[yellow]no {escape_markup(title)}[/yellow]")
		return

	# title 可能含外部输入：整个标题按字面渲染。
	table = Table(title=f"{escape_markup(title)} ({len(items)})", show_header=False)
	for _ in range(columns):
		table.add_column(max_width=20, overflow="fold")

	for i in range(0, len(items), columns):
		row = items[i : i + columns]
		while len(row) < columns:
			row.append("")
		table.add_row(*[escape_markup(item) for item in row])

	console.print(table)


def render_export_summary(data: dict[str, Any]) -> None:
	"""Render export result summary."""
	path = data.get("path", "")
	count = data.get("count", 0)
	fmt = data.get("format", "")
	if path:
		console.print(
			f"[green]exported[/green] {count} jobs to [bold]{escape_markup(path)}[/bold] ({escape_markup(fmt)})"
		)
	else:
		console.print(f"[green]exported[/green] {count} jobs ({fmt})")
	# 截断原因由 export 的 hints.operator_actions 统一渲染；这里再按 truncated
	# 写死页数上限会误报 active_target_reached，并重复显示警告（Issue #2949）。


def render_next_steps(actions: "Sequence[str] | None") -> None:
	"""把「下一步可以敲什么」渲染给真人。

	与 ``render_operator_actions`` 的分工：后者渲染 ``hints.operator_actions``
	（需要离开终端才能完成的动作，如扫码），由 ``handle_output`` 自动调用；
	本函数由各命令自己的 renderer 主动调用，把该命令的后继命令提示出来。

	这样既满足「空数据态要给可执行下一步」，又不改变 hints 双通道语义
	——``next_actions`` 仍然只是 Agent 通道，不会被自动渲染到 TTY。
	"""
	if not actions:
		return
	console.print("\n  [bold]下一步：[/bold]")
	for action in actions:
		# 命令里带数据目录等用户可控文本：不转义时 `[bold]` 会被 Rich 吞掉，
		# 未配对的 `[/bold]` 直接抛 MarkupError，整个人工提示都打不出来。
		# soft_wrap：Rich 的硬折行会把命令断成两行，照抄后无法执行（Issue #2770）。
		console.print(f"    [dim]{escape_markup(action)}[/dim]", soft_wrap=True)


def render_action_result(
	data: dict[str, Any],
	*,
	title: str,
	next_steps: "Sequence[str] | None" = None,
) -> None:
	"""动作类命令（add / remove / init / export ...）的统一确认渲染。

	替代直接回显 JSON：结果字段走面板，后继命令走 next_steps。
	"""
	render_message_panel(data, title=title)
	render_next_steps(next_steps)


def render_list_result(
	items: list[dict[str, Any]],
	title: str,
	columns: list[tuple[str, str, str]],
	*,
	next_steps: "Sequence[str] | None" = None,
) -> None:
	"""列表类命令的统一渲染：表格 + 下一步；空列表也会给出下一步（AC4）。"""
	render_simple_list(items, title, columns)
	render_next_steps(next_steps)


def render_saved_search_list(
	items: list[dict[str, Any]],
	title: str,
	*,
	next_steps: Sequence[str],
) -> None:
	"""在终端列表中展示已保存的筛选条件与本地创建时间。"""
	rows = [
		{
			"name": item["name"],
			"params": "\n".join(
				f"{key}: {cell_text(value)}" for key, value in configured_search_params(item["params"]).items()
			) or "-",
			"created_at": unix_time_label(item.get("created_at")),
		}
		for item in items
	]
	render_list_result(
		rows,
		title,
		[("名称", "name", "bold cyan"), ("条件", "params", "green"), ("创建时间", "created_at", "dim")],
		next_steps=next_steps,
	)


def render_record_result(
	data: dict[str, Any],
	*,
	title: str,
	next_steps: "Sequence[str] | None" = None,
) -> None:
	"""多段记录类命令（resume show / export）的统一渲染：分段面板 + 下一步。"""
	render_sectioned_record(data, title=title)
	render_next_steps(next_steps)


def _ai_value_lines(value: Any) -> list[str]:
	"""把单个 AI 返回值摊成可打印的行；全部转义，不截断。"""
	if isinstance(value, list):
		lines = []
		for item in value:
			if isinstance(item, dict):
				# 直接 f-string 插值会让超大整数在 ``str()`` 上限处抛 ValueError，
				# 容器还会漏出 Python repr；键值分别走安全文本与单元格归一（Issue #2826）。
				inline = "  ".join(f"{safe_text(key)}={cell_text(child)}" for key, child in item.items())
				lines.append(f"  · {escape_markup(inline)}")
			else:
				lines.append(f"  · {escape_markup(cell_text(item))}")
		return lines
	if isinstance(value, dict):
		return [f"  [bold]{escape_markup(k)}:[/bold] {escape_markup(cell_text(v))}" for k, v in value.items()]
	return [f"  {escape_markup(cell_text(value))}"]


def render_ai_result(
	data: dict[str, Any],
	*,
	title: str,
	next_steps: "Sequence[str] | None" = None,
) -> None:
	"""AI 命令结果的渲染：键名作小标题，长文本整段展示。

	AI 返回结构由 prompt 决定、跨命令不一致，因此不假设任何具体键名，
	只按值类型决定呈现方式。所有值经 ``escape`` 处理——AI 输出里的方括号
	不能被当成 Rich 标记解析。
	"""
	body: list[str] = []
	for key, value in data.items():
		body.append(f"[bold cyan]{escape_markup(key)}[/bold cyan]")
		body.extend(_ai_value_lines(value))
		body.append("")
	# 标题按外部值转义，与正文同一口径（Issue #2828）。
	console.print(
		Panel("\n".join(body).rstrip() or "[dim]empty[/dim]", title=escape_markup(title), border_style="magenta")
	)
	render_next_steps(next_steps)


class SearchProgress:
	"""``search`` 长任务的 TTY 进度（鸭子类型兼容 ``output.Logger``）。

	只在 TTY 下注入；管道 / ``--json`` 路径仍传原 Logger，行为与改造前完全一致。

	为什么走 logger 注入而不是降低全局日志级别：``output.Logger`` 用裸
	``print(file=sys.stderr)``，与 Rich 抢同一个流；而详情补抓跑在
	``ThreadPoolExecutor`` 里，无锁裸 print 会交错。这里统一走 Rich Console
	（内部有锁）并自带计数锁。

	消息分类依赖 ``search_filters`` 的进度文案标记，该耦合由
	``test_search_pipeline_progress_markers_contract`` 锁定。
	"""

	_PAGE_PREFIX = "正在搜索第"
	_MATCH_MARK = "✅"
	_EXCLUDE_MARKS = ("❌", "预筛排除")

	def __init__(self, title: str, *, max_pages: int = 1) -> None:
		self._title = title
		self._max_pages = max(1, max_pages)
		self._lock = threading.Lock()
		self.pages = 0
		self.matched = 0
		self.excluded = 0

	# ── Logger 接口 ────────────────────────────────────────

	def info(self, message: str) -> None:
		text = message.strip()
		if text.startswith(self._PAGE_PREFIX):
			with self._lock:
				self.pages += 1
			return
		if text.startswith(self._MATCH_MARK):
			with self._lock:
				self.matched += 1
			console.print(f"  [green]✓[/green] {escape_markup(text.lstrip(self._MATCH_MARK).strip())}")
			return
		if any(text.startswith(mark) for mark in self._EXCLUDE_MARKS):
			# 逐条排除只计数不打印——刷屏会把真正有用的匹配结果冲掉
			with self._lock:
				self.excluded += 1
			return

	def debug(self, message: str) -> None:
		"""调试细节不进 TTY 进度。"""

	def warning(self, message: str) -> None:
		console.print(f"  [yellow]{escape_markup(message.strip())}[/yellow]")

	def error(self, message: str) -> None:
		console.print(f"  [red]{escape_markup(message.strip())}[/red]")

	# ── 状态行 ─────────────────────────────────────────────

	def status_text(self) -> str:
		with self._lock:
			parts = [f"第 {self.pages}/{self._max_pages} 页" if self.pages else "准备中"]
			parts.append(f"匹配 {self.matched}")
			if self.excluded:
				parts.append(f"排除 {self.excluded}")
		return " · ".join(parts)

	def start(self) -> None:
		console.print(f"[bold]{escape_markup(self._title)}[/bold]")

	def finish(self) -> None:
		console.print(f"  [dim]{escape_markup(self.status_text())}[/dim]")

	def waiting(self, seconds: float) -> None:
		"""节流等待前的一次性提示（取整秒，不做倒计时刷屏）。"""
		console.print(f"  [dim]节流等待 {seconds:.0f}s…[/dim]")


# ── Auth error decorator ─────────────────────────────────────────────


#: 风控类错误码的信封契约：recovery_action 与 hints 的唯一真源。
#: ``handle_auth_errors`` 的单次命令路径与 batch-greet 等执行器的停批信封共用同一份，
#: 保证同一个码在两条路径上给 Agent 的恢复指引逐字一致（Issue #419）。
RISK_ERROR_CONTRACTS: dict[str, dict[str, Any]] = {
	"ACCOUNT_RISK": {
		"recovery_action": ERROR_CODES["ACCOUNT_RISK"]["recovery_action"],
		# next_actions 只放可执行命令；真人说明放 operator_actions，TTY 只渲染后者
		# （Issue #1522）。
		"next_actions": ["boss doctor"],
		"operator_actions": [
			"不要通过 CDP、patchright 或其他通道重试该操作",
			"锁未解除时只使用本地辅助和离线状态命令；官方目标业务页恢复后由用户显式清除本地锁",
		],
	},
	"ENVIRONMENT_RISK": {
		"recovery_action": ERROR_CODES["ENVIRONMENT_RISK"]["recovery_action"],
		"next_actions": ["boss doctor"],
		"operator_actions": [
			"不要刷新 Token、重新登录或自动重试该请求",
			"由用户在原会话确认官方目标业务页恢复后，显式清除本地锁并恢复任务",
		],
	},
	"ENVIRONMENT_RISK_LOCKED": {
		"recovery_action": ERROR_CODES["ENVIRONMENT_RISK_LOCKED"]["recovery_action"],
		"next_actions": ["boss doctor"],
		"operator_actions": [
			"不要刷新 Token、重新登录或换通道重试；本次请求未发出",
			"由用户在原会话确认官方目标业务页恢复后，执行 boss clean --risk-lock 并显式恢复任务；令牌变化不放行",
		],
	},
}


#: 契约文案里的注释性命令（默认示例）。有运行上下文时由调用方替换成带
#: ``--data-dir`` 的真实命令：风险锁写在 ``<data_dir>/.cdp_risk_lock``。
_RISK_CONTRACT_CLEAN = "boss clean --risk-lock"
_RISK_CONTRACT_DOCTOR = "boss doctor"


def _risk_contract_commands(
	text: str,
	clean_command: str | None,
	doctor_command: str | None,
) -> str:
	"""把契约文本里的默认示例替换成调用方上下文中的命令（Issue #978）。"""
	replacements = {
		literal: command
		for literal, command in (
			(_RISK_CONTRACT_CLEAN, clean_command),
			(_RISK_CONTRACT_DOCTOR, doctor_command),
		)
		if command
	}
	return rewrite_command_examples(text, replacements, lambda command: command)


def risk_error_contract(
	code: str,
	*,
	ctx: Any = None,
	clean_command: str | None = None,
	doctor_command: str | None = None,
) -> tuple[str, dict[str, list[str]]]:
	"""Return ``(recovery_action, hints)`` for a platform risk error code.

	有 ``ctx`` 的调用方得到带 ``--data-dir`` 与平台的 ``clean --risk-lock`` /
	``doctor``；无上下文的纯函数调用与静态文档保持登记表的通用示例。
	"""
	spec = RISK_ERROR_CONTRACTS.get(code)
	if spec is None:
		_, recovery = error_contract_for_code(code, fallback_recovery_action="停止自动化访问")
		return recovery or "停止自动化访问", {}
	if ctx is not None:
		if clean_command is None:
			clean_command = boss_command_for_ctx(ctx, "clean --risk-lock", include_data_dir=True)
		if doctor_command is None:
			doctor_command = boss_command_for_ctx(ctx, "doctor", include_data_dir=True)
	return (
		_risk_contract_commands(str(spec["recovery_action"]), clean_command, doctor_command),
		{
			"next_actions": [
				_risk_contract_commands(str(item), clean_command, doctor_command) for item in spec["next_actions"]
			],
			"operator_actions": [
				_risk_contract_commands(str(item), clean_command, doctor_command) for item in spec["operator_actions"]
			],
		},
	)


def risk_lock_persistence_actions(details: dict[str, Any] | None) -> list[str]:
	"""风险锁未能写盘时，补充「只在当前进程生效」的操作者提示（Issue #1270）。

	主锁与 group 目录的锁都写失败时，同进程后续请求仍被内存里的锁拦住，但进程
	退出后新进程读不到任何锁，会以为没有风控阻断。信封必须在 `persisted: false`
	时把这件事讲清楚，而不是只给普通的风控恢复指引。
	"""
	if not isinstance(details, dict):
		return []
	lock = details.get("risk_lock")
	if not isinstance(lock, dict) or lock.get("persisted") is not False:
		return []
	return [
		"本地风险锁未能写入磁盘：当前进程退出后，新的 boss 进程不会再被这把锁阻止；"
		"先修复数据目录的写权限与磁盘空间，在确认平台已恢复前不要启动新的 boss 进程",
	]


#: 浏览器来源策略表里的命令默认示例。有运行上下文时由调用方替换成带
#: ``--data-dir`` 与平台的真实命令：``doctor`` 诊断的是本地登录态、缓存与风险锁。
_BROWSER_POLICY_COMMANDS: dict[str, tuple[str, ...]] = {
	"boss status --live": ("status --live",),
	"boss doctor": ("doctor",),
	"boss login --cdp": ("login --cdp",),
	"boss clean --risk-lock": ("clean --risk-lock",),
}


def browser_policy_text(text: str, command_for: Callable[[str], str]) -> str:
	"""把来源策略文案里的默认示例换成 ``command_for`` 构造的命令（Issue #980）。

	``doctor`` / ``status --live`` / ``login --cdp`` / ``clean --risk-lock`` 读的是
	``<data_dir>`` 里的登录态与风险锁；自定义 ``--data-dir`` 下照抄裸命令只会
	诊断或清理默认目录。
	"""
	return rewrite_command_examples(
		text, {literal: command for literal, (command,) in _BROWSER_POLICY_COMMANDS.items()}, command_for
	)


def browser_policy_text_for_ctx(ctx: Any, text: str) -> str:
	"""带数据目录与平台的来源策略文案；无上下文的纯函数调用保持原文。"""
	if not text:
		return text
	return browser_policy_text(text, lambda command: boss_command_for_ctx(ctx, command, include_data_dir=True))


def httpx_only_fallback_hint(ctx: Any) -> list[str]:
	"""``--browser-source httpx-only`` 失败时给真人的回退指引。

	该来源的线上稳定性尚未证实（同一天内既有成功也有 code 37），所以失败时的
	第一建议不是重试，而是回到默认的浏览器通道——那是本项目一直在用的路径。
	"""
	browser_source = (getattr(ctx, "obj", None) or {}).get("browser_source")
	if browser_source != "httpx-only":
		return []
	return [
		"httpx-only 的线上稳定性未证实；失败后先回退到默认 --browser-source auto 再试",
		"若已命中风控，按提示保留 checkpoint，不要靠切换通道绕过",
	]


def _with_httpx_only_hint(ctx: Any, hints: dict[str, list[str]] | None) -> dict[str, list[str]] | None:
	extra = httpx_only_fallback_hint(ctx)
	if not extra:
		return hints
	merged = dict(hints or {})
	merged["operator_actions"] = [*merged.get("operator_actions", []), *extra]
	return merged


# 解析平台数据或 CLI 自身的缺陷：同样的输入必然得到同样的结果。归为 NETWORK_ERROR
# 并建议「重试」会让 Agent 反复重试，重试的若是平台请求，还会白白消耗请求预算。
# PlatformResponseInvalid：平台响应不是 JSON 对象，或深嵌套让 resp.json() 抛
# RecursionError；同一响应重试必然复现，按 INTERNAL_ERROR（不可恢复）上报（Issue #1368）。
# PlatformStatusRejected：读请求收到平台的确定性 4xx（401/403/429 已提前收口），
# 同一请求重试必然复现（Issue #2567）。
# MarkupError：Rich 标记解析失败（漏转义的外部字符串进了渲染器）。同一输入必然
# 复现，且这是 CLI 自身的转义缺陷，不能兜成可重试的网络错误（Issue #1399）。
INTERNAL_ERROR_TYPES: tuple[type[Exception], ...] = (
	TypeError,
	AttributeError,
	KeyError,
	IndexError,
	OverflowError,
	PlatformResponseInvalid,
	PlatformStatusRejected,
	MarkupError,
)


def _handle_cache_busy_error(ctx: Any, command_name: str, message: str) -> None:
	"""锁冲突的统一出口：等待写锁释放后重试，而不是删除缓存或重试平台请求。"""
	recoverable, recovery_action = error_contract_for_code("CACHE_BUSY")
	handle_error_output(
		ctx,
		command_name,
		code="CACHE_BUSY",
		message=message,
		recoverable=recoverable,
		recovery_action=recovery_action,
	)


def handle_auth_errors(command_name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
	"""装饰器：统一处理 AuthRequired / TokenRefreshFailed / Exception 三层捕获。

	用法:
		@handle_auth_errors("search")
		def _search_impl(ctx, ...):
			...  # 只写业务逻辑，不需要 try/except
	"""
	from functools import wraps

	def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
		@wraps(func)
		def wrapper(ctx: Any, *args: Any, **kwargs: Any) -> Any:
			from boss_agent_cli.api.browser_client import (
				BrowserNavigationError,
				ChatTabCdpUnreachable,
				RecruiterChatTabRequired,
			)
			from boss_agent_cli.ai.config import AIConfigValueError
			from boss_agent_cli.api._base_client import BrowserChannelRequired
			from boss_agent_cli.api.browser_source import BrowserSourceUnavailable, BrowserSourceUnsupported
			from boss_agent_cli.api.client import PlatformRateLimitError, PlatformRiskError
			from boss_agent_cli.api.proxy import ProxyConfigurationError
			from boss_agent_cli.api.request_budget import RequestBudgetExceeded
			from boss_agent_cli.auth.manager import AuthRequired, TokenRefreshFailed

			try:
				return func(ctx, *args, **kwargs)
			except BrowserSourceUnsupported as e:
				# recoverable / recovery_action 以真源契约为准，不覆盖；
				# 针对浏览器来源的具体建议走 hints.operator_actions。
				recoverable, not_supported_recovery = error_contract_for_code("NOT_SUPPORTED")
				handle_error_output(
					ctx,
					command_name,
					code="NOT_SUPPORTED",
					message=str(e),
					recoverable=recoverable,
					recovery_action=not_supported_recovery,
					hints={
						"operator_actions": [
							"该平台没有浏览器通道，改用默认 --browser-source auto 或切换到 zhipin",
						]
					},
				)
			except BrowserSourceUnavailable as e:
				# 策略表是来源耗尽时的唯一文案真源，但表本身没有运行上下文：
				# 其中内嵌的 doctor / status --live / login --cdp / clean --risk-lock
				# 读的是 <data_dir> 里的登录态与风险锁，这里补上本次目录与平台（Issue #980）。
				hints: dict[str, list[str]] = {}
				if e.policy.operator_actions:
					hints["operator_actions"] = [
						browser_policy_text_for_ctx(ctx, action) for action in e.policy.operator_actions
					]
				if e.policy.next_actions:
					hints["next_actions"] = [
						browser_policy_text_for_ctx(ctx, action) for action in e.policy.next_actions
					]
				handle_error_output(
					ctx,
					command_name,
					code=e.code,
					message=str(e),
					recoverable=True,
					recovery_action=browser_policy_text_for_ctx(ctx, e.policy.recovery_action),
					hints=hints or None,
				)
			except BrowserChannelRequired as e:
				recoverable, not_supported_recovery = error_contract_for_code("NOT_SUPPORTED")
				handle_error_output(
					ctx,
					command_name,
					code="NOT_SUPPORTED",
					message=str(e),
					recoverable=recoverable,
					recovery_action=not_supported_recovery,
					hints={
						"operator_actions": [
							"CDP 模式下所有平台请求只走浏览器通道；该操作暂无浏览器实现，请在 BOSS 直聘官方页面手动完成",
						]
					},
				)
			except ChatTabCdpUnreachable as e:
				# CDP HTTP 端点在脚本发送前就不可达：动作未派发，可安全重试。
				# 不能落成 ACTION_UNCONFIRMED（Issue #1455）。
				cdp_recoverable, cdp_recovery = error_contract_for_code("CDP_UNAVAILABLE")
				handle_error_output(
					ctx,
					command_name,
					code="CDP_UNAVAILABLE",
					message=f"CDP 调试端点不可达，聊天页动作尚未发送: {e}",
					recoverable=cdp_recoverable,
					recovery_action=cdp_recovery,
					hints={
						"operator_actions": [
							"确认 Chrome 仍以调试端口运行，且 --cdp-url 指向的地址可访问后重试",
							"本次操作没有发送，重新执行是安全的",
						]
					},
				)
			except RecruiterChatTabRequired as e:
				handle_error_output(
					ctx,
					command_name,
					code="RECRUITER_CHAT_TAB_REQUIRED",
					message=str(e),
					recoverable=True,
					recovery_action="回到 BOSS 直聘官方招聘者页面手动处理",
					hints={
						"operator_actions": [
							"在 BOSS 直聘官方页面确认候选人和沟通上下文",
							"保持 CLI 默认低风险模式，不通过自动化链路发送消息或请求简历",
						]
					},
				)
			except AuthRequired:
				login_action = login_action_for_ctx(ctx)
				handle_error_output(
					ctx,
					command_name,
					code="AUTH_REQUIRED",
					message=f"未登录，请先执行 {login_action}",
					recoverable=True,
					recovery_action=login_action,
					hints=auth_required_hints(ctx),
				)
			except TokenRefreshFailed:
				login_action = login_action_for_ctx(ctx)
				handle_error_output(
					ctx,
					command_name,
					code="TOKEN_REFRESH_FAILED",
					message="Token 刷新失败，请重新登录",
					recoverable=True,
					recovery_action=login_action,
					hints=auth_required_hints(ctx),
				)
			except PlatformRateLimitError as e:
				# 限流不是风控、也不是认证失效：等待限流窗口即可（Issue #2237）。
				# 泛化兜底会把它报成可重试的 NETWORK_ERROR，丢掉登记表的恢复指引
				# （Issue #2548）。
				rate_recoverable, rate_recovery = error_contract_for_code("RATE_LIMITED")
				handle_error_output(
					ctx,
					command_name,
					code="RATE_LIMITED",
					message=str(e) or "平台限流，请稍后重试",
					recoverable=rate_recoverable,
					recovery_action=rate_recovery,
				)
			except PlatformRiskError as e:
				recovery_action, contract_hints = risk_error_contract(e.code, ctx=ctx)
				# _with_httpx_only_hint 可能返回 None：显式声明可空，避免覆盖既有变量类型。
				risk_hints: dict[str, list[str]] | None = _with_httpx_only_hint(ctx, contract_hints)
				persistence_actions = risk_lock_persistence_actions(e.details)
				if persistence_actions:
					risk_hints = {
						**(risk_hints or {}),
						"operator_actions": [
							*((risk_hints or {}).get("operator_actions") or []),
							*persistence_actions,
						],
					}
				handle_error_output(
					ctx,
					command_name,
					code=e.code,
					# 风控信封不透传平台响应文本（可能带临时凭据），只给登记表文案。
					message=risk_error_message(e.code),
					recoverable=False,
					recovery_action=recovery_action,
					details=e.details or None,
					hints=risk_hints,
				)
			except AIConfigValueError as e:
				# AI 本地配置项非法是确定性错误（Issue #2205）：读侧校验（如
				# 手工编辑的 base_url）抛出的异常按既有的持久化配置契约报
				# CONFIG_INVALID（Issue #1636），不能掉进下面泛化的可重试分支。
				# 写入侧的参数校验仍由命令自己报 INVALID_PARAM。
				invalid_recoverable, invalid_recovery = error_contract_for_code("CONFIG_INVALID")
				handle_error_output(
					ctx,
					command_name,
					code="CONFIG_INVALID",
					message=str(e),
					recoverable=invalid_recoverable,
					recovery_action=f"{e.recovery_hint} 修正后重试" if e.recovery_hint else invalid_recovery,
				)
			except ProxyConfigurationError as e:
				# 代理环境变量非法是确定性配置错误：重试只会复现，且不能静默回退
				# 直连（出口泄露）。按 INVALID_PARAM 收口，不动网络重试语义
				# （Issue #1423）。文案由 typed error 提供，不含代理凭据。
				invalid_recoverable, invalid_recovery = error_contract_for_code(
					"INVALID_PARAM",
					fallback_recovery_action="修正或清除 HTTPS_PROXY/ALL_PROXY 等代理环境变量后重试",
				)
				handle_error_output(
					ctx,
					command_name,
					code="INVALID_PARAM",
					message=str(e),
					recoverable=invalid_recoverable,
					recovery_action=invalid_recovery,
					hints={
						"operator_actions": [
							"确认 HTTPS_PROXY / ALL_PROXY 形如 scheme://host:port，端口为 1-65535 的整数",
							"不使用代理时请清除对应环境变量；CLI 不会回退为直连",
						]
					},
				)
			except RequestBudgetExceeded as e:
				budget_recoverable, budget_recovery = error_contract_for_code(e.code)
				if e.code == "CIRCUIT_BREAKER_OPEN":
					# 熔断不是预算冷却：恢复指引指向 agent stats / --resume（Issue #1520）。
					stats_command = boss_command_for_ctx(ctx, "agent stats", include_data_dir=True, role="recruiter")
					resume_command = boss_command_for_ctx(
						ctx, "agent run --resume", include_data_dir=True, role="recruiter"
					)
					budget_hints: dict[str, Any] | None = {
						"next_actions": [stats_command],
						"operator_actions": [
							f"先到官方页面核实未确认动作；确认后执行 {resume_command} 显式恢复",
						],
					}
				else:
					budget_hints = _with_httpx_only_hint(
						ctx, {"operator_actions": ["等待冷却并检查并发任务；确认后显式恢复，不自动清理风险锁"]}
					)
				handle_error_output(
					ctx,
					command_name,
					code=e.code,
					message=str(e),
					recoverable=budget_recoverable,
					recovery_action=budget_recovery,
					details=e.details,
					hints=budget_hints,
				)
			except BrowserNavigationError as e:
				nav_recoverable, nav_recovery = error_contract_for_code(e.code)
				handle_error_output(
					ctx,
					command_name,
					code=e.code,
					message=str(e),
					recoverable=nav_recoverable,
					recovery_action=nav_recovery,
					details=e.details(),
					hints={
						"next_actions": [
							boss_command_for_ctx(ctx, "doctor", include_data_dir=True),
							login_action_for_ctx(ctx),
						],
						"operator_actions": [
							"浏览器通道会先打开 https://www.zhipin.com/ 再请求接口；--url 只解析筛选条件，不会改走搜索页",
							"首页在自动化下偶发加载卡住，CLI 已跳到空白页并重试。仍失败时，用带调试端口的 Chrome 打开官网并确认首页能加载，再执行 boss --cdp-url http://127.0.0.1:9222 <命令>",
							"若 boss status 显示登录态 degraded 或 stoken 很久未更新，先执行 boss login",
						],
					},
				)
			except CacheDatabaseInvalid as e:
				# 缓存库损坏（含取数阶段才暴露的坏页）是确定性本地失败：给登记表的
				# 「备份后删库」指引，不能兜成 NETWORK_ERROR +「重试」让 Agent 在
				# 坏库上无限 resume（Issue #1350）。
				handle_error_output(
					ctx,
					command_name,
					code="CACHE_DATABASE_INVALID",
					message=str(e),
					hints={
						"operator_actions": [
							f"备份后删除 {e.path}；候选池、预设、watch 历史与任务断点都随旧库一起作废",
						]
					},
				)
			except CacheRecordInvalid as e:
				# 本地记录损坏是确定性失败：重试同一命令只会再失败一次。BossCliGroup
				# 已有同样分支，但装饰器在命令函数外层先捕获，必须在这里收口，
				# 否则被装饰的命令会把本地坏数据报成可重试的 NETWORK_ERROR
				# （Issue #2166，与 #1885 / #1336 同口径）。
				record_recoverable, record_recovery_action = error_contract_for_code("CACHE_RECORD_INVALID")
				handle_error_output(
					ctx,
					command_name,
					code="CACHE_RECORD_INVALID",
					message=str(e),
					recoverable=record_recoverable,
					recovery_action=record_recovery_action,
				)
			except CacheWriteRejected as e:
				# 约束冲突 / 绑定值非法是输入问题，缓存库本身健康：绝不能给出
				# 「备份并删除缓存库」的指引，也不能建议重试同一组参数
				# （Issue #2166，与 #1886 同口径）。
				invalid_recoverable, _invalid_recovery = error_contract_for_code("INVALID_PARAM")
				handle_error_output(
					ctx,
					command_name,
					code="INVALID_PARAM",
					message=str(e),
					recoverable=invalid_recoverable,
					recovery_action=(
						"本次写入被本地缓存库拒绝（约束冲突或非法绑定值），库本身完好、无需删除；修正调用参数后重试"
					),
				)
			except NotImplementedError as e:
				# 平台适配器未实现该能力（platforms/base.py / recruiter_base.py 的
				# 默认实现）是确定性能力缺口：与 interviews 命令的显式捕获、wizard
				# runner 的 except NotImplementedError 分支同口径。此前 digest /
				# pipeline 漏掉本地捕获，异常落进通用分支成了可重试 NETWORK_ERROR，
				# Agent 会对同一失败无界重试（Issue #2745）。
				handle_not_supported(ctx, command_name, e, fallback_message="当前平台不支持该能力")
			except INTERNAL_ERROR_TYPES as e:
				status = parameter_status_code(e)
				if isinstance(e, PlatformStatusRejected) and status is not None:
					# 平台对同一组参数的确定性拒绝（400 / 406 / 409 / 410 / 422）：
					# 与 detail / show / wizard 同口径报 INVALID_PARAM，不能给
					# 「升级 CLI 或提交 issue」的 INTERNAL_ERROR 指引（Issue #2567）。
					rejected_recoverable, rejected_recovery = error_contract_for_code("INVALID_PARAM")
					handle_error_output(
						ctx,
						command_name,
						code="INVALID_PARAM",
						message=f"{command_name} 请求被平台拒绝（HTTP {status}），同一组参数重试不会改变结果",
						recoverable=rejected_recoverable,
						recovery_action=rejected_recovery,
					)
					return
				internal_recoverable, internal_recovery = error_contract_for_code("INTERNAL_ERROR")
				handle_error_output(
					ctx,
					command_name,
					code="INTERNAL_ERROR",
					message=f"{command_name} 失败: {type(e).__name__}: {e}",
					recoverable=internal_recoverable,
					recovery_action=internal_recovery,
				)
			except OSError as e:
				from boss_agent_cli.services.chat_snapshot import ChatSnapshotBusyError

				if isinstance(e, ChatSnapshotBusyError):
					# 并发导出的正常争用：等另一进程写完快照即可，不能落成
					# 不可重试的 LOCAL_IO_ERROR + 磁盘 / 权限指引（Issue #2538）。
					busy_recoverable, _busy_recovery = error_contract_for_code("CACHE_BUSY")
					handle_error_output(
						ctx,
						command_name,
						code="CACHE_BUSY",
						message=str(e),
						recoverable=busy_recoverable,
						recovery_action=(
							"稍后重试本命令；并发导出同一数据目录时会短暂争用当天快照锁，"
							"快照文件本身完好，不需要删除"
						),
					)
				elif is_browser_kernel_missing(str(e)):
					# Playwright 以 OSError 子类抛「内核缺失」时同样不能被兜成可重试。
					handle_browser_kernel_missing(ctx, command_name, e)
				elif is_local_io_error(e):
					# 路径不可写、目标被同名文件占用、磁盘满：重试同一条命令永远修不好，
					# 落成可重试的 NETWORK_ERROR 只会让 Agent 无限重试。
					handle_error_output(
						ctx,
						command_name,
						code="LOCAL_IO_ERROR",
						message=f"{command_name} 本地文件读写失败: {e}",
					)
				else:
					handle_error_output(
						ctx,
						command_name,
						code="NETWORK_ERROR",
						message=f"{command_name} 失败: {e}",
						recoverable=True,
						recovery_action="重试",
						hints=_with_httpx_only_hint(ctx, None),
					)
			except click.ClickException:
				# 命令体里抛出的参数校验（如 mark 的 --label）仍是用法错误：交给
				# BossCliGroup 出 INVALID_PARAM 信封，不能兜成「可重试的 NETWORK_ERROR」，
				# 否则 Agent 会拿同一个非法值反复重试。
				raise
			except Exception as e:
				from boss_agent_cli.crawler.controls import CrawlExportError

				# 采集结果写不出去是确定性本地失败：重试或恢复同一断点只会得到同一
				# 结果，也不能兜成 NETWORK_ERROR +「重试」让 Agent 反复打平台（#1135）。
				if isinstance(e, CrawlExportError):
					export_recoverable, export_recovery = error_contract_for_code("LOCAL_IO_ERROR")
					handle_error_output(
						ctx,
						command_name,
						code="LOCAL_IO_ERROR",
						message=str(e),
						recoverable=export_recoverable,
						recovery_action=export_recovery,
					)
					return
				# 本地缓存锁冲突必须在 NETWORK_ERROR「重试」之前收口（Issue #1118）：
				# 等锁释放即可，不是平台网络问题，也不需要重建缓存库。
				if isinstance(e, CacheBusyError) or is_cache_busy_error(e):
					message = str(e) if isinstance(e, CacheBusyError) else f"本地缓存库正被其他进程写入（{e}）"
					_handle_cache_busy_error(ctx, command_name, message)
					return
				# 本地 SQLite 文件损坏 / 被同名目录占位是确定性失败：非装饰器命令
				# 已由 BossCliGroup 报 LOCAL_IO_ERROR（Issue #1304），装饰器命令不能
				# 兜成 NETWORK_ERROR +「重试」（Issue #1336）。请求预算库只保存节流
				# 元数据，可删除重建；缓存库给出登记表里的通用本地 IO 指引。
				if isinstance(e, sqlite3.DatabaseError):
					io_recoverable, io_recovery = error_contract_for_code("LOCAL_IO_ERROR")
					db_recovery_action = (
						"备份后删除 request-budget.sqlite3 再重试；该文件只保存请求节流元数据，会自动重建"
						if "request-budget.sqlite3" in str(e)
						else io_recovery
					)
					handle_error_output(
						ctx,
						command_name,
						code="LOCAL_IO_ERROR",
						message=f"{command_name} 本地数据库文件无法打开: {e}",
						recoverable=io_recoverable,
						recovery_action=db_recovery_action,
					)
					return
				# 内核缺失是确定性失败：符号名 / 路径未变时重试永远同一结果，不能
				# 兜成 NETWORK_ERROR +「重试」让 Agent 反复打平台。
				if is_browser_kernel_missing(str(e)):
					handle_browser_kernel_missing(ctx, command_name, e)
					return
				# 请求参数在 httpx 编码阶段失败（孤立代理项等）是输入问题：
				# 请求确定未发出，不能给磁盘 / 权限指引，也不能建议无脑重试
				# （Issue #2533）。
				if isinstance(e, RequestTextEncodingError):
					encoding_recoverable, _encoding_recovery = error_contract_for_code("INVALID_PARAM")
					handle_error_output(
						ctx,
						command_name,
						code="INVALID_PARAM",
						message=f"{command_name} 请求参数含有无法按 UTF-8 编码的字符（如孤立代理项），请求未发出",
						recoverable=encoding_recoverable,
						recovery_action=(
							"检查命令中的标识 / 关键词 / 消息文本，去掉无效字符后重试；"
							"编号建议重新从 search 或 chat 的结果中取"
						),
					)
					return
				# 孤立代理字符等不可编码 / 不可解码的本地文本是确定性失败：
				# 重试只会复现，不能兜成 NETWORK_ERROR +「重试」（Issue #1294）。
				if isinstance(e, UnicodeError):
					encode_recoverable, encode_recovery = error_contract_for_code("LOCAL_IO_ERROR")
					handle_error_output(
						ctx,
						command_name,
						code="LOCAL_IO_ERROR",
						message=f"{command_name} 处理本地文本失败: {e}",
						recoverable=encode_recoverable,
						recovery_action=encode_recovery,
					)
					return
				handle_error_output(
					ctx,
					command_name,
					code="NETWORK_ERROR",
					message=f"{command_name} 失败: {e}",
					recoverable=True,
					recovery_action="重试",
					hints=_with_httpx_only_hint(ctx, None),
				)

		return wrapper

	return decorator
