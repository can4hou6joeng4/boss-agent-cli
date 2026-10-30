import re
import sys
from pathlib import Path
from collections.abc import Sequence
from typing import Any

import click

from boss_agent_cli import __version__
from boss_agent_cli.api.browser_source import POLICIES as BROWSER_SOURCES
from boss_agent_cli.commands.register import register_candidate_commands, register_recruiter_commands
from boss_agent_cli.config import DEFAULT_REQUEST_DELAY, load_config, validate_request_config
from boss_agent_cli.api.request_budget import validate_delay
from boss_agent_cli.hooks import create_hook_bus
from boss_agent_cli.output import emit_error, Logger, set_exit_codes_mode
from boss_agent_cli.platforms import list_platforms


def _command_path(ctx: click.Context | None) -> str:
	names: list[str] = []
	current = ctx
	while current is not None and current.parent is not None:
		if current.info_name:
			names.append(current.info_name)
		current = current.parent
	return " ".join(reversed(names))


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
			command = getattr(ctx, "info_name", None) or self.name or "boss"
			message = exc.format_message()
			recovery_action = "修正参数"
			hints = None
			misplaced = _misplaced_global_option(self, exc)
			if misplaced is not None:
				message, recovery_action = misplaced
				hints = {
					"next_actions": [recovery_action.replace(" ...", " --help")],
					"operator_actions": [
						"全局选项（--json、--data-dir、--platform、--cdp-url 等）写在子命令前面",
					],
				}
			emit_error(
				command,
				code="INVALID_PARAM",
				message=message,
				recoverable=False,
				recovery_action=recovery_action,
				hints=hints,
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
@click.option("--delay", default=None, help="请求间隔范围（秒），如 1.5-3.0")
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
	type=click.Choice(["candidate", "recruiter"]),
	help="角色模式：candidate（求职者，默认）/ recruiter（招聘者）",
)
@click.option("--log-level", default=None, type=click.Choice(["error", "warning", "info", "debug"]))
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
	except (ValueError, TypeError) as exc:
		raise click.BadParameter(str(exc), param_hint="config.json") from exc

	if delay:
		try:
			low, high = delay.split("-", 1)
			ctx.obj["delay"] = (float(low), float(high))
			validate_delay(ctx.obj["delay"])
		except ValueError as exc:
			raise click.BadParameter(
				"delay must be a finite range like 1.5-3.0 with 0 <= low <= high",
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
	ctx.obj["cdp_url"] = cdp_url or cfg.get("cdp_url")

	# 归一化（大小写/首尾空白）后再校验，与下游 resolve_policy 的 .strip().lower() 一致，
	# 避免 config.json 里 "Auto" / " stored-cookie " 这类值被 CLI 拒绝、策略层却接受的不一致。
	resolved_browser_source = (browser_source or cfg.get("browser_source") or "auto").strip().lower()
	if resolved_browser_source not in BROWSER_SOURCES:
		raise click.BadParameter(
			f"unknown browser source {resolved_browser_source!r}, supported: {', '.join(BROWSER_SOURCES)}",
			param_hint="--browser-source",
		)
	ctx.obj["browser_source"] = resolved_browser_source

	resolved_platform = platform_name or cfg.get("platform") or "zhipin"
	available = list_platforms()
	if resolved_platform not in available:
		raise click.BadParameter(
			f"unknown platform {resolved_platform!r}, supported: {', '.join(available)}",
			param_hint="--platform",
		)
	ctx.obj["platform"] = resolved_platform

	resolved_role = role or cfg.get("role") or "candidate"
	ctx.obj["role"] = resolved_role

	ctx.obj["config"] = cfg
	ctx.obj["hooks"] = create_hook_bus()

	if ctx.invoked_subcommand is None:
		from boss_agent_cli.commands.wizard import run_wizard_command

		run_wizard_command(ctx)


register_candidate_commands(cli)
register_recruiter_commands(cli)
