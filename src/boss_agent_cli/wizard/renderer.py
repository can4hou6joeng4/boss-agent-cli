"""Chinese Rich stderr rendering for workflow progress and summaries."""

from __future__ import annotations

import math
import os
import re
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

from rich.panel import Panel
from rich.table import Table

from boss_agent_cli import display
from boss_agent_cli.api.models import safe_text
from boss_agent_cli.services.chat_utils import message_text_value
from boss_agent_cli.services.job_export import truncation_operator_action
from boss_agent_cli.services.recruiter_chat_summary import message_text as last_message_text
from boss_agent_cli.services.recruiter_last_messages import as_friend_int, friend_id_for
from boss_agent_cli.wizard.catalog import GOALS
from boss_agent_cli.wizard.models import count_or_none
from boss_agent_cli.wizard.prompts import STATUS_LABELS

STEP_LABELS = {
	"auth_status": "检查登录状态",
	"candidate_search": "搜索并筛选职位",
	"candidate_recommend": "获取职位推荐",
	"candidate_detail": "读取职位详情",
	"candidate_apply": "投递或立即沟通",
	"candidate_greet": "向招聘者打招呼",
	"candidate_exchange": "交换联系方式",
	"candidate_mark": "更新联系人标签",
	"candidate_chat": "读取沟通列表",
	"candidate_chat_history": "读取聊天记录",
	"candidate_pipeline": "整理求职进度",
	"candidate_digest": "生成求职日报",
	"local_shortlist": "读取本地候选池",
	"local_resumes": "读取本地简历",
	"ai_assist": "执行 AI 辅助",
	"candidate_export": "导出职位结果",
	"candidate_watch": "处理职位监控",
	"crawl_start": "启动采集任务",
	"crawl_status": "读取采集状态",
	"crawl_resume": "恢复采集任务",
	"crawl_stop": "停止采集任务",
	"recruiter_candidates": "搜索候选人",
	"recruiter_applications": "读取投递申请",
	"recruiter_resume": "读取候选人简历",
	"recruiter_chat": "读取候选人沟通列表",
	"recruiter_last_messages": "读取最近消息",
	"recruiter_recommendations": "读取推荐牛人",
	"recruiter_reply": "回复候选人",
	"recruiter_exchange_contact": "请求交换联系方式",
	"recruiter_request_resume": "请求附件简历",
	"recruiter_chat_history": "读取候选人聊天记录",
	"recruiter_jobs_list": "读取职位列表",
	"recruiter_jobs_detail": "读取职位详情",
	"recruiter_jobs_online": "上线职位",
	"recruiter_jobs_offline": "下线职位",
}

ERROR_LABELS = {
	"AUTH_REQUIRED": "当前平台尚未登录",
	"TOKEN_REFRESH_FAILED": "登录状态已过期",
	"NETWORK_ERROR": "网络请求失败",
	"INTERNAL_ERROR": "程序内部出错",
	"BROWSER_NAV_TIMEOUT": "浏览器首页加载超时",
	"BROWSER_KERNEL_MISSING": "浏览器内核缺失或版本不匹配",
	"CDP_UNAVAILABLE": "浏览器调试端口不可达",
	"RECRUITER_CHAT_TAB_REQUIRED": "招聘者聊天页未打开，动作未发送",
	"RATE_LIMITED": "请求过于频繁",
	"ACCOUNT_RISK": "平台已暂停本次操作",
	"ENVIRONMENT_RISK": "平台判定当前访问环境异常",
	"ENVIRONMENT_RISK_LOCKED": "浏览器访问环境仍处于风控锁定状态",
	"NOT_SUPPORTED": "当前平台不支持此事项",
	"JOB_NOT_FOUND": "没有找到指定任务",
	"CONTACT_NOT_FOUND": "没有在沟通列表里找到该联系人",
	"CRAWL_RUN_NOT_FOUND": "没有找到该采集任务",
	"CRAWL_RUN_FINISHED": "采集任务已经结束，无需停止",
	"SAVED_SEARCH_NOT_FOUND": "没有找到该本地预设或监控",
	"WORKFLOW_NOT_FOUND": "没有找到该任务记录",
	"WORKFLOW_TIMEOUT": "任务运行超时",
	"WORKFLOW_STOPPED": "任务已停止",
	"WORKFLOW_BUSY": "该任务正在另一个窗口或进程中执行",
	"CRAWL_BUSY": "该采集任务正在另一个窗口或进程中执行",
	"CRAWL_PROFILE_BUSY": "采集配置目录正被运行中的采集浏览器占用",
	"CACHE_BUSY": "本地缓存正被另一个进程写入",
	"CACHE_DATABASE_INVALID": "本地缓存数据库已损坏",
	"CACHE_RECORD_INVALID": "本地任务记录已损坏",
	"CRAWL_UNAVAILABLE": "采集运行环境不可用（可选依赖或浏览器未就绪）",
	"EGRESS_CONTRADICTION": "出口地区与客户端声明的语言/时区矛盾",
	"REQUEST_BUDGET_EXHAUSTED": "本地请求节流等待超时，请求未发出",
	"WORKFLOW_PLAN_MISMATCH": "任务信息与已有记录不一致",
	"INVALID_PARAM": "请求参数无效",
	"CONFIG_INVALID": "本地配置损坏或取值无效",
	"AUTH_EXPIRED": "登录状态已过期",
	"AI_API_ERROR": "AI 服务调用失败",
	"AI_NOT_CONFIGURED": "AI 服务尚未配置",
	"ACTION_UNCONFIRMED": "操作可能已生效，但未能确认",
	"CIRCUIT_BREAKER_OPEN": "招聘自动化已停止，本次操作未发出",
	"ALREADY_APPLIED": "已投递过该职位",
	"ALREADY_GREETED": "已向该招聘者打过招呼",
	"ENDPOINT_DEPRECATED": "平台接口已迁移，当前版本无法发送",
	"EXCHANGE_NOT_AVAILABLE": "交换联系方式按钮尚未解锁",
	"LOCAL_IO_ERROR": "本地文件读写失败",
	"LOCAL_STATE_WRITE_FAILED": "平台操作已完成，但本地状态保存失败",
	"RESUME_FILE_INVALID": "本地简历文件损坏",
	"RESUME_NOT_FOUND": "本地简历不存在",
	"SAVED_SEARCH_INVALID": "本地预设或监控的参数已损坏",
	"UNKNOWN": "平台返回了未识别的错误",
	"WIZARD_INPUT_REQUIRED": "workflow 缺少必要输入",
}

