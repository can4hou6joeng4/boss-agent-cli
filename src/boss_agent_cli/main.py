import re
import sys
from pathlib import Path
from collections.abc import Sequence
from typing import Any

import click

from boss_agent_cli import __version__
from boss_agent_cli.api.browser_source import POLICIES as BROWSER_SOURCES
from boss_agent_cli.cache.store import CacheDatabaseInvalid
from boss_agent_cli.commands.register import register_candidate_commands, register_recruiter_commands
from boss_agent_cli.config import DEFAULT_REQUEST_DELAY, ROLES, load_config, validate_request_config
from boss_agent_cli.api.request_budget import validate_delay
from boss_agent_cli.display import INTERNAL_ERROR_TYPES, error_contract_for_code
from boss_agent_cli.hooks import create_hook_bus
from boss_agent_cli.output import LOG_LEVELS, emit_error, is_local_io_error, Logger, set_exit_codes_mode
from boss_agent_cli.platforms import list_platforms


def _command_path(ctx: click.Context | None) -> str:
	names: list[str] = []
	current = ctx
	while current is not None and current.parent is not None:
		if current.info_name:
			names.append(current.info_name)
		current = current.parent
	return " ".join(reversed(names))


def _missing_subcommand_error(ctx: click.Context | None) -> tuple[str, str, dict[str, Any]] | None:
	"""把「只写了命令组、没给子命令」的 Click 帮助全文换成子命令清单。

	``boss resume`` / ``boss hr`` / ``boss agent pending`` 这类调用在 Click 8
	抛 ``NoArgsIsHelpError``，异常消息就是整份英文帮助（「Usage: …」「Options: …」
	几百字符）。信封的 ``error.message`` 因此被撑成一坨帮助文本，Agent 既读不到
	「缺子命令」这个原因，也拿不到可执行的下一步。这里改成一句中文说明，并把
	子命令清单放进 ``hints.next_actions``。
	"""
	# NoArgsIsHelpError 在 Click 8.2 才出现，而依赖声明是 click>=8.0：
	# 按类名判断，避免为它把最低版本抬上去。
	if ctx is None or not isinstance(ctx.command, click.Group):
		return None
	names = sorted(ctx.command.list_commands(ctx))
	if not names:
		return None
	root = ctx.find_root().info_name or "boss"
	path = f"{root} {_command_path(ctx)}".strip()
	return (
		f"{path} 缺少子命令（可选：{', '.join(names)}）",
		f"从子命令中选一个后重试，例如 {path} {names[0]} --help",
		{"next_actions": [f"{path} {name}" for name in names]},
	)


def _unknown_subcommand_error(exc: click.ClickException) -> tuple[str, str, dict[str, Any]] | None:
	"""把 ``No such command 'bogus'.`` 换成点名可选子命令的中文消息。

	与 ``_missing_subcommand_error`` 同一主题的另一半：``boss resume bogus`` /
	``boss crawl bogus`` 这类写错子命令的调用只拿到 Click 的英文原文，Agent 看
	不出是哪个命令组、有哪些可选值，``hints`` 也是 null，只能再猜一次。
	"""
	name = getattr(exc, "command_name", None)
	if name is None:
		# NoSuchCommand 在 Click 8.4 才出现，依赖声明是 click>=8.0：旧版本用普通
		# UsageError 抛同一条英文消息，只能从消息里取名字。
		matched = re.match(r"No such command '(.+)'\.$", str(getattr(exc, "message", "")))
		if matched is None:
			return None
		name = matched.group(1)
	ctx = getattr(exc, "ctx", None)
	if ctx is None or not isinstance(ctx.command, click.Group):
		return None
	names = sorted(ctx.command.list_commands(ctx))
	if not names:
		return None
	root = ctx.find_root().info_name or "boss"
	path = f"{root} {_command_path(ctx)}".strip()
	return (
		f"{path} 没有子命令 '{name}'（可选：{', '.join(names)}）",
		f"从子命令中选一个后重试，例如 {path} {names[0]} --help",
		{"next_actions": [f"{path} {name}" for name in names]},
	)


