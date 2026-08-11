"""Interactive and headless shared workflow command."""

from __future__ import annotations

import json
import sys
from typing import Any, Mapping, NoReturn

import click

from boss_agent_cli.display import handle_error_output, handle_output
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.catalog import build_plan
from boss_agent_cli.wizard.models import WorkflowInputError, WorkflowPlan, WizardInput
from boss_agent_cli.wizard.prompts import (
	WizardCancelled,
	WizardControl,
	WizardReturnHome,
	ask_continue_session,
	collect_completed_crawl_follow_up,
	collect_entity_follow_up,
	collect_result_follow_up,
	collect_waiting_recovery,
	collect_wizard_input,
	has_result_follow_up,
)
from boss_agent_cli.wizard.renderer import (
	clear_wizard_screen,
	render_cancelled,
	render_error,
	render_event,
	render_run,
)
from boss_agent_cli.wizard.runner import WorkflowActionError, WorkflowRunner
from boss_agent_cli.wizard.store import WorkflowStore


def _is_interactive(ctx: click.Context) -> bool:
	return (
		not (ctx.obj or {}).get("json_output", False)
		and sys.stdin.isatty()
		and sys.stdout.isatty()
		and sys.stderr.isatty()
	)


def _interactive_error(
	*,
	code: str,
	message: str | None = None,
	recovery_action: str | None = None,
) -> NoReturn:
	render_error(code=code, message=message, recovery_action=recovery_action)
	raise SystemExit(1)


def _wizard_error(
	ctx: click.Context,
	interactive: bool,
	*,
	code: str,
	message: str,
	recoverable: bool = False,
	recovery_action: str | None = None,
	details: dict[str, Any] | None = None,
) -> None:
	if interactive:
		_interactive_error(code=code, message=message, recovery_action=recovery_action)
	handle_error_output(
		ctx,
		"wizard",
		code=code,
		message=message,
		recoverable=recoverable,
		recovery_action=recovery_action,
		details=details,
	)


def _plan_from_run(run: Mapping[str, Any]) -> WorkflowPlan:
	params = run.get("params") or {}
	return WorkflowPlan(
		role=str(run["role"]),
		platform=str(run["platform"]),
		goal=str(run["goal"]),
		inputs=dict(params.get("inputs") or {}),
		requested_steps=tuple(params.get("requested_steps") or ()),
		mode=str(run["mode"]),
	)


def _parse_input_json(raw: str) -> WizardInput:
	try:
		value = json.loads(raw)
	except json.JSONDecodeError as exc:
		raise WorkflowInputError(f"input-json 不是有效 JSON: {exc.msg}") from exc
	if not isinstance(value, dict):
		raise WorkflowInputError("input-json 顶层必须是 JSON object")
	return WizardInput.from_mapping(value, mode="headless")


def _build_context(ctx: click.Context, plan: WorkflowPlan) -> ActionContext:
	return ActionContext(
		data_dir=ctx.obj["data_dir"],
		platform=plan.platform,
		role=plan.role,
		logger=ctx.obj["logger"],
		delay=ctx.obj.get("delay", (1.5, 3.0)),
		cdp_url=ctx.obj.get("cdp_url"),
		config=ctx.obj.get("config"),
	)


