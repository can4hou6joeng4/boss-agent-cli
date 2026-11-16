"""Recruiter automation commands."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, NoReturn

import click

from boss_agent_cli.config import DEFAULT_REQUEST_DELAY
from boss_agent_cli.api.cdp_risk_lock import unlock_operator_actions, raise_if_locked
from boss_agent_cli.automation.adapters import build_automation_adapter
from boss_agent_cli.automation.config import automation_config_from_dict
from boss_agent_cli.automation.events import make_event, now_iso
from boss_agent_cli.automation.models import (
	AutomationMode,
	EventStatus,
	PlatformAction,
	RunReport,
)
from boss_agent_cli.automation.runner import run_automation_cycle
from boss_agent_cli.automation.storage import (
	AutomationControlBusy,
	AutomationControlBusyDispatched,
	AutomationRunBusy,
	AutomationStateError,
	AutomationStore,
)
from boss_agent_cli.ai.service import AIServiceError
from boss_agent_cli.cache.store import CacheRecordInvalid, CacheStore
from boss_agent_cli.commands.ai_cmd import _build_fit_prompt, _create_ai_service
from boss_agent_cli.commands.crawl import (
	_LoginSeedUnavailable,
	_report_corrupt_run,
	_report_crawl_runtime_missing,
	_report_login_required,
	_require_crawl_capabilities,
	_seeded_transport_factory,
	_settings_from_context,
)
from boss_agent_cli.crawler.controls import (
	CrawlExportError,
	CrawlProfileBusy,
	CrawlProfilePathInvalid,
	CrawlRuntimeUnavailable,
	CrawlSessionAuthError,
)
from boss_agent_cli.crawler.hooks import HookRegistrationError
from boss_agent_cli.crawler.operations import crawl_status, import_crawl_shortlist
from boss_agent_cli.crawler.service import CrawlBusyError, CrawlService
from boss_agent_cli.display import (
	boss_command_for_ctx,
	handle_auth_errors,
	handle_error_output,
	handle_output,
	render_action_result,
	render_list_result,
	render_record_result,
	shell_argument,
)
from boss_agent_cli.resume.models import resume_to_text
from boss_agent_cli.resume.store import ResumeStore
from boss_agent_cli.services.install_extras import extras_requirement


@click.group("agent")
def agent_group() -> None:
	"""招聘自动化入口。"""


# 复核 / 待执行队列的 TTY 展示列。键名必须是数据模型字段
# （ReviewItem.id / confidence、PendingAction.id），否则 Rich 表格恒显示 “-”
# （Issue #1411）。
_REVIEW_LIST_COLUMNS: list[tuple[str, str, str]] = [
	("ID", "id", "bold cyan"),
	("动作", "action", "green"),
	("分数", "confidence", "yellow"),
]
_PENDING_LIST_COLUMNS: list[tuple[str, str, str]] = [
	("ID", "id", "bold cyan"),
	("动作", "action", "green"),
	("状态", "status", "yellow"),
]


def _hint_command(ctx: click.Context, command: str) -> str:
	"""招聘自动化的后继与恢复命令保留数据目录与招聘者角色。

	`agent` 组不在 `hr` 组下，重放招聘者命令必须显式带 ``--role recruiter``；
	否则会按候选人角色读取另一套本地状态。
	"""
	return boss_command_for_ctx(ctx, command, include_data_dir=True, role="recruiter")


def _crawl_hint_command(ctx: click.Context, command: str) -> str:
	"""候选人侧 `agent crawl` 的后继命令沿用数据目录，但不强制招聘者角色。"""
	return boss_command_for_ctx(ctx, command, include_data_dir=True)


@agent_group.command("run")
@click.option(
	"--dry-run",
	is_flag=True,
	default=False,
	help="只演练自动化决策，不执行真实平台动作，也不改写对话阶段、待执行队列或面试线索",
)
@click.option("--limit", default=None, type=click.IntRange(min=1), help="本轮最多处理多少个会话")
@click.option(
	"--resume",
	is_flag=True,
	default=False,
	help="已完成官方验证并核实未确认动作后，显式清除停止标志并从本地 checkpoint 恢复",
)
@click.pass_context
@handle_auth_errors("agent.run")
def run_cmd(ctx: click.Context, dry_run: bool, limit: int | None, resume: bool) -> None:
	"""运行一轮招聘自动化。"""
	report = _run_agent(ctx, dry_run=dry_run, limit=limit, force_mode=None, command="agent.run", resume=resume)
	handle_output(
		ctx,
		"agent.run",
		_report_payload(report),
		render=lambda d: render_record_result(d, title="agent run", next_steps=[_hint_command(ctx, "agent stats")]),
		hints=_agent_hints(ctx),
	)


@agent_group.command("crawl")
@click.option("--run-id", default=None, help="分析指定的已完成 crawl run")
@click.option("--query", default=None, help="新采集的职位关键词")
@click.option("--city", default=None, help="新采集城市；与 --query 同时使用")
@click.option("--pages", default=5, type=click.IntRange(1), show_default=True, help="新采集严格页数上限")
@click.option("--with-detail", is_flag=True, default=False, help="新采集时串行补职位详情")
@click.option("--allow-crawl", is_flag=True, default=False, hidden=True, help="历史兼容参数；当前版本无需传入")
@click.option("--resume", "resume_name", required=True, help="用于 ai fit 的本地简历名称")
@click.option("--limit", default=10, type=click.IntRange(min=1), show_default=True, help="按匹配分返回前 N 个职位")
@click.pass_context
@handle_auth_errors("agent.crawl")
def crawl_cmd(
	ctx: click.Context,
	run_id: str | None,
	query: str | None,
	city: str | None,
	pages: int,
	with_detail: bool,
	allow_crawl: bool,
	resume_name: str,
	limit: int,
) -> None:
	"""新建或读取 crawl 结果，导入候选池并执行 ai fit。"""
	# 空简历名是合法 argv，但先跑完采集再报「简历 '' 不存在」既浪费请求也看不出
	# 是漏填了名字；与 resume 命令组同口径，在入口按 INVALID_PARAM 拒绝（Issue #1369）。
	if not resume_name.strip():
		list_cmd = _crawl_hint_command(ctx, "resume list")
		handle_error_output(
			ctx,
			"agent.crawl",
			code="INVALID_PARAM",
			message="简历名称不能为空",
			recovery_action=f"补上简历名称后重试；用 {list_cmd} 查看已有简历",
			hints={"next_actions": [list_cmd]},
		)
		return
	crawl_by_run = _crawl_hint_command(ctx, "agent crawl --run-id <run_id> --resume <简历>")
	crawl_by_query = _crawl_hint_command(ctx, "agent crawl --query <关键词> --city <城市> --resume <简历>")
	if bool(run_id) == bool(query):
		handle_error_output(
			ctx,
			"agent.crawl",
			code="INVALID_PARAM",
			message="必须且只能使用 --run-id 或 --query",
			recovery_action="只传 --run-id，或只传 --query 后重试",
			hints={"next_actions": [crawl_by_run, crawl_by_query]},
		)
		return
	if query and not _require_crawl_capabilities(ctx):
		ctx.exit(1)
		return
	if query and not city:
		handle_error_output(
			ctx,
			"agent.crawl",
			code="INVALID_PARAM",
			message="使用 --query 时必须提供 --city",
			recovery_action=f"补上 --city <城市> 后重试，例如 {crawl_by_query}",
			hints={"next_actions": [crawl_by_query]},
		)
		return

	data_dir = ctx.obj["data_dir"]
	# 风控锁是硬边界：仅对会发起平台采集的 --query 路径，先于简历校验报告，
	# 避免已记录风险锁时被 RESUME_NOT_FOUND 引导去补简历后继续触发风控；
	# --run-id 只读本地结果与短名单，不受锁影响（Issue #1407 回归）。
	if query:
		raise_if_locked(data_dir)
	# 简历必须在任何平台调用、run 创建与短名单导入之前校验：非空但不存在的
	# 名字先采集一轮再报 RESUME_NOT_FOUND，既发出真实请求又留下本地写入
	# （Issue #1407）。损坏与缺失沿用 Issue #1369 的错误码区分。
	resume_text, load_error = _resume_text(data_dir, resume_name)
	if resume_text is None:
		if load_error is not None:
			# 文件存在但读不出来：不能报成「简历不存在」，提示修正或删除坏文件（Issue #1369）。
			delete_cmd = _crawl_hint_command(ctx, f"resume delete {shell_argument(resume_name)}")
			handle_error_output(
				ctx,
				"agent.crawl",
				code="RESUME_FILE_INVALID",
				message=f"简历文件 '{resume_name}.json' 损坏：{load_error}",
				recovery_action=(
					f"修正 resumes/{resume_name}.json 的 JSON 内容后重试，"
					f"或执行 {delete_cmd} 后重新创建；该文件只存在本机"
				),
				hints={"next_actions": [delete_cmd, _crawl_hint_command(ctx, "resume list")]},
			)
			return
		handle_error_output(ctx, "agent.crawl", code="RESUME_NOT_FOUND", message=f"简历 '{resume_name}' 不存在")
		return
	try:
		with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
			if query:
				settings = _settings_from_context(
					ctx,
					query=query,
					city=city or "",
					pages=pages,
					with_detail=with_detail,
					hook_profile=None,
					hook_dir=None,
					profile_path=None,
					chrome_path=None,
					cdp_port=None,
				)
				# 与 crawl run / start 同一份登录种子：采集浏览器的配置目录是隔离的，
				# 不注入就是登录页，会落成 risk_stopped 并写下风险锁（Issue #1044）。
				outcome = CrawlService(
					cache,
					data_dir=data_dir,
					transport_factory=_seeded_transport_factory(ctx),
				).create_and_run(settings)
				run_id = outcome.run_id
			status = crawl_status(cache, run_id or "", data_dir=data_dir)
			if status["status"] != "completed":
				is_risk_stopped = status["status"] == "risk_stopped"
				unlock_actions = unlock_operator_actions(
					boss_command_for_ctx(ctx, "clean --risk-lock", include_data_dir=True)
				)
				handle_error_output(
					ctx,
					"agent.crawl",
					code="CRAWL_NOT_COMPLETED",
					message=f"crawl run {run_id} 当前状态为 {status['status']}，不会导入不完整结果",
					recoverable=True,
					recovery_action="；".join(unlock_actions) if is_risk_stopped else status["checkpoint"]["resume_command"],
					hints={"operator_actions": [*unlock_actions,
						f"完成人工恢复后，显式执行 {status['checkpoint']['resume_command']} 从原断点继续",
					]} if is_risk_stopped else None,
				)
				return
			imported = import_crawl_shortlist(
				cache,
				run_id or "",
				include_all=True,
				tags=("agent", "crawl"),
				note=f"agent:crawl:{run_id}",
			)
			# Keep raw identifiers inside the local run while returning selectors to callers.
			crawl_jobs = cache.list_crawl_jobs(run_id or "")
			jobs, missing = _crawl_fit_jobs(cache, crawl_jobs)
	except KeyError:
		handle_error_output(
			ctx,
			"agent.crawl",
			code="CRAWL_RUN_NOT_FOUND",
			message=f"未找到 crawl run: {run_id}",
			hints={"next_actions": [
				boss_command_for_ctx(ctx, "crawl start <query>", include_data_dir=True),
				boss_command_for_ctx(ctx, "crawl status <run_id>", include_data_dir=True),
			]},
		)
		return
	except CacheRecordInvalid as exc:
		# 本地 run 记录损坏不是参数错误：调整关键词或配置永远修不好（Issue #1259）。
		# 与 crawl status / crawl resume 共用「断点读不出来，重新创建任务」的指引。
		_report_corrupt_run(ctx, "agent.crawl", run_id or "", exc)
		return
	except ValueError as exc:
		configure_cmd = boss_command_for_ctx(ctx, "crawl configure --help", include_data_dir=True)
		handle_error_output(
			ctx,
			"agent.crawl",
			code="INVALID_PARAM",
			message=str(exc),
			recovery_action=f"按提示修正关键词、城市或 crawl 配置后重试；可用 {configure_cmd} 查看可配置项",
			hints={"next_actions": [configure_cmd]},
		)
		return
	except CrawlBusyError as exc:
		# 新建的 run_id 几乎不可能被别人持有；真撞上时仍要按「另一个执行器在跑」回报，
		# 不能被下面的 RuntimeError 兜成「安装 crawl 依赖」。
		status_command = boss_command_for_ctx(ctx, f"crawl status {shell_argument(exc.run_id)}", include_data_dir=True)
		handle_error_output(
			ctx,
			"agent.crawl",
			code="CRAWL_BUSY",
			message=str(exc),
			details={"run_id": exc.run_id},
			recovery_action=f"用 {status_command} 查看进度，等当前执行结束后再恢复；不要并发恢复同一个 run_id",
			hints={"next_actions": [status_command]},
		)
		return
	except _LoginSeedUnavailable as exc:
		_report_login_required(ctx, "agent.crawl", str(exc))
		return
	except CrawlSessionAuthError as exc:
		# 本地 Cookie 注入失败不是平台风控；按认证前置条件回报，不提示 clean
		# --risk-lock（Issue #1405）。
		_report_login_required(ctx, "agent.crawl", str(exc))
		return
	except HookRegistrationError as exc:
		# Hook 注入失败是确定性本地失败：不能被下面的 RuntimeError 兜成可重试的
		# CRAWL_UNAVAILABLE（Issue #1408）。
		hook_dir = f" {exc.hook_dir}" if exc.hook_dir is not None else ""
		handle_error_output(
			ctx,
			"agent.crawl",
			code="LOCAL_IO_ERROR",
			message=str(exc),
			recoverable=False,
			recovery_action=f"停止自动重试：核对 Hook 目录{hook_dir} 中的脚本与 SHA256SUMS，修复后重新执行 agent crawl",
		)
		return
	except CrawlProfileBusy as exc:
		# 同一数据目录的采集配置目录被另一个运行中的 Chrome 占用：本次没有发出
		# 请求，也不能让下面的 RuntimeError 兜成「安装 crawl 依赖」（Issue #1266）。
		status_command = boss_command_for_ctx(ctx, "crawl status", include_data_dir=True)
		handle_error_output(
			ctx,
			"agent.crawl",
			code="CRAWL_PROFILE_BUSY",
			message=str(exc),
			hints={"next_actions": [status_command]},
		)
		return
	except CrawlExportError as exc:
		# 导出失败是本地目录 / 权限问题：CrawlExportError 是 RuntimeError 子类，
		# 不单独捕获会被兜成 CRAWL_UNAVAILABLE 并让人去安装 crawl 依赖（与 crawl run 同口径）。
		handle_error_output(
			ctx,
			"agent.crawl",
			code="LOCAL_IO_ERROR",
			message=str(exc),
			recoverable=False,
			recovery_action="确认输出目录可写（不是同名的已有文件）、目录权限与磁盘空间正常，修正后重新执行 agent crawl",
		)
		return
	except CrawlProfilePathInvalid as exc:
		# 配置目录被同名文件占用或不可写：确定性本地路径失败，重试或 resume 只会
		# 复现同一个错误，不能兜成「安装 crawl 依赖」（Issue #1290 同口径）。
		handle_error_output(
			ctx,
			"agent.crawl",
			code="LOCAL_IO_ERROR",
			message=str(exc),
			recoverable=False,
			recovery_action=(
				"检查数据目录下的 crawl/chrome-profile 是否被同名文件占用、"
				"目录权限与磁盘空间是否正常，修正后重新执行"
			),
		)
		return
	except CrawlRuntimeUnavailable as exc:
		_report_crawl_runtime_missing(ctx, "agent.crawl", str(exc))
		return
	except RuntimeError as exc:
		configure_cmd = boss_command_for_ctx(ctx, "crawl configure", include_data_dir=True)
		handle_error_output(
			ctx,
			"agent.crawl",
			code="CRAWL_UNAVAILABLE",
			message=str(exc),
			recoverable=True,
			recovery_action=f"安装 {extras_requirement('crawl')} 并执行 {configure_cmd}",
		)
		return

	if not jobs:
		resume_command = boss_command_for_ctx(ctx, f"crawl resume {shell_argument(run_id or '')} --with-detail", include_data_dir=True)
		handle_output(
			ctx,
			"agent.crawl",
			{
				"run_id": run_id,
				"crawl": status,
				"shortlist": imported,
				"results": [],
				"missing": missing,
				"summary": {"analyzed": 0, "missing_details": len(missing)},
			},
			render=lambda d: render_record_result(d, title="agent crawl", next_steps=[resume_command]),
			hints={"next_actions": [resume_command]},
		)
		return

	try:
		svc = _create_ai_service(ctx)
	except ValueError as exc:
		# 本地配置坏值重试必然复现：给 INVALID_PARAM 与修复命令（Issue #1209）。
		handle_error_output(
			ctx,
			"agent.crawl",
			code="INVALID_PARAM",
			message=f"本地 AI 配置损坏：{exc}",
			recoverable=True,
			recovery_action=_crawl_hint_command(ctx, "ai config --max-tokens 4096 --temperature 0.7"),
		)
		return
	if svc is None:
		handle_error_output(
			ctx,
			"agent.crawl",
			code="AI_NOT_CONFIGURED",
			message="AI 服务未配置；crawl 结果已导入 shortlist",
			recoverable=True,
			recovery_action=_crawl_hint_command(ctx, "ai config --provider <provider> --model <model> --api-key <key>"),
		)
		return
	try:
		fit = _fit_crawl_jobs(svc, resume_text, jobs)
	except (AIServiceError, ValueError, RuntimeError) as exc:
		retry_cmd = _crawl_hint_command(
			ctx,
			f"agent crawl --run-id {shell_argument(run_id or '')} --resume {shell_argument(resume_name)}",
		)
		handle_error_output(
			ctx,
			"agent.crawl",
			code="AI_API_ERROR",
			message=f"AI 匹配失败: {exc}",
			recoverable=True,
			recovery_action=f"检查 AI 配置后重试 {retry_cmd}",
		)
		return
	results = fit.get("results", [])
	if not isinstance(results, list):
		results = []
	results = _public_fit_results(sorted(results, key=_fit_score, reverse=True)[:limit], jobs)
	next_steps = [
		boss_command_for_ctx(ctx, f"crawl results {shell_argument(run_id or '')}", include_data_dir=True),
		boss_command_for_ctx(ctx, "shortlist list", include_data_dir=True),
	]
	handle_output(
		ctx,
		"agent.crawl",
		{
			"run_id": run_id,
			"crawl": status,
			"shortlist": imported,
			"results": results,
			"missing": missing,
			"summary": {"analyzed": len(jobs), "missing_details": len(missing), "returned": len(results)},
		},
		render=lambda d: render_record_result(d, title="agent crawl", next_steps=next_steps),
		hints={"next_actions": next_steps},
	)


@agent_group.command("train")
@click.option(
	"--dry-run/--live",
	"dry_run",
	default=True,
	help="训练模式默认只演练；传 --live 执行满足阈值的动作",
)
@click.option("--limit", default=None, type=click.IntRange(min=1), help="本轮最多处理多少个会话")
@click.pass_context
@handle_auth_errors("agent.train")
def train_cmd(ctx: click.Context, dry_run: bool, limit: int | None) -> None:
	"""运行训练校准模式：默认演练，--live 直接执行满足阈值的动作。"""
	report = _run_agent(
		ctx,
		dry_run=dry_run,
		limit=limit,
		force_mode=AutomationMode.TRAINING,
		command="agent.train",
	)
	handle_output(
		ctx,
		"agent.train",
		_report_payload(report),
		render=lambda d: render_record_result(d, title="agent train", next_steps=[_hint_command(ctx, "agent review list")]),
		hints=_agent_hints(ctx),
	)


@agent_group.group("review")
def review_group() -> None:
	"""管理旧版本遗留的人工复核队列。"""


@review_group.command("list")
@click.pass_context
def review_list_cmd(ctx: click.Context) -> None:
	"""列出旧版本遗留的待人工复核动作。"""
	store = AutomationStore(ctx.obj["data_dir"])
	try:
		reviews = store.read_reviews()
	except AutomationStateError as exc:
		_emit_state_error(ctx, "agent.review.list", exc)
		return
	handle_output(
		ctx,
		"agent.review.list",
		{"items": [asdict(item) for item in reviews]},
		render=lambda d: render_list_result(
			d.get("items", []),
			"reviews",
			_REVIEW_LIST_COLUMNS,
			next_steps=[_hint_command(ctx, "agent review approve <id>"), _hint_command(ctx, "agent review reject <id>")],
		),
		hints=_agent_hints(ctx),
	)


@review_group.command("approve")
@click.argument("review_id")
@click.pass_context
def review_approve_cmd(ctx: click.Context, review_id: str) -> None:
	"""处理旧版本复核项并写入兼容 pending 队列。"""
	store = AutomationStore(ctx.obj["data_dir"])
	try:
		pending = store.approve_review(review_id, now_iso())
	except AutomationRunBusy as exc:
		_emit_run_busy(ctx, "agent.review.approve", exc)
		return
	except AutomationStateError as exc:
		_emit_state_error(ctx, "agent.review.approve", exc)
		return
	if pending is None:
		_emit_review_missing(ctx, "agent.review.approve", review_id)
		return
	handle_output(
		ctx,
		"agent.review.approve",
		{"pending": asdict(pending)},
		render=lambda d: render_record_result(d, title="agent review approve", next_steps=[_hint_command(ctx, "agent pending list")]),
		hints=_agent_hints(ctx),
	)


@review_group.command("reject")
@click.argument("review_id")
@click.option("--reason", default="human-rejected", help="拒绝原因")
@click.pass_context
def review_reject_cmd(ctx: click.Context, review_id: str, reason: str) -> None:
	"""拒绝一条旧版本人工复核动作，并记录跳过事件。"""
	store = AutomationStore(ctx.obj["data_dir"])
	try:
		rejected = store.reject_review(review_id, reason, now_iso())
	except AutomationRunBusy as exc:
		_emit_run_busy(ctx, "agent.review.reject", exc)
		return
	except AutomationStateError as exc:
		_emit_state_error(ctx, "agent.review.reject", exc)
		return
	if rejected is None:
		_emit_review_missing(ctx, "agent.review.reject", review_id)
		return
	event = make_event(
		rejected.platform,
		rejected.candidate_key,
		PlatformAction(rejected.action),
		EventStatus.SKIPPED,
		rejected.confidence,
		f"human rejected: {reason}",
	)
	store.append_event(event)
	handle_output(
		ctx,
		"agent.review.reject",
		{"review": asdict(rejected), "event": asdict(event)},
		render=lambda d: render_record_result(d, title="agent review reject", next_steps=[_hint_command(ctx, "agent review list")]),
		hints=_agent_hints(ctx),
	)


@agent_group.group("pending")
def pending_group() -> None:
	"""待执行动作队列。"""


@pending_group.command("list")
@click.pass_context
def pending_list_cmd(ctx: click.Context) -> None:
	"""列出旧版本遗留的待执行动作队列。"""
	store = AutomationStore(ctx.obj["data_dir"])
	try:
		pending = store.read_pending()
	except AutomationStateError as exc:
		_emit_state_error(ctx, "agent.pending.list", exc)
		return
	handle_output(
		ctx,
		"agent.pending.list",
		{"items": [asdict(item) for item in pending]},
		render=lambda d: render_list_result(
			d.get("items", []),
			"pending",
			_PENDING_LIST_COLUMNS,
			next_steps=[_hint_command(ctx, "agent stats")],
		),
		hints=_agent_hints(ctx),
	)


@agent_group.command("stats")
@click.pass_context
def agent_stats_cmd(ctx: click.Context) -> None:
	"""查看招聘自动化统计。"""
	store = AutomationStore(ctx.obj["data_dir"])
	try:
		stats = store.stats()
	except AutomationStateError as exc:
		_emit_state_error(ctx, "agent.stats", exc)
		return
	handle_output(
		ctx,
		"agent.stats",
		stats,
		render=lambda d: render_record_result(d, title="agent stats", next_steps=[_hint_command(ctx, "agent pending list")]),
		hints=_agent_hints(ctx),
	)


@agent_group.command("control")
@click.pass_context
def control_cmd(ctx: click.Context) -> None:
	"""返回本地控制台入口信息。"""
	handle_output(
		ctx,
		"agent.control",
		{
			"status": "available_via_cli",
			"note": (
				"首版控制台能力已统一到 agent CLI；"
				"Web 控制台将在后续接入同一状态目录"
			),
			"commands": [
				_hint_command(ctx, "agent run"),
				_hint_command(ctx, "agent stats"),
				_hint_command(ctx, "agent review list"),
				_hint_command(ctx, "agent pending list"),
			],
		},
		render=lambda d: render_record_result(d, title="agent control", next_steps=[_hint_command(ctx, "agent stats")]),
		hints=_agent_hints(ctx),
	)


@agent_group.command("stop")
@click.option("--reason", default="manual-stop", help="熔断原因")
@click.pass_context
def stop_cmd(ctx: click.Context, reason: str) -> None:
	"""打开招聘自动化熔断。"""
	store = AutomationStore(ctx.obj["data_dir"])
	try:
		store.request_stop(reason)
	except AutomationControlBusy as exc:
		_emit_control_busy(ctx, "agent.stop", exc)
	except AutomationStateError as exc:
		# 锁文件损坏 / 不可打开：确定性本地状态问题，不能把 traceback 甩给 Agent
		# （Issue #1304）。
		_emit_state_error(ctx, "agent.stop", exc)
	handle_output(
		ctx,
		"agent.stop",
		{"status": "CIRCUIT_BREAKER_OPEN", "reason": reason},
		render=lambda d: render_action_result(d, title="agent stop", next_steps=[_hint_command(ctx, "agent stats")]),
		hints=_agent_hints(ctx),
	)


def _emit_review_missing(ctx: click.Context, command: str, review_id: str) -> NoReturn:
	"""复核项不存在或已处理时给信封。

	`raise click.ClickException` 会被组层兜底成 `command: "boss"`、`INVALID_PARAM`
	和英文消息；这里直接按命令名输出，与其它子命令的信封一致。
	"""
	list_command = _agent_review_list_command(ctx)
	handle_error_output(
		ctx,
		command,
		code="INVALID_PARAM",
		message=f"复核项 '{review_id}' 不存在或已处理",
		recovery_action=f"用 {list_command} 核对当前待复核项后重试",
		hints={"next_actions": [list_command]},
	)
	ctx.exit(1)


def _emit_control_busy(ctx: click.Context, command: str, exc: AutomationControlBusy) -> NoReturn:
	"""控制锁冲突：本次控制更新没有生效，指引指向真正的锁文件（Issue #1251）。"""
	handle_error_output(
		ctx,
		command,
		code="AUTOMATION_BUSY",
		message=str(exc),
		hints={"next_actions": [_hint_command(ctx, "agent stats")]},
	)
	ctx.exit(1)


