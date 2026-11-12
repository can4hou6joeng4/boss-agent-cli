"""Rich terminal rendering + TTY-aware output routing.

TTY mode: Rich tables/panels to stderr, nothing to stdout.
Pipe mode (Agent): JSON envelope to stdout.
--json flag: Force JSON to stdout even in TTY.
"""

import datetime
import sys
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Callable

import click
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from boss_agent_cli.output import emit_success, is_local_io_error, redact_sensitive_text
from boss_agent_cli.schema.error_codes import ERROR_CODES

# Rich output goes to stderr so stdout stays clean for Agent JSON
console = Console(stderr=True)


def escape_markup(value: Any) -> str:
	"""把平台 / 用户外部字符串转成 Rich 按字面渲染的文本。

	Rich 默认把字符串当 markup 解析：未配对的闭合标签（``[/bold]``）直接抛
	MarkupError 让整条 TTY 输出崩掉，``[link=...]`` 则会把终端渲染成可点击
	超链接（OSC 8）。公司名、职位名、职位描述、聊天内容、昵称都是平台外部
	输入，进入 Rich 前一律经过这里；代码自身的样式标签保持不变。
	"""
	return escape(str(value))


_SHELL_METACHARACTERS = frozenset("'\"`$\\!*?[]{}()<>|&;#")


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


def boss_command(command: str, *, platform_name: str = "zhipin", data_dir: Path | None = None, role: str | None = None) -> str:
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


def auth_required_hints(ctx: Any) -> dict[str, list[str]]:
	"""登录类错误附带的下一步：命令给 Agent 执行，说明给真人扫码。"""
	login_action = login_action_for_ctx(ctx)
	return {
		"next_actions": [login_action],
		"operator_actions": [f"在终端执行 {login_action}，按提示用 BOSS 直聘 App 扫码完成登录后重试"],
	}


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


def platform_error_contract(code: str) -> tuple[bool, str | None, dict[str, Any] | None]:
	"""Return ``(recoverable, recovery_action, hints)`` for a platform error code.

	风控码的恢复指引与 ``next_actions`` 一律以 ``RISK_ERROR_CONTRACTS`` 为准，
	避免各命令手工拼装出互相矛盾的停止指令。
	"""
	recoverable, recovery_action = error_contract_for_code(code)
	if code in RISK_ERROR_CONTRACTS:
		recovery_action, hints = risk_error_contract(code)
		return recoverable, recovery_action, hints
	return recoverable, recovery_action, None


def risk_error_message(code: str) -> str:
	"""Return a stop-oriented message for a risk code.

	风控信封不透传异常或平台响应文本（可能带临时凭据），只暴露稳定错误码与
	登记表文案；具体停止指引由 ``recovery_action`` / ``hints`` 承载。
	"""
	label = str(ERROR_CODES.get(code, {}).get("message") or "命中平台风控")
	return f"{label}；停止自动化访问并在官方页面处理"


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
		console.print(f"[yellow]{prefix}[/yellow]{escape_markup(action)}")


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
		render(data)
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
) -> None:
	"""Smart error output: TTY -> rich error, pipe -> JSON error envelope."""
	from boss_agent_cli.output import emit_error

	# 调用方未显式声明时按登记表补齐，TTY 与 JSON 两条路径看到同一份恢复契约。
	registered_recoverable, registered_recovery_action = error_contract_for_code(code)
	if recoverable is None:
		recoverable = registered_recoverable
	if recovery_action is None:
		recovery_action = registered_recovery_action
	if (
		code in {"AUTH_REQUIRED", "AUTH_EXPIRED", "TOKEN_REFRESH_FAILED"}
		and recoverable
		and recovery_action == registered_recovery_action
	):
		# 登记表是无运行上下文的示例；只补齐通用登录动作，不覆盖写操作的人工核对要求。
		recovery_action = login_action_for_ctx(ctx)
		hints = {**auth_required_hints(ctx), **(hints or {})}

	if is_json_mode(ctx):
		emit_error(
			command,
			code=code,
			message=message,
			recoverable=recoverable,
			recovery_action=recovery_action,
			details=details,
			hints=hints,
		)
	else:
		# JSON 路径由 output.py 脱敏；TTY 分支同样不能把异常文本里的凭据
		# 明文打到终端（Issue #880）。
		safe_message = redact_sensitive_text(message)
		safe_recovery = redact_sensitive_text(recovery_action) if recovery_action else recovery_action
		# 命令文本里可能带数据目录：方括号内容会被 Rich 当作样式标签吞掉，
		# 未配对的闭合标签还会直接抛 MarkupError，恢复指引必须按字面显示。
		console.print(f"[red]error[/red] [{code}] {escape(safe_message)}")
		if safe_recovery:
			console.print(f"  [dim]recovery: {escape(safe_recovery)}[/dim]")
		render_operator_actions(hints)
		raise SystemExit(1)


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