def _run_plan(
	ctx: click.Context,
	store: WorkflowStore,
	plan: WorkflowPlan,
	*,
	interactive: bool,
	resume_run_id: str | None,
	timeout_seconds: float | None,
	max_retries: int,
) -> dict[str, Any]:
	context = _build_context(ctx, plan)
	runner = WorkflowRunner(store, DEFAULT_ACTIONS)
	run = runner.run(
		plan,
		context,
		run_id=resume_run_id,
		on_event=render_event if interactive else None,
		timeout_seconds=timeout_seconds,
		max_retries=max_retries,
	)
	if interactive and run.get("status") == "waiting_input":
		render_run(run)
		recovery = collect_waiting_recovery(run)
		if recovery is None:
			return run
		# 新建独立 recovery plan（不绑定原 waiting_input run_id），避免 plan mismatch。
		recovery_plan = build_plan(recovery)
		recovery_context = _build_context(ctx, recovery_plan)
		run = runner.run(
			recovery_plan,
			recovery_context,
			on_event=render_event,
			timeout_seconds=timeout_seconds,
			max_retries=max_retries,
		)
		if run.get("status") == "waiting_input":
			render_run(run)
			return run
		if run.get("status") != "completed":
			return run
		# completed：不提前 return——落入下方 completed 处理器渲染摘要并可浏览结果。
		# 提前 return 会让外层立刻回主菜单清屏，采集摘要一闪即逝（真实使用反馈）。

	# TTY：列表结果可连续选择；职位详情后提供打招呼等操作；失败交给 _emit_run。
	if interactive and (
		run.get("status") == "completed"
		or (run.get("effective_completed") and has_result_follow_up(run))
	):
		list_run = run
		if has_result_follow_up(list_run):
			render_run(list_run)
			return _browse_list_results(
				ctx,
				store,
				runner,
				list_run,
				timeout_seconds=timeout_seconds,
				max_retries=max_retries,
			)
		render_run(run)
		run = _run_entity_follow_ups(
			ctx,
			store,
			runner,
			run,
			timeout_seconds=timeout_seconds,
			max_retries=max_retries,
			allow_list_return=False,
		)
	return run


def _run_entity_follow_ups(
	ctx: click.Context,
	store: WorkflowStore,
	runner: WorkflowRunner,
	run: dict[str, Any],
	*,
	timeout_seconds: float | None,
	max_retries: int,
	allow_list_return: bool,
) -> dict[str, Any]:
	"""Chain greet/apply/exchange after a job-centric step without bouncing to root."""
	current = run
	while current.get("status") == "completed":
		decision = collect_entity_follow_up(current, allow_list_return=allow_list_return)
		if decision is None:
			return current
		plan = build_plan(decision)
		context = _build_context(ctx, plan)
		current = runner.run(
			plan,
			context,
			on_event=render_event,
			timeout_seconds=timeout_seconds,
			max_retries=max_retries,
		)
		if current.get("status") != "completed":
			return current
		render_run(current)
	return current


def _browse_list_results(
	ctx: click.Context,
	store: WorkflowStore,
	runner: WorkflowRunner,
	list_run: dict[str, Any],
	*,
	timeout_seconds: float | None,
	max_retries: int,
) -> dict[str, Any]:
	"""Interactive list follow-up (search / crawl jobs) without re-running the list step."""
	run = list_run
	if not has_result_follow_up(list_run):
		return run
	while True:
		follow_up = collect_result_follow_up(list_run)
		if follow_up is None:
			break
		follow_up_plan = build_plan(follow_up)
		follow_context = _build_context(ctx, follow_up_plan)
		follow_run = runner.run(
			follow_up_plan,
			follow_context,
			on_event=render_event,
			timeout_seconds=timeout_seconds,
			max_retries=max_retries,
		)
		run = follow_run
		if follow_run.get("status") == "waiting_input":
			render_run(follow_run)
			return follow_run
		if follow_run.get("status") != "completed":
			return run
		render_run(follow_run)
		run = _run_entity_follow_ups(
			ctx,
			store,
			runner,
			follow_run,
			timeout_seconds=timeout_seconds,
			max_retries=max_retries,
			allow_list_return=True,
		)
		if run.get("status") != "completed":
			return run
	return run


def _emit_run(ctx: click.Context, run: Mapping[str, Any], *, interactive: bool) -> None:
	if run.get("status") == "failed":
		error = run.get("error") or {}
		if interactive:
			render_run(run)
			return
		_wizard_error(
			ctx,
			interactive,
			code=str(error.get("code") or "NETWORK_ERROR"),
			message=str(error.get("message") or "任务执行失败"),
			recoverable=bool(error.get("recoverable")),
			recovery_action=error.get("recovery_action"),
			details={"run": dict(run)},
		)
		return
	if interactive:
		# 成功内容已在 _run_plan 中渲染，避免重复刷屏。
		return
	handle_output(
		ctx,
		"wizard",
		run,
		render=render_run,
		hints={"next_actions": [f"boss wizard --status {run['run_id']}"]},
	)