def _emit_control_busy_after_dispatch(
	ctx: click.Context, command: str, exc: AutomationControlBusyDispatched
) -> NoReturn:
	"""周期内控制锁冲突：平台动作可能已发出，禁止按「没有生效」重试（Issue #1267）。

	动作派发后队列已写回 executed，但 checkpoint 撞锁没落盘。重新运行不会重发
	已确认的行；但熔断状态可能落后，所以按 `LOCAL_STATE_WRITE_FAILED` 上报：
	先核对官方页面，不自动重发。
	"""
	handle_error_output(
		ctx,
		command,
		code="LOCAL_STATE_WRITE_FAILED",
		message=f"招聘自动化执行中断：平台动作可能已发出，但自动化状态保存失败（{exc}）",
		hints={"next_actions": [_hint_command(ctx, "agent stats")]},
	)
	ctx.exit(1)


def _emit_run_busy(ctx: click.Context, command: str, exc: AutomationRunBusy) -> NoReturn:
	"""执行锁冲突：另一个 agent run 正在执行，本次调用没有发出任何请求（Issue #1253）。"""
	handle_error_output(
		ctx,
		command,
		code="AUTOMATION_RUN_BUSY",
		message=str(exc),
		hints={"next_actions": [_hint_command(ctx, "agent stats")]},
	)
	ctx.exit(1)