RECOVERY_LABELS = {
	"AUTH_REQUIRED": "请先登录当前平台，然后选择“恢复已有任务”。",
	"TOKEN_REFRESH_FAILED": "请重新登录当前平台，然后选择“恢复已有任务”。",
	"NETWORK_ERROR": "请检查网络后重试，或返回主菜单选择其他事项。",
	"INTERNAL_ERROR": "这不是网络问题，重试不会有帮助。请升级到最新版本，仍出现时把这次的命令和报错反馈给维护者。",
	"BROWSER_NAV_TIMEOUT": "请用已登录的 Chrome 调试端口重试，或先重新登录后再恢复任务。",
	"BROWSER_KERNEL_MISSING": "这不是网络问题，重试不会有帮助。请退出向导，运行 python -m patchright install chromium（使用运行 boss 的同一个解释器）安装浏览器内核后重新执行命令。",
	"CDP_UNAVAILABLE": "本次操作尚未发送，重新执行是安全的：确认带调试端口的 Chrome 仍在运行，或改用扫码登录后重新执行。",
	"RECRUITER_CHAT_TAB_REQUIRED": "在 CDP 连接的 Chrome 里打开 BOSS 直聘招聘者聊天页（web/chat/index）后重新执行；本次操作没有发送，不需要新建任务。",
	"RATE_LIMITED": "请稍后再试。",
	"ACCOUNT_RISK": "请停止自动操作并在平台内检查账号状态；确认风险解除后，由你本人执行 boss clean --risk-lock，再重新启动向导恢复任务。",
	"ENVIRONMENT_RISK": "请停止自动操作，保留当前浏览器 profile，在平台官方页面确认风险解除后，由你本人执行 boss clean --risk-lock，再重新启动向导恢复任务。",
	"ENVIRONMENT_RISK_LOCKED": "风险锁不会自动解除：请在 BOSS 官方目标业务页确认风险已解除，由你本人执行 boss clean --risk-lock，再重新启动向导恢复任务。",
	"NOT_SUPPORTED": "请返回并选择当前平台支持的事项。",
	"JOB_NOT_FOUND": "请返回主菜单后重新选择任务。",
	"CONTACT_NOT_FOUND": "请返回并改用对方的 uid 重试；也可以先在 boss chat 里确认会话仍在。",
	"CRAWL_RUN_NOT_FOUND": "请返回并重新选择采集任务，或新建一个采集任务。",
	"CRAWL_RUN_FINISHED": "请查看该任务的最终状态或已采集结果；需要重新采集时新建一个采集任务。",
	"SAVED_SEARCH_NOT_FOUND": "请返回并重新选择预设或监控；也可以先用 boss preset list / boss watch list 查看已有条目。",
	"WORKFLOW_NOT_FOUND": "请返回主菜单后重新选择任务。",
	"WORKFLOW_TIMEOUT": "请选择“恢复已有任务”继续。",
	"WORKFLOW_PLAN_MISMATCH": "请使用原任务恢复，或新建任务。",
	"INVALID_PARAM": "请换一条结果重试，或返回结果列表重新选择。",
	"CONFIG_INVALID": "按提示修正本地配置（boss config reset <项> 或 boss ai config）后重新执行；修复前重跑会复现。",
	"AUTH_EXPIRED": "请重新登录当前平台，然后选择“恢复已有任务”。",
	"AI_API_ERROR": "检查网络连接与 AI 密钥配置后重试。",
	"AI_NOT_CONFIGURED": "先执行 boss ai config --provider <provider> --model <model> --api-key <key> 完成配置。",
	"ACTION_UNCONFIRMED": "动作可能已生效：先在平台官方页面核对本次操作，不要直接重试。",
	"CIRCUIT_BREAKER_OPEN": "人工核对未确认动作后执行 boss agent run --resume 关闭熔断，再由操作者显式重试；不要自动重发。",
	"ALREADY_APPLIED": "该职位已投递过：用 boss me --section deliver 查看投递记录，或用 boss chat 跟进。",
	"ALREADY_GREETED": "已打过招呼：用 boss chat 查看会话，或返回重新搜索换一批职位。",
	"ENDPOINT_DEPRECATED": "停止重试：该平台端点已下线，请升级到最新版本并查看 error.details.tracking_issue。",
	"EXCHANGE_NOT_AVAILABLE": "先回复候选人（boss hr reply）或请求附件简历，再重新执行交换。",
	"LOCAL_IO_ERROR": "确认目标路径是可写目录、权限与磁盘空间正常后，再执行。",
	"LOCAL_STATE_WRITE_FAILED": "先在官方页面核对操作是否已生效；不要自动重发，确认磁盘空间与缓存目录权限后再继续。",
	"RESUME_FILE_INVALID": "修正 resumes/ 下对应的 .json 内容，或执行 boss resume delete <名称> 后重新创建。",
	"RESUME_NOT_FOUND": "先用 boss resume list 查看可用简历，再重新选择。",
	"SAVED_SEARCH_INVALID": "执行 boss preset remove <名称>（watch 用 boss watch remove）后重新添加。",
	"UNKNOWN": "不要自动重试：先在平台官方页面核对操作是否生效，并用 boss doctor 检查登录态与网络。",
	"WIZARD_INPUT_REQUIRED": "headless 模式请通过 --input-json 提供完整输入，或用 run_id 恢复。",
	"WORKFLOW_STOPPED": "任务已停止；如需继续，请选择“恢复已有任务”。",
	"WORKFLOW_BUSY": "请等它结束，或选择“查看任务状态”确认进度后再恢复。",
	# 具体安装命令由 recovery_message 按当前已有的依赖组动态拼出（Issue #1068）。
	"CRAWL_UNAVAILABLE": "这不是网络问题，重试不会有帮助。请退出向导，补上 crawl 可选依赖后重新执行。",
	"EGRESS_CONTRADICTION": "保持声明的语言/时区不变，换回与之一致的出口后重新执行；不需要出口一致性检查时撤销 BOSS_EGRESS_PROBE_URL。程序不会自动改写语言或时区。",
	"CRAWL_BUSY": "请等它结束；需要确认进度时执行 boss crawl status <run_id>，不要并发恢复同一个任务。",
	"CRAWL_PROFILE_BUSY": "请等正在运行的采集结束；需要确认进度时执行 boss crawl status <run_id>，不要删除 profile 目录里的 SingletonLock，也不要并发使用同一数据目录的采集浏览器。",
	"CACHE_BUSY": "请等正在运行的 boss 命令结束后选择“恢复已有任务”继续；库文件本身完好，不需要删除。",
	"CACHE_DATABASE_INVALID": "先备份 cache/boss_agent.db（--data-dir 指定时在对应目录下），再删除该文件后重新执行；命令会自动重建空库。",
	"CACHE_RECORD_INVALID": "请返回并重新创建任务；同一条损坏记录重试无法恢复。",
	"REQUEST_BUDGET_EXHAUSTED": "等待片刻后选择“恢复已有任务”继续；这次请求没有发出，不需要新建任务。",
}

#: 风控恢复动作里由 runner / actions 注入的真实解锁命令（Issue #978 / #976）。
_RISK_CLEAN_COMMAND = re.compile(r"""boss(?: --platform \S+)? --data-dir (?:'[^']*'|"[^"]*"|\S+) clean --risk-lock""")
_RISK_CODES = frozenset({"ACCOUNT_RISK", "ENVIRONMENT_RISK", "ENVIRONMENT_RISK_LOCKED"})

#: 动作层已经给出带数据目录的完整恢复句子的错误码（Issue #1090）。静态文案的
#: 命令家族可能不同（例如 watch 失败却提示 preset remove），此时动态版本更准确。
#: 熔断恢复还要保留动作层按运行角色生成的 --role recruiter（Issue #3063）。
_DYNAMIC_RECOVERY_CODES = frozenset(
	{"ACTION_UNCONFIRMED", "SAVED_SEARCH_INVALID", "CRAWL_BUSY", "CIRCUIT_BREAKER_OPEN"}
)
_CONTEXT_AWARE_COMMAND = re.compile(r"""boss(?: --platform \S+)? --data-dir """)

#: 静态恢复文案里的默认命令示例；有运行上下文时由调用方替换成带 ``--data-dir``
#: 的命令（Issue #1090）。
_RECOVERY_COMMANDS: dict[str, tuple[str, ...]] = {
	"boss ai config": ("ai config",),
	"boss config reset": ("config reset",),
	"boss me --section deliver": ("me --section deliver",),
	"boss chat": ("chat",),
	"boss preset remove": ("preset remove",),
	"boss preset list": ("preset list",),
	"boss resume list": ("resume list",),
	"boss resume import": ("resume import",),
	"boss resume delete": ("resume delete",),
	"boss watch remove": ("watch remove",),
	"boss watch list": ("watch list",),
	"boss crawl status": ("crawl status",),
	"boss hr chatmsg": ("hr chatmsg",),
	"boss hr reply": ("hr reply",),
	"boss doctor": ("doctor",),
	"boss agent run --resume": ("agent run --resume",),
	"boss clean --risk-lock": ("clean --risk-lock",),
}


def _rewrite_recovery_commands(text: str, command_for: Callable[[str], str] | None) -> str:
	"""把静态恢复文案里的裸命令换成调用方上下文的命令（Issue #1090）。"""
	if command_for is None:
		return text
	return display.rewrite_command_examples(
		text, {literal: command for literal, (command,) in _RECOVERY_COMMANDS.items()}, command_for
	)


# 这些步骤对真人噪声大，TTY 进度里默认静默。
_SILENT_STEPS = {"auth_status"}


def goal_label(role: Any, goal: Any) -> str:
	definition = GOALS.get(_safe_text(role), {}).get(_safe_text(goal))
	return definition.description if definition else "自定义事项"


def step_label(value: Any) -> str:
	return STEP_LABELS.get(_safe_text(value), "处理任务步骤")


def status_label(value: Any) -> str:
	return STATUS_LABELS.get(_safe_text(value), "状态未知")


#: 采集任务自己的状态词表；不能复用向导 run 的 STATUS_LABELS——查询型
#: action 一返回 run 就是 completed，与采集任务状态无关（Issue #3053）。
_CRAWL_STATUS_LABELS = {
	"queued": "排队中",
	"running": "正在运行",
	"completed": "已完成",
	"stopped": "已停止",
	"stop_requested": "停止请求已受理",
	"risk_stopped": "风控暂停",
	"budget_stopped": "预算已用完",
	"throttle_stopped": "节流等待超时",
	"failed": "执行失败",
}


def crawl_status_label(value: Any) -> str:
	"""采集状态的可读标签；未知值回退到向导状态词表。"""
	text = _safe_text(value)
	label = _CRAWL_STATUS_LABELS.get(text)
	if label is not None:
		return label
	return status_label(text)


def error_message(code: str, message: str | None = None) -> str:
	"""Prefer platform Chinese text; fall back to localized code labels."""
	if message:
		text = _safe_text(message)
		if text and any("\u4e00" <= char <= "\u9fff" for char in text):
			# Avoid showing opaque "NETWORK_ERROR" wrappers when platform explained it.
			if code == "NETWORK_ERROR" and text not in {"网络请求失败", "NETWORK_ERROR"}:
				return text
			if code == "INVALID_PARAM":
				return text
			if text not in ERROR_LABELS.values():
				return text
	return ERROR_LABELS.get(code) or (_safe_text(message) if message else "") or "任务执行时遇到问题"