def _run_interactive_session(
	ctx: click.Context,
	store: WorkflowStore,
	*,
	timeout_seconds: float | None,
	max_retries: int,
) -> None:
	"""TTY multi-turn loop: main menu → execute → follow-ups → continue/exit."""
	# 登录门：在任何目标选择之前解决登录态。已登录时零输出直接放行。
	from boss_agent_cli.wizard.preflight import GATE_EXIT, GATE_LOCAL_ONLY, ensure_login

	gate = ensure_login(ctx)
	if gate == GATE_EXIT:
		render_cancelled()
		return
	local_only = gate == GATE_LOCAL_ONLY

	while True:
		try:
			selection = collect_wizard_input(
				default_role=ctx.obj.get("role") or "candidate",
				default_platform=ctx.obj.get("platform") or "zhipin",
				available_runs=store.list_recent(),
				data_dir=ctx.obj.get("data_dir"),
				local_only=local_only,
			)
		except WizardCancelled:
			render_cancelled()
			return
		except WorkflowInputError as exc:
			render_error(code="INVALID_PARAM", message=str(exc), recovery_action="请返回并重新输入")
			if not ask_continue_session():
				return
			continue

		resume_run_id: str | None = None
		turn_retries = max_retries
		plan: WorkflowPlan | None = None
		browse_run: dict[str, Any] | None = None
		try:
			if isinstance(selection, WizardControl):
				if selection.action == "status":
					run = store.get(str(selection.run_id))
					if run is None:
						render_error(code="JOB_NOT_FOUND")
						if not ask_continue_session(clear_before=False):
							return
						continue
					# 先清掉任务列表，再只展示状态（避免列表+状态+继续 三层叠屏）。
					clear_wizard_screen()
					render_run(run)
					if str(run.get("status")) == "waiting_input" and not run.get("effective_completed"):
						try:
							recovery = collect_waiting_recovery(run, clear_before=False)
						except WizardCancelled:
							render_cancelled()
							return
						except WizardReturnHome:
							continue
						if recovery is None:
							continue
						if recovery.goal == "__view_crawl_jobs__":
							browse_run = run
						else:
							plan = build_plan(recovery)
							resume_run_id = None
					elif has_result_follow_up(run):
						try:
							choice = collect_completed_crawl_follow_up(run, clear_before=False)
						except WizardCancelled:
							render_cancelled()
							return
						if choice == "browse":
							browse_run = run
						else:
							continue
					else:
						if not ask_continue_session(clear_before=False):
							return
						continue
				elif selection.action == "stop":
					if not store.request_stop(str(selection.run_id)):
						render_error(code="JOB_NOT_FOUND")
					else:
						clear_wizard_screen()
						render_run(store.get(str(selection.run_id)) or {})
					if not ask_continue_session(clear_before=False):
						return
					continue
				else:
					resume_run_id = selection.run_id
					if selection.action == "retry":
						turn_retries = max(1, max_retries)
					existing = store.get(str(resume_run_id))
					if existing is None:
						render_error(code="JOB_NOT_FOUND")
						if not ask_continue_session():
							return
						continue
					plan = _plan_from_run(existing)
			elif isinstance(selection, WizardInput) and selection.goal == "__view_crawl_jobs__":
				source_id = str(selection.inputs.get("source_run_id") or "")
				browse_run = store.get(source_id) if source_id else None
				if browse_run is None:
					render_error(code="JOB_NOT_FOUND", message="找不到可浏览的采集结果")
					if not ask_continue_session():
						return
					continue
			else:
				plan = build_plan(selection)
		except WorkflowInputError as exc:
			render_error(code="INVALID_PARAM", message=str(exc), recovery_action="请返回并重新输入")
			if not ask_continue_session():
				return
			continue

		if browse_run is not None:
			try:
				runner = WorkflowRunner(store, DEFAULT_ACTIONS)
				_browse_list_results(
					ctx,
					store,
					runner,
					browse_run,
					timeout_seconds=timeout_seconds,
					max_retries=turn_retries,
				)
			except WizardCancelled:
				render_cancelled()
				return
			except WizardReturnHome:
				continue
			except WorkflowActionError as exc:
				render_error(code=exc.code, message=str(exc), recovery_action=exc.recovery_action)
			if not ask_continue_session(clear_before=False):
				return
			continue

		if plan is None:
			continue

		try:
			run = _run_plan(
				ctx,
				store,
				plan,
				interactive=True,
				resume_run_id=resume_run_id,
				timeout_seconds=timeout_seconds,
				max_retries=turn_retries,
			)
		except WizardCancelled:
			# 列表/职位后续菜单里选「退出向导」或 Esc/q：干净退出，不抛 traceback。
			render_cancelled()
			return
		except WizardReturnHome:
			# 职位操作里选「返回主菜单」：直接回到主循环，不再多问一层。
			continue
		except WorkflowActionError as exc:
			render_error(code=exc.code, message=str(exc), recovery_action=exc.recovery_action)
			if not ask_continue_session():
				return
			continue

		_emit_run(ctx, run, interactive=True)
		if run.get("status") == "waiting_input":
			# 执行后仍 waiting：就地给出继续采集 / 浏览已有结果。
			clear_wizard_screen()
			# Re-load so live crawl completion + jobs surface after concurrent resume.
			fresh = store.get(str(run.get("run_id") or "")) or run
			render_run(fresh)
			try:
				recovery = collect_waiting_recovery(fresh, clear_before=False)
			except WizardCancelled:
				render_cancelled()
				return
			except WizardReturnHome:
				continue
			if recovery is None:
				continue
			if recovery.goal == "__view_crawl_jobs__":
				try:
					runner = WorkflowRunner(store, DEFAULT_ACTIONS)
					_browse_list_results(
						ctx,
						store,
						runner,
						fresh,
						timeout_seconds=timeout_seconds,
						max_retries=turn_retries,
					)
				except WizardCancelled:
					render_cancelled()
					return
				except WizardReturnHome:
					continue
				except WorkflowActionError as exc:
					render_error(code=exc.code, message=str(exc), recovery_action=exc.recovery_action)
				if not ask_continue_session(clear_before=False):
					return
				continue
			try:
				recovery_plan = build_plan(recovery)
				run = _run_plan(
					ctx,
					store,
					recovery_plan,
					interactive=True,
					resume_run_id=None,
					timeout_seconds=timeout_seconds,
					max_retries=turn_retries,
				)
			except WizardCancelled:
				render_cancelled()
				return
			except WizardReturnHome:
				continue
			except WorkflowActionError as exc:
				render_error(code=exc.code, message=str(exc), recovery_action=exc.recovery_action)
				if not ask_continue_session(clear_before=False):
					return
				continue
			_emit_run(ctx, run, interactive=True)
			if run.get("status") == "failed":
				if not ask_continue_session(clear_before=False):
					return
			continue
		if run.get("status") == "failed":
			if not ask_continue_session():
				return
			continue
		# 成功结束一轮后直接回主菜单（主菜单更清晰），不再强制「继续使用」确认。