def handle_platform_error_output(
	ctx: Any,
	command: str,
	platform: Any,
	response: Any,
	*,
	fallback_message: str,
	unknown_error_code: str | None = None,
	extra_hints: dict[str, Any] | None = None,
) -> None:
	"""Emit a schema-backed error envelope for an unsuccessful platform response.

	``extra_hints`` 承载命令自身的诊断字段（如 status 的 ``auth_health``）；
	风控码的 ``next_actions`` 由 ``RISK_ERROR_CONTRACTS`` 覆盖，调用方不能改写。
	"""
	code, message = platform.parse_error(response)
	if code == "UNKNOWN" and unknown_error_code is not None:
		code = unknown_error_code
	recoverable, recovery_action, risk_hints = platform_error_contract(code)
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
		if code in RISK_ERROR_CONTRACTS and response.get("code") in (31, "31", 32, "32", 35, "35", 36, "36", 37, "37"):
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
	if not items:
		console.print("[yellow]no results[/yellow]")
		return

	table = Table(title=f"{title} ({len(items)} results)", show_lines=True)
	table.add_column("#", style="dim", width=3)
	table.add_column("title", style="bold cyan", max_width=30)
	table.add_column("company", style="green", max_width=20)
	table.add_column("salary", style="yellow", max_width=12)
	table.add_column("type", max_width=8)
	table.add_column("exp", max_width=10)
	table.add_column("edu", max_width=8)
	table.add_column("city", style="blue", max_width=12)

	for i, job in enumerate(items, 1):
		table.add_row(
			str(i),
			escape_markup(job.get("title", job.get("jobName", "-"))),
			escape_markup(job.get("company", job.get("brandName", "-"))),
			escape_markup(job.get("salary", job.get("salaryDesc", "-"))),
			escape_markup(job.get("employment_type") or "-"),
			escape_markup(job.get("experience", job.get("jobExperience", "-"))),
			escape_markup(job.get("education", job.get("jobDegree", "-"))),
			escape_markup(job.get("city", job.get("cityName", "-"))),
		)

	console.print(table)
	if show_command:
		console.print(f"  [dim]use: {escape(show_command)} to view details[/dim]")
	if hint_next:
		console.print(f"  [dim]{escape(hint_next)}[/dim]")


def _snapshot_time_label(value: Any) -> str:
	"""把索引快照的 Unix 秒渲染成本地时间；损坏缓存里的非数值按 "-" 处理。"""
	if isinstance(value, bool) or not isinstance(value, (int, float)):
		return "-"
	try:
		return datetime.datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")
	except (OverflowError, OSError, ValueError):
		return "-"


