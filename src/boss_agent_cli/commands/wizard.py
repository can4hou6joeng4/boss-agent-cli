"""Interactive and headless shared workflow command."""

from __future__ import annotations

import json
import math
import sys
from typing import Any, Callable, Mapping, NoReturn, Sequence

import click

from boss_agent_cli.cache.store import CacheRecordInvalid
from boss_agent_cli.config import DEFAULT_REQUEST_DELAY
from boss_agent_cli.display import (
	RISK_ERROR_CONTRACTS,
	boss_command_for_ctx,
	console,
	escape_markup,
	handle_error_output,
	handle_output,
	shell_argument,
)
from boss_agent_cli.services.text_files import is_utf8_encodable
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.catalog import build_plan, require_supported_platform
from boss_agent_cli.wizard.models import WorkflowInputError, WorkflowPlan, WizardInput, ensure_storable_inputs
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
	wizard_screen,
)

from boss_agent_cli.wizard.runner import WorkflowActionError, WorkflowRunner, has_blocked_step
from boss_agent_cli.wizard.store import WorkflowStore


def _corrupt_run_notice(run_ids: Sequence[str]) -> str:
	"""损坏断点提示：run_id 来自数据库，按外部值转义后再交给 Rich（Issue #2823）。"""
	names = escape_markup(", ".join(run_ids))
	return (
		f"[yellow]跳过 {len(run_ids)} 条损坏的任务记录"
		f"（{names}）：断点无法恢复，需重新创建任务[/yellow]"
	)


def _validate_timeout(ctx: click.Context, param: click.Parameter, value: float | None) -> float | None:
	"""`click.FloatRange` 只比较大小，nan / inf 会穿过并让 deadline 计算失效（Issue #1385）。

	`--timeout inf` / `1e400` 时 `started_at + timeout` 是 inf，超时保护被静默
	关闭；`nan` 让任何比较都为 False，同样永远不触发。两者都必须按参数错误
	在解析阶段拒绝，而不是带着 ok:true 跑完整个 workflow。
	"""
	if value is None:
		return None
	if not math.isfinite(value) or value <= 0:
		raise click.BadParameter("必须是有限的正数秒数，例如 30", ctx=ctx, param=param)
	return value


def _is_interactive(ctx: click.Context) -> bool:
	return (
		not (ctx.obj or {}).get("json_output", False)
		and sys.stdin.isatty()
		and sys.stdout.isatty()
		and sys.stderr.isatty()
	)


def _command_for_ctx(ctx: click.Context) -> Callable[[str], str]:
	"""向导 TTY 恢复文案里的命令要定位本次运行的数据目录与平台（Issue #1090）。"""
	return lambda command: boss_command_for_ctx(ctx, command, include_data_dir=True)


def _interactive_error(
	ctx: click.Context,
	*,
	code: str,
	message: str | None = None,
	recovery_action: str | None = None,
) -> NoReturn:
	render_error(
		code=code,
		message=message,
		recovery_action=recovery_action,
		command_for=_command_for_ctx(ctx),
	)
	raise SystemExit(1)


def _error_metadata(error: Mapping[str, Any]) -> tuple[bool, str | None]:
	"""信封出口只接受已校验的布尔 / 字符串；任何漏网的漂移都按不可重试处理。

	持久化记录在 WorkflowStore 读取边界已经 fail-closed（Issue #1592）；这里
	再收一次类型，避免未来某条内存路径把 ``bool("false")`` 或数字恢复指引
	直接写进信封。
	"""
	recoverable = error.get("recoverable")
	recovery_action = error.get("recovery_action")
	return recoverable is True, recovery_action if isinstance(recovery_action, str) else None