def recovery_message(
	code: str,
	recovery_action: str | None = None,
	*,
	command_for: Callable[[str], str] | None = None,
) -> str | None:
	localized = RECOVERY_LABELS.get(code)
	action_text = _safe_text(recovery_action)
	if localized and code in _DYNAMIC_RECOVERY_CODES and action_text:
		# actions 层已给出带数据目录的完整指引，直接采用；静态文案的命令家族
		# 可能与实际失败对象不同（Issue #1090）。
		if _CONTEXT_AWARE_COMMAND.search(action_text):
			return action_text
	if localized and code in _RISK_CODES and action_text:
		# runner / actions 已把解锁命令换成带 --data-dir 的真实命令；静态文案
		# 保留面向真人的措辞，命令部分必须用动态版本，否则自定义数据目录下
		# 照抄提示会去默认目录空转（Issue #1087）。
		match = _RISK_CLEAN_COMMAND.search(action_text)
		if match:
			return localized.replace("boss clean --risk-lock", match.group(0))
	if code == "BROWSER_KERNEL_MISSING":
		# 安装命令依赖运行本 CLI 的解释器（`uv tool install` 不把 patchright
		# 放进 PATH，Issue #1060）：有动态命令时用它替换静态说法。
		from boss_agent_cli.services.browser_runtime import patchright_install_command

		command = action_text if "patchright" in action_text else None
		return (
			"这不是网络问题，重试不会有帮助。请退出向导，运行 "
			f"{command or patchright_install_command()} 安装浏览器内核后重新执行命令。"
		)
	if code == "CRAWL_UNAVAILABLE":
		# 浏览器启动失败与依赖缺失共用该码：动作层给出的动态指引（Chrome /
		# chrome_path / 调试端口）比静态安装文案更准确（Issue #1553）。
		if any(keyword in action_text for keyword in ("chrome_path", "Chrome", "调试端口")):
			return action_text
		# 安装命令要带上已经可用的依赖组：uv tool install 会重建环境（Issue #1068）。
		from boss_agent_cli.services.install_extras import extras_install_hint

		return f"这不是网络问题，重试不会有帮助。请退出向导，用 {extras_install_hint('crawl')} 补上采集依赖后重新执行。"
	if localized:
		return _rewrite_recovery_commands(localized, command_for)
	if action_text and any("\u4e00" <= char <= "\u9fff" for char in action_text):
		return action_text
	return "请返回主菜单后重试。" if recovery_action else None


def render_event(kind: str, step: str, data: Mapping[str, Any] | None) -> None:
	"""Compact TTY progress: hide auth noise and skip failed lines (final panel owns errors)."""
	if step in _SILENT_STEPS:
		return
	if kind == "step_failed":
		# Final render_run / render_error shows a single clear failure panel.
		return
	if kind == "step_finished":
		return
	label = step_label(step)
	if kind == "step_started":
		display.console.print(f"[cyan]…[/cyan] {label}")
	elif kind == "step_retrying":
		attempt = data.get("attempt") if data else "?"
		display.console.print(
			f"[yellow]正在重试[/yellow] {label}（第 {display.escape_markup(_safe_text(attempt, '?'))} 次）"
		)


def _screen_control_unavailable() -> bool:
	"""True 时一切屏幕控制都退化为 no-op：测试 / 非 TTY / dumb terminal。"""
	return bool(
		os.environ.get("PYTEST_CURRENT_TEST")
		or not sys.stderr.isatty()
		or os.environ.get("TERM", "").lower() in {"", "dumb", "unknown"}
	)


def clear_wizard_screen() -> None:
	"""Clear the terminal so only the current wizard page remains visible."""
	if _screen_control_unavailable():
		return
	# Home + clear scrollback-friendly clear for most terminals.
	sys.stderr.write("\033[H\033[2J")
	sys.stderr.flush()


@contextmanager
def wizard_screen() -> Iterator[None]:
	"""让整个交互会话独占一块备用屏缓冲。

	进入切 alternate screen、退出还原：向导期间终端像一个独立窗口，
	退出后原有内容完好，全程不往 scrollback 里留残框——clear_wizard_screen
	的 \033[2J 只清可视屏，清不掉历史，这是真人反馈里「上滚看到两个框」的根因。

	必须是 context manager：commands/wizard.py 有 59 处 return，
	外加内部会 raise SystemExit，逐点还原必然漏掉某条路径。

	非 TTY / dumb terminal / 测试下是 no-op，Agent 与管道路径完全不受影响。
	"""
	if _screen_control_unavailable():
		yield
		return
	sys.stderr.write("\033[?1049h")
	sys.stderr.flush()
	try:
		yield
	finally:
		sys.stderr.write("\033[?1049l")
		sys.stderr.flush()


@contextmanager
def busy_status(message: str) -> Iterator[None]:
	"""TTY spinner for long steps; no-op under tests / non-TTY / dumb terminals."""
	if (
		os.environ.get("PYTEST_CURRENT_TEST")
		or not sys.stderr.isatty()
		or os.environ.get("TERM", "").lower() in {"", "dumb", "unknown"}
	):
		yield
		return
	with display.console.status(f"[cyan]{message}[/cyan]", spinner="dots"):
		yield


def render_error(
	*,
	code: str,
	message: str | None = None,
	recovery_action: str | None = None,
	command_for: Callable[[str], str] | None = None,
) -> None:
	message_text = display.escape_markup(error_message(code, message))
	display.console.print(Panel(message_text, title="操作未完成", border_style="red"))
	recovery = recovery_message(code, recovery_action, command_for=command_for)
	if recovery:
		display.console.print(f"[bold]下一步：[/bold]{display.escape_markup(recovery)}")


def render_cancelled() -> None:
	display.console.print("[dim]已退出向导，未执行新的操作。[/dim]")


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
	return value if isinstance(value, Mapping) else None


def _extract_job_payload(last_result: Any) -> dict[str, Any] | None:
	"""Pull a normalized job dict from StepResult / nested result shapes."""
	root = _as_mapping(last_result)
	if root is None:
		return None
	data = _as_mapping(root.get("data")) or root
	for key in ("job", "result"):
		candidate = _as_mapping(data.get(key))
		if candidate is None:
			continue
		# Normalized job from execute_candidate_detail has title/company.
		if (
			candidate.get("title")
			or candidate.get("jobName")
			or candidate.get("description")
			or candidate.get("postDescription")
		):
			if candidate.get("jobInfo") and isinstance(candidate.get("jobInfo"), Mapping):
				# Raw job_detail envelope
				job_info = candidate["jobInfo"]
				boss_info = _as_mapping(candidate.get("bossInfo")) or {}
				brand_info = _as_mapping(candidate.get("brandComInfo")) or {}
				return {
					"title": job_info.get("jobName", ""),
					"salary": job_info.get("salaryDesc", ""),
					"city": job_info.get("cityName", ""),
					"experience": job_info.get("experienceName", ""),
					"education": job_info.get("degreeName", ""),
					"company": brand_info.get("brandName", ""),
					"boss_name": boss_info.get("name", ""),
					"boss_title": boss_info.get("title", ""),
					"description": candidate.get("jobDetail") or job_info.get("postDescription", ""),
					"skills": job_info.get("jobLabels") or [],
					"security_id": candidate.get("security_id") or "",
					"job_id": candidate.get("job_id") or job_info.get("encryptJobId") or "",
					"lid": candidate.get("lid") or "",
				}
			return {
				"title": candidate.get("title") or candidate.get("jobName") or "",
				"salary": candidate.get("salary") or candidate.get("salaryDesc") or "",
				"city": candidate.get("city") or candidate.get("cityName") or "",
				"experience": candidate.get("experience") or candidate.get("experienceName") or "",
				"education": candidate.get("education") or candidate.get("degreeName") or "",
				"company": candidate.get("company") or candidate.get("brandName") or "",
				"boss_name": candidate.get("boss_name") or candidate.get("bossName") or "",
				"boss_title": candidate.get("boss_title") or candidate.get("bossTitle") or "",
				"description": candidate.get("description") or candidate.get("postDescription") or "",
				"skills": candidate.get("skills") or candidate.get("jobLabels") or [],
				"security_id": candidate.get("security_id") or candidate.get("securityId") or "",
				"job_id": candidate.get("job_id") or candidate.get("encryptJobId") or "",
				"address": candidate.get("address") or "",
				"lid": candidate.get("lid") or "",
			}
	# Flat normalized job on data itself
	if data.get("title") and (data.get("company") is not None or data.get("description") is not None):
		return dict(data)
	return None


def _count_list_items(last_result: Any) -> int | None:
	root = _as_mapping(last_result)
	if root is None:
		return None
	data = _as_mapping(root.get("data")) or root
	for key in (
		"items",
		"jobList",
		"recommendList",
		"geekList",
		"friendList",
		"list",
		"jobs",
		"messages",
		"messageList",
	):
		value = data.get(key)
		if isinstance(value, list):
			return len(value)
	# Platform envelopes often put the list directly under result.
	result = data.get("result")
	if isinstance(result, list):
		return len(result)
	result_map = _as_mapping(result)
	if result_map is not None:
		for key in (
			"items",
			"jobList",
			"recommendList",
			"geekList",
			"friendList",
			"list",
			"jobs",
			"result",
			"messages",
			"messageList",
		):
			value = result_map.get(key)
			if isinstance(value, list):
				return len(value)
	return None