def run_wizard_command(
	ctx: click.Context,
	*,
	input_json: str | None = None,
	resume_run_id: str | None = None,
	status_run_id: str | None = None,
	stop_run_id: str | None = None,
	timeout_seconds: float | None = None,
	max_retries: int = 0,
) -> None:
	interactive = _is_interactive(ctx)
	if not interactive:
		# stdin 非 TTY 时必须使用 Agent JSON 路由，即使 stdout 仍连接终端。
		ctx.obj["json_output"] = True
	selectors = [value for value in (input_json, resume_run_id, status_run_id, stop_run_id) if value is not None]
	if len(selectors) > 1:
		_wizard_error(
			ctx,
			interactive,
			code="INVALID_PARAM",
			message="--input-json/--resume/--status/--stop 只能指定一个",
			recovery_action="选择一个 workflow 操作后重试",
		)
		return

	data_dir = ctx.obj["data_dir"]
	with WorkflowStore(data_dir) as store:
		if status_run_id:
			run = store.get(status_run_id)
			if run is None:
				_wizard_error(
					ctx,
					interactive,
					code="JOB_NOT_FOUND",
					message=f"未找到任务: {status_run_id}",
				)
				return
			handle_output(ctx, "wizard", run, render=render_run)
			return
		if stop_run_id:
			if not store.request_stop(stop_run_id):
				_wizard_error(
					ctx,
					interactive,
					code="JOB_NOT_FOUND",
					message=f"未找到可停止的任务: {stop_run_id}",
				)
				return
			run = store.get(stop_run_id) or {}
			handle_output(ctx, "wizard", run, render=render_run)
			return

		# 纯 TTY 无 flag：多轮主菜单循环；flag / headless 仍是单次执行。
		if interactive and not selectors:
			_run_interactive_session(
				ctx,
				store,
				timeout_seconds=timeout_seconds,
				max_retries=max_retries,
			)
			return

		try:
			if resume_run_id:
				existing = store.get(resume_run_id)
				if existing is None:
					_wizard_error(
						ctx,
						interactive,
						code="JOB_NOT_FOUND",
						message=f"未找到任务: {resume_run_id}",
					)
					return
				plan = _plan_from_run(existing)
			elif input_json:
				plan = build_plan(_parse_input_json(input_json))
			else:
				handle_error_output(
					ctx,
					"wizard",
					code="WIZARD_INPUT_REQUIRED",
					message="非交互模式需要 --input-json、--resume、--status 或 --stop",
					recoverable=True,
					recovery_action="boss wizard --json --input-json '<object>'",
				)
				return
		except WorkflowInputError as exc:
			_wizard_error(
				ctx,
				interactive,
				code="INVALID_PARAM",
				message=str(exc),
				recovery_action="根据 boss schema 中的 wizard catalog 修正输入",
			)
			return

		try:
			run = _run_plan(
				ctx,
				store,
				plan,
				interactive=interactive,
				resume_run_id=resume_run_id,
				timeout_seconds=timeout_seconds,
				max_retries=max_retries,
			)
		except WorkflowActionError as exc:
			_wizard_error(
				ctx,
				interactive,
				code=exc.code,
				message=str(exc),
				recoverable=exc.recoverable,
				recovery_action=exc.recovery_action,
			)
			return

	if run.get("status") == "failed":
		error = run.get("error") or {}
		if interactive:
			render_run(run)
			raise SystemExit(1)
		_wizard_error(
			ctx,
			interactive,
			code=str(error.get("code") or "NETWORK_ERROR"),
			message=str(error.get("message") or "任务执行失败"),
			recoverable=bool(error.get("recoverable")),
			recovery_action=error.get("recovery_action"),
			details={"run": run},
		)
		return
	handle_output(
		ctx,
		"wizard",
		run,
		render=render_run,
		hints={"next_actions": [f"boss wizard --status {run['run_id']}"]},
	)


