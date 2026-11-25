import re
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import click

from boss_agent_cli import __version__, display
from boss_agent_cli.api._base_client import PlatformStatusRejected, RequestTextEncodingError, parameter_status_code
from boss_agent_cli.api.browser_source import POLICIES as BROWSER_SOURCES
from boss_agent_cli.cache.store import (
	CacheBusyError,
	CacheDatabaseInvalid,
	CacheRecordInvalid,
	CacheWriteRejected,
	is_cache_busy_error,
)
from boss_agent_cli.commands.register import register_candidate_commands, register_recruiter_commands
from boss_agent_cli.config import (
	CDP_URL_REQUIREMENT,
	DEFAULT_REQUEST_DELAY,
	DEFAULTS,
	ROLES,
	expand_budget_directory,
	is_valid_cdp_url,
	load_config,
	validate_request_config,
)
from boss_agent_cli.api.request_budget import validate_delay
from boss_agent_cli.display import (
	INTERNAL_ERROR_TYPES,
	error_contract_for_code,
	handle_error_output,
	shell_argument,
)
from boss_agent_cli.hooks import create_hook_bus
from boss_agent_cli.output import LOG_LEVELS, is_local_io_error, Logger, set_exit_codes_mode
from boss_agent_cli.platforms import list_platforms
from boss_agent_cli.services.private_files import DIRECTORY_MODE, tighten_private_directory


def _command_path(ctx: click.Context | None) -> str:
	names: list[str] = []
	current = ctx
	while current is not None and current.parent is not None:
		if current.info_name:
			names.append(current.info_name)
		current = current.parent
	return " ".join(reversed(names))


def _resolve_data_dir(data_dir: str) -> Path:
	"""解析并创建 ``--data-dir``，拒绝启动期无法正确处理的值。

	``Path("")`` 等于当前目录：`--data-dir ""`（例如脚本里变量为空）会把登录态、
	缓存和配置静默写进 cwd，看起来像「未登录」而不是参数写错；``~某用户`` 在
	系统里查不到用户时 ``expanduser`` 直接抛 ``RuntimeError``，traceback 冲出
	stdout，连信封都没有。两者都在建目录之前按参数错误拒绝。目录位置被一个
	已存在的文件占住时，``mkdir`` 的 ``FileExistsError`` 原来会报成
	``LOCAL_IO_ERROR``「status 本地文件读写失败: [Errno 17] File exists」——把
	``--data-dir`` 的参数错误说成命令的本地 IO 失败，也没有可执行的下一步。

	目录下是聊天记录、候选池、简历与登录态，因此新建目录按 0700 创建；已存在
	的目录在启动时尽力收紧（共享目录与只读目录保持原样，见
	``services.private_files.tighten_private_directory``）。
	"""
	raw = data_dir.strip()
	if not raw:
		raise click.BadParameter(
			"必须是目录路径，不能为空（省略该选项时使用 ~/.boss-agent）",
			param_hint="--data-dir",
		)
	if raw != data_dir:
		raise click.BadParameter(
			f"路径首尾不能有空白，收到 {data_dir!r}",
			param_hint="--data-dir",
		)
	try:
		resolved = Path(raw).expanduser()
	except RuntimeError as exc:
		raise click.BadParameter(
			f"无法展开家目录写法，收到 {data_dir!r}（{exc}）；请改用绝对路径或 ~/ 开头的当前用户路径",
			param_hint="--data-dir",
		) from exc
	try:
		resolved.mkdir(parents=True, exist_ok=True, mode=DIRECTORY_MODE)
	except FileExistsError as exc:
		raise click.BadParameter(
			f"必须是目录路径，{str(resolved)!r} 已存在且不是目录",
			param_hint="--data-dir",
		) from exc
	except NotADirectoryError as exc:
		raise click.BadParameter(
			f"必须是目录路径，{str(resolved)!r} 的上级路径不是目录",
			param_hint="--data-dir",
		) from exc
	# 目录下是聊天记录、候选池、简历与登录态：默认 umask 会建出 0755，同机其他
	# 用户可读。历史安装的宽松权限在启动时尽力收紧；共享目录（如 /tmp）不动。
	tighten_private_directory(resolved)
	return resolved


class _StartupConfigBadParameter(click.BadParameter):
	"""启动期配置文件错误：恢复指引要直接点名需要修正的文件。

	通用的 ``BadParameter`` 恢复指引停在「用 boss --help 查看参数说明」，
	而这里要修的是数据目录外的另一份 config.json，必须把路径交给 Agent。
	"""

	def __init__(self, message: str, *, recovery_action: str, param_hint: str) -> None:
		super().__init__(message, param_hint=param_hint)
		self.recovery_action = recovery_action


