"""Rich terminal rendering + TTY-aware output routing.

TTY mode: Rich tables/panels to stderr, nothing to stdout.
Pipe mode (Agent): JSON envelope to stdout.
--json flag: Force JSON to stdout even in TTY.
"""

import sys
from typing import Any, Callable

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from boss_agent_cli.output import emit_success

# Rich output goes to stderr so stdout stays clean for Agent JSON
console = Console(stderr=True)


def is_json_mode(ctx) -> bool:
	"""Check if --json flag is set or stdout is piped (non-TTY)."""
	force_json = ctx.obj.get("json_output", False) if ctx and ctx.obj else False
	return force_json or not sys.stdout.isatty()


def handle_output(
	ctx,
	command: str,
	data: Any,
	*,
	render: Callable[[Any], None] | None = None,
	pagination: dict | None = None,
	hints: dict | None = None,
) -> None:
	"""Smart output: TTY -> rich render, pipe -> JSON envelope."""
	if is_json_mode(ctx):
		emit_success(command, data, pagination=pagination, hints=hints)
	elif render:
		render(data)
	else:
		# Fallback: no render function, emit JSON even in TTY
		emit_success(command, data, pagination=pagination, hints=hints)


def handle_error_output(
	ctx,
	command: str,
	*,
	code: str,
	message: str,
	recoverable: bool = False,
	recovery_action: str | None = None,
	hints: dict | None = None,
) -> None:
	"""Smart error output: TTY -> rich error, pipe -> JSON error envelope."""
	from boss_agent_cli.output import emit_error

	if is_json_mode(ctx):
		emit_error(
			command, code=code, message=message,
			recoverable=recoverable, recovery_action=recovery_action,
			hints=hints,
		)
	else:
		console.print(f"[red]error[/red] [{code}] {message}")
		if recovery_action:
			console.print(f"  [dim]recovery: {recovery_action}[/dim]")
		raise SystemExit(1)


# ── Table builders ──────────────────────────────────────────────────


def render_job_table(
	items: list[dict],
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
	table.add_column("exp", max_width=10)
	table.add_column("edu", max_width=8)
	table.add_column("city", style="blue", max_width=12)

	for i, job in enumerate(items, 1):
		table.add_row(
			str(i),
			job.get("title", job.get("jobName", "-")),
			job.get("company", job.get("brandName", "-")),
			job.get("salary", job.get("salaryDesc", "-")),
			job.get("experience", job.get("jobExperience", "-")),
			job.get("education", job.get("jobDegree", "-")),
			job.get("city", job.get("cityName", "-")),
		)

	console.print(table)
	console.print("  [dim]use: boss show <#> to view details[/dim]")
	if hint_next:
		console.print(f"  [dim]{hint_next}[/dim]")


def render_job_detail(data: dict) -> None:
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
		console.print(f"  [dim]greet: boss greet {sid} {jid}[/dim]")


def render_status(data: dict) -> None:
	"""Render login status."""
	if data.get("logged_in"):
		name = data.get("user_name", "unknown")
		console.print(f"[green]logged in[/green] as [bold]{name}[/bold]")
	else:
		console.print("[yellow]not logged in[/yellow] - run: boss login")


def render_simple_list(
	items: list[dict],
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


def render_message_panel(data: dict, *, title: str = "result") -> None:
	"""Render a simple key-value result as a panel."""
	lines = []
	for k, v in data.items():
		lines.append(f"[bold]{k}:[/bold] {v}")
	panel = Panel("\n".join(lines), title=title, border_style="green")
	console.print(panel)


def render_batch_operation_summary(data: dict, *, title: str = "batch result") -> None:
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


def render_sectioned_record(data: dict, *, title: str = "info") -> None:
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


def render_export_summary(data: dict) -> None:
	"""Render export result summary."""
	path = data.get("path", "")
	count = data.get("count", 0)
	fmt = data.get("format", "")
	if path:
		console.print(f"[green]exported[/green] {count} jobs to [bold]{path}[/bold] ({fmt})")
	else:
		console.print(f"[green]exported[/green] {count} jobs ({fmt})")


# ── Auth error decorator ─────────────────────────────────────────────


def handle_auth_errors(command_name: str):
	"""装饰器：统一处理 AuthRequired / TokenRefreshFailed / Exception 三层捕获。

	用法:
		@handle_auth_errors("search")
		def _search_impl(ctx, ...):
			...  # 只写业务逻辑，不需要 try/except
	"""
	from functools import wraps

	def decorator(func):
		@wraps(func)
		def wrapper(ctx, *args, **kwargs):
			from boss_agent_cli.api.client import AccountRiskError
			from boss_agent_cli.auth.manager import AuthRequired, TokenRefreshFailed
			try:
				return func(ctx, *args, **kwargs)
			except AuthRequired:
				handle_error_output(
					ctx, command_name, code="AUTH_REQUIRED",
					message="未登录，请先执行 boss login",
					recoverable=True, recovery_action="boss login",
				)
			except TokenRefreshFailed:
				handle_error_output(
					ctx, command_name, code="TOKEN_REFRESH_FAILED",
					message="Token 刷新失败，请重新登录",
					recoverable=True, recovery_action="boss login",
				)
			except AccountRiskError as e:
				recovery = (
					"以 --remote-debugging-port=9222 启动 Chrome，然后重试（CDP 模式）"
					if not e.is_cdp
					else "联系 BOSS 直聘客服解除风控限制"
				)
				handle_error_output(
					ctx, command_name, code="ACCOUNT_RISK",
					message=str(e),
					recoverable=not e.is_cdp,
					recovery_action=recovery,
					hints={"next_actions": [
						"boss-chrome  # 启动带调试端口的 Chrome",
						"boss search <query>  # CDP 模式自动生效",
					]} if not e.is_cdp else None,
				)
			except Exception as e:
				handle_error_output(
					ctx, command_name, code="NETWORK_ERROR",
					message=f"{command_name} 失败: {e}",
					recoverable=True, recovery_action="重试",
				)
		return wrapper
	return decorator