def render_job_card_zh(job: Mapping[str, Any]) -> None:
	"""Human-facing Chinese job detail panel for TTY wizard."""
	esc = display.escape_markup
	title = esc(_first_safe_text(job, ("title",), "未命名职位"))
	salary = esc(_first_safe_text(job, ("salary",), "-"))
	company = esc(_company_text(job) or "-")
	city = esc(_first_safe_text(job, ("city",), "-"))
	experience = esc(_first_safe_text(job, ("experience",), "-"))
	education = esc(_first_safe_text(job, ("education",), "-"))
	boss = esc(_first_safe_text(job, ("boss_name",), "-"))
	boss_title = esc(_first_safe_text(job, ("boss_title",)))
	# 与 display.render_job_detail 同口径：字符串按单条、其余非列表按空（Issue #2252）；
	# 列表元素同样过安全标量，容器 / 超大整数不显示 repr 或中断面板（Issue #2812）。
	skills = display.label_items(job.get("skills")) or display.label_items(job.get("labels"))
	skill_texts = [text for item in skills[:12] if (text := _safe_text(item))]
	skill_str = "、".join(esc(item) for item in skill_texts) if skill_texts else "-"
	desc = _safe_text(job.get("description"))
	if len(desc) > 420:
		desc = desc[:420] + "…"
	desc = esc(desc)

	lines = [
		f"[bold cyan]{title}[/bold cyan]  [yellow]{salary}[/yellow]",
		f"公司 {company}  ·  城市 {city}",
		f"经验 {experience}  ·  学历 {education}",
		f"技能 {skill_str}",
		f"招聘者 {boss}" + (f"（{boss_title}）" if boss_title else ""),
	]
	address_text = _safe_text(job.get("address"))
	if address_text:
		lines.append(f"地址 {esc(address_text)}")
	if desc:
		lines.append("")
		lines.append(f"[bold]职位描述[/bold]\n{desc}")
	display.console.print(Panel("\n".join(lines), title="职位详情", border_style="cyan"))


def render_crawl_job_brief(job: Mapping[str, Any], *, with_description: bool = False) -> None:
	"""On-demand local crawl row: compact by default, optional short description."""
	esc = display.escape_markup
	title = esc(_first_safe_text(job, ("title",), "未命名职位"))
	salary = esc(_first_safe_text(job, ("salary",), "-"))
	company = esc(_company_text(job) or "-")
	city = esc(_first_safe_text(job, ("city",), "-"))
	experience = esc(_first_safe_text(job, ("experience",), "-"))
	education = esc(_first_safe_text(job, ("education",), "-"))
	boss = esc(_first_safe_text(job, ("boss_name",)))
	lines = [
		f"[bold cyan]{title}[/bold cyan]  [yellow]{salary}[/yellow]",
		f"公司 {company}  ·  城市 {city}",
		f"经验 {experience}  ·  学历 {education}",
	]
	if boss:
		boss_title = esc(_first_safe_text(job, ("boss_title",)))
		lines.append(f"招聘者 {boss}" + (f"（{boss_title}）" if boss_title else ""))
	labels = display.label_items(job.get("labels"))
	label_texts = [text for item in labels[:8] if (text := _safe_text(item))]
	if label_texts:
		lines.append("标签 " + "、".join(esc(item) for item in label_texts))
	page_text = _safe_text(job.get("crawl_page"))
	if page_text:
		# 断点里的计数可能漂移成任意字符串：按外部值转义（Issue #2252）。
		lines.append(f"采集页 第 {esc(page_text)} 页")
	if with_description:
		desc = esc(_safe_text(job.get("description"))).strip()
		if desc:
			lines.append("")
			lines.append(f"[bold]本地描述[/bold]\n{desc}")
		else:
			lines.append("")
			lines.append("[dim]本地暂无职位描述（采集时未拉详情）。可再选「在线职位详情」。[/dim]")
	display.console.print(Panel("\n".join(lines), title="采集职位（本地）", border_style="green"))


def render_run(
	run: Mapping[str, Any],
	*,
	with_preview: bool = True,
	command_for: Callable[[str], str] | None = None,
) -> None:
	"""Single, human-first summary: content card or one error panel — not a debug dump.

	``with_preview=False`` 用于结果列表上方：那里下方已有可选职位菜单，
	框里再列一遍职位既冗余、页码也跟菜单对不上。``command_for`` 让失败面板
	的恢复命令带上本次运行的数据目录（Issue #1090）。
	"""
	status = _safe_text(run.get("status"))
	if status == "failed":
		raw_error = run.get("error")
		error = _as_mapping(raw_error) or {}
		raw_text = "" if raw_error is None or isinstance(raw_error, Mapping) else _safe_text(raw_error)
		render_error(
			code=_safe_text(error.get("code")) or raw_text or "NETWORK_ERROR",
			message=_safe_text(error.get("message")) or raw_text,
			recovery_action=_safe_text(error.get("recovery_action")) or None,
			command_for=command_for,
		)
		return

	# Stale waiting_input after a later successful crawl: show results, not a yellow wait card.
	if status == "waiting_input" and run.get("effective_completed") is True:
		if _render_crawl_summary(run, with_preview=with_preview):
			return

	if status == "waiting_input":
		last = _as_mapping(run.get("last_result")) or {}
		data = _as_mapping(last.get("data")) or {}
		# Live crawl already finished with jobs — prefer crawl result panel.
		live = _safe_text(data.get("live_crawl_status")) or _safe_text(data.get("status"))
		jobs_seen = _safe_count(data.get("jobs_seen"))
		if live == "completed" and (jobs_seen or 0) > 0:
			if _render_crawl_summary(run, with_preview=with_preview):
				return
		reason = display.escape_markup(
			_safe_text(data.get("error")) or _safe_text(last.get("next_action")) or "需要补充信息或人工处理后才能继续"
		)
		inner_text = _safe_text(data.get("run_id"))
		inner = display.escape_markup(inner_text) if inner_text else None
		lines = [
			f"[bold]{goal_label(run.get('role'), run.get('goal'))}[/bold] 需要你处理后再继续。",
			"",
			f"原因：{reason}",
		]
		if inner:
			lines.append(f"内部采集编号：{inner}")
		if (jobs_seen or 0) > 0:
			lines.append(f"本地已有职位：{_count_text(jobs_seen, '0')} 条（可在「查看采集进度与产物」中浏览）")
		operator_actions = last.get("operator_actions")
		if not isinstance(operator_actions, (list, tuple)):
			# 值级类型漂移（旧 run / 手工编辑）按缺失处理：字符串会被逐字符
			# 迭代成多条假指引，非序列类型直接中断状态面板（Issue #2369）。
			operator_actions = []
		action_lines = [text for item in operator_actions if (text := _safe_text(item))]
		if action_lines:
			# 数据驱动：step 自己声明「人该做什么」，renderer 不再猜。
			lines.append("")
			lines.extend(display.escape_markup(item) for item in action_lines)
		elif data.get("status") == "risk_stopped" or "非 JSON" in reason or "未登录" in reason or "环境" in reason:
			# 兜底：SQLite 里的历史 run 没有 operator_actions 字段，仍走旧的子串启发式。
			lines.append("")
			if data.get("browser_kept_open") is True:
				lines.extend(
					[
						"[green]采集用 Chrome 已保持打开[/green]。",
						"若窗口里已是正常职位列表：直接点「继续采集」（会用页面内请求重试，不必再找验证码页）。",
						"若仍失败且提示环境异常：在该窗口手动刷新职位搜索页后再点「继续采集」。",
						"窗口被关闭时，「继续采集」会重新打开并注入 boss login Cookie。",
					]
				)
			else:
				lines.extend(
					[
						"请选「继续采集」重试（会注入登录态；失败时会尝试页面内请求）。",
						"若提示环境异常，多半是采集专用浏览器指纹问题，不是主账号登录失效。",
					]
				)
		next_action = _safe_text(last.get("next_action"))
		# 风险路径的 operator_actions 已含「命令行恢复：<cmd>」：不能再追加
		# 一遍，否则同一命令连打两次（Issue #3053）。
		if next_action and not any(next_action in line for line in action_lines):
			lines.append(f"命令行恢复：{display.escape_markup(next_action)}")
		display.console.print(Panel("\n".join(lines), title="等待继续", border_style="yellow"))
		return

	# Crawl goals own a dedicated summary (metadata + sample titles + paths).
	if _render_crawl_summary(run, with_preview=with_preview):
		return

	# Management / digest / AI / empty-list friendly summaries.
	if _render_management_result(run, command_for=command_for):
		return

	job = _extract_job_payload(run.get("last_result"))
	if job is not None and (job.get("title") or job.get("description")):
		render_job_card_zh(job)
		return

	count = _count_list_items(run.get("last_result"))
	goal = goal_label(run.get("role"), run.get("goal"))
	if count is not None:
		if count == 0:
			_render_empty_result(run, command_for=command_for)
			return
		display.console.print(f"[green]已完成[/green] {goal}，共 [bold]{count}[/bold] 条结果可继续选择。")
		# Compact markdown preview so list goals are not “只有数字”。
		_render_list_preview_if_any(run)
		return

	# Compact fallback for non-list, non-job outcomes.
	table = Table(show_header=False, box=None, pad_edge=False)
	table.add_column("项目", style="dim", width=10, no_wrap=True)
	table.add_column("内容")
	table.add_row("事项", f"[bold]{goal}[/bold]")
	table.add_row("状态", f"[bold]{status_label(status)}[/bold]")
	run_id_text = _safe_text(run.get("run_id"))
	if run_id_text:
		table.add_row("任务编号", f"[dim]{display.escape_markup(run_id_text)}[/dim]")
	params = run.get("params") if isinstance(run.get("params"), Mapping) else {}
	inputs = params.get("inputs") if isinstance(params, Mapping) else {}
	if isinstance(inputs, Mapping) and inputs:
		pairs = []
		for key in ("query", "city", "run_id", "resume", "prompt", "name"):
			value_text = _safe_text(inputs.get(key))
			if value_text:
				pairs.append(f"{key}={value_text}")
		hint = " · ".join(pairs)
		if hint:
			table.add_row("参数", f"[bold]{display.escape_markup(hint)}[/bold]")
	last = _as_mapping(run.get("last_result")) or {}
	data = _as_mapping(last.get("data")) or {}
	result_text = _safe_text(data.get("result"))
	if result_text:
		if len(result_text) > 600:
			result_text = result_text[:600] + "…"
		table.add_row("结果", display.escape_markup(result_text))
	summary_text = _safe_text(data.get("summary"))
	if summary_text:
		table.add_row("摘要", f"[bold]{display.escape_markup(summary_text)}[/bold]")
	path_text = _safe_text(data.get("path"))
	if path_text:
		table.add_row("文件", f"[bold]{display.escape_markup(path_text)}[/bold]")
	artifacts = last.get("artifacts")
	if not isinstance(artifacts, (Mapping, list, tuple)):
		# 类型漂移的产物字段按缺失处理：字符串会被逐字符迭代成假产物、
		# 数字直接让面板崩溃（Issue #2370）。
		artifacts = {}
	if artifacts:
		names, directory = _short_artifact_lines(artifacts)
		if names:
			table.add_row("产物", names)
		if directory:
			table.add_row("目录", directory)
	display.console.print(Panel(table, title="任务状态", border_style="cyan"))


