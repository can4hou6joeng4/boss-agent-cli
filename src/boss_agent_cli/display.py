"""Rich terminal rendering + TTY-aware output routing.

TTY mode: Rich tables/panels to stderr, nothing to stdout.
Pipe mode (Agent): JSON envelope to stdout.
--json flag: Force JSON to stdout even in TTY.
"""

import sys
import threading
from collections.abc import Sequence
from typing import Any, Callable

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from boss_agent_cli.output import emit_success

# Rich output goes to stderr so stdout stays clean for Agent JSON
console = Console(stderr=True)


def boss_command_for_ctx(ctx: Any, command: str) -> str:
	"""Return a platform-aware boss subcommand."""
	platform_name = "zhipin"
	if ctx and getattr(ctx, "obj", None):
		platform_name = ctx.obj.get("platform") or "zhipin"
	prefix = "boss" if platform_name == "zhipin" else f"boss --platform {platform_name}"
	return f"{prefix} {command}".strip()


def login_action_for_ctx(ctx: Any) -> str:
	"""Return the platform-aware login recovery command."""
	return boss_command_for_ctx(ctx, "login")


def error_contract_for_code(
	code: str,
	*,
	fallback_recoverable: bool = False,
	fallback_recovery_action: str | None = None,
) -> tuple[bool, str | None]:
	"""Return recoverability metadata for a known error code."""
	from boss_agent_cli.commands.schema import SCHEMA_DATA

	error_codes = SCHEMA_DATA.get("error_codes", {})
	spec = error_codes.get(code, {}) if isinstance(error_codes, dict) else {}
	if not isinstance(spec, dict):
		spec = {}
	return (
		bool(spec.get("recoverable", fallback_recoverable)),
		spec.get("recovery_action", fallback_recovery_action),
	)


def is_json_mode(ctx: Any) -> bool:
	"""Check if --json flag is set or stdout is piped (non-TTY)."""
	force_json = ctx.obj.get("json_output", False) if ctx and ctx.obj else False
	return force_json or not sys.stdout.isatty()


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
		console.print(f"[yellow]{prefix}[/yellow]{action}")


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
	recoverable: bool = False,
	recovery_action: str | None = None,
	details: dict[str, Any] | None = None,
	hints: dict[str, Any] | None = None,
) -> None:
	"""Smart error output: TTY -> rich error, pipe -> JSON error envelope."""
	from boss_agent_cli.output import emit_error

	if is_json_mode(ctx):
		emit_error(
			command, code=code, message=message,
			recoverable=recoverable, recovery_action=recovery_action,
			details=details,
			hints=hints,
		)
	else:
		console.print(f"[red]error[/red] [{code}] {message}")
		if recovery_action:
			console.print(f"  [dim]recovery: {recovery_action}[/dim]")
		render_operator_actions(hints)
		raise SystemExit(1)


def handle_not_supported(ctx: Any, command: str, exc: Exception, *, fallback_message: str) -> None:
	"""命令因平台不支持而抛 NotImplementedError 时的统一错误信封。"""
	_, recovery_action = error_contract_for_code("NOT_SUPPORTED")
	handle_error_output(
		ctx, command, code="NOT_SUPPORTED",
		message=str(exc) or fallback_message,
		recoverable=True, recovery_action=recovery_action,
	)


def handle_platform_error_output(
	ctx: Any,
	command: str,
	platform: Any,
	response: Any,
	*,
	fallback_message: str,
) -> None:
	"""Emit a schema-backed error envelope for an unsuccessful platform response."""
	code, message = platform.parse_error(response)
	recoverable, recovery_action = error_contract_for_code(code)
	details = None
	if isinstance(response, dict):
		error = response.get("error")
		if isinstance(error, dict):
			raw_details = error.get("details")
			if isinstance(raw_details, dict):
				details = raw_details
	handle_error_output(
		ctx,
		command,
		code=code,
		message=message or fallback_message,
		recoverable=recoverable,
		recovery_action=recovery_action,
		details=details,
	)


# ── Table builders ──────────────────────────────────────────────────


def render_job_table(
	items: list[dict[str, Any]],
	title: str,
	*,
	page: int = 1,
	hint_next: str = "",
) -> None:
	"""Render a list of jobs as a rich table."""
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
			job.get("title", job.get("jobName", "-")),
			job.get("company", job.get("brandName", "-")),
			job.get("salary", job.get("salaryDesc", "-")),
			job.get("employment_type") or "-",
			job.get("experience", job.get("jobExperience", "-")),
			job.get("education", job.get("jobDegree", "-")),
			job.get("city", job.get("cityName", "-")),
		)

	console.print(table)
	console.print("  [dim]use: boss show <#> to view details[/dim]")
	if hint_next:
		console.print(f"  [dim]{hint_next}[/dim]")