def render_job_detail(data: dict[str, Any], *, greet_command: str | None = None) -> None:
	"""Render job detail as a rich panel."""
	title = escape_markup(data.get("title", "-"))
	salary = escape_markup(data.get("salary", "-"))
	exp = escape_markup(data.get("experience", "-"))
	edu = escape_markup(data.get("education", "-"))
	city = escape_markup(data.get("city", "-"))
	company = escape_markup(data.get("company", "-"))
	boss = escape_markup(data.get("boss_name", "-"))
	boss_title = escape_markup(data.get("boss_title", "-"))
	desc = str(data.get("description", ""))

	skills = data.get("skills", [])
	skill_str = ", ".join(escape_markup(skill) for skill in skills) if skills else "-"

	text = (
		f"[bold cyan]{title}[/bold cyan]  [yellow]{salary}[/yellow]\n"
		f"exp: {exp} | edu: {edu} | city: {city}\n"
		f"skills: {skill_str}\n"
		f"\n"
		f"[bold green]company:[/bold green] {company}\n"
		f"\n"
		f"[bold magenta]boss:[/bold magenta] {boss} ({boss_title})\n"
	)

	if desc:
		if len(desc) > 500:
			desc = desc[:500] + "..."
		# 先截断再转义：截断可能落在转义序列中间。
		text += f"\n[bold]description:[/bold]\n{escape_markup(desc)}"

	panel = Panel(text, title="job detail", border_style="cyan")
	console.print(panel)

	# 编号绑定的是索引快照，不是职位本身：把来源和保存时间放在详情下方，
	# 重搜之后一眼就能看出这条编号是不是已经指向另一次搜索。
	snapshot = data.get("index_snapshot")
	if isinstance(snapshot, dict):
		source = escape(str(snapshot.get("source") or "-"))
		count = snapshot.get("count")
		count_label = str(count) if isinstance(count, int) and not isinstance(count, bool) else "-"
		page = snapshot.get("page")
		page_label = f"第 {page} 页 · " if isinstance(page, int) and not isinstance(page, bool) else ""
		index = data.get("index", "-")
		console.print(
			f"  [dim]#{index} · {page_label}{source} · 共 {count_label} 条 · 保存于 {_snapshot_time_label(snapshot.get('saved_at'))}[/dim]"
		)

	sid = data.get("security_id", "")
	jid = data.get("job_id", "")
	if sid and jid:
		greet_command = greet_command or "boss greet <security_id> <job_id>"
		console.print(f"  [dim]next: {escape(greet_command)}[/dim]")


def render_status(data: dict[str, Any], *, login_action: str = "boss login") -> None:
	"""Render login status."""
	if data.get("logged_in"):
		name = escape_markup(data.get("user_name", "unknown"))
		console.print(f"[green]logged in[/green] as [bold]{name}[/bold]")
	else:
		console.print(f"[yellow]not logged in[/yellow] - run: {login_action}")
	lock = data.get("cdp_risk_lock")
	if isinstance(lock, dict) and lock.get("locked"):
		since = escape_markup(lock.get("locked_at") or "unknown")
		reason = escape_markup(lock.get("reason") or "ENVIRONMENT_RISK")
		console.print(
			f"[yellow]BOSS 本地风控锁定中[/yellow]（{since} 起，{reason}）：确认官方目标业务页已恢复后，"
			"由用户执行 boss clean --risk-lock 再显式恢复任务；令牌变化不会自动解锁"
		)


def render_simple_list(
	items: list[dict[str, Any]],
	title: str,
	columns: list[tuple[str, str, str]],
) -> None:
	"""Render a generic list as a rich table.

	columns: list of (header, dict_key, style)
	"""
	if not items:
		console.print(f"[yellow]no {title}[/yellow]")
		return

	table = Table(title=f"{title} ({len(items)})", show_lines=True)
	table.add_column("#", style="dim", width=3)
	for header, _, style in columns:
		table.add_column(header, style=style, max_width=25)

	for i, item in enumerate(items, 1):
		row = [str(i)]
		for _, key, _ in columns:
			row.append(escape_markup(item.get(key, "-")))
		table.add_row(*row)

	console.print(table)


# ── Additional renderers ────────────────────────────────────────────


def render_message_panel(data: dict[str, Any], *, title: str = "result") -> None:
	"""Render a simple key-value result as a panel."""
	lines = []
	for k, v in data.items():
		lines.append(f"[bold]{escape_markup(k)}:[/bold] {escape_markup(v)}")
	panel = Panel("\n".join(lines), title=title, border_style="green")
	console.print(panel)