def _unknown_option_error(exc: click.ClickException) -> tuple[str, str, dict[str, Any]] | None:
	"""把 ``No such option '--limit'. Did you mean '--city'?`` 换成中文说明。

	与 ``_unknown_subcommand_error`` 同口径：Agent 最常踩的 ``boss search golang
	--limit 10`` 只拿到 Click 的英文原文，拼写建议还按编辑距离把 ``--limit`` 猜成
	``--city``，与「限制结果数」的意图毫无关系。这里只指向命令自己的帮助。
	"""
	if not isinstance(exc, click.NoSuchOption):
		return None
	ctx = getattr(exc, "ctx", None)
	if ctx is None:
		return None
	raw = exc.option_name or ""
	token = raw if raw.startswith("-") else f"--{raw}"
	root = ctx.find_root().info_name or "boss"
	path = f"{root} {_command_path(ctx)}".strip()
	return (
		f"{path} 没有选项 '{token}'",
		f"用 {path} --help 查看可用选项",
		{"next_actions": [f"{path} --help"]},
	)


_EXTRA_ARGUMENT_RE = re.compile(r"^Got unexpected extra arguments? \((.+)\)$")


def _usage_path(exc: click.ClickException) -> str | None:
	"""用法错误所在命令的完整路径（``boss resume diff``）。"""
	ctx = getattr(exc, "ctx", None)
	if ctx is None:
		return None
	root = ctx.find_root().info_name or "boss"
	return f"{root} {_command_path(ctx)}".strip()


def _parameter_display_name(exc: click.BadParameter) -> str:
	"""参数 / 选项在消息里的展示名，与 Click 自己的英文消息同源。

	位置参数是 metavar（``NAME2``），选项是 ``'--label'``；直接用 ``param.name``
	会退化成小写的内部名（``name2`` / ``label``）。
	"""
	hint = exc.param_hint
	if hint is None and exc.param is not None:
		hint = exc.param.get_error_hint(exc.ctx) if exc.ctx is not None else exc.param.human_readable_name
	if isinstance(hint, (list, tuple)):
		hint = " / ".join(hint)
	return str(hint or "").strip().strip("'\"")


def _missing_parameter_error(exc: click.ClickException) -> tuple[str, str, dict[str, Any]] | None:
	"""把 ``Missing option '--label'.`` 换成点明命令与参数的中文消息。

	``boss mark <uid>`` 漏掉 ``--label``、``boss resume diff <旧名>`` 漏掉第二个
	名字时，信封里只有 Click 的英文原文和笼统的「修正参数」，Agent 既看不出
	是哪个命令、缺的是选项还是位置参数，也没有可执行的下一步。这里按类型
	分成「缺少必填选项」与「缺少必填参数」，并指向该命令的 ``--help``。
	"""
	if not isinstance(exc, click.MissingParameter):
		return None
	path = _usage_path(exc)
	if path is None:
		return None
	hint = _parameter_display_name(exc)
	kind = "选项" if isinstance(exc.param, click.Option) else "参数"
	shown = f" '{hint}'" if hint else ""
	return (
		f"{path} 缺少必填{kind}{shown}",
		f"用 {path} --help 查看用法",
		{"next_actions": [f"{path} --help"]},
	)


def _unexpected_extra_argument_error(exc: click.ClickException) -> tuple[str, str, dict[str, Any]] | None:
	"""把 ``Got unexpected extra argument (x)`` 换成中文说明。

	``boss search golang extra`` / ``boss resume init extra`` 这类多给参数的调用
	只拿到英文原文，Agent 读到的原因（多余的参数）与真正该做的动作（去掉它）
	都对不上。Click 8.x 没有专门的异常类，按消息格式识别，未知格式保持原样。
	"""
	matched = _EXTRA_ARGUMENT_RE.match(str(getattr(exc, "message", "") or ""))
	if matched is None:
		return None
	path = _usage_path(exc)
	if path is None:
		return None
	return (
		f"{path} 收到了多余的参数 '{matched.group(1).strip()}'",
		f"去掉多余的参数后重试；用 {path} --help 查看用法",
		{"next_actions": [f"{path} --help"]},
	)