def _validate_group_budget_config(data_dir: Path, cfg: dict[str, Any]) -> None:
	"""启动期加载并校验共享请求预算目录的 config.json。

	成员目录的 ``request_budget_dir`` 指向组目录时，``RequestBudget.for_data_dir``
	会在客户端构造阶段读取组目录配置。组配置是坏 JSON 或限流值非法时，那里的
	``ValueError`` 会被命令层通用兜底报成 ``NETWORK_ERROR`` + ``recoverable=true``，
	而重试必然复现（Issue #1367）。这里在启动期按参数错误拒绝，命令不会构造客户端。
	"""
	raw = cfg.get("request_budget_dir")
	if raw is None:
		return
	if not isinstance(raw, str):
		# validate_request_config 已把非法类型按参数错误拒绝；这里只防御直接调用。
		return
	try:
		group_dir = expand_budget_directory(raw).resolve()
	# RuntimeError：路径含符号链接环时 resolve() 在 3.11 / 3.12 抛错（3.13 起返回
	# 原路径）。这是确定性的本地配置错误，与 ValueError 同口径拒绝；否则它会冲出
	# BossCliGroup 的兜底链，连信封都没有（Issue #2159）。
	except (ValueError, RuntimeError) as exc:
		raise _StartupConfigBadParameter(
			f"成员配置的 request_budget_dir 无效：{exc}",
			recovery_action=f"修正 {data_dir / 'config.json'} 的 request_budget_dir 后重试",
			param_hint="config.json 的 request_budget_dir",
		) from exc
	if group_dir == data_dir.resolve():
		# 指向自身时没有共享组目录，仍是启动期已校验过的主目录 config，不重复校验。
		return
	group_config_path = group_dir / "config.json"
	recovery = (
		f"修正或删除 {group_config_path} 后重试；也可用 boss --data-dir {shell_argument(str(data_dir))} "
		"config reset request_budget_dir 清空该设置；每次平台命令都会在启动期读取该文件"
	)
	try:
		group_cfg = load_config(group_config_path)
	except OSError as exc:
		raise _StartupConfigBadParameter(
			f"无法读取共享请求预算目录的配置 {group_config_path}: {exc.strerror or exc}；"
			"修正文件权限或删除该文件后重试",
			recovery_action=recovery,
			param_hint="config.json 的 request_budget_dir",
		) from exc
	except (ValueError, TypeError) as exc:
		# load_config 的消息已带文件路径与行列，统一前缀便于定位是组目录配置。
		raise _StartupConfigBadParameter(
			f"共享请求预算目录的配置不可用：{exc}",
			recovery_action=recovery,
			param_hint="config.json 的 request_budget_dir",
		) from exc
	try:
		validate_request_config(group_cfg)
	except (ValueError, TypeError) as exc:
		raise _StartupConfigBadParameter(
			f"共享请求预算目录的配置 {group_config_path} 无效：{exc}",
			recovery_action=recovery,
			param_hint="config.json 的 request_budget_dir",
		) from exc


def _missing_subcommand_error(ctx: click.Context | None) -> tuple[str, str, dict[str, Any]] | None:
	"""把「只写了命令组、没给子命令」的 Click 帮助全文换成子命令清单。

	``boss resume`` / ``boss hr`` / ``boss agent pending`` 这类调用在 Click 8
	抛 ``NoArgsIsHelpError``，异常消息就是整份英文帮助（「Usage: …」「Options: …」
	几百字符）。信封的 ``error.message`` 因此被撑成一坨帮助文本，Agent 既读不到
	「缺子命令」这个原因，也拿不到可执行的下一步。这里改成一句中文说明，并把
	各子命令的帮助命令放进 ``hints.next_actions``；直接列裸子命令会缺参或执行写入。
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
		{"next_actions": [f"{path} {name} --help" for name in names]},
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
		{"next_actions": [f"{path} {name} --help" for name in names]},
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


_EXTRA_ARGUMENT_RE = re.compile(r"^Got unexpected extra arguments? \((.*)\)$")
_OPTION_VALUE_MISSING_RE = re.compile(r"^Option '(.+)' requires an argument\.$")


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
	# 多别名选项的 hint 是 ``'-o' / '--output'``：逐个去引号，只剥首尾会留下
	# ``-o' / '--output`` 这种半截引号。
	return str(hint or "").replace("'", "").replace('"', "").strip()


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
	括号里为空是 ``boss chat ""``：空字符串也是 argv 里的一个额外参数。
	"""
	matched = _EXTRA_ARGUMENT_RE.match(str(getattr(exc, "message", "") or ""))
	if matched is None:
		return None
	path = _usage_path(exc)
	if path is None:
		return None
	extra = matched.group(1).strip()
	if extra:
		message = f"{path} 收到了多余的参数 '{extra}'"
	else:
		message = f"{path} 不接受额外的空参数"
	return (
		message,
		f"去掉多余的参数后重试；用 {path} --help 查看用法",
		{"next_actions": [f"{path} --help"]},
	)