def render_batch_operation_summary(data: dict[str, Any], *, title: str = "batch result") -> None:
	"""Render batch operation summary (greeted/failed counts + items)."""
	greeted = data.get("greeted", [])
	failed = data.get("failed", [])
	dry_run = data.get("dry_run", False)

	if dry_run:
		candidates = data.get("candidates", [])
		console.print(f"[yellow]dry run[/yellow] — {len(candidates)} candidates")
		if candidates:
			render_job_table(candidates, f"{title} (dry run)")
		return

	console.print(f"[green]success: {len(greeted)}[/green]  [red]failed: {len(failed)}[/red]")
	if greeted:
		table = Table(title="greeted", show_lines=True)
		table.add_column("title", style="cyan", max_width=25)
		table.add_column("company", style="green", max_width=20)
		for item in greeted:
			table.add_row(escape_markup(item.get("title", "-")), escape_markup(item.get("company", "-")))
		console.print(table)
	if data.get("stopped_reason"):
		console.print(f"  [yellow]stopped: {escape_markup(data['stopped_reason'])}[/yellow]")


def render_sectioned_record(data: dict[str, Any], *, title: str = "info") -> None:
	"""Render multi-section record (e.g., me command) as panels."""
	for section, content in data.items():
		if isinstance(content, dict):
			lines = []
			for k, v in content.items():
				if isinstance(v, (list, dict)):
					v = str(v)[:200]
				lines.append(f"[bold]{escape_markup(k)}:[/bold] {escape_markup(v or '-')}")
			panel = Panel("\n".join(lines) if lines else "[dim]empty[/dim]", title=section, border_style="cyan")
			console.print(panel)
		else:
			console.print(f"[bold]{escape_markup(section)}:[/bold] {escape_markup(content)}")


def render_string_grid(items: list[str], title: str, *, columns: int = 4) -> None:
	"""Render a list of strings as a multi-column grid."""
	if not items:
		console.print(f"[yellow]no {title}[/yellow]")
		return

	table = Table(title=f"{title} ({len(items)})", show_header=False)
	for _ in range(columns):
		table.add_column(max_width=20)

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
		console.print(f"[green]exported[/green] {count} jobs to [bold]{escape_markup(path)}[/bold] ({escape_markup(fmt)})")
	else:
		console.print(f"[green]exported[/green] {count} jobs ({fmt})")
	if data.get("truncated"):
		console.print("[yellow]注意：扫描到内部页数上限，结果可能不完整[/yellow]")


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
		console.print(f"    [dim]{escape(action)}[/dim]")


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
				inline = "  ".join(f"{k}={v}" for k, v in item.items())
				lines.append(f"  · {escape(str(inline))}")
			else:
				lines.append(f"  · {escape(str(item))}")
		return lines
	if isinstance(value, dict):
		return [f"  [bold]{escape(str(k))}:[/bold] {escape(str(v))}" for k, v in value.items()]
	return [f"  {escape(str(value))}"]


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
		body.append(f"[bold cyan]{escape(str(key))}[/bold cyan]")
		body.extend(_ai_value_lines(value))
		body.append("")
	console.print(Panel("\n".join(body).rstrip() or "[dim]empty[/dim]", title=title, border_style="magenta"))
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
			console.print(f"  [green]✓[/green] {escape(text.lstrip(self._MATCH_MARK).strip())}")
			return
		if any(text.startswith(mark) for mark in self._EXCLUDE_MARKS):
			# 逐条排除只计数不打印——刷屏会把真正有用的匹配结果冲掉
			with self._lock:
				self.excluded += 1
			return

	def debug(self, message: str) -> None:
		"""调试细节不进 TTY 进度。"""

	def warning(self, message: str) -> None:
		console.print(f"  [yellow]{escape(message.strip())}[/yellow]")

	def error(self, message: str) -> None:
		console.print(f"  [red]{escape(message.strip())}[/red]")

	# ── 状态行 ─────────────────────────────────────────────

	def status_text(self) -> str:
		with self._lock:
			parts = [f"第 {self.pages}/{self._max_pages} 页" if self.pages else "准备中"]
			parts.append(f"匹配 {self.matched}")
			if self.excluded:
				parts.append(f"排除 {self.excluded}")
		return " · ".join(parts)

	def start(self) -> None:
		console.print(f"[bold]{escape(self._title)}[/bold]")

	def finish(self) -> None:
		console.print(f"  [dim]{escape(self.status_text())}[/dim]")

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
		"next_actions": [
			"不要通过 CDP、patchright 或其他通道重试该操作",
			"锁未解除时只使用本地辅助和离线状态命令；官方目标业务页恢复后由用户显式清除本地锁",
		],
	},
	"ENVIRONMENT_RISK": {
		"recovery_action": ERROR_CODES["ENVIRONMENT_RISK"]["recovery_action"],
		"next_actions": [
			"不要刷新 Token、重新登录或自动重试该请求",
			"由用户在原会话确认官方目标业务页恢复后，显式清除本地锁并恢复任务",
		],
	},
	"ENVIRONMENT_RISK_LOCKED": {
		"recovery_action": ERROR_CODES["ENVIRONMENT_RISK_LOCKED"]["recovery_action"],
		"next_actions": [
			"不要刷新 Token、重新登录或换通道重试；本次请求未发出",
			"由用户在原会话确认官方目标业务页恢复后，执行 boss clean --risk-lock 并显式恢复任务；令牌变化不放行",
			"boss doctor",
		],
	},
}