_RANGE_INVALID_RE = re.compile(r"^(.+) is not in the range (.+)\.$")
_CHOICE_INVALID_RE = re.compile(r"^(.+) is not one of (.+)\.$")
_TYPE_INVALID_RE = re.compile(r"^(.+) is not a valid (.+)\.$")
_PATH_MISSING_RE = re.compile(r"^Path '(.*)' does not exist\.$")
_PATH_INVALID_RE = re.compile(r"^Path '(.*)' is (a file|a directory|not readable|not writable|not executable)\.$")
_PATH_PROBLEMS = {
	"a file": "是文件，但这里需要目录",
	"a directory": "是目录，但这里需要文件",
	"not readable": "不可读",
	"not writable": "不可写",
	"not executable": "不可执行",
}
# click.File 打不开文件时抛出的正文形如 ``'/tmp/x': No such file or directory``，
# 后半段是 OSError.strerror（英文）。
_FILE_ERROR_RE = re.compile(r"^'(.*)': ([^:]+)$")
_FILE_ERROR_PROBLEMS = {
	"No such file or directory": "不存在",
	"Is a directory": "是目录，但这里需要文件",
	"Permission denied": "没有读取权限",
	"File name too long": "路径过长",
	"Too many open files": "打开的文件过多",
	"Too many open files in system": "系统打开的文件过多",
	"Too many levels of symbolic links": "符号链接层级过深",
}


def _invalid_value_error(exc: click.ClickException) -> tuple[str, str, dict[str, Any]] | None:
	"""把 Click 自带校验（范围 / 枚举 / 数值转换）的英文正文换成中文。

	``boss search golang --page 0`` 回 ``Invalid value for '--page': 0 is not in
	the range x>=1.``，``boss export golang --format ""`` 回 ``'' is not one of
	'html', 'csv', 'json'.``，``boss hr reply u1 hi`` 回 ``'u1' is not a valid
	integer.``：错误码是对的，但正文是英文、``recovery_action`` 只有「修正参数」、
	``hints`` 为 null。这里按消息里的取值与范围 / 可选值重排成中文，正文格式不
	认识（例如已本地化）时保持原样。
	"""
	if not isinstance(exc, click.BadParameter) or _has_cjk(exc.message):
		return None
	path = _usage_path(exc)
	if path is None:
		return None
	hint = _parameter_display_name(exc)
	label = f"{hint}" if hint else "参数"
	raw = str(exc.message)
	matched = _RANGE_INVALID_RE.match(raw)
	if matched is not None:
		message = f"{path} 的 {label} 取值无效：{matched.group(1)} 不在范围 {matched.group(2)} 内"
	elif (matched := _CHOICE_INVALID_RE.match(raw)) is not None:
		message = f"{path} 的 {label} 取值无效：{matched.group(1)} 不在可选值 {matched.group(2)} 中"
	else:
		matched = _TYPE_INVALID_RE.match(raw)
		if matched is None:
			return None
		kind = "整数" if "integer" in matched.group(2) else "数字"
		message = f"{path} 的 {label} 取值无效：{matched.group(1)} 不是{kind}"
	return (
		message,
		f"用 {path} --help 查看参数说明",
		{"next_actions": [f"{path} --help"]},
	)


