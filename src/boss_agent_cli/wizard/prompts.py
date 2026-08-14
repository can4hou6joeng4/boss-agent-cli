"""TTY-only Chinese wizard input collection."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, cast

import click

from boss_agent_cli.wizard.catalog import GOALS, catalog_data
from boss_agent_cli.wizard.models import WizardInput, WorkflowInputError


@dataclass(frozen=True)
class MenuOption:
	value: str
	label: str
	description: str = ""
	# item = content choice; nav = paging/back; danger = exit/stop-like
	kind: str = "item"


@dataclass(frozen=True)
class WizardControl:
	action: str
	run_id: str | None = None


class WizardCancelled(Exception):
	"""Raised when the human explicitly exits the wizard."""


class WizardBack(Exception):
	"""Raised when the human requests the preceding menu."""


class WizardReturnHome(Exception):
	"""Raised when the human asks to return to the top-level main menu."""


class MenuDriver(Protocol):
	def select(
		self,
		title: str,
		options: Sequence[MenuOption],
		*,
		default: str | None = None,
		allow_back: bool = True,
		allow_exit: bool = True,
		clear_before: bool = True,
	) -> str: ...

	def text(self, label: str, *, default: str = "", required: bool = True) -> str: ...


_BACK = "__wizard_back__"
_EXIT = "__wizard_exit__"


def _is_nav_option(item: MenuOption | Any) -> bool:
	"""True for paging / back / exit — rendered apart from content choices."""
	kind = getattr(item, "kind", "item") or "item"
	if kind in {"nav", "danger"}:
		return True
	value = str(getattr(item, "value", "") or "")
	if value in {_BACK, _EXIT}:
		return True
	# Internal control values (result paging, etc.) use __ prefixes.
	return value.startswith("__")


def _display_width(text: str) -> int:
	"""Terminal cell width: CJK / fullwidth chars count as 2."""
	import unicodedata

	width = 0
	for char in text:
		if unicodedata.east_asian_width(char) in {"F", "W"}:
			width += 2
		elif unicodedata.category(char) in {"Mn", "Me", "Cf"}:
			continue
		else:
			width += 1
	return width


def _pad_label(label: str, target_width: int) -> str:
	"""Right-pad label so trailing descriptions share one left column."""
	pad = max(0, target_width - _display_width(label))
	return f"{label}{' ' * pad}"


def _menu_label_widths(items: Sequence[MenuOption | Any]) -> tuple[int, int]:
	"""Return (content_label_width, nav_label_width) for description alignment."""
	content_w = 0
	nav_w = 0
	for item in items:
		label = str(getattr(item, "label", "") or "")
		w = _display_width(label)
		if _is_nav_option(item):
			nav_w = max(nav_w, w)
		else:
			content_w = max(content_w, w)
	# Soft caps keep long job titles from pushing descriptions off-screen.
	return min(content_w, 36), min(nav_w, 28)


def _format_menu_description(description: str, *, limit: int = 40) -> str:
	desc = " ".join(str(description or "").split())
	if len(desc) > limit:
		return desc[: limit - 1] + "…"
	return desc


def _augment_menu_options(
	options: Sequence[MenuOption],
	*,
	allow_back: bool,
	allow_exit: bool,
) -> list[MenuOption]:
	"""Append back/exit nav items without duplicating an explicit exit choice."""
	items = list(options)
	if allow_back and not any(item.value == _BACK for item in items):
		items.append(MenuOption(_BACK, "返回上一步", kind="nav"))
	if allow_exit and not any(item.value in {_EXIT, "exit"} or item.label.strip() == "退出向导" for item in items):
		items.append(MenuOption(_EXIT, "退出向导", kind="danger"))
	return items


def _default_select_index(items: Sequence[MenuOption], default: str | None) -> int:
	"""Prefer explicit default; never fall onto trailing exit when content exists."""
	if default is not None:
		for index, item in enumerate(items):
			if item.value == default:
				return index
	for index, item in enumerate(items):
		if not _is_nav_option(item):
			return index
	return 0


ROLE_LABELS = {
	"candidate": "求职者",
	"recruiter": "招聘者",
}

PLATFORM_LABELS = {
	"zhipin": "BOSS 直聘",
	"zhilian": "智联招聘",
	"qiancheng": "前程无忧",
	"51job": "前程无忧",
}

STATUS_LABELS = {
	"pending": "等待开始",
	"running": "正在运行",
	"waiting_input": "等待补充信息",
	"completed": "已完成",
	"failed": "执行失败",
	"stopped": "已停止",
}

INPUT_LABELS = {
	"query": "关键词",
	"city": "城市",
	"security_id": "职位或联系人安全编号",
	"job_id": "职位编号",
	"gid": "联系人编号",
	"geek_id": "候选人编号",
	"friend_id": "沟通联系人编号",
	"message": "消息内容",
	"label": "联系人标签",
	"resume": "本地简历名称",
	"prompt": "希望 AI 完成的任务",
	"run_id": "任务编号",
	"name": "监控名称",
	"welfare": "福利条件（多个条件用逗号分隔）",
}

GOAL_GROUPS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
	"candidate": (
		("找职位", ("job_search", "recommendations", "job_detail", "shortlist", "export")),
		("投递与沟通", ("apply", "greet", "exchange", "mark", "communication", "chat_history")),
		("求职管理", ("pipeline", "digest", "resumes", "ai_assist", "watch")),
		("采集任务", ("crawl_start", "crawl_status", "crawl_resume", "crawl_stop")),
	),
	"recruiter": (
		("候选人", ("candidates", "applications", "candidate_resume")),
		("沟通", ("communication", "last_messages", "chat_history", "reply", "exchange_contact", "request_resume")),
		("职位管理", ("jobs_list", "jobs_detail", "jobs_online", "jobs_offline")),
	),
}

# Secondary explanations for menus (same-line, column-aligned with labels).
GOAL_GROUP_HINTS: dict[str, str] = {
	"找职位": "搜索、推荐、详情与导出",
	"投递与沟通": "打招呼、投递、沟通与标记",
	"求职管理": "进度、日报、简历、AI 与监控",
	"采集任务": "可恢复长任务采集",
	"候选人": "搜索候选人与投递申请",
	"沟通": "消息、回复与联系方式",
	"职位管理": "职位上下线与详情",
}

GOAL_HINTS: dict[str, str] = {
	"job_search": "按关键词筛选职位列表",
	"recommendations": "读取平台个性化推荐",
	"job_detail": "查看单个职位完整信息",
	"shortlist": "浏览本地收藏的职位",
	"export": "导出搜索结果到文件",
	"apply": "发起投递或立即沟通",
	"greet": "向招聘者发送招呼",
	"exchange": "请求手机号或微信",
	"mark": "更新沟通联系人标签",
	"communication": "查看最近沟通列表",
	"chat_history": "读取与对方的聊天记录",
	"pipeline": "汇总沟通与面试进度",
	"digest": "生成今日求职摘要",
	"resumes": "查看本地已保存简历",
	"ai_assist": "基于本地简历做 AI 分析",
	"watch": "保存或运行职位监控",
	"crawl_start": "启动浏览器采集任务",
	"crawl_status": "查看采集进度与产物",
	"crawl_resume": "从断点继续采集",
	"crawl_stop": "请求停止进行中的采集",
	"candidates": "按条件搜索候选人",
	"applications": "查看收到的投递",
	"candidate_resume": "读取候选人简历",
	"last_messages": "批量查看最近消息",
	"reply": "回复候选人消息",
	"exchange_contact": "请求候选人联系方式",
	"request_resume": "请求附件简历",
	"jobs_list": "查看已发布职位",
	"jobs_detail": "查看单个职位详情",
	"jobs_online": "上线指定职位",
	"jobs_offline": "下线指定职位",
}


def _advanced_terminal_available() -> bool:
	if os.environ.get("PYTEST_CURRENT_TEST"):
		return False
	if os.environ.get("TERM", "").lower() in {"", "dumb", "unknown"}:
		return False
	if not (sys.stdin.isatty() and sys.stderr.isatty()):
		return False
	try:
		import prompt_toolkit  # noqa: F401
	except (ImportError, OSError):
		return False
	return True


def _menu_content_lines(
	items: Sequence[MenuOption],
	*,
	selected: int,
	title: str,
) -> list[tuple[str, str]]:
	# Same-line description, left-aligned as a column after padded labels:
	#   ○  浏览职位列表          ·  选择职位查看详情或打招呼
	#   ○  返回主菜单            ·  稍后再处理
	content_label_w, nav_label_w = _menu_label_widths(items)
	lines: list[tuple[str, str]] = [
		("class:hint", "↑↓ 选择  ·  Enter 确认  ·  ←/Esc 返回\n\n"),
	]
	saw_nav = False
	for index, item in enumerate(items):
		is_nav = _is_nav_option(item)
		if is_nav and not saw_nav:
			# Separate content choices from paging / exit controls.
			lines.append(("class:sep", "  ────────────────────────\n"))
			saw_nav = True
		active = index == selected
		desc = _format_menu_description(item.description)
		if is_nav:
			marker = "▸" if active else " "
			style = "class:nav-selected" if active else "class:nav"
			label = _pad_label(item.label, nav_label_w) if desc else item.label
			# Indent nav under content; explanations share one left edge.
			lines.append((style, f"    {marker} {label}"))
			if desc:
				lines.append(("class:nav-desc", f"  ·  {desc}"))
			lines.append(("", "\n"))
		else:
			marker = "●" if active else "○"
			style = "class:selected" if active else "class:item"
			prefix = "▌ " if active else "  "
			label = _pad_label(item.label, content_label_w) if desc else item.label
			# Only pad when any peer has a description; still left-align the "·" column.
			if not desc and any(
				(getattr(peer, "description", None) and not _is_nav_option(peer)) for peer in items
			):
				label = _pad_label(item.label, content_label_w)
			lines.append((style, f"{prefix}{marker}  {label}"))
			if desc:
				lines.append(("class:description", f"  ·  {desc}"))
			lines.append(("", "\n"))
	return lines


def _build_menu_container(title: str, control: Any) -> Any:
	"""把菜单内容包进带标题的边框（D1：标题用当前步骤）。

	抽成模块级函数是为了可测：_advanced_terminal_available() 在 pytest 下恒 False，
	select() 整条 prompt_toolkit 分支跑不到，只能直接断言这里返回的结构。
	"""
	from prompt_toolkit.layout.containers import HSplit, Window
	from prompt_toolkit.widgets import Frame

	return Frame(HSplit([Window(control)]), title=title, style="class:frame")


class PromptToolkitMenu:
	"""Arrow-key menu whose input and rendering are bound to stderr."""

	def select(
		self,
		title: str,
		options: Sequence[MenuOption],
		*,
		default: str | None = None,
		allow_back: bool = True,
		allow_exit: bool = True,
		clear_before: bool = True,
	) -> str:
		from prompt_toolkit.application import Application
		from prompt_toolkit.input.defaults import create_input
		from prompt_toolkit.key_binding import KeyBindings
		from prompt_toolkit.layout import Layout
		from prompt_toolkit.layout.controls import FormattedTextControl
		from prompt_toolkit.output.defaults import create_output
		from prompt_toolkit.styles import Style

		from boss_agent_cli.wizard.renderer import clear_wizard_screen

		if clear_before:
			clear_wizard_screen()
		items = _augment_menu_options(options, allow_back=allow_back, allow_exit=allow_exit)
		selected = _default_select_index(items, default)
		content_label_w, nav_label_w = _menu_label_widths(items)

		def content() -> list[tuple[str, str]]:
			return _menu_content_lines(items, selected=selected, title=title)

		control = FormattedTextControl(cast(Any, content), focusable=True)
		bindings = KeyBindings()

		@bindings.add("up")
		@bindings.add("k")
		def move_up(event: Any) -> None:
			nonlocal selected
			selected = (selected - 1) % len(items)
			event.app.invalidate()

		@bindings.add("down")
		@bindings.add("j")
		def move_down(event: Any) -> None:
			nonlocal selected
			selected = (selected + 1) % len(items)
			event.app.invalidate()

		@bindings.add("enter")
		def accept(event: Any) -> None:
			event.app.exit(result=items[selected].value)

		@bindings.add("escape")
		@bindings.add("left")
		def go_back(event: Any) -> None:
			event.app.exit(result=_BACK if allow_back else _EXIT)

		@bindings.add("c-c")
		@bindings.add("q")
		def cancel(event: Any) -> None:
			event.app.exit(result=_EXIT)

		application: Application[str] = Application(
			layout=Layout(_build_menu_container(title, control)),
			key_bindings=bindings,
			style=Style.from_dict(
				{
					"title": "bold #00a6a6",
					"frame": "#00a6a6",
					"hint": "#6b7280",
					"selected": "bold reverse #00a6a6",
					"item": "",
					"description": "#888888",
					"sep": "#4b5563",
					"nav": "#6b7280",
					"nav-selected": "bold reverse #6b7280",
					"nav-desc": "#6b7280",
				}
			),
			full_screen=False,
			input=create_input(stdin=sys.stdin),
			output=create_output(stdout=sys.stderr),
		)
		try:
			value = application.run()
		except (KeyboardInterrupt, EOFError) as exc:
			# 与 Click 回退路径一致：Ctrl-C / EOF 统一退出向导，不用作“返回上一步”。
			raise WizardCancelled from exc
		return _resolve_navigation(value)

	def text(self, label: str, *, default: str = "", required: bool = True) -> str:
		from prompt_toolkit import PromptSession
		from prompt_toolkit.input.defaults import create_input
		from prompt_toolkit.output.defaults import create_output

		session: PromptSession[str] = PromptSession(
			input=create_input(stdin=sys.stdin),
			output=create_output(stdout=sys.stderr),
		)
		while True:
			try:
				value = session.prompt(f"{label}: ", default=default).strip()
			except (KeyboardInterrupt, EOFError) as exc:
				raise WizardCancelled from exc
			if value or not required:
				return value


class ClickMenu:
	"""Portable numbered fallback for limited terminals and test input."""

	def select(
		self,
		title: str,
		options: Sequence[MenuOption],
		*,
		default: str | None = None,
		allow_back: bool = True,
		allow_exit: bool = True,
		clear_before: bool = True,
	) -> str:
		from boss_agent_cli.wizard.renderer import clear_wizard_screen

		if clear_before:
			clear_wizard_screen()
		items = _augment_menu_options(options, allow_back=allow_back, allow_exit=allow_exit)
		content_label_w, nav_label_w = _menu_label_widths(items)
		# Number prefix width: "  12. " / "    3. "
		click.echo(f"\n{title}", err=True)
		click.echo("  （输入序号 · Enter 确认）", err=True)
		saw_nav = False
		for index, item in enumerate(items, start=1):
			if _is_nav_option(item) and not saw_nav:
				click.echo("  ────────────", err=True)
				saw_nav = True
			is_nav = _is_nav_option(item)
			indent = "    " if is_nav else "  "
			desc = _format_menu_description(item.description)
			label_w = nav_label_w if is_nav else content_label_w
			label = _pad_label(item.label, label_w) if desc else item.label
			if not desc and not is_nav and any(
				(getattr(peer, "description", None) and not _is_nav_option(peer)) for peer in items
			):
				label = _pad_label(item.label, label_w)
			detail = f"  ·  {desc}" if desc else ""
			click.echo(f"{indent}{index}. {label}{detail}", err=True)
		default_index = _default_select_index(items, default) + 1
		while True:
			try:
				raw = str(click.prompt("请输入序号", default=str(default_index), err=True)).strip()
			except click.Abort as exc:
				raise WizardCancelled from exc
			if raw.isdigit() and 1 <= int(raw) <= len(items):
				return _resolve_navigation(items[int(raw) - 1].value)
			click.echo(f"请输入 1 到 {len(items)} 之间的序号。", err=True)

	def text(self, label: str, *, default: str = "", required: bool = True) -> str:
		while True:
			try:
				value = str(click.prompt(label, default=default, show_default=bool(default), err=True)).strip()
			except click.Abort as exc:
				raise WizardCancelled from exc
			if value or not required:
				return value


class ResilientMenu:
	"""Use prompt_toolkit when possible and fall back if terminal setup fails."""

	def __init__(self) -> None:
		self._advanced: MenuDriver = PromptToolkitMenu()
		self._fallback: MenuDriver = ClickMenu()
		self._using_fallback = False

	def select(
		self,
		title: str,
		options: Sequence[MenuOption],
		*,
		default: str | None = None,
		allow_back: bool = True,
		allow_exit: bool = True,
		clear_before: bool = True,
	) -> str:
		if not self._using_fallback:
			try:
				return self._advanced.select(
					title,
					options,
					default=default,
					allow_back=allow_back,
					allow_exit=allow_exit,
					clear_before=clear_before,
				)
			except (ImportError, OSError, RuntimeError):
				self._using_fallback = True
		return self._fallback.select(
			title,
			options,
			default=default,
			allow_back=allow_back,
			allow_exit=allow_exit,
			clear_before=clear_before,
		)

	def text(self, label: str, *, default: str = "", required: bool = True) -> str:
		if not self._using_fallback:
			try:
				return self._advanced.text(label, default=default, required=required)
			except (ImportError, OSError, RuntimeError):
				self._using_fallback = True
		return self._fallback.text(label, default=default, required=required)


def _resolve_navigation(value: str) -> str:
	if value == _BACK:
		raise WizardBack
	if value == _EXIT:
		raise WizardCancelled
	return value


def _menu_driver() -> MenuDriver:
	return ResilientMenu() if _advanced_terminal_available() else ClickMenu()


def _collect_control(
	menu: MenuDriver,
	action: str,
	available_runs: Sequence[Mapping[str, Any]],
) -> WizardControl:
	allowed_statuses = {
		"resume": {"pending", "running", "waiting_input", "failed"},
		"retry": {"failed"},
		"status": set(STATUS_LABELS),
		"stop": {"pending", "running", "waiting_input"},
	}[action]
	runs = [run for run in available_runs if str(run.get("status")) in allowed_statuses]
	# Status view: also include waiting shells that already have a completed live crawl.
	if action == "status":
		seen = {str(run.get("run_id")) for run in runs}
		extra = [
			run
			for run in available_runs
			if str(run.get("run_id")) not in seen
			and (run.get("effective_completed") or int(run.get("live_jobs_seen") or 0) > 0)
		]
		runs = list(runs) + extra
	if not runs:
		return WizardControl(action=action, run_id=menu.text("任务编号"))
	options = []
	for index, run in enumerate(runs, start=1):
		role = str(run.get("role") or "")
		goal = str(run.get("goal") or "")
		definition = GOALS.get(role, {}).get(goal)
		goal_text = definition.description if definition is not None else "自定义事项"
		raw_status = str(run.get("status") or "")
		if run.get("effective_completed") or (
			raw_status == "waiting_input" and str(run.get("live_crawl_status")) == "completed"
		):
			status_text = "已完成"
		else:
			status_text = STATUS_LABELS.get(raw_status, "状态未知")
		platform_text = PLATFORM_LABELS.get(str(run.get("platform") or ""), "其他招聘平台")
		jobs = run.get("live_jobs_seen")
		if jobs is None:
			last = run.get("last_result") or {}
			data = last.get("data") if isinstance(last, Mapping) else {}
			if isinstance(data, Mapping) and data.get("jobs_seen") is not None:
				jobs = data.get("jobs_seen")
		# 主标签给人看：事项 + 状态 + 职位数；编号放次要描述，避免满屏 wrn_。
		label = f"{index}. {goal_text} · {status_text}"
		if jobs not in (None, "", 0):
			label = f"{label} · {jobs} 个职位"
		elif jobs == 0 and goal.startswith("crawl"):
			label = f"{label} · 暂无职位"
		description = f"{platform_text} · 编号 {run['run_id']}"
		inner = None
		last = run.get("last_result") or {}
		if isinstance(last, Mapping):
			data = last.get("data") if isinstance(last.get("data"), Mapping) else last
			if isinstance(data, Mapping) and data.get("run_id"):
				inner = data.get("run_id")
		if inner:
			description = f"{description} · 采集 {inner}"
		options.append(MenuOption(str(run["run_id"]), label, description))
	run_id = menu.select("请选择任务", options, default=options[0].value, allow_back=True)
	return WizardControl(action=action, run_id=run_id)


def _visible_goal_groups(
	role: str, *, local_only: bool = False
) -> tuple[tuple[str, tuple[str, ...]], ...]:
	"""goal 分组；local_only 时只保留无需登录（步骤不含 auth_status）的目标。"""
	groups = GOAL_GROUPS.get(role, ())
	if not local_only:
		return groups
	from boss_agent_cli.wizard.preflight import local_goal_names

	allowed = set(local_goal_names(role))
	filtered = []
	for name, goals in groups:
		kept = tuple(goal for goal in goals if goal in allowed)
		if kept:
			filtered.append((name, kept))
	return tuple(filtered)


def _collect_new_plan(
	menu: MenuDriver,
	*,
	default_role: str,
	default_platform: str,
	data_dir: Path | None = None,
	local_only: bool = False,
) -> WizardInput:
	role: str | None = None
	platform: str | None = None
	group_name: str | None = None
	while True:
		try:
			if role is None:
				role = menu.select(
					"请选择使用身份",
					[MenuOption(value, f"我是{label}") for value, label in ROLE_LABELS.items()],
					default=default_role,
					allow_back=True,
				)
			if platform is None:
				platforms = catalog_data()["roles"][role]["platforms"]
				platform = menu.select(
					"请选择招聘平台",
					[MenuOption(value, PLATFORM_LABELS.get(value, value)) for value in platforms],
					default=default_platform if default_platform in platforms else platforms[0],
				)
			groups = _visible_goal_groups(role, local_only=local_only)
			if not groups:
				raise WorkflowInputError(f"{ROLE_LABELS.get(role, role)}的所有能力都需要登录")
			if group_name is None:
				group_name = menu.select(
					"请选择目标分类",
					[
						MenuOption(name, name, GOAL_GROUP_HINTS.get(name, ""))
						for name, _ in groups
					],
				)
			goal_names = next(goals for name, goals in groups if name == group_name)
			goal = menu.select(
				"请选择本次要完成的事项",
				[
					MenuOption(
						name,
						GOALS[role][name].description,
						GOAL_HINTS.get(name, ""),
					)
					for name in goal_names
				],
			)
			inputs = _collect_inputs(menu, role, goal, data_dir=data_dir)
			return WizardInput(role=role, platform=platform, goal=goal, inputs=inputs, mode="tty")
		except WizardBack:
			if group_name is not None:
				group_name = None
			elif platform is not None:
				platform = None
			elif role is not None:
				role = None
			else:
				raise


def _collect_inputs(
	menu: MenuDriver,
	role: str,
	goal: str,
	*,
	data_dir: Path | None = None,
) -> dict[str, Any]:
	inputs: dict[str, Any] = {}
	# AI 辅助：优先从本地简历列表选择，避免空输入导致“看起来没效果”。
	if role == "candidate" and goal == "ai_assist":
		from boss_agent_cli.resume.store import ResumeStore

		root = data_dir or Path(os.environ.get("BOSS_DATA_DIR") or (Path.home() / ".boss-agent"))
		names = [str(item.get("name") or "") for item in ResumeStore(root / "resumes").list_all()]
		names = [name for name in names if name]
		if names:
			inputs["resume"] = menu.select(
				"请选择本地简历",
				[MenuOption(name, name, "用于 AI 分析的简历") for name in names],
				default=names[0],
			)
		else:
			inputs["resume"] = menu.text(
				"本地简历名称（当前无简历，可先用 boss resume 导入）",
				required=True,
			)
		inputs["prompt"] = menu.text("希望 AI 完成的任务", required=True)
		return inputs

	for name in GOALS[role][goal].required_inputs:
		inputs[name] = menu.text(INPUT_LABELS.get(name, "必要信息"))
	if goal in {"job_search", "candidates"}:
		city = menu.text("城市（可留空）", required=False)
		if city:
			inputs["city"] = city
	if role == "candidate" and goal in {"job_search", "export"}:
		welfare = menu.text(INPUT_LABELS["welfare"], required=False)
		if welfare:
			inputs["welfare"] = welfare
	if goal in {"exchange", "exchange_contact"}:
		inputs["type"] = menu.select(
			"请选择联系方式",
			[MenuOption("phone", "手机号", "请求手机号"), MenuOption("wechat", "微信", "请求微信号")],
			default="phone",
		)
	if role == "candidate" and goal == "watch":
		action = menu.select(
			"请选择监控操作",
			[
				MenuOption("list", "查看已有监控", "列出已保存的监控条件"),
				MenuOption("add", "新建监控", "保存一组搜索条件"),
				MenuOption("run", "运行监控", "按已有条件搜索并记录新职位"),
				MenuOption("remove", "删除监控", "移除指定监控"),
			],
			default="list",
		)
		inputs["action"] = action
		if action in {"add", "run", "remove"}:
			inputs["name"] = menu.text(INPUT_LABELS["name"])
		if action == "add":
			inputs["query"] = menu.text(INPUT_LABELS["query"])
	return inputs


def _runs_with_status(available_runs: Sequence[Mapping[str, Any]], statuses: set[str]) -> list[Mapping[str, Any]]:
	return [run for run in available_runs if str(run.get("status")) in statuses]


def _main_menu_options(available_runs: Sequence[Mapping[str, Any]]) -> list[MenuOption]:
	"""Only expose resume/retry/stop when matching unfinished tasks exist."""
	options = [MenuOption("new", "开始新任务", "选择身份、平台和目标")]
	if _runs_with_status(available_runs, {"pending", "running", "waiting_input", "failed"}):
		options.append(MenuOption("resume", "恢复已有任务", "从上次未完成的位置继续"))
	if _runs_with_status(available_runs, {"failed"}):
		options.append(MenuOption("retry", "重试失败任务", "重新执行失败的步骤"))
	if available_runs:
		options.append(MenuOption("status", "查看任务状态", "查看最近任务进度"))
	if _runs_with_status(available_runs, {"pending", "running", "waiting_input"}):
		options.append(MenuOption("stop", "停止运行中的任务", "请求停止进行中的任务"))
	return options


def collect_wizard_input(
	*,
	default_role: str,
	default_platform: str,
	menu: MenuDriver | None = None,
	show_controls: bool = True,
	available_runs: Sequence[Mapping[str, Any]] = (),
	data_dir: Path | None = None,
	local_only: bool = False,
) -> WizardInput | WizardControl:
	"""Collect a plan or run-control action without executing business logic."""
	driver = menu or _menu_driver()
	while True:
		try:
			if show_controls:
				action = driver.select(
					"请选择要进行的操作",
					_main_menu_options(available_runs),
					default="new",
					allow_back=False,
				)
				if action != "new":
					return _collect_control(driver, action, available_runs)
			return _collect_new_plan(
				driver,
				default_role=default_role,
				default_platform=default_platform,
				data_dir=data_dir,
				local_only=local_only,
			)
		except WizardBack:
			if not show_controls:
				raise WizardCancelled


def _first_value(item: Mapping[str, Any], names: Sequence[str]) -> Any:
	for name in names:
		value = item.get(name)
		if value not in (None, ""):
			return value
	return None


def _find_result_items(value: Any, keys: Sequence[str]) -> list[Mapping[str, Any]]:
	if not isinstance(value, Mapping):
		return []
	for key in keys:
		items = value.get(key)
		if isinstance(items, list):
			return [item for item in items if isinstance(item, Mapping)]
	for key in ("data", "result"):
		items = _find_result_items(value.get(key), keys)
		if items:
			return items
	return []


# Result list page size: on-demand browsing, never dump dozens of options at once.
RESULT_PAGE_SIZE = 12
_RESULT_MORE = "__result_more__"
_RESULT_PREV = "__result_prev__"
_RESULT_DONE = "__result_done__"


def _result_menu_options(
	items: Sequence[Mapping[str, Any]],
	*,
	kind: str,
	index_offset: int = 0,
) -> list[MenuOption]:
	options: list[MenuOption] = []
	for index, item in enumerate(items):
		absolute = index_offset + index
		if kind in {"job", "shortlist"}:
			title = str(_first_value(item, ("title", "jobName", "name")) or "")
			if not title:
				job_id = _first_value(item, ("job_id", "encryptJobId"))
				title = f"职位 {str(job_id)[:10]}…" if job_id else "未命名职位"
			owner = str(_first_value(item, ("company", "brandName", "companyName")) or "公司未注明")
			details = [
				str(value)
				for value in (
					_first_value(item, ("city", "cityName", "areaDistrict")),
					_first_value(item, ("salary", "salaryDesc")),
				)
				if value not in (None, "")
			]
		elif kind == "resume":
			title = str(_first_value(item, ("name", "title")) or "未命名简历")
			owner = "本地简历"
			details = [
				str(value)
				for value in (_first_value(item, ("updated_at", "created_at")),)
				if value not in (None, "")
			]
		elif kind == "watch":
			title = str(_first_value(item, ("name",)) or "未命名监控")
			params = item.get("params") if isinstance(item.get("params"), Mapping) else {}
			owner = str((params or {}).get("query") or item.get("query") or "无关键词")
			details = [
				str(value)
				for value in (
					(params or {}).get("city") if isinstance(params, Mapping) else None,
					item.get("updated_at"),
				)
				if value not in (None, "")
			]
		elif kind == "friend":
			title = str(_first_value(item, ("name", "friendName", "geekName")) or "未命名联系人")
			owner = str(_first_value(item, ("brandName", "company", "companyName", "title")) or "职位未注明")
			details = [
				str(value)
				for value in (
					_first_value(item, ("lastMsg", "last_msg")),
					_first_value(item, ("relationTypeName", "relation")),
				)
				if value not in (None, "")
			]
		elif kind == "pipeline":
			title = str(_first_value(item, ("title", "jobName")) or "未命名事项")
			owner = str(_first_value(item, ("company", "brandName")) or "公司未注明")
			details = [
				str(value)
				for value in (
					_first_value(item, ("stage", "relation")),
					_first_value(item, ("reason", "last_msg")),
				)
				if value not in (None, "")
			]
		else:
			title = str(_first_value(item, ("name", "geekName", "candidateName")) or "未命名候选人")
			owner = str(_first_value(item, ("expectPosition", "positionName", "jobName")) or "意向职位未注明")
			details = [
				str(value)
				for value in (
					_first_value(item, ("city", "cityName")),
					_first_value(item, ("degree", "degreeName")),
					_first_value(item, ("experience", "workYearDesc")),
				)
				if value not in (None, "")
			]
		description = " · ".join([owner, *details])
		# Number in label without trailing "." cluster so nav rows stay distinct.
		options.append(
			MenuOption(
				str(absolute),
				f"{absolute + 1}  {title}",
				description,
				kind="item",
			)
		)
	return options


def _is_crawl_local_job(item: Mapping[str, Any]) -> bool:
	return str(item.get("source") or "") == "crawl" or item.get("crawl_page") is not None


def _candidate_job_follow_up(menu: MenuDriver, item: Mapping[str, Any], platform: str) -> WizardInput | None:
	security_id = _first_value(item, ("security_id", "securityId"))
	job_id = _first_value(item, ("job_id", "encryptJobId", "jobId"))
	lid = _first_value(item, ("lid",))
	inputs: dict[str, Any] = {
		"security_id": str(security_id) if security_id not in (None, "") else "",
		"job_id": str(job_id) if job_id not in (None, "") else "",
	}
	if lid not in (None, ""):
		inputs["lid"] = str(lid)

	while True:
		actions: list[MenuOption] = []
		# Crawl rows: local content first; online detail is optional and explicit.
		if _is_crawl_local_job(item):
			actions.append(MenuOption("local_brief", "查看本地摘要", "仅展示采集到的字段，不请求网络"))
			if str(item.get("description") or "").strip():
				actions.append(MenuOption("local_detail", "查看本地描述", "展示采集到的职位描述片段"))
			if security_id not in (None, ""):
				actions.append(MenuOption("job_detail", "在线拉取职位详情", "请求平台接口获取完整详情"))
		elif security_id not in (None, ""):
			actions.append(MenuOption("job_detail", "查看职位详情"))
		if security_id not in (None, "") and job_id not in (None, ""):
			actions.extend(
				[
					MenuOption("apply", "投递或立即沟通"),
					MenuOption("greet", "向招聘者打招呼"),
				]
			)
		if security_id not in (None, ""):
			actions.append(MenuOption("exchange", "交换联系方式"))
		actions.append(MenuOption("done", "结束并返回结果列表"))
		if not actions:
			return None
		action = menu.select("请选择下一步", actions, default=actions[0].value)
		if action == "done":
			return None
		if action in {"local_brief", "local_detail"}:
			from boss_agent_cli.wizard.renderer import render_crawl_job_brief

			render_crawl_job_brief(item, with_description=action == "local_detail")
			continue
		if action == "greet":
			message = menu.text("招呼内容（可留空）", required=False)
			if message:
				inputs["message"] = message
		if action == "exchange":
			inputs["type"] = menu.select(
				"请选择联系方式",
				[MenuOption("phone", "手机号"), MenuOption("wechat", "微信")],
				default="phone",
			)
		return WizardInput(role="candidate", platform=platform, goal=action, inputs=inputs, mode="tty")


def _candidate_friend_follow_up(menu: MenuDriver, item: Mapping[str, Any], platform: str) -> WizardInput | None:
	security_id = _first_value(item, ("security_id", "securityId"))
	gid = _first_value(item, ("gid", "uid", "friendId", "friend_id"))
	job_id = _first_value(item, ("job_id", "encryptJobId", "jobId"))
	actions: list[MenuOption] = []
	if security_id not in (None, "") and gid not in (None, ""):
		actions.append(MenuOption("chat_history", "查看聊天记录"))
	if security_id not in (None, ""):
		actions.extend(
			[
				MenuOption("exchange", "交换联系方式"),
				MenuOption("mark", "标记沟通联系人"),
			]
		)
	if security_id not in (None, "") and job_id not in (None, ""):
		actions.append(MenuOption("greet", "再次打招呼"))
	actions.append(MenuOption("done", "结束并返回结果列表"))
	action = menu.select("请选择下一步", actions, default=actions[0].value)
	if action == "done":
		return None
	inputs: dict[str, Any] = {}
	if security_id not in (None, ""):
		inputs["security_id"] = str(security_id)
	if gid not in (None, ""):
		inputs["gid"] = str(gid)
	if job_id not in (None, ""):
		inputs["job_id"] = str(job_id)
	if action == "exchange":
		inputs["type"] = menu.select(
			"请选择联系方式",
			[MenuOption("phone", "手机号"), MenuOption("wechat", "微信")],
			default="phone",
		)
	if action == "mark":
		inputs["label"] = menu.select(
			"请选择联系人标签",
			[
				MenuOption("新招呼", "新招呼"),
				MenuOption("沟通中", "沟通中"),
				MenuOption("已约面", "已约面"),
				MenuOption("已获取简历", "已获取简历"),
				MenuOption("不合适", "不合适"),
				MenuOption("收藏", "收藏"),
			],
			default="沟通中",
		)
	if action == "greet":
		message = menu.text("招呼内容（可留空）", required=False)
		if message:
			inputs["message"] = message
	return WizardInput(role="candidate", platform=platform, goal=action, inputs=inputs, mode="tty")


def _candidate_resume_follow_up(menu: MenuDriver, item: Mapping[str, Any], platform: str) -> WizardInput | None:
	name = _first_value(item, ("name", "title"))
	if name in (None, ""):
		return None
	action = menu.select(
		f"请选择对简历「{name}」的操作",
		[
			MenuOption("ai_assist", "AI 辅助分析", "基于该简历提问或优化建议"),
			MenuOption("done", "结束并返回结果列表"),
		],
		default="ai_assist",
	)
	if action == "done":
		return None
	prompt = menu.text("希望 AI 完成的任务", required=True)
	return WizardInput(
		role="candidate",
		platform=platform,
		goal="ai_assist",
		inputs={"resume": str(name), "prompt": prompt},
		mode="tty",
	)


def _candidate_watch_follow_up(menu: MenuDriver, item: Mapping[str, Any], platform: str) -> WizardInput | None:
	name = _first_value(item, ("name",))
	if name in (None, ""):
		return None
	action = menu.select(
		f"请选择对监控「{name}」的操作",
		[
			MenuOption("run", "运行该监控", "按条件搜索并记录新职位"),
			MenuOption("remove", "删除该监控", "从本地移除"),
			MenuOption("done", "结束并返回结果列表"),
		],
		default="run",
	)
	if action == "done":
		return None
	return WizardInput(
		role="candidate",
		platform=platform,
		goal="watch",
		inputs={"action": action, "name": str(name)},
		mode="tty",
	)


def _candidate_pipeline_follow_up(menu: MenuDriver, item: Mapping[str, Any], platform: str) -> WizardInput | None:
	security_id = _first_value(item, ("security_id", "securityId"))
	job_id = _first_value(item, ("job_id", "encryptJobId", "jobId"))
	if security_id in (None, ""):
		return None
	actions = [
		MenuOption("exchange", "交换联系方式"),
		MenuOption("mark", "标记沟通联系人"),
	]
	if job_id not in (None, ""):
		actions.insert(0, MenuOption("job_detail", "查看关联职位详情"))
	actions.append(MenuOption("done", "结束并返回结果列表"))
	action = menu.select("请选择下一步", actions, default=actions[0].value)
	if action == "done":
		return None
	inputs: dict[str, Any] = {"security_id": str(security_id)}
	if job_id not in (None, ""):
		inputs["job_id"] = str(job_id)
	if action == "exchange":
		inputs["type"] = menu.select(
			"请选择联系方式",
			[MenuOption("phone", "手机号"), MenuOption("wechat", "微信")],
			default="phone",
		)
	if action == "mark":
		inputs["label"] = menu.select(
			"请选择联系人标签",
			[
				MenuOption("新招呼", "新招呼"),
				MenuOption("沟通中", "沟通中"),
				MenuOption("已约面", "已约面"),
				MenuOption("已获取简历", "已获取简历"),
				MenuOption("不合适", "不合适"),
				MenuOption("收藏", "收藏"),
			],
			default="沟通中",
		)
	return WizardInput(role="candidate", platform=platform, goal=action, inputs=inputs, mode="tty")


def _recruiter_follow_up(menu: MenuDriver, item: Mapping[str, Any], platform: str) -> WizardInput | None:
	geek_id = _first_value(item, ("geek_id", "geekId", "encryptGeekId"))
	job_id = _first_value(item, ("job_id", "jobId", "encryptJobId"))
	security_id = _first_value(item, ("security_id", "securityId"))
	friend_id = _first_value(item, ("friend_id", "friendId", "uid", "gid"))
	actions: list[MenuOption] = []
	if geek_id not in (None, "") and job_id not in (None, ""):
		actions.append(MenuOption("candidate_resume", "查看候选人简历"))
	if friend_id not in (None, ""):
		actions.extend(
			[
				MenuOption("chat_history", "查看聊天记录"),
				MenuOption("reply", "回复候选人"),
				MenuOption("exchange_contact", "请求交换联系方式"),
				MenuOption("request_resume", "请求附件简历"),
			]
		)
	actions.append(MenuOption("done", "结束并返回结果列表"))
	action = menu.select("请选择下一步", actions, default=actions[0].value)
	if action == "done":
		return None
	inputs: dict[str, Any] = {}
	if geek_id not in (None, ""):
		inputs["geek_id"] = str(geek_id)
	if job_id not in (None, ""):
		inputs["job_id"] = str(job_id)
	if security_id not in (None, ""):
		inputs["security_id"] = str(security_id)
	if friend_id not in (None, ""):
		inputs["friend_id"] = friend_id
	if action == "reply":
		inputs["message"] = menu.text("回复内容")
	if action == "exchange_contact":
		inputs["type"] = menu.select(
			"请选择联系方式",
			[MenuOption("phone", "手机号"), MenuOption("wechat", "微信")],
			default="phone",
		)
	return WizardInput(role="recruiter", platform=platform, goal=action, inputs=inputs, mode="tty")


def _recruiter_job_follow_up(menu: MenuDriver, item: Mapping[str, Any], platform: str) -> WizardInput | None:
	job_id = _first_value(item, ("job_id", "jobId", "encryptJobId", "id"))
	if job_id in (None, ""):
		return None
	action = menu.select(
		"请选择下一步",
		[
			MenuOption("jobs_detail", "查看职位详情"),
			MenuOption("jobs_online", "上线职位"),
			MenuOption("jobs_offline", "下线职位"),
			MenuOption("done", "结束并返回结果列表"),
		],
		default="jobs_detail",
	)
	if action == "done":
		return None
	return WizardInput(
		role="recruiter",
		platform=platform,
		goal=action,
		inputs={"job_id": str(job_id)},
		mode="tty",
	)


def _resolve_list_follow_up(run: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], str, Any] | None:
	"""Return (items, result_kind, follow_up_builder) for a completed list-like run."""
	role = str(run.get("role") or "")
	goal = str(run.get("goal") or "")
	last_result = run.get("last_result") or {}
	status = str(run.get("status") or "")
	# Crawl jobs live under last_result.data.jobs (often filled by live_crawl enrich).
	if role == "candidate" and goal in {"crawl_start", "crawl_resume", "crawl_status"}:
		if status not in {"completed", "waiting_input"} and not run.get("effective_completed"):
			return None
		items = _find_result_items(last_result, ("jobs", "items", "jobList", "list"))
		if items:
			return items, "job", _candidate_job_follow_up
		return None
	if role == "candidate" and goal in {"job_search", "recommendations", "shortlist", "export"}:
		items = _find_result_items(last_result, ("items", "jobList", "recommendList", "list", "jobs"))
		return items, "job" if goal != "shortlist" else "shortlist", _candidate_job_follow_up
	if role == "candidate" and goal == "communication":
		items = _find_result_items(last_result, ("result", "friendList", "list", "items"))
		return items, "friend", _candidate_friend_follow_up
	if role == "candidate" and goal == "pipeline":
		items = _find_result_items(last_result, ("items", "list", "result"))
		return items, "pipeline", _candidate_pipeline_follow_up
	if role == "candidate" and goal == "resumes":
		items = _find_result_items(last_result, ("items", "list"))
		if items:
			return items, "resume", _candidate_resume_follow_up
		return None
	if role == "candidate" and goal == "watch":
		# Only list results expose selectable monitors.
		items = _find_result_items(last_result, ("items", "list"))
		if items:
			return items, "watch", _candidate_watch_follow_up
		return None
	if role == "recruiter" and goal in {"candidates", "applications", "communication", "last_messages"}:
		items = _find_result_items(last_result, ("items", "geekList", "friendList", "list", "result", "messages"))
		return items, "candidate", _recruiter_follow_up
	if role == "recruiter" and goal == "jobs_list":
		items = _find_result_items(last_result, ("jobs", "jobList", "items", "list", "result"))
		return items, "job", _recruiter_job_follow_up
	return None


def has_result_follow_up(run: Mapping[str, Any]) -> bool:
	"""Whether a completed run has selectable list items for TTY follow-up."""
	if str(run.get("status")) not in {"completed", "waiting_input"} and not run.get("effective_completed"):
		return False
	if str(run.get("status")) == "waiting_input" and not (
		run.get("effective_completed") or int(run.get("live_jobs_seen") or 0) > 0
	):
		# Only allow browsing when live crawl already produced jobs.
		last = run.get("last_result") or {}
		data = last.get("data") if isinstance(last, Mapping) else {}
		if not (isinstance(data, Mapping) and int(data.get("jobs_seen") or 0) > 0 and data.get("jobs")):
			return False
	resolved = _resolve_list_follow_up(run)
	return resolved is not None and bool(resolved[0])


def _render_list_header(run: Mapping[str, Any]) -> None:
	"""在结果列表菜单上方重绘任务摘要。

	与「重新进入任务状态」走同一套三段式（清屏 → 摘要 → 菜单不再清屏），
	让两个入口看到同一个框。放在翻页循环内是必需的：只靠一次渲染 + 常驻
	不清屏会让菜单层层堆叠，翻一页就丢摘要。
	"""
	from boss_agent_cli.wizard import renderer

	renderer.clear_wizard_screen()
	renderer.render_run(run, with_preview=False)


def collect_result_follow_up(
	run: Mapping[str, Any],
	*,
	menu: MenuDriver | None = None,
) -> WizardInput | None:
	"""Choose an item from a completed list result and return the next plan input.

	Lists longer than RESULT_PAGE_SIZE are paged so the TUI never dumps everything.
	"""
	resolved = _resolve_list_follow_up(run)
	if resolved is None:
		return None
	items, kind, next_input = resolved
	if not items:
		return None
	platform = str(run.get("platform") or "")
	driver = menu or _menu_driver()
	page = 0
	page_size = RESULT_PAGE_SIZE
	try:
		while True:
			start = page * page_size
			chunk = list(items[start : start + page_size])
			if not chunk and page > 0:
				page = max(0, page - 1)
				continue
			options = _result_menu_options(chunk, kind=kind, index_offset=start)
			if page > 0:
				options.append(MenuOption(_RESULT_PREV, "‹ 上一页", kind="nav"))
			remaining = len(items) - (start + len(chunk))
			if remaining > 0:
				options.append(MenuOption(_RESULT_MORE, f"› 下一页 · 还有 {remaining} 条", kind="nav"))
			options.append(MenuOption(_RESULT_DONE, "结束浏览", "返回上一级", kind="nav"))
			end = start + len(chunk)
			_render_list_header(run)
			selected = driver.select(
				f"请选择职位（第 {start + 1}–{end} / 共 {len(items)} 条）",
				options,
				default=str(start) if chunk else _RESULT_DONE,
				allow_back=True,
				clear_before=False,
			)
			if selected == _RESULT_DONE:
				return None
			if selected == _RESULT_MORE:
				page += 1
				continue
			if selected == _RESULT_PREV:
				page = max(0, page - 1)
				continue
			index = int(selected)
			if index < 0 or index >= len(items):
				return None
			return next_input(driver, items[index], platform)
	except (WizardBack, WizardCancelled):
		return None


def _job_inputs_from_run(run: Mapping[str, Any]) -> dict[str, Any] | None:
	"""Extract security_id/job_id/lid from a completed job_detail-like run."""
	from boss_agent_cli.wizard.renderer import _extract_job_payload

	job = _extract_job_payload(run.get("last_result"))
	if job is None:
		# Fallback: plan params from the run itself.
		params = run.get("params") or {}
		inputs = params.get("inputs") if isinstance(params, Mapping) else {}
		if not isinstance(inputs, Mapping):
			return None
		security_id = inputs.get("security_id")
		job_id = inputs.get("job_id")
		if not security_id:
			return None
		result = {"security_id": str(security_id), "job_id": str(job_id or "")}
		if inputs.get("lid"):
			result["lid"] = str(inputs["lid"])
		return result
	security_id = job.get("security_id") or ""
	job_id = job.get("job_id") or ""
	if not security_id:
		return None
	result = {"security_id": str(security_id), "job_id": str(job_id)}
	if job.get("lid"):
		result["lid"] = str(job["lid"])
	return result


def collect_entity_follow_up(
	run: Mapping[str, Any],
	*,
	menu: MenuDriver | None = None,
	allow_list_return: bool = False,
) -> WizardInput | None:
	"""After viewing a job, offer greet/apply/exchange before leaving the entity.

	Returns:
	- WizardInput: run this next action on the same job
	- None: return to the previous result list (when allow_list_return)
	Raises WizardReturnHome when the human wants the top-level menu.
	"""
	role = str(run.get("role") or "")
	goal = str(run.get("goal") or "")
	platform = str(run.get("platform") or "")
	if role != "candidate" or goal not in {"job_detail", "apply", "greet", "exchange"}:
		return None
	inputs = _job_inputs_from_run(run)
	if inputs is None or not inputs.get("security_id"):
		return None
	params = run.get("params") or {}
	plan_inputs = params.get("inputs") if isinstance(params, Mapping) else {}
	if isinstance(plan_inputs, Mapping):
		for key in ("security_id", "job_id", "lid"):
			if plan_inputs.get(key) and not inputs.get(key):
				inputs[key] = str(plan_inputs[key])

	from boss_agent_cli.wizard.renderer import _extract_job_payload

	job = _extract_job_payload(run.get("last_result")) or {}
	title = str(job.get("title") or "当前职位")
	company = str(job.get("company") or "")
	context = f"{title}" + (f" · {company}" if company else "")

	actions: list[MenuOption] = []
	if inputs.get("security_id") and inputs.get("job_id"):
		actions.extend(
			[
				MenuOption("greet", "向招聘者打招呼", "发送招呼语"),
				MenuOption("apply", "投递或立即沟通", "发起沟通/投递"),
			]
		)
	if inputs.get("security_id"):
		actions.append(MenuOption("exchange", "交换联系方式", "请求手机号或微信"))
	if goal != "job_detail":
		actions.insert(0, MenuOption("job_detail", "再次查看职位详情"))
	if allow_list_return:
		actions.append(MenuOption("list", "再选其他职位", "返回刚才的结果列表"))
	actions.append(MenuOption("home", "返回主菜单", "结束本轮职位操作"))
	if not actions:
		return None

	driver = menu or _menu_driver()
	try:
		action = driver.select(
			f"请选择对「{context}」的下一步",
			actions,
			default=actions[0].value,
			allow_back=False,
			allow_exit=True,
		)
	except WizardCancelled:
		raise
	except WizardBack:
		raise WizardReturnHome from None

	if action == "list":
		return None
	if action == "home":
		raise WizardReturnHome
	payload = dict(inputs)
	if action == "greet":
		message = driver.text("招呼内容（可留空）", required=False)
		if message:
			payload["message"] = message
	if action == "exchange":
		payload["type"] = driver.select(
			"请选择联系方式",
			[MenuOption("phone", "手机号"), MenuOption("wechat", "微信")],
			default="phone",
			allow_back=False,
		)
	return WizardInput(role="candidate", platform=platform, goal=action, inputs=payload, mode="tty")


def ask_continue_session(menu: MenuDriver | None = None, *, clear_before: bool = True) -> bool:
	"""Ask whether the human wants another interactive wizard turn.

	Pass clear_before=False after status panels so the just-rendered content stays visible.
	"""
	driver = menu or _menu_driver()
	try:
		choice = driver.select(
			"还要继续使用向导吗？",
			[
				MenuOption("continue", "继续使用", "返回主菜单处理下一项"),
				MenuOption("exit", "退出向导", "结束本次交互"),
			],
			default="continue",
			allow_back=False,
			# 业务选项已含「退出向导」，勿再自动追加同名项。
			allow_exit=False,
			clear_before=clear_before,
		)
		return choice == "continue"
	except WizardCancelled:
		return False


def _inner_crawl_run_id(run: Mapping[str, Any]) -> str | None:
	from boss_agent_cli.wizard.live_crawl import inner_crawl_run_id

	return inner_crawl_run_id(run)


def collect_waiting_recovery(
	run: Mapping[str, Any],
	*,
	menu: MenuDriver | None = None,
	clear_before: bool = False,
) -> WizardInput | None:
	"""Offer recovery actions when a workflow is waiting_input (e.g. crawl risk stop).

	Default clear_before=False so the just-rendered status panel stays visible above the menu.
	When the live crawl is already completed, prefer browsing jobs over resume.
	"""
	if str(run.get("status")) != "waiting_input":
		return None
	role = str(run.get("role") or "candidate")
	platform = str(run.get("platform") or "zhipin")
	goal = str(run.get("goal") or "")
	inner = _inner_crawl_run_id(run)
	driver = menu or _menu_driver()
	actions: list[MenuOption] = []
	live_done = bool(run.get("effective_completed")) or str(run.get("live_crawl_status")) == "completed"
	jobs_ready = int(run.get("live_jobs_seen") or 0) > 0 or has_result_follow_up(run)
	if goal in {"crawl_start", "crawl_resume"} and inner:
		if live_done and jobs_ready:
			actions.extend(
				[
					MenuOption("view_jobs", "浏览已采集职位", "查看职位列表并可继续详情/打招呼"),
					MenuOption("crawl_status", "查看采集摘要与产物路径", "只读查看内部采集状态与文件"),
				]
			)
		else:
			actions.extend(
				[
					MenuOption("crawl_resume", "继续采集", "用当前登录态续跑已暂停的采集"),
					MenuOption("crawl_status", "查看采集进度与产物", "只读查看内部采集状态"),
				]
			)
			if jobs_ready:
				actions.insert(
					0,
					MenuOption("view_jobs", "先浏览已有职位", "本地已有部分职位，可先查看"),
				)
	actions.append(MenuOption("home", "返回主菜单", "稍后再处理"))
	try:
		choice = driver.select(
			"请选择下一步",
			actions,
			default=actions[0].value,
			allow_back=False,
			allow_exit=True,
			clear_before=clear_before,
		)
	except WizardCancelled:
		# 「退出向导」必须真正结束，不能只回到主菜单再退一次。
		raise
	except WizardBack:
		raise WizardReturnHome from None
	if choice == "home":
		raise WizardReturnHome
	if choice == "view_jobs":
		# Signal to the interactive loop: browse list without starting a new crawl step.
		return WizardInput(
			role=role,
			platform=platform,
			goal="__view_crawl_jobs__",
			inputs={"run_id": inner or "", "source_run_id": str(run.get("run_id") or "")},
			mode="tty",
		)
	if choice == "crawl_resume" and inner:
		return WizardInput(
			role=role,
			platform=platform,
			goal="crawl_resume",
			inputs={"run_id": inner},
			mode="tty",
		)
	if choice == "crawl_status" and inner:
		return WizardInput(
			role=role,
			platform=platform,
			goal="crawl_status",
			inputs={"run_id": inner},
			mode="tty",
		)
	return None


def collect_completed_crawl_follow_up(
	run: Mapping[str, Any],
	*,
	menu: MenuDriver | None = None,
	clear_before: bool = False,
) -> str | None:
	"""After a completed crawl status panel, ask whether to browse jobs or leave.

	Returns:
	- "browse": open job list follow-up
	- "home": return to main menu
	- None: no menu (nothing to offer)
	"""
	if not has_result_follow_up(run):
		return None
	goal = str(run.get("goal") or "")
	if goal not in {"crawl_start", "crawl_resume", "crawl_status"} and not run.get("effective_completed"):
		return None
	driver = menu or _menu_driver()
	count = int(run.get("live_jobs_seen") or 0)
	if count <= 0:
		last = run.get("last_result") or {}
		data = last.get("data") if isinstance(last, Mapping) else {}
		if isinstance(data, Mapping):
			count = int(data.get("jobs_seen") or len(data.get("jobs") or []) or 0)
	try:
		choice = driver.select(
			f"采集结果已就绪（约 {count} 个职位），请选择",
			[
				MenuOption("browse", "浏览职位列表", "选择职位查看详情或打招呼"),
				MenuOption("home", "返回主菜单", "稍后再处理"),
			],
			default="browse",
			allow_back=False,
			allow_exit=True,
			clear_before=clear_before,
		)
	except WizardCancelled:
		raise
	except WizardBack:
		return "home"
	return choice