def _render_empty_result(
	run: Mapping[str, Any],
	*,
	command_for: Callable[[str], str] | None = None,
) -> None:
	"""Friendly empty-state panel for management goals with zero items."""
	goal = _safe_text(run.get("goal"))
	title = goal_label(run.get("role"), goal)
	hints: dict[str, str] = {
		"resumes": "当前还没有本地简历。可用命令导入：boss resume import <文件>",
		"shortlist": "候选池为空。可在职位列表里收藏，或从采集结果导入。",
		"pipeline": "暂无沟通/面试进度。先去「投递与沟通」打个招呼吧。",
		"watch": "还没有职位监控。可选「新建监控」保存一组搜索条件。",
		"communication": "沟通列表为空。可先搜索职位并打招呼。",
		"job_search": "未搜到职位，可换关键词或城市再试。",
		"recommendations": "暂无推荐结果。",
		"chat_history": "暂无聊天记录。",
		"export": "没有可导出的职位结果。",
		"candidates": "未找到候选人，可调整筛选条件。",
		"applications": "暂无投递申请。",
		"last_messages": "暂无最近消息。",
		"jobs_list": "暂无已发布职位。",
	}
	# 恢复命令可能内嵌 `--data-dir`（用户可控）：整段按字面量转义，路径里的
	# `[/bold]` 之类不能再当作 Rich 标签（Issue #2368）。
	message = display.escape_markup(
		_rewrite_recovery_commands(hints.get(goal, "没有可展示的结果。"), command_for)
	)
	display.console.print(
		Panel(
			f"[bold]{title}[/bold]\n\n[dim]{message}[/dim]",
			title="暂无内容",
			border_style="yellow",
		)
	)


def _list_items_from_run(
	run: Mapping[str, Any], *, preferred_keys: tuple[str, ...] = ()
) -> list[Mapping[str, Any]]:
	last = _as_mapping(run.get("last_result")) or {}
	data = _as_mapping(last.get("data")) or last
	keys = tuple(dict.fromkeys((*preferred_keys, "items")))
	for key in keys + (
		"jobs",
		"jobList",
		"recommendList",
		"friendList",
		"list",
		"geekList",
		"messages",
		"messageList",
	):
		value = data.get(key)
		if isinstance(value, list):
			return [item for item in value if isinstance(item, Mapping)]
	result = data.get("result")
	if isinstance(result, list):
		return [item for item in result if isinstance(item, Mapping)]
	result_map = _as_mapping(result)
	if result_map is not None:
		for key in (
			"items",
			"jobs",
			"jobList",
			"recommendList",
			"friendList",
			"list",
			"geekList",
			"messages",
			"messageList",
			"result",
		):
			value = result_map.get(key)
			if isinstance(value, list):
				return [item for item in value if isinstance(item, Mapping)]
	return []


def _chat_direction_text(item: Mapping[str, Any]) -> str:
	"""聊天记录「方向」列：平台 `from` 可能是对象，不能直接当集合键判断。"""
	direction = item.get("from")
	if isinstance(direction, Mapping):
		name = direction.get("name")
		if isinstance(name, str) and name.strip():
			return name.strip()
		uid = direction.get("uid") if direction.get("uid") is not None else direction.get("id")
		if isinstance(uid, str) and uid.strip():
			return uid.strip()
		if type(uid) is int:
			return _safe_text(uid) or "对方"
		return "对方"
	if direction is None or direction == "":
		direction = item.get("role") or item.get("side")
	if direction is None or direction == "":
		return "我" if item.get("isSelf") is True or item.get("self") is True else "对方"
	if isinstance(direction, str):
		return direction.strip() or "对方"
	return (_safe_text(direction) if type(direction) is int else "") or "对方"


def _chat_content_text(item: Mapping[str, Any]) -> str:
	"""聊天记录「内容」列：优先正文字符串，body 是对象时读 body.text，不显示 repr。"""
	sources = [item]
	body = item.get("body")
	if isinstance(body, Mapping):
		sources.append(body)
	for source in sources:
		for key in ("text", "content"):
			text = message_text_value(source.get(key))
			if text:
				return text
	# 旧 checkpoint 可能把正文直接放在 body 字符串里；容器值仍不进 repr。
	body_text = message_text_value(body)
	return body_text or "—"


def _last_message_friend_ids(data: Mapping[str, Any]) -> set[int]:
	friend_ids = data.get("friend_ids")
	if isinstance(friend_ids, list):
		return {parsed for value in friend_ids if (parsed := as_friend_int(value)) is not None}
	# 旧断点可能只有消息列表。摘要中的 ID 可用于识别原始记录的 from/to。
	messages = data.get("messages")
	if not isinstance(messages, list):
		return set()
	return {
		parsed
		for message in messages
		if isinstance(message, Mapping) and (parsed := as_friend_int(message.get("friendId"))) is not None
	}