def _invalid_path_error(exc: click.ClickException) -> tuple[str, str, dict[str, Any]] | None:
	"""把 ``Path '' does not exist.`` 这类 Click 路径校验换成中文。

	``boss ai local import --path ""`` 原来回 ``Invalid value for '--path':
	Path '' does not exist.``：正文英文、``hints`` 为 null，Agent 读不出是路径
	不存在还是类型不对。
	"""
	if not isinstance(exc, click.BadParameter) or _has_cjk(exc.message):
		return None
	raw = str(exc.message)
	matched = _PATH_MISSING_RE.match(raw)
	if matched is not None:
		problem = "不存在"
	else:
		matched = _PATH_INVALID_RE.match(raw)
		if matched is None:
			return None
		problem = _PATH_PROBLEMS[matched.group(2)]
	path = _usage_path(exc)
	if path is None:
		return None
	hint = _parameter_display_name(exc)
	label = hint or "参数"
	return (
		f"{path} 的 {label} 无效：路径 '{matched.group(1)}' {problem}",
		f"用 {path} --help 查看参数说明",
		{"next_actions": [f"{path} --help"]},
	)


def _invalid_file_error(exc: click.ClickException) -> tuple[str, str, dict[str, Any]] | None:
	"""``click.File`` 打不开文件时的英文正文同样要换成中文。

	``boss login --curl-file ''`` 回 ``Invalid value for '--curl-file': '':
	No such file or directory``，``--curl-file /tmp`` 回 ``'/tmp': Is a directory``：
	错误码对，但正文是 OSError 的英文 strerror，``recovery_action`` 只有「修正参数」、
	``hints`` 为 null。只翻译认识的 strerror，其余（含已本地化的消息）保持原样。
	"""
	if not isinstance(exc, click.BadParameter) or _has_cjk(exc.message):
		return None
	raw = str(exc.message)
	matched = _FILE_ERROR_RE.match(raw)
	if matched is None:
		return None
	problem = _FILE_ERROR_PROBLEMS.get(matched.group(2))
	if problem is None:
		return None
	path = _usage_path(exc)
	if path is None:
		return None
	hint = _parameter_display_name(exc)
	label = hint or "参数"
	if not matched.group(1).strip():
		message = f"{path} 的 {label} 的值不能为空"
	else:
		message = f"{path} 的 {label} 无效：路径 '{matched.group(1)}' {problem}"
	return (
		message,
		f"用 {path} --help 查看参数说明",
		{"next_actions": [f"{path} --help"]},
	)


def _usage_error_detail(exc: click.ClickException, ctx: click.Context | None) -> tuple[str, str, dict[str, Any]] | None:
	"""按异常类型给用法错误配中文说明；不认识时返回 ``None``，保留 Click 原文。"""
	if type(exc).__name__ == "NoArgsIsHelpError":
		# NoArgsIsHelpError 在 Click 8.2 才出现，依赖声明是 click>=8.0：
		# 按类名判断，避免为它把最低版本抬上去。
		missing = _missing_subcommand_error(ctx)
		if missing is not None:
			return missing
	for converter in (
		_unknown_subcommand_error,
		_missing_parameter_error,
		_unexpected_extra_argument_error,
		_invalid_value_error,
		_invalid_path_error,
		_invalid_file_error,
	):
		detail = converter(exc)
		if detail is not None:
			return detail
	return None


def _misplaced_global_option(group: click.Group, exc: click.ClickException) -> tuple[str, str] | None:
	"""Return a clearer message when a root option was parsed by a subcommand.

	``allow_interspersed_args`` stays false so Click's option ownership does not
	change. Users who write ``boss search --json`` otherwise only see
	``No such option '--json'``.
	"""
	if not isinstance(exc, click.NoSuchOption):
		return None
	ctx = exc.ctx
	if ctx is None or ctx.parent is None:
		return None
	raw = exc.option_name or ""
	token = raw if raw.startswith("-") else f"--{raw}"
	matched: click.Option | None = None
	for param in group.params:
		if isinstance(param, click.Option) and token in (*param.opts, *param.secondary_opts):
			matched = param
			break
	if matched is None:
		return None
	command = _command_path(ctx) or "<command>"
	option_usage = token if matched.is_flag else f"{token} <value>"
	example = f"boss {option_usage} {command} ..."
	message = f"选项 {token} 是全局选项，必须写在子命令之前，例如：{example}"
	return message, example