def _wizard_error(
	ctx: click.Context,
	interactive: bool,
	*,
	code: str,
	message: str,
	recoverable: bool = False,
	recovery_action: str | None = None,
	details: dict[str, Any] | None = None,
	hints: dict[str, Any] | None = None,
) -> None:
	if interactive:
		_interactive_error(ctx, code=code, message=message, recovery_action=recovery_action)
	handle_error_output(
		ctx,
		"wizard",
		code=code,
		message=message,
		recoverable=recoverable,
		recovery_action=recovery_action,
		details=details,
		hints=hints,
	)


def _run_after_stop(store: WorkflowStore, run_id: str) -> dict[str, Any]:
	"""停止已落盘后取展示用的 run；读不出来时退回最小可用记录。

	停止标记已经写入，不能因为展示读取失败就把这次停止报成错误：叠加的实时
	采集损坏时退回断点本身；断点本体也损坏（如 ``params`` 不是合法 JSON）时，
	只回报已确认的 ``run_id`` 与 ``stopped``，让调用方看到停止确实生效。
	``--status`` 仍按展示契约报告损坏（Issue #1186）。
	"""
	try:
		return store.get(run_id) or {}
	except CacheRecordInvalid:
		pass
	try:
		return store.get_checkpoint(run_id, validate_replay_markers=False) or {}
	except CacheRecordInvalid:
		return {"run_id": run_id, "status": "stopped", "steps": []}


def _usable_identifier(value: Any) -> str | None:
	"""只接受可直接放进命令参数的可读标识；容器 / 空串一律跳过。

	``hints.next_actions`` 是给 Agent 执行的命令，不是展示文本。断点字段漂移成
	对象 / 列表时 ``str()`` 只会产出 Python repr，执行必然失败（Issue #2924）。
	"""
	if not isinstance(value, str):
		return None
	text = value.strip()
	if not text:
		return None
	try:
		text.encode("utf-8")
	except UnicodeEncodeError:
		return None
	return text


def _workflow_inner_run_id(run: Mapping[str, Any]) -> str:
	"""取被 stop 的 run 里绑定的 crawl 子任务 run_id（如有）。

	与 ``WorkflowStore.request_stop`` 的取值口径一致：步骤结果里的
	``inner_run_id``，或 crawl 动作返回数据里的 ``run_id``。
	"""
	for step in run.get("steps") or []:
		if not isinstance(step, Mapping):
			continue
		result = step.get("result") or {}
		if not isinstance(result, Mapping):
			continue
		inner = _usable_identifier(result.get("inner_run_id"))
		if inner is None and isinstance(result.get("data"), Mapping):
			inner = _usable_identifier(result["data"].get("run_id"))
		if inner:
			return inner
	return ""


def _workflow_risk_code(run: Mapping[str, Any]) -> str | None:
	"""run 或任一步骤是否记录了风控错误；命中即按终止契约处理。"""
	errors: list[Any] = [run.get("error")]
	for step in run.get("steps") or []:
		if isinstance(step, Mapping):
			errors.append(step.get("error"))
	for error in errors:
		if isinstance(error, Mapping) and error.get("code") in RISK_ERROR_CONTRACTS:
			return str(error["code"])
	return None


def _workflow_hints(ctx: click.Context, run: Mapping[str, Any]) -> dict[str, list[str]]:
	"""状态与停止后的后继提示；风控断点不给 ``--resume``（Issue #2608）。"""
	code = _workflow_risk_code(run)
	if code:
		return {
			"next_actions": [boss_command_for_ctx(ctx, "doctor", include_data_dir=True)],
			"operator_actions": list(RISK_ERROR_CONTRACTS[code]["operator_actions"]),
		}
	return {"next_actions": _workflow_next_actions(ctx, run)}