def _missing_option_value_error(
	exc: click.ClickException, fallback_path: str = ""
) -> tuple[str, str, dict[str, Any]] | None:
	"""把 ``Option '--city' requires an argument.`` 换成中文说明。

	``boss search --city``（选项后面没跟取值）原来把 Click 的英文原文直接交给
	用户和 Agent，``recovery_action`` 也停在「修正参数」；真正的原因是选项后面
	少了值。这类错误在 parser 层抛出、异常上不挂 ``ctx``，命令路径只能由调用方
	从 argv 推断后作为 ``fallback_path`` 传入。
	"""
	matched = _OPTION_VALUE_MISSING_RE.match(str(getattr(exc, "message", "") or ""))
	if matched is None:
		return None
	path = _usage_path(exc) or (fallback_path or None)
	if path is None:
		return None
	option = matched.group(1)
	return (
		f"{path} 的选项 {option} 缺少取值",
		f"在 {option} 后面补上取值后重试；用 {path} --help 查看用法",
		{"next_actions": [f"{path} --help"]},
	)


_RANGE_INVALID_RE = re.compile(r"^(.+) is not in the range (.+)\.$")
_CHOICE_INVALID_RE = re.compile(r"^(.+) is not one of (.+)\.$")
_TYPE_INVALID_RE = re.compile(r"^(.+) is not a valid (.+)\.$")
_PATH_MISSING_RE = re.compile(r"^(?:Path|File|Directory) '(.*)' does not exist\.$")
_PATH_INVALID_RE = re.compile(
	r"^(?:Path|File|Directory) '(.*)' is (a file|a directory|not readable|not writable|not executable)\.$"
)
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

	不能按「消息里有没有中文」判断是不是 Click 自己的英文骨架：``--industry`` /
	``--stage`` 这类选项的候选值本身就是中文，``__nope__ is not one of '不限',
	'电子商务', …`` 含中文却仍是 Click 生成的英文正文。只按格式匹配，匹配不上
	才原样透出。
	"""
	if not isinstance(exc, click.BadParameter):
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

	Click 按 ``dir_okay`` / ``file_okay`` 决定前缀：只收文件时是 ``File '/tmp' is
	a directory.``（``boss stats --output <目录>``），只收目录时是 ``Directory …``，
	两种都收才是 ``Path …``——三种前缀都要认。

	同样不能因为消息含中文就跳过：路径本身可以是中文，``Path '/tmp/不存在的模型
	目录' does not exist.`` 的英文骨架仍要说清楚。
	"""
	if not isinstance(exc, click.BadParameter):
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
	路径名带中文时消息同样含中文，不能据此跳过。
	"""
	if not isinstance(exc, click.BadParameter):
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
		_localized_bad_parameter_error,
	):
		detail = converter(exc)
		if detail is not None:
			return detail
	return None


def _localized_bad_parameter_error(exc: click.ClickException) -> tuple[str, str, dict[str, Any]] | None:
	"""自定义回调已经写好中文正文的 ``BadParameter``：至少补齐可执行的下一步。

	``--platform`` / ``--delay`` / ``--role`` / ``mark --label`` 这类校验由回调自己
	raise，正文是中文，四类 Click 自带格式（范围 / 枚举 / 类型 / 路径）都匹配不上，
	原来的兜底只有 ``recovery_action="修正参数"``、``hints=null``——Agent 拿不到
	任何能执行的命令。正文保留，恢复指引与 ``next_actions`` 指向出错命令的 ``--help``。
	"""
	if not isinstance(exc, click.BadParameter) or not _has_cjk(str(exc.message)):
		return None
	path = _usage_path(exc)
	if path is None:
		return None
	hint = _parameter_display_name(exc)
	subject = f"的 {hint} 取值无效" if hint else "的参数取值无效"
	custom_recovery = getattr(exc, "recovery_action", None)
	recovery_action = (
		custom_recovery if isinstance(custom_recovery, str) and custom_recovery else f"用 {path} --help 查看参数说明"
	)
	return (
		f"{path} {subject}：{exc.message}",
		recovery_action,
		{"next_actions": [f"{path} --help"]},
	)


def _misplaced_global_option(group: click.Group, exc: click.ClickException) -> tuple[str, str | None] | None:
	"""Return a clearer message when a root option was parsed by a subcommand.

	``allow_interspersed_args`` stays false so Click's option ownership does not
	change. Users who write ``boss search --json`` otherwise only see
	``No such option '--json'``.

	第二个返回值是**可直接执行**的修正命令：带取值的全局选项需要用户先确定值
	（如 ``--delay 30-60``），给不出可执行命令，返回 ``None``，由调用方把
	next_actions 退回 ``boss --help``，而不是拼一个含 ``<value>`` 的假命令。
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
	if matched.is_flag:
		corrected = f"boss {token} {command}"
		return f"选项 {token} 是全局选项，必须写在子命令之前，例如：{corrected}", corrected
	example = f"boss {token} <value> {command} ..."
	return f"选项 {token} 是全局选项，必须写在子命令之前，例如：{example}", None


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
	are normalized to envelope by set_exit_codes_mode. Tokens after `--` are
	positional arguments, and values of other global options are not options."""
	tokens = list(args) if args is not None else sys.argv[1:]
	index = 0
	while index < len(tokens):
		token = tokens[index]
		if token == "--":
			break
		if token == "--exit-codes":
			if index + 1 < len(tokens):
				set_exit_codes_mode(tokens[index + 1])
				index += 2
			else:
				index += 1
			continue
		if token.startswith("--exit-codes="):
			set_exit_codes_mode(token.split("=", 1)[1])
		elif token in _GLOBAL_OPTIONS_WITH_VALUE:
			# `--data-dir --exit-codes` 中后者是前者的值，不是新的全局选项。
			index += 2
			continue
		index += 1


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


def _argv_command_path(group: click.Group, args: Sequence[str] | None, root: str) -> str:
	"""从 argv 推断出错命令的完整路径（``boss crawl start``）。

	解析器层抛出的异常不挂 ``ctx``，``_usage_path`` 拿不到命令层级；信封的
	``command`` 仍按顶层命令口径，这里只给 message 里的示例命令用完整路径。
	"""
	tokens = list(args) if args is not None else sys.argv[1:]
	path = [root]
	current: click.Command = group
	index = 0
	while index < len(tokens):
		token = tokens[index]
		if token in _GLOBAL_OPTIONS_WITH_VALUE:
			index += 2
			continue
		if token.startswith("-"):
			index += 1
			continue
		if isinstance(current, click.Group) and token in current.commands:
			path.append(token)
			current = current.commands[token]
			index += 1
			continue
		break
	return " ".join(path)


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


def _option_value_count(param: click.Option) -> int:
	"""一个选项要吃掉几个后续 argv。布尔开关（is_flag / count）不取值。"""
	if param.is_flag or param.count:
		return 0
	return max(int(param.nargs), 0)


def _match_declared_option(command: click.Command, token: str) -> tuple[click.Option | None, bool]:
	"""按 Click 参数元数据匹配选项，返回 ``(选项, 取值是否附着在同一 token)``。

	长选项支持 ``--name`` 与 ``--name=value``；短选项支持 ``-o`` 与 ``-ovalue``
	（Click 会把短选项后面剩余字符当成取值）。只匹配命令声明过的选项，其余
	横杠 token 由调用方按「未知选项」跳过。
	"""
	if token in ("-", "--") or not token.startswith("-"):
		return None, False
	for param in command.params:
		if not isinstance(param, click.Option):
			continue
		for name in (*param.opts, *param.secondary_opts):
			if token == name:
				return param, False
			if name.startswith("--") and token.startswith(f"{name}="):
				return param, True
	for param in command.params:
		if not isinstance(param, click.Option):
			continue
		for name in (*param.opts, *param.secondary_opts):
			if not name.startswith("--") and len(name) == 2 and token.startswith(name) and len(token) > 2:
				return param, True
	return None, False


def _resolve_argv_command(
	group: click.Group, args: Sequence[str]
) -> tuple[click.Command, int] | None:
	"""定位 argv 里真正要执行的命令及其 token 结束位置。

	子命令前可能有带取值的全局选项（``boss --data-dir <dir> detail …``），直接取
	「第一个非横杠 token」会落在选项取值上；这里按参数元数据跳过选项及其取值，
	并递归进入 ``hr`` 这类 Group，让后面的位置参数判定拿到叶子命令的选项表。
	"""
	current: click.Command = group
	index = 0
	path_end = 0
	while index < len(args):
		token = args[index]
		if token == "--":
			break
		if isinstance(current, click.Group) and token in current.commands:
			current = current.commands[token]
			index += 1
			path_end = index
			continue
		if token.startswith("-") and token != "-":
			param, attached = _match_declared_option(current, token)
			index += 1
			if param is not None and not attached:
				index += _option_value_count(param)
			continue
		break
	if path_end == 0:
		return None
	return current, path_end


def _declare_dash_prefixed_positionals(
	group: click.Group, ctx: click.Context | None, args: Sequence[str]
) -> list[str]:
	"""在形如 security_id 的横杠开头位置参数前插入 ``--``。

	BOSS 的 ``security_id`` 是 base64url，**可以合法地以 ``-`` 开头**（实测样本
	``-`` 开头的长 base64url 串，实测 328 字符）。Click 会把它当成未知选项，于是
	``boss detail`` 直接传该值时报告 ``No such option``，用户只能自己想到加 ``--``。

	限定两件事以免掩盖真正的拼写错误：

	- 只处理**看起来像 security_id** 的位置参数（``-`` 开头 + ≥20 个 base64url
	  字符），选项取值即使形如 security_id 也不参与改写（Issue #1449）；
	- 只处理命令**未声明**的选项名，且命令行里还没有 ``--`` 分隔符。

	所有位置参数整体后移到 ``--`` 之后，选项及其取值保持原有相对顺序，避免补出来的
	分隔符把「位置参数在先、选项在后」的调用（``boss detail <sid> --job-id <jid>``）
	里的选项吞成位置参数（Issue #1448）。
	"""
	if "--" in args:
		return list(args)

	resolved = _resolve_argv_command(group, args)
	if resolved is None:
		return list(args)
	command, path_end = resolved

	kept = list(args[:path_end])
	positionals: list[str] = []
	index = path_end
	while index < len(args):
		token = args[index]
		param, attached = _match_declared_option(command, token)
		if param is not None:
			kept.append(token)
			index += 1
			if not attached:
				value_count = _option_value_count(param)
				kept.extend(args[index : index + value_count])
				index += value_count
			continue
		if token.startswith("-") and token != "-":
			if _SECURITY_ID_LIKE.match(token):
				# 选项匹配之后仍命中的，就是本函数要保护的位置参数。
				positionals.append(token)
			else:
				kept.append(token)
			index += 1
			continue
		positionals.append(token)
		index += 1

	if not any(_SECURITY_ID_LIKE.match(token) for token in positionals):
		return list(args)
	return [*kept, "--", *positionals]


def _entry_json_mode(args: Sequence[str] | None) -> bool:
	"""启动期错误也可能带显式 ``--json``：即使 stdout 是 TTY 也要出信封。"""
	ctx = click.get_current_context(silent=True)
	if ctx is not None:
		# 复用 display 的判定：显式 --json 或非 TTY 都要出信封，TTY 测试也可替换该函数。
		return display.is_json_mode(ctx)
	# console script 调用 cli.main(args=None)，参数在 sys.argv 里；与 _prescan_exit_codes 同款回退。
	effective_args = sys.argv[1:] if args is None else args
	return "--json" in tuple(effective_args) or display.is_json_mode(None)


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

		def report_error(command: str, **kwargs: Any) -> None:
			# ctx=None 保持原信封文案不变；显式 --json 由 _entry_json_mode 决定通道。
			handle_error_output(None, command, json_mode=_entry_json_mode(args), **kwargs)

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
			if detail is None:
				# 选项缺取值的异常不挂 ctx，命令路径从 argv 推断。
				detail = _missing_option_value_error(exc, _argv_command_path(self, args, self.name or "boss"))
			if detail is not None:
				message, recovery_action, hints = detail
			misplaced = _misplaced_global_option(self, exc)
			if misplaced is not None:
				message, corrected = misplaced
				if corrected is not None:
					recovery_action = corrected
					next_actions = [corrected]
				else:
					# 带取值的全局选项给不出可执行命令：next_actions 必须是真能跑的
					# 命令，退回 `boss --help`，修正示例只留在 message 里。
					recovery_action = "把全局选项移到子命令之前；用 boss --help 查看全部全局选项"
					next_actions = ["boss --help"]
				hints = {
					"next_actions": next_actions,
					"operator_actions": [
						"全局选项（--json、--data-dir、--platform、--cdp-url 等）写在子命令前面",
					],
				}
			else:
				unknown_option = _unknown_option_error(exc)
				if unknown_option is not None:
					message, recovery_action, hints = unknown_option
			report_error(
				command,
				code="INVALID_PARAM",
				message=message,
				recoverable=False,
				recovery_action=recovery_action,
				hints=hints,
			)
			return None
		except CacheBusyError as exc:
			# 缓存库被其他连接写了超过 busy timeout：库文件完好，等锁释放后重试即可。
			# 没有单独兜底的命令（crawl stop 等）原来 stdout 为空并带 traceback
			# （Issue #1118），resume 则被兜成 NETWORK_ERROR「重试」。
			if not standalone_mode:
				raise
			command = _invoked_command_name(args, self.name or "boss")
			busy_recoverable, busy_recovery_action = error_contract_for_code("CACHE_BUSY")
			report_error(
				command,
				code="CACHE_BUSY",
				message=str(exc),
				recoverable=busy_recoverable,
				recovery_action=busy_recovery_action,
			)
			return None
		except CacheRecordInvalid as exc:
			# 本地任务 / 候选池记录损坏是确定性失败：重试同一条命令只会再失败
			# 一次，不能兜成可重试的 NETWORK_ERROR（Issue #1885）。
			if not standalone_mode:
				raise
			record_recoverable, record_recovery_action = error_contract_for_code("CACHE_RECORD_INVALID")
			report_error(
				_invoked_command_name(args, self.name or "boss"),
				code="CACHE_RECORD_INVALID",
				message=str(exc),
				recoverable=record_recoverable,
				recovery_action=record_recovery_action,
			)
			return None
		except CacheWriteRejected as exc:
			# 约束冲突 / 绑定值非法是输入问题，缓存库本身健康：这里绝不能给出
			# 「备份并删除缓存库」的指引（Issue #1886）。
			if not standalone_mode:
				raise
			invalid_recoverable, _invalid_recovery = error_contract_for_code("INVALID_PARAM")
			report_error(
				_invoked_command_name(args, self.name or "boss"),
				code="INVALID_PARAM",
				message=str(exc),
				recoverable=invalid_recoverable,
				recovery_action="本次写入被本地缓存库拒绝（约束冲突或非法绑定值），库本身完好、无需删除；修正调用参数后重试",
			)
			return None
		except sqlite3.DatabaseError as exc:
			# 锁冲突统一收口；其余的数据库错误（文件损坏、路径被同名目录占用）
			# 是确定性本地失败：原样上抛会让命令丢掉整个信封，或在带装饰器的
			# 命令里被兜成可重试的 NETWORK_ERROR（Issue #1304）。
			if not (isinstance(exc, sqlite3.OperationalError) and is_cache_busy_error(exc)):
				if not standalone_mode:
					raise
				command = _invoked_command_name(args, self.name or "boss")
				db_recoverable, db_recovery_action = error_contract_for_code("LOCAL_IO_ERROR")
				report_error(
					command,
					code="LOCAL_IO_ERROR",
					message=f"{command} 本地数据库文件无法打开: {exc}",
					recoverable=db_recoverable,
					recovery_action=db_recovery_action,
				)
				return None
			if not standalone_mode:
				raise
			command = _invoked_command_name(args, self.name or "boss")
			busy_recoverable, busy_recovery_action = error_contract_for_code("CACHE_BUSY")
			report_error(
				command,
				code="CACHE_BUSY",
				message=f"本地缓存库正被其他进程写入（{exc}）",
				recoverable=busy_recoverable,
				recovery_action=busy_recovery_action,
			)
			return None
		except CacheDatabaseInvalid as exc:
			# 缓存在每条命令启动时就会打开：库文件被写坏或被别的文件占位时，
			# 异常发生在命令自己的 try 之外，只能在这里统一给信封。
			if not standalone_mode:
				raise
			report_error(
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
			status = parameter_status_code(exc)
			if isinstance(exc, PlatformStatusRejected) and status is not None:
				# 平台对同一组参数的确定性拒绝（400 / 406 / 409 / 410 / 422）：
				# 与命令层同口径报 INVALID_PARAM，不给 INTERNAL_ERROR 的升级指引
				# （Issue #2567）。
				invalid_recoverable, invalid_recovery = error_contract_for_code("INVALID_PARAM")
				report_error(
					command,
					code="INVALID_PARAM",
					message=f"{command} 请求被平台拒绝（HTTP {status}），同一组参数重试不会改变结果",
					recoverable=invalid_recoverable,
					recovery_action=invalid_recovery,
				)
				return None
			internal_recoverable, internal_recovery_action = error_contract_for_code("INTERNAL_ERROR")
			report_error(
				command,
				code="INTERNAL_ERROR",
				message=f"{command} 失败: {type(exc).__name__}: {exc}",
				recoverable=internal_recoverable,
				recovery_action=internal_recovery_action,
			)
			return None
		except RequestTextEncodingError:
			# 请求参数编码失败（孤立代理项等）是输入问题：请求未发出，不能给
			# 磁盘 / 权限指引（Issue #2533）。装饰器命令已由 handle_auth_errors
			# 收口，这里兜住没有装饰器的命令。
			if not standalone_mode:
				raise
			command = _invoked_command_name(args, self.name or "boss")
			encoding_recoverable, _encoding_recovery = error_contract_for_code("INVALID_PARAM")
			report_error(
				command,
				code="INVALID_PARAM",
				message=f"{command} 请求参数含有无法按 UTF-8 编码的字符（如孤立代理项），请求未发出",
				recoverable=encoding_recoverable,
				recovery_action=(
					"检查命令中的标识 / 关键词 / 消息文本，去掉无效字符后重试；"
					"编号建议重新从 search 或 chat 的结果中取"
				),
			)
			return None
		except UnicodeError as exc:
			# 孤立代理字符等不可编码内容属于确定性本地数据问题：既不是平台网络
			# 故障（重试无意义），也不是「升级版本可解」的内部缺陷（Issue #1294）。
			if not standalone_mode:
				raise
			command = _invoked_command_name(args, self.name or "boss")
			encode_recoverable, encode_recovery_action = error_contract_for_code("LOCAL_IO_ERROR")
			report_error(
				command,
				code="LOCAL_IO_ERROR",
				message=f"{command} 处理本地文本失败: {exc}",
				recoverable=encode_recoverable,
				recovery_action=encode_recovery_action,
			)
			return None
		except OSError as exc:
			from boss_agent_cli.services.chat_snapshot import ChatSnapshotBusyError

			# 快照锁争用是并发导出的正常现象：可重试，且不能给磁盘 / 权限指引
			# （Issue #2538）。chat 命令已有装饰器收口，这里兜住没有装饰器的
			# 调用路径。
			if isinstance(exc, ChatSnapshotBusyError):
				if not standalone_mode:
					raise
				command = _invoked_command_name(args, self.name or "boss")
				busy_recoverable, _ = error_contract_for_code("CACHE_BUSY")
				report_error(
					command,
					code="CACHE_BUSY",
					message=str(exc),
					recoverable=busy_recoverable,
					recovery_action=(
						"稍后重试本命令；并发导出同一数据目录时会短暂争用当天快照锁，"
						"快照文件本身完好，不需要删除"
					),
				)
				return None
			# 网兜：导出、保存配置这类直接写文件的路径没有统一装饰器，
			# 写失败时必须给信封，而不是把 traceback 甩给 Agent。
			if not standalone_mode:
				raise
			command = _invoked_command_name(args, self.name or "boss")
			code = "LOCAL_IO_ERROR" if is_local_io_error(exc) else "NETWORK_ERROR"
			# 本地 IO 失败要给出可执行的下一步：recovery_action 里说的「确认目标路径
			# 可写 / 磁盘空间正常」对 Agent 只是自然语言，没有一条能直接跑的命令。
			# 全量路径带本机绝对路径，只在 details 里保留，message 不进敏感位置信息
			# （与其他错误码一致：message 说原因，details 说位置）。
			report_error(
				command,
				code=code,
				message=f"{command} 本地文件读写失败: {exc}"
				if code == "LOCAL_IO_ERROR"
				else f"{command} 失败: {exc}",
				hints={
					"operator_actions": ["确认目标路径在可写目录下、磁盘空间充足后重新执行原命令"]
				}
				if code == "LOCAL_IO_ERROR"
				else None,
			)
			return None
		except ValueError as exc:
			# AI 本地配置项的确定性错误（如手工编辑的 base_url）也是 ValueError：
			# ai local 这类没有统一装饰器的命令以前会把 traceback 直接甩出去
			# （Issue #2205）。本分支放在链尾是有意的：CacheRecordInvalid /
			# CacheWriteRejected / ProxyConfigurationError / PlatformResponseInvalid
			# 都是 ValueError 子类，各自的专用分支必须先有机会处理；只认
			# AIConfigValueError 一种 typed error，其余 ValueError 维持既有行为。
			# 持久化配置的问题按既有契约报 CONFIG_INVALID（Issue #1636），
			# 与本命令自己的写入参数校验（INVALID_PARAM）分开。
			from boss_agent_cli.ai.config import AIConfigValueError

			if not isinstance(exc, AIConfigValueError) or not standalone_mode:
				raise
			command = _invoked_command_name(args, self.name or "boss")
			invalid_recoverable, invalid_recovery = error_contract_for_code("CONFIG_INVALID")
			report_error(
				command,
				code="CONFIG_INVALID",
				message=str(exc),
				recoverable=invalid_recoverable,
				recovery_action=f"{exc.recovery_hint} 修正后重试" if exc.recovery_hint else invalid_recovery,
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
	resolved_dir = _resolve_data_dir(data_dir)
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

	# 显式空串与未提供不同（Issue #1578）：真值回退会把 `--delay ""` 换成
	# 配置文件里的区间，用户以为显式值已生效。空串交给下面的区间解析拒绝。
	if delay is not None:
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
		raw_delay = cfg["request_delay"]
		if not isinstance(raw_delay, (list, tuple)):
			# 坏形状不能静默换成默认值：用户以为节流配置已生效，实际每条命令
			# 都按默认区间跑，风控节奏与感知不符。config 子命令放行以便 reset。
			if ctx.invoked_subcommand != "config":
				raise click.BadParameter(
					f"request_delay 必须是两个数值的数组，收到 {raw_delay!r}",
					param_hint="config.json",
				)
			raw_delay = DEFAULT_REQUEST_DELAY
		ctx.obj["delay"] = tuple(raw_delay)
	if ctx.invoked_subcommand != "config":
		try:
			validate_request_config({**cfg, "request_delay": ctx.obj["delay"]})
		except ValueError as exc:
			if "request_budget_dir" in str(exc):
				# 启动期拒绝时给出能直接执行的恢复命令，而不是「用 --help 看参数」（Issue #1822）。
				raise _StartupConfigBadParameter(
					f"boss 的 config.json 的 request_budget_dir 取值无效：{exc}",
					recovery_action=(
						f"用 boss --data-dir {shell_argument(str(resolved_dir))} "
						"config reset request_budget_dir 清空该设置后重试"
					),
					param_hint="config.json 的 request_budget_dir",
				) from exc
			raise click.BadParameter(str(exc), param_hint="config.json") from exc
		# 组目录配置只在客户端构造时才会加载；启动期先校验，避免确定性配置
		# 错误被兜成可重试的 NETWORK_ERROR，也避免命令先构造客户端再失败。
		_validate_group_budget_config(resolved_dir, cfg)

	raw_level = log_level or cfg["log_level"]
	# Logger 对未知级别会静默回退到 error 阈值：config.json 手改出的坏值会让
	# 用户以为 debug 已打开却什么都看不到。与 cdp_url 同规则放行 config 子命令，
	# 但归一成合法默认值再交给 Logger，避免非字符串在字典查找处抛 TypeError。
	if isinstance(raw_level, str) and raw_level in LOG_LEVELS:
		level = raw_level
	elif ctx.invoked_subcommand == "config":
		level = DEFAULTS["log_level"]
	else:
		raise click.BadParameter(
			f"必须是 {' / '.join(LOG_LEVELS)} 之一，收到 {raw_level!r}",
			param_hint="config.json 的 log_level",
		)
	ctx.obj["log_level"] = level
	ctx.obj["logger"] = Logger(level)
	# config 子命令是唯一的修复入口：坏值归一成默认值放行，否则连
	# `boss config reset <键>` 也会被同一个坏值挡住，用户只能手改文件。
	repair_mode = ctx.invoked_subcommand == "config"

	# 显式空串同样按「显式值」处理（Issue #1578）：不能回落到配置里的调试地址。
	resolved_cdp_url = cdp_url if cdp_url is not None else cfg.get("cdp_url")
	# 与 browser_source 同理：config.json 被手改成数字后，doctor 等命令会在 shlex.quote
	# 上抛 TypeError traceback，启动期就要按参数错误挡住。
	if resolved_cdp_url is not None and not isinstance(resolved_cdp_url, str):
		if repair_mode:
			resolved_cdp_url = None
		else:
			raise click.BadParameter(
				f"必须是调试地址字符串或 null，收到 {resolved_cdp_url!r}",
				param_hint="--cdp-url",
			)
	if resolved_cdp_url is not None and (cdp_url is not None or not repair_mode):
		# 显式 --cdp-url 任何时候都要校验；config.json 里的坏值则放行 config
		# 子命令，否则 `boss config reset cdp_url` 会在启动期被同一个坏值挡住。
		if not is_valid_cdp_url(resolved_cdp_url):
			raise click.BadParameter(
				f"{CDP_URL_REQUIREMENT}，收到 {resolved_cdp_url!r}",
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
		if not repair_mode or browser_source is not None:
			raise click.BadParameter(
				f"不支持的浏览器通道 {resolved_browser_source!r}，可选: {', '.join(BROWSER_SOURCES)}",
				param_hint="--browser-source",
			)
		resolved_browser_source = DEFAULTS["browser_source"]
	ctx.obj["browser_source"] = resolved_browser_source

	if platform_name == "":
		# 显式空平台与未提供不同：真值回退会静默使用配置或 zhipin，MCP 宿主
		# 传错值却拿到另一份平台状态（Issue #1471）。
		raise click.BadParameter("平台名不能为空", param_hint="--platform")
	resolved_platform = platform_name or cfg.get("platform") or "zhipin"
	available = list_platforms()
	if resolved_platform not in available:
		if not repair_mode or platform_name is not None:
			raise click.BadParameter(
				f"不支持的平台 {resolved_platform!r}，可选: {', '.join(available)}",
				param_hint="--platform",
			)
		resolved_platform = DEFAULTS["platform"]
	ctx.obj["platform"] = resolved_platform

	resolved_role = role or cfg.get("role") or "candidate"
	# config.json 的 role 写成数字或拼错时，下游会把非字符串塞进诊断命令的
	# shlex.quote（doctor）或静默按求职者处理；启动期就按参数错误挡住。
	if resolved_role not in ROLES:
		if not repair_mode or role is not None:
			raise click.BadParameter(
				f"不支持的角色 {resolved_role!r}，可选: {', '.join(ROLES)}",
				param_hint="--role",
			)
		resolved_role = DEFAULTS["role"]
	ctx.obj["role"] = resolved_role

	ctx.obj["config"] = cfg
	ctx.obj["hooks"] = create_hook_bus()

	if ctx.invoked_subcommand is None:
		from boss_agent_cli.commands.wizard import run_wizard_command

		run_wizard_command(ctx)


register_candidate_commands(cli)
register_recruiter_commands(cli)
