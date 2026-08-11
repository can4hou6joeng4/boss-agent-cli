"""向导入口的登录门：在任何目标选择之前解决登录态。

为什么放在这一层：`auth_status` 是每个 goal 的第一个 step，而 step 只在
`collect_wizard_input` 走完之后才执行——用户要先选身份、平台、目标分类、
目标并填完参数（共 5 层），才会被告知未登录，之前的选择全部白做。

为什么 TTY 下内联阻塞等扫码是对的：headless / Agent 路径的「不阻塞」原则
（见 wizard/actions.py 的 _auth_status）是为了避免 Agent 卡在子进程里空等。
TTY 恰恰相反——人就坐在终端前，他要的就是「给我个二维码我现在扫」。
两种受众的正确行为相反，不要把 headless 的原则套到这里。
"""

from __future__ import annotations

from typing import Any

from rich.panel import Panel

from boss_agent_cli import display
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.wizard.catalog import GOALS

# 登录门的三种出口。
GATE_READY = "ready"
GATE_LOCAL_ONLY = "local_only"
GATE_EXIT = "exit"

_LOGIN_TIMEOUT_SECONDS = 180


def local_goal_names(role: str) -> tuple[str, ...]:
	"""不需要登录态的 goal（步骤里不含 auth_status）。"""
	return tuple(name for name, goal in GOALS.get(role, {}).items() if "auth_status" not in goal.steps)


def _browser_kernel_status() -> tuple[str, str]:
	"""复用 doctor 的浏览器内核判定，返回 (status, detail)。

	只在真要开浏览器之前调用——已登录用户不该为此付出任何启动开销。
	"""
	from boss_agent_cli.commands.doctor_checks import (
		_evaluate_patchright_chromium,
		_patchright_browser_cache_dirs,
		_patchright_chromium_revision,
	)

	try:
		return _evaluate_patchright_chromium(
			_patchright_chromium_revision(),
			_patchright_browser_cache_dirs(),
		)
	except Exception as exc:  # 预检本身不该阻断登录，退化成 warn
		return "warn", f"无法确认浏览器内核状态：{exc}"


def _render_health_card(platform_name: str) -> None:
	lines = [
		"[yellow]✗[/yellow] 未登录 —— 大部分能力需要登录后才能使用",
		f"[dim]平台：{platform_name}[/dim]",
	]
	display.console.print(Panel("\n".join(lines), title="登录态检查", border_style="yellow"))


def _render_kernel_blocked(detail: str) -> None:
	display.console.print(
		Panel(
			"\n".join(
				[
					"[red]无法自动打开浏览器[/red]：缺少 patchright 所需的 Chromium 内核。",
					"",
					detail,
					"",
					"[bold]安装完成后回到这里重新选择「现在登录」即可。[/bold]",
				]
			),
			title="环境不支持自动扫码",
			border_style="red",
		)
	)


def _render_login_failure(ctx: Any, exc: Exception) -> None:
	"""复用 login 命令的错误分类，把双通道文案渲染给真人。"""
	from boss_agent_cli.commands.login import _classify_login_error

	payload = _classify_login_error(exc, ctx)
	raw_hints = payload.get("hints")
	hints: dict[str, Any] = raw_hints if isinstance(raw_hints, dict) else {}
	lines = [f"[red]{payload.get('message')}[/red]"]
	operator_actions = hints.get("operator_actions") or []
	if operator_actions:
		lines.append("")
		lines.extend(f"· {action}" for action in operator_actions)
	display.console.print(Panel("\n".join(lines), title="登录失败", border_style="red"))


def _gate_options(role: str) -> list[Any]:
	from boss_agent_cli.wizard.prompts import MenuOption

	options = [MenuOption("login", "现在登录", "打开浏览器扫码，登录后继续")]
	if local_goal_names(role):
		options.append(MenuOption("local", "仅看本地功能", "简历、候选池、采集任务等无需登录的能力"))
	options.append(MenuOption("exit", "退出", ""))
	return options


def ensure_login(ctx: Any, *, menu: Any = None, role: str | None = None) -> str:
	"""向导入口登录门。返回 GATE_READY / GATE_LOCAL_ONLY / GATE_EXIT。

	已登录时零输出、零开销直接返回 GATE_READY——老用户感知不到这一层。

	role 默认从 ctx.obj 派生，避免调用方漏传导致按错误身份判断本地能力。
	"""
	from boss_agent_cli.wizard.prompts import WizardCancelled, _menu_driver

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	platform_name = ctx.obj.get("platform") or "zhipin"
	active_role = role or ctx.obj.get("role") or "candidate"
	auth = AuthManager(data_dir, logger=logger, platform=platform_name)

	if auth.check_status() is not None:
		return GATE_READY

	driver = menu or _menu_driver()
	_render_health_card(platform_name)

	while True:
		try:
			choice = driver.select(
				"需要先登录才能继续，现在登录吗？",
				_gate_options(active_role),
				default="login",
				allow_back=False,
			)
		except WizardCancelled:
			return GATE_EXIT

		if choice == "exit":
			return GATE_EXIT

		if choice == "local":
			if not local_goal_names(active_role):
				display.console.print(
					"[yellow]当前身份的所有能力都需要登录，没有可离线使用的功能。[/yellow]"
				)
				continue
			return GATE_LOCAL_ONLY

		# choice == "login"：先确认环境能开浏览器，再真开。
		status, detail = _browser_kernel_status()
		if status == "error":
			_render_kernel_blocked(detail)
			continue

		try:
			from boss_agent_cli.wizard.renderer import busy_status

			with busy_status("正在打开浏览器，请在弹出的窗口中扫码登录…"):
				auth.login(timeout=_LOGIN_TIMEOUT_SECONDS, cdp_url=ctx.obj.get("cdp_url"))
		except Exception as exc:
			_render_login_failure(ctx, exc)
			continue

		display.console.print("[green]✓[/green] 登录成功，继续")
		return GATE_READY