def _workflow_next_actions(ctx: click.Context, run: Mapping[str, Any]) -> list[str]:
	"""按 run 状态给出可直接执行的后继命令。

	``--status`` / ``--stop`` 成功后此前不带 hints：Agent 查完状态或停掉任务
	就断了，看不出该恢复、继续等待还是新建（主执行路径跑完反而给了
	``--status <run_id>``）。这里按状态补齐，取值来自 ``WorkflowStatus``。
	"""
	prefix = boss_command_for_ctx(ctx, "wizard", include_data_dir=True)
	run_id = str(run.get("run_id") or "").strip()
	if not run_id:
		return [prefix]
	status_cmd = f"{prefix} --status {shell_argument(run_id)}"
	status = str(run.get("status") or "")
	if status == "stopped":
		# stopped 是终态：runner 对 stopped 直接返回原 run，preserve_stopped 也保证
		# 状态无法离开 stopped——给 `--resume` 会让 Agent 得到一个永远 ok 的空转
		# 循环（Issue #870）。只能新建 workflow，或恢复它当时启动的内部 crawl。
		actions = [prefix]
		inner_run_id = _workflow_inner_run_id(run)
		if inner_run_id:
			# crawl run 也只存在于同一个数据目录，指引必须带上它（同 #861 的迁移）。
			actions.insert(
				0,
				boss_command_for_ctx(ctx, f"crawl resume {shell_argument(inner_run_id)}", include_data_dir=True),
			)
		actions.append(status_cmd)
		return actions
	if status == "failed":
		busy_hints = _busy_hints(ctx, run.get("error") or {})
		if busy_hints is not None:
			return busy_hints["next_actions"]
	if status == "failed" and has_blocked_step(run):
		# 失败步骤的结果无法重放（replay_safe=false）：--resume 只会原样返回同一个失败，
		# 照做的 Agent 会把它读成「再试一次」而空转。先看 --status 里该步骤的核对命令，
		# 确认平台实际状态后再决定；与 TTY「恢复已有任务」列表的筛选口径一致（Issue #1021）。
		return [status_cmd, prefix]
	if status in {"failed", "waiting_input"}:
		# 断点仍在本地：恢复是唯一能接着往下跑的动作，status 方便先核对。
		return [f"{prefix} --resume {shell_argument(run_id)}", status_cmd]
	if status in {"running", "pending"}:
		return [status_cmd, f"{prefix} --stop {shell_argument(run_id)}"]
	return [prefix, status_cmd]


def _busy_hints(ctx: click.Context, error: Mapping[str, Any]) -> dict[str, list[str]] | None:
	"""workflow 或其内部采集正由另一次调用执行时，只建议查看对应任务的状态。

	不给 ``--resume``（照做只会再撞一次）也不给 ``--stop``（照做会打断一次正常
	的执行）；要不要停由查到状态之后的调用方决定。
	"""
	details = error.get("details")
	if not isinstance(details, Mapping):
		return None
	run_id = _usable_identifier(details.get("run_id"))
	if not run_id:
		return None
	if error.get("code") == "WORKFLOW_BUSY":
		status_cmd = boss_command_for_ctx(ctx, f"wizard --status {shell_argument(run_id)}", include_data_dir=True)
	elif error.get("code") == "CRAWL_BUSY":
		status_cmd = boss_command_for_ctx(ctx, f"crawl status {shell_argument(run_id)}", include_data_dir=True)
	else:
		return None
	return {"next_actions": [status_cmd]}


def _failed_run_error(run: Mapping[str, Any]) -> Mapping[str, Any]:
	"""失败 run 的出口错误：顶层缺少有效 code 时回退到断点步骤的错误（Issue #2187）。

	写入器总会给 run 的 ``error`` 带上 code；空对象只可能来自外部漂移。步骤里
	仍保留 ``ACTION_UNCONFIRMED`` 与核对指引，用它出口比兜底成
	``NETWORK_ERROR`` +「重试」更接近真实状态——重试并不会让被阻断的 workflow
	继续，结果未知的写步骤需要先核对平台页面。
	"""
	top = run.get("error")
	if isinstance(top, Mapping) and isinstance(top.get("code"), str) and top["code"]:
		return top
	current = run.get("current_step")
	steps = [step for step in run.get("steps") or [] if isinstance(step, Mapping)]
	# 优先当前步骤，其次按落盘顺序取第一个带有效错误的步骤。
	steps.sort(key=lambda step: step.get("step_name") != current)
	for step in steps:
		step_error = step.get("error")
		if isinstance(step_error, Mapping) and isinstance(step_error.get("code"), str) and step_error["code"]:
			return step_error
	return top if isinstance(top, Mapping) else {}