def risk_error_contract(code: str) -> tuple[str, dict[str, list[str]]]:
	"""Return ``(recovery_action, hints)`` for a platform risk error code."""
	spec = RISK_ERROR_CONTRACTS.get(code)
	if spec is None:
		_, recovery = error_contract_for_code(code, fallback_recovery_action="停止自动化访问")
		return recovery or "停止自动化访问", {}
	return str(spec["recovery_action"]), {"next_actions": list(spec["next_actions"])}


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
INTERNAL_ERROR_TYPES: tuple[type[Exception], ...] = (TypeError, AttributeError, KeyError, IndexError, OverflowError)


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
			from boss_agent_cli.api.browser_client import BrowserNavigationError, RecruiterChatTabRequired
			from boss_agent_cli.api._base_client import BrowserChannelRequired
			from boss_agent_cli.api.browser_source import BrowserSourceUnavailable, BrowserSourceUnsupported
			from boss_agent_cli.api.client import PlatformRiskError
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
				hints: dict[str, list[str]] = {}
				if e.policy.operator_actions:
					hints["operator_actions"] = list(e.policy.operator_actions)
				if e.policy.next_actions:
					hints["next_actions"] = list(e.policy.next_actions)
				handle_error_output(
					ctx,
					command_name,
					code=e.code,
					message=str(e),
					recoverable=True,
					recovery_action=e.policy.recovery_action,
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
			except PlatformRiskError as e:
				recovery_action, hints = risk_error_contract(e.code)
				handle_error_output(
					ctx,
					command_name,
					code=e.code,
					# 风控信封不透传平台响应文本（可能带临时凭据），只给登记表文案。
					message=risk_error_message(e.code),
					recoverable=False,
					recovery_action=recovery_action,
					details=e.details or None,
					hints=_with_httpx_only_hint(ctx, hints),
				)
			except RequestBudgetExceeded as e:
				budget_recoverable, budget_recovery = error_contract_for_code(e.code)
				handle_error_output(
					ctx,
					command_name,
					code=e.code,
					message=str(e),
					recoverable=budget_recoverable,
					recovery_action=budget_recovery,
					details=e.details,
					hints=_with_httpx_only_hint(
						ctx, {"operator_actions": ["等待冷却并检查并发任务；确认后显式恢复，不自动清理风险锁"]}
					),
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
			except INTERNAL_ERROR_TYPES as e:
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
				if is_local_io_error(e):
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