def _has_cjk(text: str) -> bool:
	"""消息里出现中日韩统一表意文字，就认为是我们自己写的中文文案。"""
	return any(0x4E00 <= ord(char) <= 0x9FFF for char in text)


def _click_error_message(exc: click.ClickException) -> str:
	"""把本仓库自己写的参数文案拼成中文信封消息。

	Click 的 ``BadParameter`` 会在正文前拼英文 ``Invalid value for ...``；正文是中文时
	（也就是我们自己 raise 的那些），前缀也换成中文。Click 自带的校验（类型转换、
	Choice、IntRange）正文仍是英文，保持原样，免得把英文正文混进中文前缀。
	"""
	if isinstance(exc, click.BadParameter) and _has_cjk(exc.message):
		hint: str | Sequence[str] | None = exc.param_hint
		if hint is None and exc.param is not None and exc.ctx is not None:
			hint = exc.param.get_error_hint(exc.ctx)
		if isinstance(hint, (list, tuple)):
			hint = " / ".join(hint)
		if hint:
			return f"参数 {hint} 的值无效: {exc.message}"
	return exc.format_message()


def _prescan_exit_codes(args: Sequence[str] | None) -> None:
	"""Usage errors are raised before the group callback runs; scan raw argv so an
	explicit `--exit-codes sysexits` still governs their exit code. Unknown values
	are normalized to envelope by set_exit_codes_mode."""
	tokens = list(args) if args is not None else sys.argv[1:]
	for index, token in enumerate(tokens):
		if token == "--exit-codes" and index + 1 < len(tokens):
			set_exit_codes_mode(tokens[index + 1])
		elif token.startswith("--exit-codes="):
			set_exit_codes_mode(token.split("=", 1)[1])


_SECURITY_ID_LIKE = re.compile(r"^-[A-Za-z0-9_-]{19,}$")

_GLOBAL_OPTIONS_WITH_VALUE = frozenset({
	"--data-dir",
	"--delay",
	"--cdp-url",
	"--browser-source",
	"--platform",
	"--role",
	"--log-level",
	"--exit-codes",
})


def _invoked_command_name(args: Sequence[str] | None, fallback: str) -> str:
	"""从原始 argv 取子命令名，给「启动期就失败」的信封用。

	这类异常在 Click 完成 context 解析前抛出，拿不到 ``ctx.info_name``；
	这里只做最小解析：跳过已知带值的全局选项与其它选项，第一个非选项
	token 就是子命令。
	"""
	tokens = list(args) if args is not None else sys.argv[1:]
	index = 0
	while index < len(tokens):
		token = tokens[index]
		if token in _GLOBAL_OPTIONS_WITH_VALUE:
			index += 2
			continue
		if token.startswith("-"):
			index += 1
			continue
		return token
	return fallback


def _top_level_command_name(ctx: click.Context | None, fallback: str) -> str:
	"""用法错误落到最外层子命令上，与成功信封的 command 口径一致。

	Click 把解析失败记在出错的那一层 context 上：`boss resume show`（缺参数）
	的 ``info_name`` 是 ``show``，而同一命令成功时的信封是 ``resume``；
	``boss hr whoami --bogus`` 同理报成 ``whoami``。这些叶子名不在能力清单里，
	Agent 按 command 归档 / 路由时会看到一个不存在的命令。这里沿 context 链
	上溯到根 context 的直接子级（真正的顶层命令）；根 context 自身的错误
	（如 ``boss --bogus``）仍报 ``boss``。
	"""
	if ctx is None:
		return fallback
	top = ctx
	while top.parent is not None and top.parent.parent is not None:
		top = top.parent
	return top.info_name or fallback