def _plan_from_run(run: Mapping[str, Any]) -> WorkflowPlan:
	# 历史 run 可能保存着已移除的平台（如 zhilian）；恢复前与新建 plan 走同一套校验，
	# 否则会绕过 --platform 校验去读遗留登录态，并被兜底成可重试的 NETWORK_ERROR。
	require_supported_platform(str(run["role"]), str(run["platform"]))
	params = run.get("params") or {}
	inputs = dict(params.get("inputs") or {})
	# 新建 plan 在 `WizardInput.from_mapping` 已拒绝 NaN / 孤立代理字符（Issue
	# #1883、#2058），但 #2058 之前写入的历史 run 可能带着这类值；恢复时动作层
	# 多数参数没有可编码性校验，会在 sqlite 绑参 / httpx 编码阶段落成误导性的
	# LOCAL_IO_ERROR。本地记录写坏与 params 结构漂移同口径报 CACHE_RECORD_INVALID
	# （Issue #2687）。
	try:
		ensure_storable_inputs(inputs)
	except WorkflowInputError as exc:
		raise CacheRecordInvalid(
			"workflow_run", str(run.get("run_id") or "-"), "params.inputs", str(exc)
		) from exc
	return WorkflowPlan(
		role=str(run["role"]),
		platform=str(run["platform"]),
		goal=str(run["goal"]),
		inputs=inputs,
		requested_steps=tuple(params.get("requested_steps") or ()),
		mode=str(run["mode"]),
	)


def _parse_input_json(raw: str) -> WizardInput:
	try:
		value = json.loads(raw)
	# 超长整数（3.11+）与深嵌套和语法错误一样：都是 input-json 不合法。
	except (ValueError, RecursionError) as exc:
		raise WorkflowInputError(f"input-json 不是有效 JSON: {exc}") from exc
	if not isinstance(value, dict):
		raise WorkflowInputError("input-json 顶层必须是 JSON object")
	return WizardInput.from_mapping(value, mode="headless")


