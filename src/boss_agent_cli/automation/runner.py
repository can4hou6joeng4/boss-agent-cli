"""Recruiter automation runner."""

from __future__ import annotations

from typing import Any

from boss_agent_cli.api.client import PlatformRiskError
from boss_agent_cli.api.request_budget import RequestBudgetExceeded, request_control
from boss_agent_cli.automation.adapters import RecruiterAutomationPlatform
from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.events import make_event
from boss_agent_cli.automation.execution import process_pending, process_ref
from boss_agent_cli.automation.models import (
	AutomationEvent,
	AutomationMode,
	EventStatus,
	PlatformAction,
	RunReport,
)
from boss_agent_cli.automation.safety import SafetyGuard
from boss_agent_cli.automation.storage import (
	AutomationControlBusy,
	AutomationControlBusyDispatched,
	AutomationStore,
)


def run_automation_cycle(
	adapter: RecruiterAutomationPlatform,
	store: AutomationStore,
	config: AutomationConfig,
	*,
	platform: str,
	dry_run: bool,
	limit: int | None = None,
	resume: bool = False,
) -> RunReport:
	"""Run one recruiter automation cycle under the cross-process execution lock.

	队列是整文件回写的：两个 executor 同时运行会互相覆盖在途标记，同一条动作
	可能被派发两次（Issue #1253）。显式 `--resume` 也在锁内执行，第二个 executor
	不会在第一个运行时抢先清除熔断。
	"""
	with store.execution_lock():
		if resume:
			store.resume()
		# 本周期是否真的把动作派发到平台：控制锁冲突的语义取决于它——
		# 零派发周期里只有本地状态写回撞锁，「没有生效」才是准确的（Issue #1307）。
		dispatched = [False]
		try:
			return _run_cycle_locked(
				adapter,
				store,
				config,
				platform=platform,
				dry_run=dry_run,
				limit=limit,
				dispatched=dispatched,
			)
		except AutomationControlBusy as exc:
			if dry_run or not dispatched[0]:
				# 预览没有发出任何请求；零派发周期也没有平台动作可能生效。
				raise
			# 周期内的冲突发生在动作派发与 checkpoint 之间：队列写回成功时平台
			# 动作可能已经发出，不能按「没有生效」让操作者直接重试（Issue #1267）。
			# `--resume` 在锁内、try 之外：它撞锁时确实什么都没做，保持原语义。
			raise AutomationControlBusyDispatched(str(exc)) from exc