def _emit_state_error(ctx: click.Context, command: str, exc: AutomationStateError) -> NoReturn:
	"""状态文件损坏时输出信封：自动化不能带着未知状态继续发送。"""
	handle_error_output(
		ctx,
		command,
		code="AUTOMATION_STATE_INVALID",
		message=f"{exc.path} 无法解析：{exc.detail}",
		# 登记表的指引只针对 state.json；队列文件损坏或读不出来时指向实际出问题的文件。
		recovery_action=(
			None
			if exc.path.name == "state.json"
			else (
				f"确认 {exc.path} 可读（权限正常、路径不是目录），"
				"修正或移除其中的问题记录后重试；建议先备份该文件"
			)
		),
	)
	ctx.exit(1)


def _run_agent(
	ctx: click.Context,
	*,
	dry_run: bool,
	limit: int | None,
	force_mode: AutomationMode | None,
	command: str,
	resume: bool = False,
) -> RunReport:
	raw_config = (ctx.obj.get("config") or {}).get("automation")
	if raw_config is not None and not isinstance(raw_config, dict):
		# 手工编辑 config.json 把 automation 写成字符串时，不能带着 traceback 退出：
		# 按信封契约给出修复入口，用户执行后即可恢复。
		handle_error_output(
			ctx,
			command,
			code="INVALID_PARAM",
			message="automation 配置必须是 JSON object",
			recoverable=True,
			recovery_action=boss_command_for_ctx(ctx, "config reset automation", include_data_dir=True),
		)
		ctx.exit(1)
	try:
		cfg = automation_config_from_dict(raw_config)
	except (TypeError, ValueError) as exc:
		# 枚举值或数值字段写错（如 mode=nonsense）同样不能抛 traceback，
		# 与形状错误走同一个修复入口。
		handle_error_output(
			ctx,
			command,
			code="INVALID_PARAM",
			message=f"automation 配置无效: {exc}",
			recoverable=True,
			recovery_action=boss_command_for_ctx(ctx, "config reset automation", include_data_dir=True),
		)
		ctx.exit(1)
	if force_mode is not None:
		cfg = replace(cfg, mode=force_mode)
	platform = ctx.obj.get("platform") or "zhipin"
	store = AutomationStore(ctx.obj["data_dir"])
	try:
		adapter = build_automation_adapter(
			platform,
			data_dir=ctx.obj["data_dir"],
			delay=ctx.obj.get("delay", DEFAULT_REQUEST_DELAY),
			cdp_url=ctx.obj.get("cdp_url"),
			live=not dry_run,
		)
		return run_automation_cycle(
			adapter,
			store,
			cfg,
			platform=platform,
			dry_run=dry_run,
			limit=limit,
			resume=resume,
		)
	except AutomationRunBusy as exc:
		_emit_run_busy(ctx, command, exc)
	except AutomationControlBusyDispatched as exc:
		_emit_control_busy_after_dispatch(ctx, command, exc)
	except AutomationControlBusy as exc:
		_emit_control_busy(ctx, command, exc)
	except AutomationStateError as exc:
		_emit_state_error(ctx, command, exc)
	except KeyboardInterrupt:
		# runner 已打开熔断并保留 checkpoint；stdout 仍必须有一个信封：Click 的
		# Abort 会让 MCP 拿到空输出，把这次写调用只当成结果未知（Issue #1262）。
		handle_error_output(
			ctx,
			command,
			code="CIRCUIT_BREAKER_OPEN",
			message="招聘自动化执行被中断，已打开熔断并保留 checkpoint",
		)
		ctx.exit(1)