def _declare_dash_prefixed_positionals(
	group: click.Group, ctx: click.Context | None, args: list[str]
) -> list[str]:
	"""在形如 security_id 的横杠开头位置参数前插入 ``--``。

	BOSS 的 ``security_id`` 是 base64url，**可以合法地以 ``-`` 开头**（实测样本
	``-`` 开头的长 base64url 串，实测 328 字符）。Click 会把它当成未知选项，于是
	``boss detail`` 直接传该值时报告 ``No such option``，用户只能自己想到加 ``--``。

	限定两件事以免掩盖真正的拼写错误：

	- 只处理**看起来像 security_id** 的取值（``-`` 开头 + ≥20 个 base64url 字符）；
	- 只处理命令**未声明**的选项名，且命令行里还没有 ``--`` 分隔符。
	"""
	if "--" in args:
		return args

	command_index = next((i for i, token in enumerate(args) if not token.startswith("-")), None)
	if command_index is None:
		return args
	if ctx is None:
		command = group.commands.get(args[command_index])
	else:
		command = group.get_command(ctx, args[command_index])
	if command is None or not isinstance(command, click.Command):
		return args

	declared: set[str] = set()
	for param in command.params:
		if isinstance(param, click.Option):
			declared.update(param.opts)
			declared.update(param.secondary_opts)

	for index in range(command_index + 1, len(args)):
		token = args[index]
		if token in declared:
			continue
		if _SECURITY_ID_LIKE.match(token):
			return [*args[:index], "--", *args[index:]]
	return args


class BossCliGroup(click.Group):
	"""Click group that preserves the JSON envelope contract for usage errors."""

	def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
		"""先给横杠开头的 security_id 补 ``--``，再交给 Click 解析。"""
		return super().parse_args(ctx, _declare_dash_prefixed_positionals(self, ctx, list(args)))

	def main(
		self,
		args: Sequence[str] | None = None,
		prog_name: str | None = None,
		complete_var: str | None = None,
		standalone_mode: bool = True,
		**extra: Any,
	) -> Any:
		_prescan_exit_codes(args)
		try:
			return super().main(
				args=args,
				prog_name=prog_name,
				complete_var=complete_var,
				standalone_mode=False,
				**extra,
			)
		except click.ClickException as exc:
			if not standalone_mode:
				raise
			ctx = getattr(exc, "ctx", None)
			if ctx is not None:
				command = _top_level_command_name(ctx, self.name or "boss")
			else:
				# 选项缺取值时 Click 在 parser 层直接抛 UsageError，异常上不挂
				# ctx（缺位置参数 / 未知选项都会挂）：一律退回 argv，否则
				# `boss search --city` 会被报成 command: "boss"。
				command = _invoked_command_name(args, self.name or "boss")
			message = _click_error_message(exc)
			recovery_action = "修正参数"
			hints = None
			detail = _usage_error_detail(exc, ctx)
			if detail is not None:
				message, recovery_action, hints = detail
			misplaced = _misplaced_global_option(self, exc)
			if misplaced is not None:
				message, recovery_action = misplaced
				hints = {
					"next_actions": [recovery_action.replace(" ...", " --help")],
					"operator_actions": [
						"全局选项（--json、--data-dir、--platform、--cdp-url 等）写在子命令前面",
					],
				}
			else:
				unknown_option = _unknown_option_error(exc)
				if unknown_option is not None:
					message, recovery_action, hints = unknown_option
			emit_error(
				command,
				code="INVALID_PARAM",
				message=message,
				recoverable=False,
				recovery_action=recovery_action,
				hints=hints,
			)
			return None
		except CacheDatabaseInvalid as exc:
			# 缓存在每条命令启动时就会打开：库文件被写坏或被别的文件占位时，
			# 异常发生在命令自己的 try 之外，只能在这里统一给信封。
			if not standalone_mode:
				raise
			emit_error(
				_invoked_command_name(args, self.name or "boss"),
				code="CACHE_DATABASE_INVALID",
				message=str(exc),
				recoverable=True,
				recovery_action="缓存库只存本地数据：先备份 cache/boss_agent.db（--data-dir 指定时在对应目录下），再删除该文件后重试，命令会自动重建一个空库",
				hints={
					"operator_actions": [
						f"备份后删除 {exc.path}；候选池、预设、watch 历史与任务断点都随旧库一起作废",
					],
				},
			)
			return None
		except INTERNAL_ERROR_TYPES as exc:
			# 与 handle_auth_errors 的兜底同口径：确定性异常（类型错误、索引越界、
			# 整数溢出）不是网络问题。stats / cities / config 这类没有统一装饰器的
			# 本地命令以前会把 traceback 直接甩给 Agent，连信封都没有。
			if not standalone_mode:
				raise
			command = _invoked_command_name(args, self.name or "boss")
			internal_recoverable, internal_recovery_action = error_contract_for_code("INTERNAL_ERROR")
			emit_error(
				command,
				code="INTERNAL_ERROR",
				message=f"{command} 失败: {type(exc).__name__}: {exc}",
				recoverable=internal_recoverable,
				recovery_action=internal_recovery_action,
			)
			return None
		except OSError as exc:
			# 网兜：导出、保存配置这类直接写文件的路径没有统一装饰器，
			# 写失败时必须给信封，而不是把 traceback 甩给 Agent。
			if not standalone_mode:
				raise
			command = _invoked_command_name(args, self.name or "boss")
			code = "LOCAL_IO_ERROR" if is_local_io_error(exc) else "NETWORK_ERROR"
			emit_error(
				command,
				code=code,
				message=f"{command} 本地文件读写失败: {exc}"
				if code == "LOCAL_IO_ERROR"
				else f"{command} 失败: {exc}",
			)
			return None


