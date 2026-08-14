"""Chinese Rich stderr rendering for workflow progress and summaries."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

from rich.panel import Panel
from rich.table import Table

from boss_agent_cli import display
from boss_agent_cli.wizard.catalog import GOALS
from boss_agent_cli.wizard.prompts import PLATFORM_LABELS, ROLE_LABELS, STATUS_LABELS

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
	"RATE_LIMITED": "请求过于频繁",
	"ACCOUNT_RISK": "平台已暂停本次操作",
	"NOT_SUPPORTED": "当前平台不支持此事项",
	"JOB_NOT_FOUND": "没有找到指定任务",
	"WORKFLOW_TIMEOUT": "任务运行超时",
	"WORKFLOW_STOPPED": "任务已停止",
	"WORKFLOW_PLAN_MISMATCH": "任务信息与已有记录不一致",
	"INVALID_PARAM": "请求参数无效",
}

RECOVERY_LABELS = {
	"AUTH_REQUIRED": "请先登录当前平台，然后选择“恢复已有任务”。",
	"TOKEN_REFRESH_FAILED": "请重新登录当前平台，然后选择“恢复已有任务”。",
	"NETWORK_ERROR": "请检查网络后重试，或返回主菜单选择其他事项。",
	"RATE_LIMITED": "请稍后再试。",
	"ACCOUNT_RISK": "请停止自动操作并在平台内检查账号状态。",
	"NOT_SUPPORTED": "请返回并选择当前平台支持的事项。",
	"JOB_NOT_FOUND": "请返回主菜单后重新选择任务。",
	"WORKFLOW_TIMEOUT": "请选择“恢复已有任务”继续。",
	"WORKFLOW_PLAN_MISMATCH": "请使用原任务恢复，或新建任务。",
	"INVALID_PARAM": "请换一条结果重试，或返回结果列表重新选择。",
}

# 这些步骤对真人噪声大，TTY 进度里默认静默。
_SILENT_STEPS = {"auth_status"}


def role_label(value: Any) -> str:
	return ROLE_LABELS.get(str(value), "未知身份")


def platform_label(value: Any) -> str:
	return PLATFORM_LABELS.get(str(value), "其他招聘平台")


def goal_label(role: Any, goal: Any) -> str:
	definition = GOALS.get(str(role), {}).get(str(goal))
	return definition.description if definition else "自定义事项"


def step_label(value: Any) -> str:
	return STEP_LABELS.get(str(value), "处理任务步骤")


def status_label(value: Any) -> str:
	return STATUS_LABELS.get(str(value), "状态未知")


def error_message(code: str, message: str | None = None) -> str:
	"""Prefer platform Chinese text; fall back to localized code labels."""
	if message:
		text = str(message).strip()
		if text and any("\u4e00" <= char <= "\u9fff" for char in text):
			# Avoid showing opaque "NETWORK_ERROR" wrappers when platform explained it.
			if code == "NETWORK_ERROR" and text not in {"网络请求失败", "NETWORK_ERROR"}:
				return text
			if code == "INVALID_PARAM":
				return text
			if text not in ERROR_LABELS.values():
				return text
	return ERROR_LABELS.get(code) or (str(message).strip() if message else "任务执行时遇到问题")


def recovery_message(code: str, recovery_action: str | None = None) -> str | None:
	localized = RECOVERY_LABELS.get(code)
	if localized:
		return localized
	if recovery_action and any("\u4e00" <= char <= "\u9fff" for char in str(recovery_action)):
		return str(recovery_action)
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
		display.console.print(f"[yellow]正在重试[/yellow] {label}（第 {attempt} 次）")


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


def render_error(*, code: str, message: str | None = None, recovery_action: str | None = None) -> None:
	display.console.print(Panel(error_message(code, message), title="操作未完成", border_style="red"))
	recovery = recovery_message(code, recovery_action)
	if recovery:
		display.console.print(f"[bold]下一步：[/bold]{recovery}")


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
	title = str(job.get("title") or "未命名职位")
	salary = str(job.get("salary") or "-")
	company = str(job.get("company") or "-")
	city = str(job.get("city") or "-")
	experience = str(job.get("experience") or "-")
	education = str(job.get("education") or "-")
	boss = str(job.get("boss_name") or "-")
	boss_title = str(job.get("boss_title") or "")
	skills = job.get("skills") or job.get("labels") or []
	skill_str = "、".join(str(item) for item in skills[:12]) if skills else "-"
	desc = str(job.get("description") or "")
	if len(desc) > 420:
		desc = desc[:420] + "…"

	lines = [
		f"[bold cyan]{title}[/bold cyan]  [yellow]{salary}[/yellow]",
		f"公司 {company}  ·  城市 {city}",
		f"经验 {experience}  ·  学历 {education}",
		f"技能 {skill_str}",
		f"招聘者 {boss}" + (f"（{boss_title}）" if boss_title else ""),
	]
	if job.get("address"):
		lines.append(f"地址 {job.get('address')}")
	if desc:
		lines.append("")
		lines.append(f"[bold]职位描述[/bold]\n{desc}")
	display.console.print(Panel("\n".join(lines), title="职位详情", border_style="cyan"))


def render_crawl_job_brief(job: Mapping[str, Any], *, with_description: bool = False) -> None:
	"""On-demand local crawl row: compact by default, optional short description."""
	title = str(job.get("title") or "未命名职位")
	salary = str(job.get("salary") or "-")
	company = str(job.get("company") or "-")
	city = str(job.get("city") or "-")
	experience = str(job.get("experience") or "-")
	education = str(job.get("education") or "-")
	boss = str(job.get("boss_name") or "")
	lines = [
		f"[bold cyan]{title}[/bold cyan]  [yellow]{salary}[/yellow]",
		f"公司 {company}  ·  城市 {city}",
		f"经验 {experience}  ·  学历 {education}",
	]
	if boss:
		boss_title = str(job.get("boss_title") or "")
		lines.append(f"招聘者 {boss}" + (f"（{boss_title}）" if boss_title else ""))
	labels = job.get("labels") or []
	if isinstance(labels, list) and labels:
		lines.append("标签 " + "、".join(str(item) for item in labels[:8]))
	if job.get("crawl_page") is not None:
		lines.append(f"采集页 第 {job.get('crawl_page')} 页")
	if with_description:
		desc = str(job.get("description") or "").strip()
		if desc:
			lines.append("")
			lines.append(f"[bold]本地描述[/bold]\n{desc}")
		else:
			lines.append("")
			lines.append("[dim]本地暂无职位描述（采集时未拉详情）。可再选「在线职位详情」。[/dim]")
	display.console.print(Panel("\n".join(lines), title="采集职位（本地）", border_style="green"))


def render_result_preview(items: list[Mapping[str, Any]], *, kind: str) -> None:
	"""Optional compact preview (kept for tests/tools; interactive path no longer dual-renders)."""
	if not items:
		return
	table = Table(show_header=True, box=None, pad_edge=False)
	table.add_column("#", style="dim", no_wrap=True)
	table.add_column("标题")
	table.add_column("补充")
	for index, item in enumerate(items[:20], start=1):
		if kind in {"job", "shortlist"}:
			title = str(item.get("title") or item.get("jobName") or item.get("name") or "未命名职位")
			extra = str(item.get("company") or item.get("brandName") or "-")
		elif kind == "friend":
			title = str(item.get("name") or item.get("friendName") or "未命名联系人")
			extra = str(item.get("brandName") or item.get("lastMsg") or "-")
		elif kind == "pipeline":
			title = str(item.get("title") or "未命名事项")
			extra = str(item.get("company") or item.get("stage") or "-")
		else:
			title = str(item.get("name") or item.get("geekName") or "未命名候选人")
			extra = str(item.get("expectPosition") or item.get("jobName") or "-")
		table.add_row(str(index), title, extra)
	display.console.print(Panel(table, title=f"共 {min(len(items), 20)} 条", border_style="cyan"))


def render_run(run: Mapping[str, Any], *, with_preview: bool = True) -> None:
	"""Single, human-first summary: content card or one error panel — not a debug dump.

	``with_preview=False`` 用于结果列表上方：那里下方已有可选职位菜单，
	框里再列一遍职位既冗余、页码也跟菜单对不上。
	"""
	status = str(run.get("status") or "")
	if status == "failed":
		error = run.get("error") or {}
		render_error(
			code=str(error.get("code") or "NETWORK_ERROR"),
			message=str(error.get("message") or ""),
			recovery_action=error.get("recovery_action"),
		)
		return

	# Stale waiting_input after a later successful crawl: show results, not a yellow wait card.
	if status == "waiting_input" and run.get("effective_completed"):
		if _render_crawl_summary(run, with_preview=with_preview):
			return

	if status == "waiting_input":
		last = _as_mapping(run.get("last_result")) or {}
		data = _as_mapping(last.get("data")) or {}
		# Live crawl already finished with jobs — prefer crawl result panel.
		live = str(data.get("live_crawl_status") or data.get("status") or "")
		jobs_seen = data.get("jobs_seen")
		if live == "completed" and int(jobs_seen or 0) > 0:
			if _render_crawl_summary(run, with_preview=with_preview):
				return
		reason = str(data.get("error") or last.get("next_action") or "需要补充信息或人工处理后才能继续")
		inner = data.get("run_id")
		lines = [
			f"[bold]{goal_label(run.get('role'), run.get('goal'))}[/bold] 需要你处理后再继续。",
			"",
			f"原因：{reason}",
		]
		if inner:
			lines.append(f"内部采集编号：{inner}")
		if int(jobs_seen or 0) > 0:
			lines.append(f"本地已有职位：{jobs_seen} 条（可在「查看采集进度与产物」中浏览）")
		operator_actions = last.get("operator_actions") or []
		if operator_actions:
			# 数据驱动：step 自己声明「人该做什么」，renderer 不再猜。
			lines.append("")
			lines.extend(str(item) for item in operator_actions)
		elif data.get("status") == "risk_stopped" or "非 JSON" in reason or "未登录" in reason or "环境" in reason:
			# 兜底：SQLite 里的历史 run 没有 operator_actions 字段，仍走旧的子串启发式。
			lines.append("")
			if data.get("browser_kept_open"):
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
		next_action = last.get("next_action")
		if next_action:
			lines.append(f"命令行恢复：{next_action}")
		display.console.print(Panel("\n".join(lines), title="等待继续", border_style="yellow"))
		return

	# Crawl goals own a dedicated summary (metadata + sample titles + paths).
	if _render_crawl_summary(run, with_preview=with_preview):
		return

	# Management / digest / AI / empty-list friendly summaries.
	if _render_management_result(run):
		return

	job = _extract_job_payload(run.get("last_result"))
	if job is not None and (job.get("title") or job.get("description")):
		render_job_card_zh(job)
		return

	count = _count_list_items(run.get("last_result"))
	goal = goal_label(run.get("role"), run.get("goal"))
	if count is not None:
		if count == 0:
			_render_empty_result(run)
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
	if run.get("run_id"):
		table.add_row("任务编号", f"[dim]{run.get('run_id')}[/dim]")
	params = run.get("params") if isinstance(run.get("params"), Mapping) else {}
	inputs = params.get("inputs") if isinstance(params, Mapping) else {}
	if isinstance(inputs, Mapping) and inputs:
		hint = " · ".join(
			f"{key}={value}"
			for key, value in inputs.items()
			if value not in (None, "") and key in {"query", "city", "run_id", "resume", "prompt", "name"}
		)
		if hint:
			table.add_row("参数", f"[bold]{hint}[/bold]")
	last = _as_mapping(run.get("last_result")) or {}
	data = _as_mapping(last.get("data")) or {}
	if data.get("result"):
		text = str(data.get("result"))
		if len(text) > 600:
			text = text[:600] + "…"
		table.add_row("结果", text)
	if data.get("summary"):
		table.add_row("摘要", f"[bold]{data.get('summary')}[/bold]")
	if data.get("path"):
		table.add_row("文件", f"[bold]{data.get('path')}[/bold]")
	artifacts = last.get("artifacts") or []
	if artifacts:
		names, directory = _short_artifact_lines(artifacts)
		if names:
			table.add_row("产物", names)
		if directory:
			table.add_row("目录", directory)
	display.console.print(Panel(table, title="任务状态", border_style="cyan"))


def _render_empty_result(run: Mapping[str, Any]) -> None:
	"""Friendly empty-state panel for management goals with zero items."""
	goal = str(run.get("goal") or "")
	title = goal_label(run.get("role"), goal)
	hints = {
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
	message = hints.get(goal, "没有可展示的结果。")
	display.console.print(
		Panel(
			f"[bold]{title}[/bold]\n\n[dim]{message}[/dim]",
			title="暂无内容",
			border_style="yellow",
		)
	)


def _list_items_from_run(run: Mapping[str, Any]) -> list[Mapping[str, Any]]:
	last = _as_mapping(run.get("last_result")) or {}
	data = _as_mapping(last.get("data")) or last
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


def _render_list_preview_if_any(run: Mapping[str, Any], *, limit: int = 8) -> None:
	"""Markdown-style preview for generic list goals (search / shortlist / pipeline…)."""
	from rich import box
	from rich.console import Group
	from rich.text import Text

	items = _list_items_from_run(run)
	if not items:
		return
	goal = str(run.get("goal") or "")
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
			table.add_row(
				str(index),
				_clip_text(item.get("name") or item.get("title") or "未命名简历", 24),
				_clip_text(item.get("updated_at") or item.get("created_at") or "—", 20),
			)
	elif goal in {"watch"}:
		table.add_column("#", style="dim", justify="right", no_wrap=True)
		table.add_column("监控名", style="bold", no_wrap=True)
		table.add_column("关键词", style="dim", no_wrap=True)
		for index, item in enumerate(items[:limit], start=1):
			params = item.get("params") if isinstance(item.get("params"), Mapping) else {}
			table.add_row(
				str(index),
				_clip_text(item.get("name") or "未命名", 16),
				_clip_text((params or {}).get("query") or item.get("query") or "—", 24),
			)
	elif goal in {"pipeline"}:
		table.add_column("#", style="dim", justify="right", no_wrap=True)
		table.add_column("事项", style="bold", overflow="ellipsis", max_width=24, no_wrap=True)
		table.add_column("公司", style="dim", overflow="ellipsis", max_width=14, no_wrap=True)
		table.add_column("阶段", style="cyan", no_wrap=True)
		for index, item in enumerate(items[:limit], start=1):
			table.add_row(
				str(index),
				_clip_text(item.get("title") or item.get("jobName") or "未命名", 24),
				_clip_text(item.get("company") or item.get("brandName") or "—", 14),
				_clip_text(item.get("stage") or item.get("relation") or "—", 12),
			)
	elif goal in {"communication", "last_messages"}:
		table.add_column("#", style="dim", justify="right", no_wrap=True)
		table.add_column("联系人", style="bold", overflow="ellipsis", max_width=16, no_wrap=True)
		table.add_column("公司/职位", style="dim", overflow="ellipsis", max_width=20, no_wrap=True)
		table.add_column("最近消息", style="dim", overflow="ellipsis", max_width=28, no_wrap=True)
		for index, item in enumerate(items[:limit], start=1):
			table.add_row(
				str(index),
				_clip_text(item.get("name") or item.get("friendName") or item.get("geekName") or "未命名", 16),
				_clip_text(
					item.get("brandName")
					or item.get("company")
					or item.get("jobName")
					or item.get("title")
					or "—",
					20,
				),
				_clip_text(item.get("lastMsg") or item.get("last_msg") or item.get("content") or "—", 28),
			)
	elif goal in {"chat_history"}:
		table.add_column("#", style="dim", justify="right", no_wrap=True)
		table.add_column("方向", style="cyan", no_wrap=True)
		table.add_column("内容", overflow="ellipsis", max_width=48, no_wrap=True)
		for index, item in enumerate(items[:limit], start=1):
			direction = item.get("from") or item.get("role") or item.get("side") or ""
			if direction in {"", None}:
				direction = "我" if item.get("isSelf") or item.get("self") else "对方"
			table.add_row(
				str(index),
				_clip_text(direction, 6),
				_clip_text(item.get("content") or item.get("text") or item.get("body") or "—", 48),
			)
	elif goal in {"candidates", "applications"}:
		table.add_column("#", style="dim", justify="right", no_wrap=True)
		table.add_column("候选人", style="bold", overflow="ellipsis", max_width=16, no_wrap=True)
		table.add_column("意向", style="dim", overflow="ellipsis", max_width=20, no_wrap=True)
		table.add_column("城市", style="dim", no_wrap=True)
		for index, item in enumerate(items[:limit], start=1):
			table.add_row(
				str(index),
				_clip_text(item.get("name") or item.get("geekName") or "未命名", 16),
				_clip_text(item.get("expectPosition") or item.get("jobName") or item.get("title") or "—", 20),
				_clip_text(item.get("city") or item.get("cityName") or "—", 8),
			)
	elif goal in {"jobs_list"}:
		table.add_column("#", style="dim", justify="right", no_wrap=True)
		table.add_column("职位", style="bold", overflow="ellipsis", max_width=28, no_wrap=True)
		table.add_column("状态", style="cyan", no_wrap=True)
		for index, item in enumerate(items[:limit], start=1):
			table.add_row(
				str(index),
				_clip_text(item.get("title") or item.get("jobName") or item.get("name") or "未命名", 28),
				_clip_text(item.get("status") or item.get("jobStatus") or "—", 10),
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
				_clip_text(item.get("title") or item.get("jobName") or item.get("name") or "未命名", 28),
				_clip_text(item.get("company") or item.get("brandName") or "—", 14),
				_clip_text(item.get("city") or item.get("cityName") or "—", 8),
				_clip_text(item.get("salary") or item.get("salaryDesc") or "—", 12),
			)
	footer = Text()
	if len(items) > limit:
		footer.append(f"… 另有 {len(items) - limit} 条，可在下方列表中选择", style="dim")
	else:
		footer.append("可在下方列表中选择一项继续操作", style="dim")
	display.console.print(Group(table, footer))


def _render_management_result(run: Mapping[str, Any]) -> bool:
	"""Rich panels for digest / AI / structured management outcomes."""
	from rich.console import Group
	from rich.text import Text

	goal = str(run.get("goal") or "")
	last = _as_mapping(run.get("last_result")) or {}
	data = _as_mapping(last.get("data")) or {}
	if not data:
		return False

	if goal == "digest":
		headline = Text()
		headline.append("● ", style="bold green")
		headline.append("求职日报", style="bold")
		summary = str(data.get("summary") or "")
		meta = Table(show_header=False, box=None, pad_edge=False)
		meta.add_column("k", style="dim", width=10)
		meta.add_column("v", style="bold")
		meta.add_row("新匹配", str(data.get("new_match_count") or 0))
		meta.add_row("待跟进", str(data.get("follow_up_count") or 0))
		meta.add_row("面试", str(data.get("interview_count") or 0))
		if summary:
			meta.add_row("摘要", summary)
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
				label = item.get("title") or item.get("jobName") or item.get("name") or "—"
				company = item.get("company") or item.get("brandName") or ""
				preview.add_row(kind, f"{label}" + (f" · {company}" if company else ""))
		parts: list[Any] = [headline, Text(""), meta]
		if preview.row_count:
			parts.extend([Text(""), Text("明细预览", style="bold"), preview])
		else:
			parts.extend([Text(""), Text("暂无沟通/面试明细，日报计数均为 0。", style="dim")])
		display.console.print(Panel(Group(*parts), title="求职日报", border_style="green"))
		return True

	if goal == "ai_assist":
		result = str(data.get("result") or "").strip()
		resume = str(data.get("resume") or "")
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
				_render_empty_result(run)
				return True
			display.console.print(
				f"[green]已完成[/green] {goal_label(run.get('role'), goal)}，共 [bold]{count}[/bold] 条监控。"
			)
			_render_list_preview_if_any(run)
			return True
		action = str(data.get("action") or "")
		name = str(data.get("name") or "")
		if action or name:
			table = Table(show_header=False, box=None, pad_edge=False)
			table.add_column("k", style="dim", width=10)
			table.add_column("v", style="bold")
			table.add_row("事项", goal_label(run.get("role"), goal))
			if action:
				table.add_row("操作", action)
			if name:
				table.add_row("名称", name)
			if data.get("removed") is not None:
				table.add_row("已删除", "是" if data.get("removed") else "否")
			if data.get("new_count") is not None:
				table.add_row("新发现", str(data.get("new_count")))
			if data.get("total") is not None:
				table.add_row("命中", str(data.get("total")))
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
		items = _list_items_from_run(run)
		# export may only have path/count
		if goal == "export" and data.get("path"):
			table = Table(show_header=False, box=None, pad_edge=False)
			table.add_column("k", style="dim", width=10)
			table.add_column("v", style="bold")
			table.add_row("事项", goal_label(run.get("role"), goal))
			table.add_row("格式", str(data.get("format") or "—"))
			table.add_row("条数", str(data.get("count") if data.get("count") is not None else len(items)))
			table.add_row("文件", str(data.get("path")))
			display.console.print(Panel(table, title="导出完成", border_style="green"))
			return True
		count = len(items)
		if count == 0:
			_render_empty_result(run)
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
			if data.get(key) not in (None, ""):
				value = str(data.get(key))
				if key in {"security_id"} and len(value) > 18:
					value = value[:12] + "…"
				table.add_row(label, f"[bold]{value}[/bold]")
		if data.get("status"):
			table.add_row("详情", f"[dim]{data.get('status')}[/dim]")
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
	code = str(city_code or "").strip()
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
			path = Path(str(raw))
			entries.append((str(kind), path))
	else:
		for raw in paths:
			path = Path(str(raw))
			entries.append((path.suffix.lstrip(".") or "file", path))
	if not entries:
		return "", ""
	names = "  ".join(f"[bold]{kind}[/bold] {path.name}" for kind, path in entries)
	parent = str(entries[0][1].parent)
	# Prefer compact home-relative path.
	home = str(Path.home())
	if parent.startswith(home):
		parent = "~" + parent[len(home) :]
	return names, f"[dim]{parent}[/dim]"


def _clip_text(value: Any, limit: int) -> str:
	text = " ".join(str(value or "").split())
	if len(text) <= limit:
		return text
	if limit <= 1:
		return text[:limit]
	return text[: limit - 1] + "…"


def _preview_jobs_from_data(data: Mapping[str, Any], *, limit: int = 5) -> list[dict[str, str]]:
	"""Build structured preview rows from jobs or fallback sample_titles."""
	rows: list[dict[str, str]] = []
	jobs = data.get("jobs")
	if isinstance(jobs, list):
		for job in jobs:
			if not isinstance(job, Mapping):
				continue
			title = _clip_text(job.get("title") or job.get("jobName") or "未命名职位", 28)
			company = _clip_text(job.get("company") or job.get("brandName") or "-", 14)
			city = _clip_text(job.get("city") or job.get("cityName") or "", 8)
			salary = _clip_text(job.get("salary") or job.get("salaryDesc") or "", 12)
			rows.append({"title": title, "company": company, "city": city, "salary": salary})
			if len(rows) >= limit:
				return rows
	if rows:
		return rows
	# Fallback: "title · company" strings from sample_titles.
	samples = data.get("sample_titles")
	if isinstance(samples, list):
		for raw in samples[:limit]:
			text = str(raw or "").strip()
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
		table.add_row(
			"…",
			f"[dim]另有 {total - len(rows)} 条，选下方「浏览职位列表」查看[/dim]",
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

	goal = str(run.get("goal") or "")
	if goal not in {"crawl_start", "crawl_resume", "crawl_status", "crawl_stop"}:
		return False
	last = _as_mapping(run.get("last_result")) or {}
	data = _as_mapping(last.get("data")) or {}
	if not data and not last:
		return False

	crawl_status = data.get("live_crawl_status") or data.get("status") or last.get("status")
	jobs_seen = data.get("jobs_seen")
	completed_like = (
		str(crawl_status) == "completed"
		or bool(run.get("effective_completed"))
		or str(run.get("status")) == "completed"
	)
	success = completed_like and int(jobs_seen or 0) > 0

	# ── Headline: strongest visual weight ───────────────────────────
	headline = Text()
	jobs_count = int(jobs_seen or 0)
	if success:
		headline.append("● ", style="bold green")
		headline.append(f"{jobs_count} 个职位", style="bold cyan")
	else:
		status_text = "已完成" if str(crawl_status) == "completed" else status_label(crawl_status)
		if str(crawl_status) == "risk_stopped":
			status_text = "风控暂停"
		headline.append("● ", style="bold yellow")
		headline.append(status_text, style="bold yellow")
		if jobs_seen is not None:
			headline.append(f"  ·  已有 {jobs_count} 个职位", style="bold")

	query = str(data.get("query") or "").strip()
	city = _city_display(data.get("city_code"))
	pages = data.get("pages_completed")
	meta_bits = [bit for bit in (query, city, f"{pages} 页" if pages is not None else "") if bit]
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
	wf_status = str(run.get("display_status") or run.get("status") or "")
	if run.get("effective_completed"):
		wf_status = "completed"
	status_display = status_label(wf_status)
	if success:
		row("状态", f"[bold green]{status_display}[/bold green]")
	elif str(crawl_status) == "risk_stopped":
		row("状态", "[bold yellow]风控暂停[/bold yellow]")
	else:
		row("状态", f"[bold]{status_display}[/bold]")

	if jobs_seen is not None and not success:
		row("职位数", f"[bold cyan]{jobs_seen}[/bold cyan]")
	if pages is not None and not success:
		row("已完成页", f"[bold]{pages}[/bold]")
	if data.get("next_page") is not None and str(crawl_status) != "completed":
		row("下一页", f"[bold]{data.get('next_page')}[/bold]")
	pending = data.get("details_pending")
	done = data.get("details_completed")
	if pending is not None or done is not None:
		row("详情进度", f"完成 [bold]{done or 0}[/bold]  ·  待补 [bold]{pending or 0}[/bold]")

	# Artifacts: short names, not full absolute paths.
	paths = data.get("output_paths") if isinstance(data.get("output_paths"), Mapping) else {}
	artifact_names = ""
	artifact_dir = ""
	if paths:
		artifact_names, artifact_dir = _short_artifact_lines(paths)
	else:
		artifacts = last.get("artifacts") or []
		if artifacts:
			artifact_names, artifact_dir = _short_artifact_lines(artifacts)
	if artifact_names:
		row("产物", artifact_names)
	if artifact_dir:
		row("目录", artifact_dir)

	if data.get("error") and str(crawl_status) != "completed":
		row("说明", f"[yellow]{data.get('error')}[/yellow]")

	# Secondary ids, dim — debugging aids.
	id_bits = []
	if run.get("run_id"):
		id_bits.append(f"向导 {run.get('run_id')}")
	inner = data.get("run_id") or last.get("run_id")
	if inner:
		id_bits.append(f"采集 {inner}")
	if id_bits:
		row("编号", f"[dim]{'  ·  '.join(str(bit) for bit in id_bits)}[/dim]")

	footer_hint: Text | None = None
	if success:
		footer_hint = Text("选「浏览职位列表」分页查看  ·  单条可看本地摘要或在线详情", style="dim")
	elif str(run.get("status")) == "waiting_input" and str(crawl_status) != "completed":
		next_action = last.get("next_action") or (
			(data.get("checkpoint") or {}).get("resume_command")
			if isinstance(data.get("checkpoint"), Mapping)
			else None
		)
		if next_action:
			row("下一步", f"[bold]{next_action}[/bold]")

	# ── Preview under meta: Markdown-style pipe table ───────────────
	preview_rows = _preview_jobs_from_data(data, limit=5) if with_preview else []
	parts: list[Any] = [headline, Text(""), meta]

	if preview_rows:
		shown = len(preview_rows)
		total = int(jobs_seen or shown)
		section = Text()
		section.append("职位预览", style="bold")
		section.append(f"  ·  前 {shown}/{total} 条", style="dim")
		parts.extend(
			[
				Text(""),
				Rule(style="dim"),
				section,
				_render_job_preview_table(
					preview_rows,
					total=int(jobs_seen) if jobs_seen is not None else None,
				),
			]
		)

	if footer_hint is not None:
		parts.append(footer_hint)

	border = "green" if success else (
		"yellow"
		if str(run.get("status")) == "waiting_input"
		or str(crawl_status) in {"risk_stopped", "budget_stopped", "stopped"}
		else "cyan"
	)
	panel_title = "采集已完成" if success else "采集任务状态"
	display.console.print(Panel(Group(*parts), title=panel_title, border_style=border))
	return True