def _run_cycle_locked(
	adapter: RecruiterAutomationPlatform,
	store: AutomationStore,
	config: AutomationConfig,
	*,
	platform: str,
	dry_run: bool,
	limit: int | None = None,
	dispatched: list[bool] | None = None,
) -> RunReport:
	"""Run one recruiter automation cycle while holding the execution lock."""
	state = store.read_state()
	guard = SafetyGuard(config, state, dry_run=dry_run, store=store)
	events: list[AutomationEvent] = []
	if config.mode is AutomationMode.PAUSED:
		return RunReport(
			status="PAUSED",
			events=(),
			dry_run=dry_run,
			platform=platform,
			mode=config.mode,
		)

	# 本周期是否以异常收尾：`finally` 只在正常路径写回 state。异常路径的本地
	# 写入（熔断闩锁 / 状态快照）可能撞上控制锁，不能让它覆盖原始异常
	# （Issue #1470）。
	failure: BaseException | None = None
	try:
		startup_warning = guard.stop_reason() or adapter.detect_safety_warning()
		if startup_warning:
			return _stop_for_warning(store, guard, state, platform, config, dry_run, startup_warning)

		with request_control(check=guard.raise_if_stopped):
			adapter.ensure_session()
			if guard.stop_reason() is None:
				pending_events, pending_conversations = process_pending(
					adapter, store, guard, platform, dry_run, state=state, dispatched=dispatched, limit=limit
				)
				events.extend(pending_events)
			if guard.stop_reason() is None:
				# --limit 是「本轮最多处理多少个会话」的全局上限（Issue #2021）：
				# 待执行队列先派发的会话占用预算，剩余部分再给扫描路径
				# （Issue #2100）。用 is None 而不是 or：limit=0 表示「本轮不处理
				# 任何会话」，而 `0 or max_per_tab` 会静默放大成默认的 20 个；
				# 负数在这里归一为 0，否则 `items[:负数]` 会反转成「去掉最后 N 个」。
				remaining = None if limit is None else max(0, limit - len(pending_conversations))
				if remaining == 0:
					# 预算已被待执行队列用满：不再发起扫描请求。
					refs = []
				else:
					max_per_tab = config.max_per_tab if remaining is None else remaining
					refs = adapter.scan_conversations(list(config.tabs), max_per_tab)
					# 队列（含本轮被平台拒绝后退回 pending、以及排队等下一轮的）
					# 拥有该会话的最高优先级：扫描路径跳过这些候选人，不能在同一
					# 轮用模板话术顶替操作者批准的动作，也不能跨轮把同一动作再发
					# 一次（Issue #2211）。verification-required 同样认领：操作者
					# 按指引删掉会话在途标记后，队列行是该候选人唯一的重发保护，
					# 不认领会让扫描路径把批准文案当成缺失、用模板问卷补位
					# （Issue #2452）。
					claimed = set(pending_conversations)
					claimed.update(
						row.candidate_key
						for row in store.read_pending()
						if row.platform == platform
						and row.status in {"pending", "executing", "verification-required"}
					)
					refs = [ref for ref in refs if ref.id not in claimed]
					if remaining is not None:
						# 每个 tab 各切一份会让默认两个 tab 时处理 2×limit 个会话；
						# max_per_tab 只用于限制每 tab 的拉取量。
						refs = refs[:remaining]
				for ref in refs:
					if guard.stop_reason() is not None:
						break
					if ref.diagnostic:
						event = make_event(
							platform,
							ref.id,
							PlatformAction.SKIP,
							EventStatus.STOPPED_BY_SAFETY,
							0.0,
							ref.diagnostic,
						)
						events.append(event)
						store.append_event(event)
						continue
					events.append(process_ref(adapter, store, config, guard, state, platform, dry_run, ref, dispatched=dispatched))
	except PlatformRiskError as exc:
		failure = exc
		latch_failure = _persist_breaker_latch(guard, store, platform, exc.code)
		if latch_failure is not None:
			exc.details.update(latch_failure)
		raise
	except KeyboardInterrupt as exc:
		failure = exc
		try:
			guard.open_circuit_breaker("执行被中断，恢复前请先核对未完成的操作")
		except Exception:
			# 本地闩锁写不进去不能把中断替换成控制锁错误：调用方仍要看到中断。
			pass
		raise
	except RequestBudgetExceeded as exc:
		failure = exc
		latch_failure = _persist_breaker_latch(guard, store, platform, exc.code)
		if latch_failure is not None:
			exc.details.update(latch_failure)
		raise
	except BaseException as exc:
		failure = exc
		raise
	finally:
		if failure is None:
			store.write_state(state)
	# dry-run 不落熔断闩锁，但报告仍需体现「真实运行会停下」：按本轮事件里的
	# CIRCUIT_BREAKER_OPEN 判定，而不是只看 guard.stop_reason()（Issue #1459）。
	breaker_open = guard.stop_reason() is not None or any(
		event.status == EventStatus.CIRCUIT_BREAKER_OPEN.value for event in events
	)
	return RunReport(
		status=EventStatus.CIRCUIT_BREAKER_OPEN.value if breaker_open else "OK",
		events=tuple(events),
		dry_run=dry_run,
		platform=platform,
		mode=config.mode,
	)


def _persist_breaker_latch(
	guard: SafetyGuard,
	store: AutomationStore,
	platform: str,
	code: str,
) -> dict[str, object] | None:
	"""落盘熔断闩锁与事件；本地写入失败时返回可合并进原始异常的说明。

	风控 / 预算停止的闩锁写回可能撞上控制锁（``AutomationControlBusy``）。本地
	写入失败不能掩盖原始异常：调用方必须继续看到 ACCOUNT_RISK / 预算停止，而不是
	AUTOMATION_BUSY（Issue #1470）。闩锁与事件日志必须分别判定：锁闩已成功落盘、
	仅日志追加失败时，不能把 ``risk_latch_persisted`` 报成 false（Issue #3067）。
	这里只捕获 ``Exception``，中断与 ``SystemExit`` 仍然照常上抛。
	"""
	latch_error: str | None = None
	try:
		guard.open_circuit_breaker(code)
	except Exception as exc:
		latch_error = f"{type(exc).__name__}: {exc}"
	try:
		store.append_event(
			make_event(platform, "", PlatformAction.SKIP, EventStatus.CIRCUIT_BREAKER_OPEN, 0.0, code)
		)
	except Exception as exc:
		details: dict[str, object] = {
			"risk_latch_persisted": latch_error is None,
			"risk_latch_event_error": f"{type(exc).__name__}: {exc}",
		}
		if latch_error is not None:
			details["risk_latch_error"] = latch_error
		return details
	if latch_error is not None:
		return {"risk_latch_persisted": False, "risk_latch_error": latch_error}
	return None


def _stop_for_warning(
	store: AutomationStore,
	guard: SafetyGuard,
	state: dict[str, Any],
	platform: str,
	config: AutomationConfig,
	dry_run: bool,
	warning: str,
) -> RunReport:
	if not dry_run:
		# 预览不落熔断闩锁：stop.json 会让后续真实运行在显式 --resume 前一律停下
		# （Issue #1459）。
		guard.open_circuit_breaker(warning)
	event = make_event(
		platform,
		"",
		PlatformAction.SKIP,
		EventStatus.CIRCUIT_BREAKER_OPEN,
		0.0,
		warning,
	)
	store.append_event(event)
	store.write_state(state)
	return RunReport(
		status=EventStatus.CIRCUIT_BREAKER_OPEN.value,
		events=(event,),
		dry_run=dry_run,
		platform=platform,
		mode=config.mode,
	)