def _render_list_preview_if_any(run: Mapping[str, Any], *, limit: int = 8) -> None:
	"""Markdown-style preview for generic list goals (search / shortlist / pipeline…)."""
	from rich import box
	from rich.console import Group
	from rich.text import Text

	goal = _safe_text(run.get("goal"))
	last = _as_mapping(run.get("last_result")) or {}
	data = _as_mapping(last.get("data")) or last
	known_friend_ids = _last_message_friend_ids(data) if goal == "last_messages" else set()
	# 聊天记录的 messages 已与 CLI 同口径；裸 items 可能含 dict.from / dict.body。
	items = _list_items_from_run(run, preferred_keys=("messages",) if goal == "chat_history" else ())
	if not items:
		return
	table = Table(
		show_header=True,
		header_style="bold",
		box=box.MARKDOWN,
		pad_edge=False,
		expand=False,
		padding=(0, 1),
		border_style="dim",
	)
	if goal in {"resumes"}:
		table.add_column("#", style="dim", justify="right", no_wrap=True)
		table.add_column("简历", style="bold", no_wrap=True)
		table.add_column("更新", style="dim", no_wrap=True)
		for index, item in enumerate(items[:limit], start=1):
			updated = item.get("updated_at")
			if isinstance(updated, str) and "T" in updated:
				converted = display.iso_time_label(updated)
				if converted != "-":
					updated = converted
			table.add_row(
				str(index),
				_clip_text(_first_safe_text(item, ("name", "title"), "未命名简历"), 24),
				_clip_text(
					_first_safe_text(
						{"updated_at": updated, "created_at": item.get("created_at")}, ("updated_at", "created_at"), "—"
					),
					20,
				),
			)
	elif goal in {"watch"}:
		table.add_column("#", style="dim", justify="right", no_wrap=True)
		table.add_column("监控名", style="bold", no_wrap=True)
		table.add_column("关键词", style="dim", no_wrap=True)
		for index, item in enumerate(items[:limit], start=1):
			raw_params = item.get("params")
			params: Mapping[str, Any] = raw_params if isinstance(raw_params, Mapping) else {}
			table.add_row(
				str(index),
				_clip_text(_first_safe_text(item, ("name",), "未命名"), 16),
				_clip_text(
					_first_safe_text(
						{"query": params.get("query"), "fallback": item.get("query")}, ("query", "fallback"), "—"
					),
					24,
				),
			)
	elif goal in {"pipeline"}:
		table.add_column("#", style="dim", justify="right", no_wrap=True)
		table.add_column("事项", style="bold", overflow="ellipsis", max_width=24, no_wrap=True)
		table.add_column("公司", style="dim", overflow="ellipsis", max_width=14, no_wrap=True)
		table.add_column("阶段", style="cyan", no_wrap=True)
		for index, item in enumerate(items[:limit], start=1):
			table.add_row(
				str(index),
				_clip_text(_first_safe_text(item, ("title", "jobName"), "未命名"), 24),
				_clip_text(_first_safe_text(item, ("company", "brandName"), "—"), 14),
				_clip_text(_first_safe_text(item, ("stage", "relation"), "—"), 12),
			)
	elif goal in {"communication", "last_messages"}:
		table.add_column("#", style="dim", justify="right", no_wrap=True)
		table.add_column("联系人", style="bold", overflow="ellipsis", max_width=16, no_wrap=True)
		table.add_column("公司/职位", style="dim", overflow="ellipsis", max_width=20, no_wrap=True)
		table.add_column("最近消息", style="dim", overflow="ellipsis", max_width=28, no_wrap=True)
		for index, item in enumerate(items[:limit], start=1):
			name = _first_safe_text(item, ("name", "friendName", "geekName"))
			last_msg = _first_safe_text(item, ("lastMsg", "last_msg", "content"))
			if goal == "last_messages":
				# 逐条使用与 CLI 一致的识别规则。同一联系人可返回多条消息。
				raw = dict(item)
				name = name or _safe_text(friend_id_for(raw, known_friend_ids))
				last_msg = (
					_safe_text(last_message_text(raw, skip_blank=True))
					or last_msg
					or _safe_text(_chat_content_text(item))
				)
			table.add_row(
				str(index),
				_clip_text(name or "未命名", 16),
				_clip_text(_first_safe_text(item, ("brandName", "company", "jobName", "title"), "—"), 20),
				_clip_text(last_msg or "—", 28),
			)
	elif goal in {"chat_history"}:
		table.add_column("#", style="dim", justify="right", no_wrap=True)
		table.add_column("方向", style="cyan", no_wrap=True)
		table.add_column("内容", overflow="ellipsis", max_width=48, no_wrap=True)
		for index, item in enumerate(items[:limit], start=1):
			table.add_row(
				str(index),
				_clip_text(_chat_direction_text(item), 6),
				_clip_text(_chat_content_text(item), 48),
			)
	elif goal in {"candidates", "applications"}:
		table.add_column("#", style="dim", justify="right", no_wrap=True)
		table.add_column("候选人", style="bold", overflow="ellipsis", max_width=16, no_wrap=True)
		table.add_column("意向", style="dim", overflow="ellipsis", max_width=20, no_wrap=True)
		table.add_column("城市", style="dim", no_wrap=True)
		for index, item in enumerate(items[:limit], start=1):
			table.add_row(
				str(index),
				_clip_text(_first_safe_text(item, ("name", "geekName"), "未命名"), 16),
				_clip_text(_first_safe_text(item, ("expectPosition", "positionName", "jobName", "title"), "—"), 20),
				_clip_text(_first_safe_text(item, ("city", "cityName"), "—"), 8),
			)
	elif goal in {"jobs_list"}:
		table.add_column("#", style="dim", justify="right", no_wrap=True)
		table.add_column("职位", style="bold", overflow="ellipsis", max_width=28, no_wrap=True)
		table.add_column("状态", style="cyan", no_wrap=True)
		for index, item in enumerate(items[:limit], start=1):
			table.add_row(
				str(index),
				_clip_text(_first_safe_text(item, ("title", "jobName", "name"), "未命名"), 28),
				_clip_text(_first_safe_text(item, ("jobStatusDesc", "statusDesc", "status", "jobStatus"), "—"), 10),
			)
	else:
		table.add_column("#", style="dim", justify="right", no_wrap=True)
		table.add_column("标题", style="bold", overflow="ellipsis", max_width=28, no_wrap=True)
		table.add_column("公司", style="dim", overflow="ellipsis", max_width=14, no_wrap=True)
		table.add_column("城市", style="dim", no_wrap=True)
		table.add_column("薪资", style="yellow", justify="right", no_wrap=True)
		for index, item in enumerate(items[:limit], start=1):
			table.add_row(
				str(index),
				_clip_text(_first_safe_text(item, ("title", "jobName", "name"), "未命名"), 28),
				_clip_text(_first_safe_text(item, ("company", "brandName"), "—"), 14),
				_clip_text(_first_safe_text(item, ("city", "cityName"), "—"), 8),
				_clip_text(_first_safe_text(item, ("salary", "salaryDesc"), "—"), 12),
			)
	footer = Text()
	if len(items) > limit:
		footer.append(f"… 另有 {len(items) - limit} 条，可在下方列表中选择", style="dim")
	else:
		footer.append("可在下方列表中选择一项继续操作", style="dim")
	display.console.print(Group(table, footer))


def _render_management_result(
	run: Mapping[str, Any],
	*,
	command_for: Callable[[str], str] | None = None,
) -> bool:
	"""Rich panels for digest / AI / structured management outcomes."""
	from rich.console import Group
	from rich.text import Text

	goal = _safe_text(run.get("goal"))
	last = _as_mapping(run.get("last_result")) or {}
	data = _as_mapping(last.get("data")) or {}
	if not data:
		return False

	if goal == "digest":
		headline = Text()
		headline.append("● ", style="bold green")
		headline.append("求职日报", style="bold")
		summary = _safe_text(data.get("summary"))
		meta = Table(show_header=False, box=None, pad_edge=False)
		meta.add_column("k", style="dim", width=10)
		meta.add_column("v", style="bold")
		meta.add_row("新匹配", display.escape_markup(_count_text(data.get("new_match_count"), "0")))
		meta.add_row("待跟进", display.escape_markup(_count_text(data.get("follow_up_count"), "0")))
		meta.add_row("面试", display.escape_markup(_count_text(data.get("interview_count"), "0")))
		if summary:
			meta.add_row("摘要", display.escape_markup(summary))
		# Sample rows from each bucket.
		preview = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
		preview.add_column("类型", style="cyan", no_wrap=True)
		preview.add_column("内容", overflow="ellipsis", max_width=48)
		for kind, key in (("新匹配", "new_matches"), ("待跟进", "follow_ups"), ("面试", "interviews")):
			raw_bucket = data.get(key)
			bucket: list[Any] = raw_bucket if isinstance(raw_bucket, list) else []
			for item in bucket[:3]:
				if not isinstance(item, Mapping):
					continue
				label = _first_safe_text(item, ("title", "jobName", "name"), "—")
				company = _company_text(item)
				text = f"{label}" + (f" · {company}" if company else "")
				preview.add_row(kind, display.escape_markup(text))
		parts: list[Any] = [headline, Text(""), meta]
		if preview.row_count:
			parts.extend([Text(""), Text("明细预览", style="bold"), preview])
		else:
			parts.extend([Text(""), Text("暂无沟通/面试明细，日报计数均为 0。", style="dim")])
		display.console.print(Panel(Group(*parts), title="求职日报", border_style="green"))
		return True

	if goal == "ai_assist":
		result = _safe_text(data.get("result"))
		resume = _safe_text(data.get("resume"))
		if not result:
			return False
		body = Text()
		if resume:
			body.append(f"简历 {resume}\n\n", style="dim")
		clipped = result if len(result) <= 1200 else result[:1200] + "…"
		body.append(clipped)
		display.console.print(Panel(body, title="AI 辅助结果", border_style="cyan"))
		return True

	if goal == "watch":
		# list / add / run / remove shapes
		if isinstance(data.get("items"), list):
			count = len(data["items"])
			if count == 0:
				_render_empty_result(run, command_for=command_for)
				return True
			display.console.print(
				f"[green]已完成[/green] {goal_label(run.get('role'), goal)}，共 [bold]{count}[/bold] 条监控。"
			)
			_render_list_preview_if_any(run)
			return True
		if data.get("mode") == "all" and isinstance(data.get("watches"), list):
			# 批量运行（Issue #2698）：逐条列出新发现数或失败原因，损坏记录单独提示。
			batch = Table(box=None, pad_edge=False)
			batch.add_column("监控名", style="bold", no_wrap=True)
			batch.add_column("新发现", justify="right", no_wrap=True)
			batch.add_column("状态", style="dim")
			for entry in data["watches"]:
				if not isinstance(entry, Mapping):
					continue
				failed = entry.get("error")
				failed_text = _safe_text(failed)
				batch.add_row(
					display.escape_markup(_safe_text(entry.get("name")) or "—"),
					"—" if failed else display.escape_markup(_count_text(entry.get("new_count"))),
					display.escape_markup(failed_text) if failed_text else ("失败" if failed else "完成"),
				)
			display.console.print(Panel(batch, title=f"批量监控结果（{len(data['watches'])} 条）", border_style="cyan"))
			skipped = data.get("skipped")
			if isinstance(skipped, list) and skipped:
				names = "、".join(display.escape_markup(_safe_text(item) or "未命名") for item in skipped)
				display.console.print(f"[yellow]以下监控的参数已损坏、本次未执行：[/yellow]{names}")
			return True
		action = _safe_text(data.get("action"))
		name = _safe_text(data.get("name"))
		if action or name:
			table = Table(show_header=False, box=None, pad_edge=False)
			table.add_column("k", style="dim", width=10)
			table.add_column("v", style="bold")
			table.add_row("事项", goal_label(run.get("role"), goal))
			if action:
				table.add_row("操作", display.escape_markup(action))
			if name:
				table.add_row("名称", display.escape_markup(name))
			removed = data.get("removed")
			if isinstance(removed, bool):
				table.add_row("已删除", "是" if removed else "否")
			if data.get("new_count") is not None:
				table.add_row("新发现", display.escape_markup(_count_text(data.get("new_count"))))
			if data.get("total") is not None:
				table.add_row("命中", display.escape_markup(_count_text(data.get("total"))))
			display.console.print(Panel(table, title="监控结果", border_style="cyan"))
			return True

	list_goals = {
		"resumes",
		"pipeline",
		"shortlist",
		"job_search",
		"recommendations",
		"communication",
		"chat_history",
		"export",
		"candidates",
		"applications",
		"last_messages",
		"jobs_list",
	}
	if goal in list_goals:
		items = _list_items_from_run(run, preferred_keys=("messages",) if goal == "chat_history" else ())
		# export may only have path/count
		if goal == "export" and data.get("path"):
			table = Table(show_header=False, box=None, pad_edge=False)
			table.add_column("k", style="dim", width=10)
			table.add_column("v", style="bold")
			table.add_row("事项", goal_label(run.get("role"), goal))
			table.add_row("格式", display.escape_markup(_safe_text(data.get("format")) or "—"))
			count_value = _safe_count(data.get("count"))
			requested = _safe_count(data.get("requested"))
			if count_value is None:
				# 非数值 count 是外部漂移值：保留字面量并转义展示（Issue #2252），
				# 不能换成 items 长度把证据吞掉。
				count_text = _count_text(data.get("count"), str(len(items)))
			else:
				count_text = _count_text(count_value, str(len(items)))
				if requested is not None and requested > count_value:
					# 没取满请求条数时把请求量一起列出：真人才分得清「平台只有这些」
					# 与「被预算截断」（Issue #3054）。
					count_text = f"{count_text} / 请求 {requested}"
			table.add_row("条数", display.escape_markup(count_text))
			table.add_row("文件", display.escape_markup(_safe_text(data.get("path")) or "—"))
			truncated = data.get("truncated") is True
			if truncated:
				note = truncation_operator_action(_safe_text(data.get("stop_reason")))
				table.add_row("说明", f"[yellow]{display.escape_markup(note)}[/yellow]")
			display.console.print(
				Panel(
					table,
					title="导出结果被截断" if truncated else "导出完成",
					border_style="yellow" if truncated else "green",
				)
			)
			return True
		count = len(items)
		if count == 0:
			_render_empty_result(run, command_for=command_for)
			return True
		display.console.print(
			f"[green]已完成[/green] {goal_label(run.get('role'), goal)}，共 [bold]{count}[/bold] 条结果。"
		)
		_render_list_preview_if_any(run)
		return True

	# Write / action outcomes: apply, greet, mark, reply, job online/offline…
	action_goals = {
		"apply",
		"greet",
		"exchange",
		"mark",
		"reply",
		"exchange_contact",
		"request_resume",
		"jobs_online",
		"jobs_offline",
		"crawl_stop",
	}
	if goal in action_goals:
		table = Table(show_header=False, box=None, pad_edge=False)
		table.add_column("k", style="dim", width=10)
		table.add_column("v")
		table.add_row("事项", f"[bold]{goal_label(run.get('role'), goal)}[/bold]")
		table.add_row("状态", "[bold green]已完成[/bold green]")
		for key, label in (
			("security_id", "安全编号"),
			("job_id", "职位编号"),
			("friend_id", "联系人"),
			("label", "标签"),
			("run_id", "采集编号"),
		):
			value = _safe_text(data.get(key))
			if value:
				if key in {"security_id"} and len(value) > 18:
					value = value[:12] + "…"
				table.add_row(label, f"[bold]{display.escape_markup(value)}[/bold]")
		status_text = _safe_text(data.get("status"))
		if status_text:
			table.add_row("详情", f"[dim]{display.escape_markup(status_text)}[/dim]")
		display.console.print(Panel(table, title="操作成功", border_style="green"))
		return True

	if goal in {"candidate_resume", "jobs_detail"}:
		# Prefer a readable payload dump of key fields rather than a blank shell.
		payload = data.get("result") if isinstance(data.get("result"), Mapping) else data
		if isinstance(payload, Mapping) and payload:
			table = Table(show_header=False, box=None, pad_edge=False)
			table.add_column("k", style="dim", width=12)
			table.add_column("v")
			table.add_row("事项", f"[bold]{goal_label(run.get('role'), goal)}[/bold]")
			shown = 0
			for key in (
				"name",
				"geekName",
				"title",
				"jobName",
				"company",
				"brandName",
				"city",
				"salary",
				"status",
				"degree",
				"experience",
			):
				if payload.get(key) not in (None, ""):
					table.add_row(key, f"[bold]{_clip_text(payload.get(key), 40)}[/bold]")
					shown += 1
				if shown >= 8:
					break
			if shown:
				display.console.print(Panel(table, title="详情", border_style="cyan"))
				return True

	return False