def _build_context(ctx: click.Context, plan: WorkflowPlan) -> ActionContext:
	return ActionContext(
		data_dir=ctx.obj["data_dir"],
		platform=plan.platform,
		role=plan.role,
		logger=ctx.obj["logger"],
		delay=ctx.obj.get("delay", DEFAULT_REQUEST_DELAY),
		cdp_url=ctx.obj.get("cdp_url"),
		browser_source=ctx.obj.get("browser_source"),
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
		run.get("status") == "completed" or (run.get("effective_completed") and has_result_follow_up(run))
	):
		list_run = run
		if has_result_follow_up(list_run):
			# 摘要由 collect_result_follow_up 在每页菜单前重绘；这里再画一次会因为
			# clear_wizard_screen 只清可视屏、不清 scrollback 而留下一个残框。
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
		error = _failed_run_error(run)
		if interactive:
			render_run(run, command_for=_command_for_ctx(ctx))
			return
		recoverable, recovery_action = _error_metadata(error)
		_wizard_error(
			ctx,
			interactive,
			code=str(error.get("code") or "NETWORK_ERROR"),
			message=str(error.get("message") or "任务执行失败"),
			recoverable=recoverable,
			recovery_action=recovery_action,
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
		render=lambda payload: render_run(payload, command_for=_command_for_ctx(ctx)),
		hints={
			"next_actions": [
				boss_command_for_ctx(
					ctx, f"wizard --status {shell_argument(str(run['run_id']))}", include_data_dir=True
				)
			]
		},
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
			available_runs = store.list_recent()
			if store.corrupt_run_ids:
				# 坏记录会被列表跳过；不说明会让用户以为任务凭空消失。
				console.print(_corrupt_run_notice(store.corrupt_run_ids))
			selection = collect_wizard_input(
				default_role=ctx.obj.get("role") or "candidate",
				default_platform=ctx.obj.get("platform") or "zhipin",
				available_runs=available_runs,
				data_dir=ctx.obj.get("data_dir"),
				local_only=local_only,
			)
		except WizardCancelled:
			render_cancelled()
			return
		except WorkflowInputError as exc:
			render_error(
				code="INVALID_PARAM",
				message=str(exc),
				recovery_action="请返回并重新输入",
				command_for=_command_for_ctx(ctx),
			)
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
						render_error(code="WORKFLOW_NOT_FOUND", command_for=_command_for_ctx(ctx))
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
						render_error(code="WORKFLOW_NOT_FOUND", command_for=_command_for_ctx(ctx))
					else:
						clear_wizard_screen()
						render_run(_run_after_stop(store, str(selection.run_id)))
					if not ask_continue_session(clear_before=False):
						return
					continue
				else:
					resume_run_id = selection.run_id
					if selection.action == "retry":
						turn_retries = max(1, max_retries)
					# 恢复只认断点：展示用的实时采集叠加损坏不能挡住 resume（Issue #1176）。
					existing = store.get_checkpoint(str(resume_run_id))
					if existing is None:
						render_error(code="WORKFLOW_NOT_FOUND", command_for=_command_for_ctx(ctx))
						if not ask_continue_session():
							return
						continue
					plan = _plan_from_run(existing)
			elif isinstance(selection, WizardInput) and selection.goal == "__view_crawl_jobs__":
				source_id = str(selection.inputs.get("source_run_id") or "")
				browse_run = store.get(source_id) if source_id else None
				if browse_run is None:
					render_error(
						code="WORKFLOW_NOT_FOUND",
						message="找不到可浏览的采集结果",
						command_for=_command_for_ctx(ctx),
					)
					if not ask_continue_session():
						return
					continue
			else:
				plan = build_plan(selection)
		except WorkflowInputError as exc:
			render_error(
				code="INVALID_PARAM",
				message=str(exc),
				recovery_action="请返回并重新输入",
				command_for=_command_for_ctx(ctx),
			)
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
				render_error(
					code=exc.code,
					message=str(exc),
					recovery_action=exc.recovery_action,
					command_for=_command_for_ctx(ctx),
				)
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
			render_error(
				code=exc.code,
				message=str(exc),
				recovery_action=exc.recovery_action,
				command_for=_command_for_ctx(ctx),
			)
			if not ask_continue_session():
				return
			continue

		_emit_run(ctx, run, interactive=True)
		if run.get("status") == "waiting_input":
			# 执行后仍 waiting：就地给出继续采集 / 浏览已有结果。
			clear_wizard_screen()
			# Re-load so live crawl completion + jobs surface after concurrent resume.
			fresh = store.get_for_result(str(run.get("run_id") or "")) or run
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
					render_error(
						code=exc.code,
						message=str(exc),
						recovery_action=exc.recovery_action,
						command_for=_command_for_ctx(ctx),
					)
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
				render_error(
					code=exc.code,
					message=str(exc),
					recovery_action=exc.recovery_action,
					command_for=_command_for_ctx(ctx),
				)
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
	try:
		_run_wizard_command(
			ctx,
			input_json=input_json,
			resume_run_id=resume_run_id,
			status_run_id=status_run_id,
			stop_run_id=stop_run_id,
			timeout_seconds=timeout_seconds,
			max_retries=max_retries,
		)
	except CacheRecordInvalid as exc:
		# 本地 run 记录写坏后断点读不出来：重试同样的 run_id 只会再失败一次，
		# 统一在这里给信封和重建指引，不让古老的坏行把 TTY 也打成 traceback。
		_wizard_error(
			ctx,
			_is_interactive(ctx),
			code="CACHE_RECORD_INVALID",
			message=str(exc),
			recoverable=True,
			recovery_action=f"{boss_command_for_ctx(ctx, 'wizard --input-json <object>', include_data_dir=True)} 重新创建任务；原记录无法恢复，已导出的文件不会自动删除",
		)
	except WorkflowActionError as exc:
		# 断点落盘失败：runner 已把原始失败原因合成到这条错误里（Issue #1963）。
		# 不能落到全局兜底变成 INTERNAL_ERROR，否则风控 / 预算的停止语义会丢。
		_wizard_error(
			ctx,
			_is_interactive(ctx),
			code=exc.code,
			message=str(exc),
			recoverable=exc.recoverable,
			recovery_action=exc.recovery_action,
			details=exc.details,
		)


def _run_wizard_command(
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
	selectors = [
		(name, value)
		for name, value in (
			("--input-json", input_json),
			("--resume", resume_run_id),
			("--status", status_run_id),
			("--stop", stop_run_id),
		)
		if value is not None
	]
	if len(selectors) > 1:
		_wizard_error(
			ctx,
			interactive,
			code="INVALID_PARAM",
			message="--input-json/--resume/--status/--stop 只能指定一个",
			recovery_action="选择一个 workflow 操作后重试",
		)
		return
	if len(selectors) == 1 and not selectors[0][1].strip():
		# 传了 flag 但值为空时，旧逻辑把它当成「没有选择器」，回一句
		# 「非交互模式需要 --input-json、--resume、--status 或 --stop」——
		# 用户明明传了，看到的却是「你没传」，只能再猜一次。
		is_json_input = selectors[0][0] == "--input-json"
		recovery = (
			"给 --input-json 传入 JSON 对象；根据 boss schema 中的 wizard catalog 修正输入"
			if is_json_input
			else f"补上 run_id 后重试；用 {boss_command_for_ctx(ctx, 'wizard --status <run_id>', include_data_dir=True)} 核对已有任务"
		)
		next_action = boss_command_for_ctx(ctx, "schema" if is_json_input else "wizard", include_data_dir=True)
		_wizard_error(
			ctx,
			interactive,
			code="INVALID_PARAM",
			message=f"{selectors[0][0]} 的值不能为空",
			recovery_action=recovery,
			hints={"next_actions": [next_action]},
		)
		return
	if (
		len(selectors) == 1
		and selectors[0][0] != "--input-json"
		and not is_utf8_encodable(selectors[0][1])
	):
		# run_id 从 argv 来：非法 UTF-8 字节经 surrogateescape 成为孤立代理项。
		# 绑定 sqlite 抛 UnicodeEncodeError 会被兜成带磁盘指引的 LOCAL_IO_ERROR，
		# 与「输入非法」不符；入口直接拒绝（Issue #2499）。
		_wizard_error(
			ctx,
			interactive,
			code="INVALID_PARAM",
			message=f"{selectors[0][0]} 的 run_id 含有无法按 UTF-8 编码的字符（孤立代理项）",
			recovery_action=(
				f"从 {boss_command_for_ctx(ctx, 'wizard --status <run_id>', include_data_dir=True)} "
				"或任务列表重新复制 run_id 后重试"
			),
			hints={"next_actions": [boss_command_for_ctx(ctx, "wizard", include_data_dir=True)]},
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
					code="WORKFLOW_NOT_FOUND",
					message=f"未找到任务: {status_run_id}",
					recovery_action=(
						f"{boss_command_for_ctx(ctx, 'wizard', include_data_dir=True)}（新建 workflow），或 "
						f"{boss_command_for_ctx(ctx, 'wizard --status <run_id>', include_data_dir=True)} 核对 ID"
					),
					hints={"next_actions": [boss_command_for_ctx(ctx, "wizard", include_data_dir=True)]},
				)
				return
			handle_output(
				ctx,
				"wizard",
				run,
				render=lambda payload: render_run(payload, command_for=_command_for_ctx(ctx)),
				hints=_workflow_hints(ctx, run),
			)
			return
		if stop_run_id:
			# 先读断点区分「不存在」与「已结束」：request_stop 只排除 completed，
			# 把存在但已完成的任务误报为 ID 写错（Issue #1585）。损坏的断点不影响
			# 停止标记本身，仍交给 request_stop 按既有补偿语义处理。
			def _stop_already_finished(*, recovery_action: str, next_actions: list[str]) -> None:
				_wizard_error(
					ctx,
					interactive,
					code="WORKFLOW_ALREADY_FINISHED",
					message=f"任务已结束，无法停止: {stop_run_id}",
					recovery_action=recovery_action,
					hints={"next_actions": next_actions},
				)

			existing_run: dict[str, Any] | None = None
			record_corrupt = False
			try:
				existing_run = store.get_checkpoint(stop_run_id, validate_replay_markers=False)
			except CacheRecordInvalid:
				# 记录存在但读不出来（params 等漂移）：request_stop 之后返回
				# False 时不能报成「未找到」，否则与 --status 的损坏结论矛盾
				# （Issue #2035）。
				record_corrupt = True
			if existing_run is not None and existing_run.get("status") == "completed":
				_stop_already_finished(
					recovery_action=(
						f"{boss_command_for_ctx(ctx, 'wizard --status <run_id>', include_data_dir=True)} 查看结果与最终状态，或 "
						f"{boss_command_for_ctx(ctx, 'wizard', include_data_dir=True)} 新建 workflow"
					),
					next_actions=[
						boss_command_for_ctx(
							ctx, f"wizard --status {shell_argument(stop_run_id)}", include_data_dir=True
						)
					],
				)
				return
			if not store.request_stop(stop_run_id):
				if record_corrupt:
					# request_stop 的 SQL 只在 status = completed 时匹配 0 行，而记录
					# 确实存在（刚读到损坏）：任务是已结束，不是 ID 写错（Issue #2035）。
					_wizard_error(
						ctx,
						interactive,
						code="WORKFLOW_ALREADY_FINISHED",
						message=f"任务已结束，无法停止: {stop_run_id}（本地断点损坏，--status 可能无法读取）",
						recovery_action=(
							f"{boss_command_for_ctx(ctx, 'wizard', include_data_dir=True)} 新建 workflow；"
							"如需查看结果，该 run 的断点已损坏、无法恢复"
						),
						hints={"next_actions": [boss_command_for_ctx(ctx, "wizard", include_data_dir=True)]},
					)
					return
				# pre-read 与 request_stop 之间 worker 可能刚把 run 落成 completed
				# （TOCTOU）：request_stop 的 WHERE status != 'completed' 返回 0 行，
				# 与 run 不存在无法区分。重读断点再分类，恢复指引不能把已完成任务
				# 说成 ID 写错（Issue #2057）。
				after: dict[str, Any] | None = None
				try:
					after = store.get_checkpoint(stop_run_id, validate_replay_markers=False)
				except CacheRecordInvalid:
					after = None
				if after is not None and after.get("status") == "completed":
					_stop_already_finished(
						recovery_action=(
							f"{boss_command_for_ctx(ctx, 'wizard --status <run_id>', include_data_dir=True)} 查看结果与最终状态，或 "
							f"{boss_command_for_ctx(ctx, 'wizard', include_data_dir=True)} 新建 workflow"
						),
						next_actions=[
							boss_command_for_ctx(
								ctx, f"wizard --status {shell_argument(stop_run_id)}", include_data_dir=True
							)
						],
					)
					return
				_wizard_error(
					ctx,
					interactive,
					code="WORKFLOW_NOT_FOUND",
					message=f"未找到可停止的任务: {stop_run_id}",
					recovery_action=(
						f"{boss_command_for_ctx(ctx, 'wizard', include_data_dir=True)}（新建 workflow），或 "
						f"{boss_command_for_ctx(ctx, 'wizard --status <run_id>', include_data_dir=True)} 核对 ID"
					),
					hints={"next_actions": [boss_command_for_ctx(ctx, "wizard", include_data_dir=True)]},
				)
				return
			run = _run_after_stop(store, stop_run_id)
			handle_output(
				ctx, "wizard", run, render=render_run, hints=_workflow_hints(ctx, run)
			)
			return

		# 纯 TTY 无 flag：多轮主菜单循环；flag / headless 仍是单次执行。
		if interactive and not selectors:
			# 整个多轮会话独占一块备用屏：退出后终端恢复原样，不留残框。
			# 单次 flag 路径（--status/--resume/--stop）刻意不进——一进一出会把
			# 刚渲染的内容直接抹掉，用户什么都看不到。
			with wizard_screen():
				_run_interactive_session(
					ctx,
					store,
					timeout_seconds=timeout_seconds,
					max_retries=max_retries,
				)
			return

		try:
			if resume_run_id:
				# 恢复只认断点：展示用的实时采集叠加损坏不能挡住 resume（Issue #1176）。
				existing = store.get_checkpoint(resume_run_id)
				if existing is None:
					_wizard_error(
						ctx,
						interactive,
						code="WORKFLOW_NOT_FOUND",
						message=f"未找到任务: {resume_run_id}",
						recovery_action=(
							f"{boss_command_for_ctx(ctx, 'wizard', include_data_dir=True)}（新建 workflow），或 "
							f"{boss_command_for_ctx(ctx, 'wizard --status <run_id>', include_data_dir=True)} 核对 ID"
						),
						hints={"next_actions": [boss_command_for_ctx(ctx, "wizard", include_data_dir=True)]},
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
					recovery_action=f"在原来的数据目录执行 {boss_command_for_ctx(ctx, 'wizard --json --input-json <object>', include_data_dir=True)} 创建新任务",
				)
				return
		except WorkflowInputError as exc:
			_wizard_error(
				ctx,
				interactive,
				code="INVALID_PARAM",
				message=str(exc),
				recovery_action="根据 boss schema 中的 wizard catalog 修正输入",
				# recovery_action 是给真人的说明；Agent 需要一条能直接执行的动作。
				hints={"next_actions": ["boss schema"]},
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
				details=exc.details,
				hints=_busy_hints(ctx, exc.to_dict()),
			)
			return

	if run.get("status") == "failed":
		error = _failed_run_error(run)
		if interactive:
			render_run(run, command_for=_command_for_ctx(ctx))
			raise SystemExit(1)
		recoverable, recovery_action = _error_metadata(error)
		_wizard_error(
			ctx,
			interactive,
			code=str(error.get("code") or "NETWORK_ERROR"),
			message=str(error.get("message") or "任务执行失败"),
			recoverable=recoverable,
			recovery_action=recovery_action,
			details={"run": run},
			hints=_busy_hints(ctx, error),
		)
		return
	handle_output(
		ctx,
		"wizard",
		run,
		render=lambda payload: render_run(payload, command_for=_command_for_ctx(ctx)),
		hints={
			"next_actions": [
				boss_command_for_ctx(
					ctx, f"wizard --status {shell_argument(str(run['run_id']))}", include_data_dir=True
				)
			]
		},
	)


@click.command("wizard")
@click.option("--json", "wizard_json", is_flag=True, default=False, help="强制 JSON 输出")
@click.option("--input-json", default=None, help="headless workflow JSON object")
@click.option("--resume", "resume_run_id", default=None, help="恢复指定 workflow run_id")
@click.option("--status", "status_run_id", default=None, help="查看指定 workflow run_id")
@click.option("--stop", "stop_run_id", default=None, help="停止指定 workflow run_id")
@click.option(
	"--timeout",
	"timeout_seconds",
	type=click.FloatRange(min=0.001),
	default=None,
	callback=_validate_timeout,
	help="workflow 超时秒数",
)
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