def _resume_text(data_dir: Path, resume_name: str) -> tuple[str | None, str | None]:
	"""读取简历纯文本；同时返回读不出来的原因，交给调用方区分损坏与缺失。

	``(None, None)`` 表示文件不存在；``(None, 原因)`` 表示文件存在但损坏，
	命令层据此报 ``RESUME_FILE_INVALID``（Issue #1369）。
	"""
	resume, load_error = ResumeStore(data_dir / "resumes").get_with_error(resume_name)
	if resume is None:
		return None, load_error
	return resume_to_text(resume), None


def _crawl_fit_jobs(cache: CacheStore, crawl_jobs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
	jobs: list[dict[str, Any]] = []
	missing: list[dict[str, Any]] = []
	for crawl_job in crawl_jobs:
		item = crawl_job["payload"]
		selector = str(crawl_job["selector"])
		job_id = str(item.get("job_id") or "")
		description = cache.get_job_desc(job_id) or str(item.get("post_description") or "")
		if description:
			jobs.append({
				"selector": selector,
				"title": item.get("title", ""),
				"company": item.get("company", ""),
				"city": item.get("city", ""),
				"salary": item.get("salary", ""),
				"description": description,
			})
		else:
			missing.append({
				"selector": selector,
				"title": item.get("title", ""),
				"company": item.get("company", ""),
				"status": "缺详情",
			})
	return jobs, missing


def _public_fit_results(results: list[Any], jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
	"""Replace local identifiers in AI fit output with the run-scoped selector."""
	selectors = {str(job["selector"]) for job in jobs}
	selectors_by_title = {
		str(job["title"]): str(job["selector"])
		for job in jobs
		if sum(str(candidate["title"]) == str(job["title"]) for candidate in jobs) == 1
	}
	public: list[dict[str, Any]] = []
	for result in results:
		if not isinstance(result, dict):
			continue
		selector = str(result.get("selector") or "")
		item = {
			key: value
			for key, value in result.items()
			if key not in {"job_id", "security_id", "boss_name", "boss_title", "recruiter"}
		}
		if selector in selectors:
			item["selector"] = selector
		elif str(result.get("title") or "") in selectors_by_title:
			item["selector"] = selectors_by_title[str(result["title"])]
		public.append(item)
	return public


def _fit_crawl_jobs(svc: Any, resume_text: str, jobs: list[dict[str, Any]]) -> dict[str, Any]:
	raw = svc.chat([
		{"role": "system", "content": "你是求职顾问。所有输出使用 JSON 格式。"},
		{"role": "user", "content": _build_fit_prompt(resume_text, jobs)},
	])
	text = str(raw).strip()
	if text.startswith("```"):
		text = "\n".join(line for line in text.splitlines() if not line.startswith("```")).strip()
	try:
		payload = json.loads(text)
	# 超长整数（3.11+）与深嵌套同样是模型输出不可用，按解析失败报错。
	except (ValueError, RecursionError) as exc:
		raise ValueError("AI 返回结果不是 JSON") from exc
	if not isinstance(payload, dict):
		raise ValueError("AI 返回结果不是对象")
	return payload


def _fit_score(item: Any) -> int:
	if not isinstance(item, dict):
		return 0
	try:
		return int(item.get("match_score", 0))
	# OverflowError：模型输出 1e400 解析成 inf，按 0 分处理并继续返回结果。
	except (TypeError, ValueError, OverflowError):
		return 0


def _report_payload(report: RunReport) -> dict[str, Any]:
	payload = asdict(report)
	payload["mode"] = report.mode.value
	return payload


def _agent_review_list_command(ctx: click.Context) -> str:
	return _hint_command(ctx, "agent review list")


def _agent_hints(ctx: click.Context) -> dict[str, Any]:
	return {
		"next_actions": [
			_hint_command(ctx, "agent stats"),
			_hint_command(ctx, "agent review list"),
			_hint_command(ctx, "agent pending list"),
		],
	}