def _city_display(city_code: Any) -> str:
	"""Map BOSS city codes to Chinese names when possible."""
	code = _safe_text(city_code)
	if not code:
		return ""
	try:
		from boss_agent_cli.api.endpoints import CITY_CODES

		for name, value in CITY_CODES.items():
			if str(value) == code:
				return str(name)
	except Exception:
		pass
	return code


def _short_artifact_lines(paths: Mapping[str, Any] | Sequence[Any]) -> tuple[str, str]:
	"""Return (file basenames line, optional parent dir line)."""
	entries: list[tuple[str, Path]] = []
	if isinstance(paths, Mapping):
		for kind, raw in paths.items():
			text = _safe_text(raw)
			if not text or any(ord(char) < 32 for char in text):
				continue
			try:
				path = Path(text)
			except (OSError, ValueError):
				continue
			entries.append((_safe_text(kind), path))
	elif isinstance(paths, (str, bytes)):
		# 单个路径不是产物列表：旧 run 的字段漂移不能让字符串被逐字符拆开
		# 显示成十几条假产物（Issue #2370）。
		return "", ""
	else:
		for raw in paths:
			text = _safe_text(raw)
			if not text or any(ord(char) < 32 for char in text):
				continue
			try:
				path = Path(text)
			except (OSError, ValueError):
				continue
			entries.append((path.suffix.lstrip(".") or "file", path))
	if not entries:
		return "", ""
	names = "  ".join(
		f"[bold]{display.escape_markup(kind)}[/bold] {display.escape_markup(path.name)}" for kind, path in entries
	)
	parent = str(entries[0][1].parent)
	# Prefer compact home-relative path.
	home = str(Path.home())
	if parent.startswith(home):
		parent = "~" + parent[len(home) :]
	return names, f"[dim]{display.escape_markup(parent)}[/dim]"


def _safe_text(value: Any, default: str = "") -> str:
	"""表格文本字段：容器 / 布尔 / 非有限数按缺失，整数走 ``safe_text`` 不预检位长。"""
	if value is None or isinstance(value, (dict, list, tuple, bool)):
		return default
	if isinstance(value, float) and not math.isfinite(value):
		return default
	return safe_text(value).strip() or default


def _first_safe_text(item: Any, keys: tuple[str, ...], default: str = "") -> str:
	"""按优先级取第一个可用文本；前一键漂移成容器 / 非文本时不吞掉后续回退。"""
	if not isinstance(item, Mapping):
		return default
	for key in keys:
		text = _safe_text(item.get(key))
		if text:
			return text
	return default


def _count_text(value: Any, default: str = "—") -> str:
	return _safe_text(value, default)


def _safe_count(value: Any) -> int | None:
	"""展示层计数：漂移 / 超大整数（``str()`` 抛 ``ValueError``）按缺失处理。"""
	count = count_or_none(value)
	return count if count is not None and _count_text(count) != "—" else None


def _company_text(item: Mapping[str, Any]) -> str:
	"""公司名按既有优先级取：company 再 brandName；对象取 name / brandName。"""
	for key in ("company", "brandName"):
		value = item.get(key)
		if isinstance(value, Mapping):
			text = _first_safe_text(value, ("name", "brandName"))
		else:
			text = _safe_text(value)
		if text:
			return text
	return ""


def _clip_text(value: Any, limit: int) -> str:
	"""裁剪成表格单元格用的单行文本，并按 Rich 字面量转义（见 ``display.escape_markup``）。"""
	text = " ".join(_safe_text(value).split())
	if len(text) <= limit:
		return display.escape_markup(text)
	if limit <= 1:
		return display.escape_markup(text[:limit])
	return display.escape_markup(text[: limit - 1] + "…")


def _preview_jobs_from_data(data: Mapping[str, Any], *, limit: int = 5) -> list[dict[str, str]]:
	"""Build structured preview rows from jobs or fallback sample_titles."""
	rows: list[dict[str, str]] = []
	jobs = data.get("jobs")
	if isinstance(jobs, list):
		for job in jobs:
			if not isinstance(job, Mapping):
				continue
			title = _clip_text(_first_safe_text(job, ("title", "jobName"), "未命名职位"), 28)
			company = _clip_text(_company_text(job) or "-", 14)
			city = _clip_text(_first_safe_text(job, ("city", "cityName")), 8)
			salary = _clip_text(_first_safe_text(job, ("salary", "salaryDesc")), 12)
			rows.append({"title": title, "company": company, "city": city, "salary": salary})
			if len(rows) >= limit:
				return rows
	if rows:
		return rows
	# Fallback: "title · company" strings from sample_titles.
	samples = data.get("sample_titles")
	if isinstance(samples, list):
		for raw in samples[:limit]:
			text = _safe_text(raw)
			if not text:
				continue
			if " · " in text:
				left, right = text.split(" · ", 1)
			elif "·" in text:
				left, right = text.split("·", 1)
			else:
				left, right = text, ""
			rows.append(
				{
					"title": _clip_text(left, 28),
					"company": _clip_text(right, 14) or "-",
					"city": "",
					"salary": "",
				}
			)
	return rows