@click.command("wizard")
@click.option("--json", "wizard_json", is_flag=True, default=False, help="强制 JSON 输出")
@click.option("--input-json", default=None, help="headless workflow JSON object")
@click.option("--resume", "resume_run_id", default=None, help="恢复指定 workflow run_id")
@click.option("--status", "status_run_id", default=None, help="查看指定 workflow run_id")
@click.option("--stop", "stop_run_id", default=None, help="停止指定 workflow run_id")
@click.option("--timeout", "timeout_seconds", type=click.FloatRange(min=0.001), default=None, help="workflow 超时秒数")
@click.option("--max-retries", type=click.IntRange(min=0, max=10), default=0, help="可恢复步骤的最大重试次数")
@click.pass_context
def wizard_cmd(
	ctx: click.Context,
	wizard_json: bool,
	input_json: str | None,
	resume_run_id: str | None,
	status_run_id: str | None,
	stop_run_id: str | None,
	timeout_seconds: float | None,
	max_retries: int,
) -> None:
	"""启动真人向导或执行 headless workflow。"""
	if wizard_json:
		ctx.obj["json_output"] = True
	run_wizard_command(
		ctx,
		input_json=input_json,
		resume_run_id=resume_run_id,
		status_run_id=status_run_id,
		stop_run_id=stop_run_id,
		timeout_seconds=timeout_seconds,
		max_retries=max_retries,
	)
