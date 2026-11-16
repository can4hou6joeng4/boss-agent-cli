"""Automation execution primitives."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from boss_agent_cli.api.request_budget import RequestBudgetExceeded
from boss_agent_cli.automation.adapters import RecruiterAutomationPlatform
from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.decision import decide_action
from boss_agent_cli.automation.events import make_event, now_iso
from boss_agent_cli.automation.models import (
	AutomationEvent,
	CandidateKey,
	ConversationRef,
	Decision,
	EventStatus,
	PendingAction,
	PlatformAction,
)
from boss_agent_cli.automation.reply_ai import apply_reply_strategy
from boss_agent_cli.automation.safety import SafetyGuard
from boss_agent_cli.automation.storage import AutomationStore, queued_action


def process_pending(
	adapter: RecruiterAutomationPlatform,
	store: AutomationStore,
	guard: SafetyGuard,
	platform: str,
	dry_run: bool,
	*,
	state: dict[str, Any] | None = None,
) -> list[AutomationEvent]:
	actions = store.read_pending()
	events: list[AutomationEvent] = []
	for index, item in enumerate(actions):
		if guard.stop_reason() is not None:
			break
		if item.status == "executing":
			# 上一次执行在写请求已发出后中断：结果未知，不能自动重发（Issue #1245）。
			# 转成 verification-required 并打开熔断，先让操作者核对官方页面。
			if item.platform != platform:
				continue
			reason = f"pending action {item.id!r} was interrupted while executing"
			guard.open_circuit_breaker(f"platform verification required: {reason}")
			actions[index] = _pending_with_status(item, "verification-required")
			# 队列标记先落盘，再写事件：事件写失败也不会让该行回到可重放状态。
			store.write_pending(actions)
			event = make_event(
				platform,
				item.candidate_key,
				queued_action(item, store.pending_path),
				EventStatus.PLATFORM_VERIFICATION_REQUIRED,
				item.confidence,
				reason,
			)
			events.append(event)
			store.append_event(event)
			break
		if item.status != "pending":
			continue
		action = queued_action(item, store.pending_path)
		ref = ConversationRef(id=item.candidate_key, tab="pending")
		decision = Decision(
			action=action,
			confidence=item.confidence,
			reason=item.reason,
			candidate_key=CandidateKey(item.candidate_key),
			message=item.message,
		)
		if item.platform != platform:
			continue
		if not dry_run:
			# 先落盘「执行中」再派发：进程在写请求发出后中断时，下一轮看到
			# executing 行就知道结果未知，不会把可能已生效的动作重发一遍
			# （Issue #1245）。写盘失败时动作尚未发出，行仍是 pending，可安全重试。
			actions[index] = _pending_with_status(item, "executing")
			store.write_pending(actions)
			actions[index] = item
		try:
			status, event_reason = execute_or_dry_run(adapter, guard, decision, ref, dry_run)
		except RequestBudgetExceeded as exc:
			# 预算 / 时限在动作发出前抛出的停止（details.request_sent=False）可以安全
			# 恢复 pending；其它异常（含中断与响应风控）请求可能已发出，保守保留
			# executing，下一轮按结果未知处理（Issue #1245）。
			if exc.details.get("request_sent") is False:
				actions[index] = _pending_with_status(item, "pending")
				store.write_pending(actions)
			raise
		next_status = _next_pending_status(status)
		actions[index] = _pending_with_status(item, next_status)
		# Commit each confirmed queue effect before attempting another item. A
		# later risk/error must not leave a successful action pending for replay.
		store.write_pending(actions)
		# 预览不是执行：dry-run 期间写 prior 会让下一次真实运行误以为动作已发生
		# （Issue #889），因此只有真正执行成功才更新业务状态。
		if state is not None and status is EventStatus.AUTO_EXECUTED:
			prior = state.setdefault("conversations", {}).setdefault(item.candidate_key, {})
			update_prior(prior, decision)
		guard.checkpoint()
		event = make_event(
			platform,
			item.candidate_key,
			action,
			status,
			decision.confidence,
			event_reason,
		)
		events.append(event)
		store.append_event(event)
	return events


def process_ref(
	adapter: RecruiterAutomationPlatform,
	store: AutomationStore,
	config: AutomationConfig,
	guard: SafetyGuard,
	state: dict[str, Any],
	platform: str,
	dry_run: bool,
	ref: ConversationRef,
) -> AutomationEvent:
	conversation = adapter.read_conversation(ref)
	candidate_key = conversation.title or str(conversation.fingerprint) or ref.id
	prior = state.setdefault("conversations", {}).setdefault(candidate_key, {})
	decision = decide_action(conversation, config, prior)
	decision = apply_reply_strategy(decision, conversation, config, store.root.parent)
	status = status_for_decision(config, decision, dry_run)
	event_reason = decision.reason
	if decision.action is PlatformAction.CREATE_INTERVIEW_LEAD and status in {
		EventStatus.AUTO_EXECUTED,
		EventStatus.DRY_RUN,
	}:
		safety = guard.before_action(decision)
		if safety.allowed:
			# 预览不落盘线索记录（Issue #889）：dry-run 后重复运行不能看见
			# 一份从未真实产生过的面试线索清单。
			if status is EventStatus.AUTO_EXECUTED:
				store.append_interview_lead(candidate_key, decision.interview_time, decision.reason)
		else:
			status = EventStatus.CIRCUIT_BREAKER_OPEN if safety.circuit_breaker else EventStatus.STOPPED_BY_SAFETY
			event_reason = safety.reason
	elif status is EventStatus.AUTO_EXECUTED or status is EventStatus.DRY_RUN:
		status, event_reason = execute_or_dry_run(adapter, guard, decision, ref, dry_run)
		# 只有真正执行成功才把动作记进对话阶段；dry-run 写 prior 会让下一次
		# 真实运行误判「问卷已发」并跳过它（Issue #889）。
		if status is EventStatus.AUTO_EXECUTED:
			update_prior(prior, decision)
	guard.checkpoint()
	event = make_event(
		platform,
		candidate_key,
		decision.action,
		status,
		decision.confidence,
		event_reason,
	)
	store.append_event(event)
	return event


def status_for_decision(
	config: AutomationConfig,
	decision: Decision,
	dry_run: bool,
) -> EventStatus:
	match decision.action:
		case PlatformAction.SKIP | PlatformAction.QUEUE_REVIEW:
			return EventStatus.SKIPPED
		case PlatformAction.CREATE_INTERVIEW_LEAD:
			return _lead_status(config, decision, dry_run)
		case _:
			return _action_status(config, decision, dry_run)


def execute_or_dry_run(
	adapter: RecruiterAutomationPlatform,
	guard: SafetyGuard,
	decision: Decision,
	ref: ConversationRef,
	dry_run: bool,
) -> tuple[EventStatus, str]:
	stop = guard.stop_reason()
	if stop is not None:
		return EventStatus.CIRCUIT_BREAKER_OPEN, stop
	safety = guard.before_action(decision, adapter.detect_safety_warning() or "")
	if not safety.allowed:
		if safety.circuit_breaker:
			guard.open_circuit_breaker(safety.reason)
			return EventStatus.CIRCUIT_BREAKER_OPEN, safety.reason
		return EventStatus.STOPPED_BY_SAFETY, safety.reason
	if dry_run:
		return EventStatus.DRY_RUN, decision.reason
	result = adapter.execute_action(decision.action, decision.message, ref)
	if result.status == "unconfirmed":
		# 结果可能已生效：不重试、不继续处理其他候选人，
		# 先停下来让操作者在官方页面核对后再显式 --resume。
		reason = str(result.details.get("reason") or "ACTION_UNCONFIRMED")
		guard.open_circuit_breaker(f"platform verification required: {reason}")
		return EventStatus.PLATFORM_VERIFICATION_REQUIRED, reason
	if result.status != "executed":
		reason = str(result.details.get("reason", result.status))
		guard.record_failure(reason)
		return EventStatus.STOPPED_BY_SAFETY, reason
	guard.after_action(decision)
	return EventStatus.AUTO_EXECUTED, decision.reason


def update_prior(prior: dict[str, str], decision: Decision) -> None:
	match decision.action:
		case PlatformAction.SEND_QUESTIONNAIRE:
			prior["questionnaire_sent_at"] = now_iso()
		case PlatformAction.SEND_FOLLOW_UP:
			prior["follow_up_sent_at"] = now_iso()
		case PlatformAction.EXCHANGE_CONTACT:
			prior["exchange_contact_at"] = now_iso()
		case _:
			return


def _common_gate(config: AutomationConfig, decision: Decision) -> EventStatus | None:
	"""Apply decision-level risk and confidence controls without a human approval gate."""
	if decision.risk_flags:
		return EventStatus.SKIPPED
	if decision.confidence < config.human_review_threshold:
		return EventStatus.SKIPPED
	return None


def _lead_status(
	config: AutomationConfig,
	decision: Decision,
	dry_run: bool,
) -> EventStatus:
	gated = _common_gate(config, decision)
	if gated is not None:
		return gated
	return EventStatus.DRY_RUN if dry_run else EventStatus.AUTO_EXECUTED


def _action_status(
	config: AutomationConfig,
	decision: Decision,
	dry_run: bool,
) -> EventStatus:
	gated = _common_gate(config, decision)
	if gated is not None:
		return gated
	if decision.confidence < config.auto_execute_threshold:
		return EventStatus.SKIPPED
	if decision.action not in config.allowed_actions:
		return EventStatus.SKIPPED
	return EventStatus.DRY_RUN if dry_run else EventStatus.AUTO_EXECUTED


def _pending_with_status(item: PendingAction, status: str) -> PendingAction:
	values = asdict(item)
	values["status"] = status
	values["updated_at"] = now_iso()
	return PendingAction(**values)


def _next_pending_status(status: EventStatus) -> str:
	match status:
		case EventStatus.AUTO_EXECUTED:
			return "executed"
		case EventStatus.DRY_RUN:
			# 预览没有发出任何东西：队列项必须留在 pending，下一次真实运行才会执行
			# （Issue #889）。旧实现把它改成 "dry-run"，演练一次就永久消耗掉
			# 操作者批准过的待执行动作。
			return "pending"
		case EventStatus.PLATFORM_VERIFICATION_REQUIRED:
			# 动作可能已经生效：退出 pending 队列，避免下一轮自动重发。
			return "verification-required"
		case (
			EventStatus.STOPPED_BY_SAFETY
			| EventStatus.CIRCUIT_BREAKER_OPEN
			| EventStatus.SKIPPED
			| EventStatus.QUEUED_FOR_REVIEW
			| EventStatus.QUEUED_PENDING_ACTION
		):
			return "pending"