def _render_job_preview_table(
	rows: Sequence[Mapping[str, str]],
	*,
	total: int | None = None,
) -> Table:
	"""Markdown-style preview table: # | 职位 | 公司 | 城市 | 薪资."""
	from rich import box

	table = Table(
		show_header=True,
		header_style="bold",
		box=box.MARKDOWN,
		pad_edge=False,
		expand=False,
		padding=(0, 1),
		show_edge=True,
		collapse_padding=False,
		border_style="dim",
	)
	table.add_column("#", style="dim", justify="right", no_wrap=True)
	table.add_column("职位", style="bold", overflow="ellipsis", max_width=32, no_wrap=True)
	table.add_column("公司", style="dim", overflow="ellipsis", max_width=16, no_wrap=True)
	table.add_column("城市", style="dim", no_wrap=True)
	table.add_column("薪资", style="yellow", justify="right", no_wrap=True)
	for index, row in enumerate(rows, start=1):
		table.add_row(
			str(index),
			str(row.get("title") or "未命名职位"),
			str(row.get("company") or "-"),
			str(row.get("city") or "—"),
			str(row.get("salary") or "—"),
		)
	if total is not None and total > len(rows):
		remaining = _count_text(total - len(rows))
		if remaining != "—":
			table.add_row(
				"…",
				f"[dim]另有 {remaining} 条，选下方「浏览职位列表」查看[/dim]",
				"",
				"",
				"",
			)
	return table


def _render_crawl_summary(run: Mapping[str, Any], *, with_preview: bool = True) -> bool:
	"""Rich summary for crawl_start/resume/status results. Returns True if rendered."""
	from rich.console import Group
	from rich.rule import Rule
	from rich.text import Text

	goal = _safe_text(run.get("goal"))
	if goal not in {"crawl_start", "crawl_resume", "crawl_status", "crawl_stop"}:
		return False
	last = _as_mapping(run.get("last_result")) or {}
	data = _as_mapping(last.get("data")) or {}
	if not data and not last:
		return False

	crawl_status = (
		_safe_text(data.get("live_crawl_status")) or _safe_text(data.get("status")) or _safe_text(last.get("status"))
	)
	jobs_seen = _safe_count(data.get("jobs_seen"))
	# 向导 run 的 completed 只说明本次动作返回了：crawl_status / crawl_stop 是
	# 查询型动作，采集任务可能仍在运行或被风控暂停（Issue #3053）。只有
	# crawl_start / crawl_resume 的 run 完成才等价于采集结束；旧 run 没有
	# data.status 时仍靠这条兜底。
	completed_like = (
		str(crawl_status) == "completed"
		or run.get("effective_completed") is True
		or (
			goal in {"crawl_start", "crawl_resume"}
			and _safe_text(run.get("status")) == "completed"
		)
	)
	jobs_seen_text = _count_text(jobs_seen, "0")
	success = completed_like and jobs_seen is not None and jobs_seen > 0

	# ── Headline: strongest visual weight ───────────────────────────
	headline = Text()
	if success:
		headline.append("● ", style="bold green")
		headline.append(f"{jobs_seen_text} 个职位", style="bold cyan")
	else:
		status_text = "已完成" if str(crawl_status) == "completed" else crawl_status_label(crawl_status)
		headline.append("● ", style="bold yellow")
		headline.append(status_text, style="bold yellow")
		if jobs_seen is not None:
			headline.append(f"  ·  已有 {jobs_seen_text} 个职位", style="bold")

	query = _safe_text(data.get("query"))
	city = _city_display(data.get("city_code"))
	pages = data.get("pages_completed")
	pages_text = _safe_text(pages)
	meta_bits = [bit for bit in (query, city, f"{pages_text} 页" if pages_text else "") if bit]
	if meta_bits:
		headline.append("\n")
		headline.append("  " + "  ·  ".join(meta_bits), style="dim")

	# ── Meta first: 事项 / 状态 / 产物 … then job preview below ─────
	meta = Table(show_header=False, box=None, pad_edge=False, expand=True)
	meta.add_column("k", style="dim", width=10, no_wrap=True)
	meta.add_column("v", overflow="fold")

	def row(label: str, value: str) -> None:
		meta.add_row(label, value)

	row("事项", f"[bold]{goal_label(run.get('role'), goal)}[/bold]")
	# 采集状态优先：查询型 run 的「已完成」不是采集结果（Issue #3053）。
	if crawl_status:
		status_display = crawl_status_label(crawl_status)
	else:
		wf_status = _safe_text(run.get("display_status")) or _safe_text(run.get("status"))
		if run.get("effective_completed") is True:
			wf_status = "completed"
		status_display = status_label(wf_status)
	if success:
		row("状态", f"[bold green]{status_display}[/bold green]")
	elif str(crawl_status) == "risk_stopped":
		row("状态", "[bold yellow]风控暂停[/bold yellow]")
	else:
		row("状态", f"[bold]{status_display}[/bold]")

	if jobs_seen is not None and not success:
		row("职位数", f"[bold cyan]{jobs_seen_text}[/bold cyan]")
	# 进度计数来自断点 / 命令结果，类型漂移成字符串时不能当 markup 解析（Issue #2252）。
	esc = display.escape_markup
	if pages_text and not success:
		row("已完成页", f"[bold]{esc(pages_text)}[/bold]")
	next_page_text = _safe_text(data.get("next_page"))
	if next_page_text and str(crawl_status) != "completed":
		row("下一页", f"[bold]{esc(next_page_text)}[/bold]")
	pending = data.get("details_pending")
	done = data.get("details_completed")
	if pending is not None or done is not None:
		row(
			"详情进度",
			f"完成 [bold]{esc(_count_text(done, '0'))}[/bold]  ·  待补 [bold]{esc(_count_text(pending, '0'))}[/bold]",
		)

	# Artifacts: short names, not full absolute paths.
	paths = data.get("output_paths") if isinstance(data.get("output_paths"), Mapping) else {}
	artifact_names = ""
	artifact_dir = ""
	if paths:
		artifact_names, artifact_dir = _short_artifact_lines(paths)
	else:
		artifacts = last.get("artifacts")
		if isinstance(artifacts, (Mapping, list, tuple)):
			if artifacts:
				artifact_names, artifact_dir = _short_artifact_lines(artifacts)
	if artifact_names:
		row("产物", artifact_names)
	if artifact_dir:
		row("目录", artifact_dir)

	error_text = _safe_text(data.get("error"))
	if error_text and str(crawl_status) != "completed":
		row("说明", f"[yellow]{display.escape_markup(error_text)}[/yellow]")

	# 风控解锁等人工步骤由 action 以 operator_actions 声明；查询型 run 不是
	# waiting_input，不会走「等待继续」面板，这里必须自己渲染（Issue #3053）。
	raw_operator_actions = last.get("operator_actions")
	if not isinstance(raw_operator_actions, (list, tuple)):
		raw_operator_actions = []
	operator_lines = [text for item in raw_operator_actions if (text := _safe_text(item))]

	# Secondary ids, dim — debugging aids.
	id_bits = []
	run_id_text = _safe_text(run.get("run_id"))
	if run_id_text:
		id_bits.append(f"向导 {display.escape_markup(run_id_text)}")
	inner = _safe_text(data.get("run_id")) or _safe_text(last.get("run_id"))
	if inner:
		id_bits.append(f"采集 {display.escape_markup(inner)}")
	if id_bits:
		row("编号", f"[dim]{'  ·  '.join(str(bit) for bit in id_bits)}[/dim]")

	footer_hint: Text | None = None
	if success:
		footer_hint = Text("选「浏览职位列表」分页查看  ·  单条可看本地摘要或在线详情", style="dim")
	# 查询型 action（crawl_status）的 run 一返回就是 completed，next_action
	# 不能只在 waiting_input 时读取，否则 running_stale 的 resume 指引会丢
	# （Issue #3053）。
	next_action = _safe_text(last.get("next_action"))
	if not next_action and _safe_text(run.get("status")) == "waiting_input":
		checkpoint = data.get("checkpoint")
		next_action = _safe_text(checkpoint.get("resume_command") if isinstance(checkpoint, Mapping) else None)
	if next_action and str(crawl_status) != "completed":
		row("下一步", f"[bold]{display.escape_markup(next_action)}[/bold]")

	# ── Preview under meta: Markdown-style pipe table ───────────────
	preview_rows = _preview_jobs_from_data(data, limit=5) if with_preview else []
	parts: list[Any] = [headline, Text(""), meta]

	if operator_lines:
		parts.append(Text(""))
		parts.append(Text("人工处理", style="bold"))
		# 命令里内嵌 --data-dir（用户可控路径）：整行按 Text 组装，不解析 markup。
		parts.extend(Text(line, style="yellow") for line in operator_lines)

	if preview_rows:
		shown = len(preview_rows)
		total_text = _count_text(jobs_seen, str(shown))
		section = Text()
		section.append("职位预览", style="bold")
		section.append(f"  ·  前 {shown}/{total_text} 条", style="dim")
		parts.extend(
			[
				Text(""),
				Rule(style="dim"),
				section,
				_render_job_preview_table(
					preview_rows,
					total=jobs_seen,
				),
			]
		)

	if footer_hint is not None:
		parts.append(footer_hint)

	border = (
		"green"
		if success
		else (
			"yellow"
			if _safe_text(run.get("status")) == "waiting_input"
			or str(crawl_status)
			in {
				"risk_stopped",
				"budget_stopped",
				"throttle_stopped",
				"stopped",
				"stop_requested",
			}
			else "cyan"
		)
	)
	panel_title = "采集已完成" if success else "采集任务状态"
	display.console.print(Panel(Group(*parts), title=panel_title, border_style=border))
	return True