@click.group(
	name="boss",
	cls=BossCliGroup,
	invoke_without_command=True,
	no_args_is_help=False,
	context_settings={"allow_interspersed_args": False},
)
@click.version_option(version=__version__, prog_name="boss")
@click.option("--data-dir", default="~/.boss-agent", help="数据存储目录")
@click.option("--delay", default=None, help="请求间隔范围（秒），如 30-60")
@click.option(
	"--cdp-url", default=None, help="Chrome CDP 调试地址（如 http://localhost:9222），启用则优先用用户 Chrome"
)
@click.option(
	"--browser-source",
	default=None,
	type=click.Choice(list(BROWSER_SOURCES)),
	help="浏览器通道来源：auto（默认，允许降级）/ existing-browser（复用现有浏览器）/ stored-cookie（fail-closed，只连指定 CDP，不降级）/ httpx-only（不打开任何浏览器，只读走 httpx）",
)
@click.option("--platform", "platform_name", default=None, help="指定招聘平台适配器（默认 zhipin，即 BOSS 直聘）")
@click.option(
	"--role",
	default=None,
	type=click.Choice(ROLES),
	help="角色模式：candidate（求职者，默认）/ recruiter（招聘者）",
)
@click.option("--log-level", default=None, type=click.Choice(LOG_LEVELS), help="运行日志级别（全局选项，日志只写 stderr）；省略时用默认级别")
@click.option(
	"--json/--no-json",
	"json_output",
	default=False,
	help="强制 JSON 输出（全局选项，必须写在子命令之前，例如 boss --json search …）",
)
@click.option(
	"--exit-codes",
	default="envelope",
	type=click.Choice(["envelope", "sysexits"]),
	help="退出码风格：envelope（默认，0=成功/1=失败）或 sysexits（按错误类别返回语义化退出码）",
)
@click.pass_context
def cli(
	ctx: click.Context,
	data_dir: str,
	delay: str | None,
	cdp_url: str | None,
	browser_source: str | None,
	platform_name: str | None,
	role: str | None,
	log_level: str | None,
	json_output: bool,
	exit_codes: str,
) -> None:
	ctx.ensure_object(dict)
	resolved_dir = Path(data_dir).expanduser()
	resolved_dir.mkdir(parents=True, exist_ok=True)
	ctx.obj["data_dir"] = resolved_dir
	ctx.obj["json_output"] = json_output
	ctx.obj["exit_codes"] = exit_codes
	set_exit_codes_mode(exit_codes)

	try:
		cfg = load_config(resolved_dir / "config.json")
	except OSError as exc:
		raise click.BadParameter(
			f"无法读取 {resolved_dir / 'config.json'}: {exc.strerror or exc}；"
			"修正文件权限或删除该文件后重试",
			param_hint="config.json",
		) from exc
	except (ValueError, TypeError) as exc:
		raise click.BadParameter(str(exc), param_hint="config.json") from exc

	if delay:
		try:
			low, high = delay.split("-", 1)
			ctx.obj["delay"] = (float(low), float(high))
			validate_delay(ctx.obj["delay"])
		except ValueError as exc:
			raise click.BadParameter(
				"必须是形如 30-60 的数值区间，且满足 0 <= 下限 <= 上限",
				param_hint="--delay",
			) from exc
	else:
		# Config inspection/reset must remain available to repair an invalid limit.
		ctx.obj["delay"] = (
			tuple(cfg["request_delay"]) if isinstance(cfg["request_delay"], (list, tuple)) else DEFAULT_REQUEST_DELAY
		)
	if ctx.invoked_subcommand != "config":
		try:
			validate_request_config({**cfg, "request_delay": ctx.obj["delay"]})
		except ValueError as exc:
			raise click.BadParameter(str(exc), param_hint="config.json") from exc

	level = log_level or cfg["log_level"]
	ctx.obj["log_level"] = level
	ctx.obj["logger"] = Logger(level)
	resolved_cdp_url = cdp_url or cfg.get("cdp_url")
	# 与 browser_source 同理：config.json 被手改成数字后，doctor 等命令会在 shlex.quote
	# 上抛 TypeError traceback，启动期就要按参数错误挡住。
	if resolved_cdp_url is not None and not isinstance(resolved_cdp_url, str):
		raise click.BadParameter(
			f"必须是调试地址字符串或 null，收到 {resolved_cdp_url!r}",
			param_hint="--cdp-url",
		)
	ctx.obj["cdp_url"] = resolved_cdp_url

	# 归一化（大小写/首尾空白）后再校验，与下游 resolve_policy 的 .strip().lower() 一致，
	# 避免 config.json 里 "Auto" / " stored-cookie " 这类值被 CLI 拒绝、策略层却接受的不一致。
	# config.json 被手改成数字 / 数组时不能直接 .strip()：那会在每条命令启动期抛
	# AttributeError traceback，连 `config reset browser_source` 都跑不了。
	raw_browser_source = browser_source or cfg.get("browser_source") or "auto"
	resolved_browser_source = (
		raw_browser_source.strip().lower() if isinstance(raw_browser_source, str) else raw_browser_source
	)
	if not isinstance(resolved_browser_source, str) or resolved_browser_source not in BROWSER_SOURCES:
		raise click.BadParameter(
			f"不支持的浏览器通道 {resolved_browser_source!r}，可选: {', '.join(BROWSER_SOURCES)}",
			param_hint="--browser-source",
		)
	ctx.obj["browser_source"] = resolved_browser_source

	resolved_platform = platform_name or cfg.get("platform") or "zhipin"
	available = list_platforms()
	if resolved_platform not in available:
		raise click.BadParameter(
			f"不支持的平台 {resolved_platform!r}，可选: {', '.join(available)}",
			param_hint="--platform",
		)
	ctx.obj["platform"] = resolved_platform

	resolved_role = role or cfg.get("role") or "candidate"
	# config.json 的 role 写成数字或拼错时，下游会把非字符串塞进诊断命令的
	# shlex.quote（doctor）或静默按求职者处理；启动期就按参数错误挡住。
	if resolved_role not in ROLES:
		raise click.BadParameter(
			f"不支持的角色 {resolved_role!r}，可选: {', '.join(ROLES)}",
			param_hint="--role",
		)
	ctx.obj["role"] = resolved_role

	ctx.obj["config"] = cfg
	ctx.obj["hooks"] = create_hook_bus()

	if ctx.invoked_subcommand is None:
		from boss_agent_cli.commands.wizard import run_wizard_command

		run_wizard_command(ctx)


register_candidate_commands(cli)
register_recruiter_commands(cli)