def render_job_detail(data: dict[str, Any], *, greet_command: str | None = None) -> None:
	"""Render job detail as a rich panel."""
	title = data.get("title", "-")
	salary = data.get("salary", "-")
	exp = data.get("experience", "-")
	edu = data.get("education", "-")
	city = data.get("city", "-")
	company = data.get("company", "-")
	boss = data.get("boss_name", "-")
	boss_title = data.get("boss_title", "-")
	desc = data.get("description", "")

	skills = data.get("skills", [])
	skill_str = ", ".join(skills) if skills else "-"

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
		text += f"\n[bold]description:[/bold]\n{desc}"

	panel = Panel(text, title="job detail", border_style="cyan")
	console.print(panel)

	sid = data.get("security_id", "")
	jid = data.get("job_id", "")
	if sid and jid:
		greet_command = greet_command or "boss greet <security_id> <job_id>"
		console.print(f"  [dim]next: {greet_command}[/dim]")


def render_status(data: dict[str, Any], *, login_action: str = "boss login") -> None:
	"""Render login status."""
	if data.get("logged_in"):
		name = data.get("user_name", "unknown")
		console.print(f"[green]logged in[/green] as [bold]{name}[/bold]")
	else:
		console.print(f"[yellow]not logged in[/yellow] - run: {login_action}")


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
			row.append(str(item.get(key, "-")))
		table.add_row(*row)

	console.print(table)


# ── Additional renderers ────────────────────────────────────────────


def render_message_panel(data: dict[str, Any], *, title: str = "result") -> None:
	"""Render a simple key-value result as a panel."""
	lines = []
	for k, v in data.items():
		lines.append(f"[bold]{k}:[/bold] {v}")
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
			table.add_row(item.get("title", "-"), item.get("company", "-"))
		console.print(table)
	if data.get("stopped_reason"):
		console.print(f"  [yellow]stopped: {data['stopped_reason']}[/yellow]")


def render_sectioned_record(data: dict[str, Any], *, title: str = "info") -> None:
	"""Render multi-section record (e.g., me command) as panels."""
	for section, content in data.items():
		if isinstance(content, dict):
			lines = []
			for k, v in content.items():
				if isinstance(v, (list, dict)):
					v = str(v)[:200]
				lines.append(f"[bold]{k}:[/bold] {v or '-'}")
			panel = Panel("\n".join(lines) if lines else "[dim]empty[/dim]", title=section, border_style="cyan")
			console.print(panel)
		else:
			console.print(f"[bold]{section}:[/bold] {content}")


def render_string_grid(items: list[str], title: str, *, columns: int = 4) -> None:
	"""Render a list of strings as a multi-column grid."""
	if not items:
		console.print(f"[yellow]no {title}[/yellow]")
		return

	table = Table(title=f"{title} ({len(items)})", show_header=False)
	for _ in range(columns):
		table.add_column(max_width=20)

	for i in range(0, len(items), columns):
		row = items[i:i + columns]
		while len(row) < columns:
			row.append("")
		table.add_row(*row)

	console.print(table)


def render_export_summary(data: dict[str, Any]) -> None:
	"""Render export result summary."""
	path = data.get("path", "")
	count = data.get("count", 0)
	fmt = data.get("format", "")
	if path:
		console.print(f"[green]exported[/green] {count} jobs to [bold]{path}[/bold] ({fmt})")
	else:
		console.print(f"[green]exported[/green] {count} jobs ({fmt})")


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
		console.print(f"    [dim]{action}[/dim]")


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
			from boss_agent_cli.api.browser_client import RecruiterChatTabRequired
			from boss_agent_cli.api.client import AccountRiskError
			from boss_agent_cli.auth.manager import AuthRequired, TokenRefreshFailed
			try:
				return func(ctx, *args, **kwargs)
			except RecruiterChatTabRequired as e:
				handle_error_output(
					ctx, command_name, code="RECRUITER_CHAT_TAB_REQUIRED",
					message=str(e),
					recoverable=True,
					recovery_action="回到 BOSS 直聘官方招聘者页面手动处理",
					hints={"next_actions": [
						"在 BOSS 直聘官方页面确认候选人和沟通上下文",
						"保持 CLI 默认低风险模式，不通过自动化链路发送消息或请求简历",
					]},
				)
			except AuthRequired:
				login_action = login_action_for_ctx(ctx)
				handle_error_output(
					ctx, command_name, code="AUTH_REQUIRED",
					message=f"未登录，请先执行 {login_action}",
					recoverable=True, recovery_action=login_action,
				)
			except TokenRefreshFailed:
				login_action = login_action_for_ctx(ctx)
				handle_error_output(
					ctx, command_name, code="TOKEN_REFRESH_FAILED",
					message="Token 刷新失败，请重新登录",
					recoverable=True, recovery_action=login_action,
				)
			except AccountRiskError as e:
				handle_error_output(
					ctx, command_name, code="ACCOUNT_RISK",
					message=str(e),
					recoverable=False,
					recovery_action="停止自动化访问；回到 BOSS 直聘官方页面手动处理，必要时联系 BOSS 直聘客服",
					hints={"next_actions": [
						"不要通过 CDP、patchright 或 Bridge 重试该操作",
						"只保留本地辅助和用户主动触发的只读命令",
					]},
				)
			except Exception as e:
				handle_error_output(
					ctx, command_name, code="NETWORK_ERROR",
					message=f"{command_name} 失败: {e}",
					recoverable=True, recovery_action="重试",
				)
		return wrapper
	return decorator
