"""Automation execution primitives."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from boss_agent_cli.automation.adapters import RecruiterAutomationPlatform
from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.decision import decide_action
from boss_agent_cli.automation.events import make_event, now_iso, stable_action_id
from boss_agent_cli.automation.models import (
	AutomationEvent,
	AutomationMode,
	CandidateKey,
	ConversationRef,
	Decision,
	EventStatus,
	PendingAction,
	PlatformAction,
	ReviewItem,
)
from boss_agent_cli.automation.reply_ai import apply_reply_strategy
from boss_agent_cli.automation.safety import SafetyGuard
from boss_agent_cli.automation.storage import AutomationStore


def process_pending(
	adapter: RecruiterAutomationPlatform,
	store: AutomationStore,
	guard: SafetyGuard,
	platform: str,
	dry_run: bool,
) -> list[AutomationEvent]:
	actions = store.read_pending()
	events: list[AutomationEvent] = []
	updated: list[PendingAction] = []
	for item in actions:
		if item.status != "pending":
			updated.append(item)
			continue
		action = PlatformAction(item.action)
		ref = ConversationRef(id=item.candidate_key, tab="pending")
		decision = Decision(
			action=action,
			confidence=item.confidence,
			reason=item.reason,
			candidate_key=CandidateKey(item.candidate_key),
			message=item.message,
		)
		if item.platform != platform:
			updated.append(item)
			continue
		status, event_reason = execute_or_dry_run(adapter, guard, decision, ref, dry_run)
		next_status = _next_pending_status(status)
		updated.append(_pending_with_status(item, next_status))
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
	store.write_pending(updated)
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
		store.append_interview_lead(candidate_key, decision.interview_time, decision.reason)
	elif status is EventStatus.AUTO_EXECUTED or status is EventStatus.DRY_RUN:
		status, event_reason = execute_or_dry_run(adapter, guard, decision, ref, dry_run)
		if status in {EventStatus.AUTO_EXECUTED, EventStatus.DRY_RUN}:
			update_prior(prior, decision)
	elif status is EventStatus.QUEUED_FOR_REVIEW:
		store.append_review(_review_item(platform, candidate_key, decision))
	elif status is EventStatus.QUEUED_PENDING_ACTION:
		store.append_pending(_pending_action(platform, candidate_key, decision, ""))
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
		case PlatformAction.SKIP:
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
	safety = guard.before_action(decision, adapter.detect_safety_warning() or "")
	if not safety.allowed:
		if safety.circuit_breaker:
			guard.open_circuit_breaker(safety.reason)
			return EventStatus.CIRCUIT_BREAKER_OPEN, safety.reason
		return EventStatus.STOPPED_BY_SAFETY, safety.reason
	if dry_run:
		return EventStatus.DRY_RUN, decision.reason
	result = adapter.execute_action(decision.action, decision.message, ref)
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


def _lead_status(
	config: AutomationConfig,
	decision: Decision,
	dry_run: bool,
) -> EventStatus:
	if decision.requires_human or decision.risk_flags:
		return EventStatus.QUEUED_FOR_REVIEW
	if config.mode in {AutomationMode.ASSIST, AutomationMode.TRAINING}:
		return EventStatus.QUEUED_FOR_REVIEW
	if decision.confidence < config.human_review_threshold:
		return EventStatus.SKIPPED
	return EventStatus.DRY_RUN if dry_run else EventStatus.AUTO_EXECUTED


def _action_status(
	config: AutomationConfig,
	decision: Decision,
	dry_run: bool,
) -> EventStatus:
	if decision.requires_human or decision.risk_flags:
		return EventStatus.QUEUED_FOR_REVIEW
	if config.mode in {AutomationMode.ASSIST, AutomationMode.TRAINING}:
		return EventStatus.QUEUED_FOR_REVIEW
	if decision.confidence < config.human_review_threshold:
		return EventStatus.SKIPPED
	if decision.confidence < config.auto_execute_threshold:
		return EventStatus.QUEUED_FOR_REVIEW
	if decision.action not in config.allowed_actions:
		return EventStatus.QUEUED_PENDING_ACTION
	return EventStatus.DRY_RUN if dry_run else EventStatus.AUTO_EXECUTED


def _review_item(
	platform: str,
	candidate_key: str,
	decision: Decision,
) -> ReviewItem:
	ts = now_iso()
	return ReviewItem(
		id=stable_action_id(platform, candidate_key, decision.action, ts),
		ts=ts,
		platform=platform,
		candidate_key=candidate_key,
		action=decision.action.value,
		status="review",
		confidence=decision.confidence,
		reason=decision.reason,
		message=decision.message,
	)


def _pending_action(
	platform: str,
	candidate_key: str,
	decision: Decision,
	approved_review_id: str,
) -> PendingAction:
	ts = now_iso()
	return PendingAction(
		id=stable_action_id(platform, candidate_key, decision.action, ts),
		ts=ts,
		platform=platform,
		candidate_key=candidate_key,
		action=decision.action.value,
		status="pending",
		confidence=decision.confidence,
		reason=decision.reason,
		message=decision.message,
		approved_review_id=approved_review_id,
	)


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
			return "dry-run"
		case (
			EventStatus.STOPPED_BY_SAFETY
			| EventStatus.CIRCUIT_BREAKER_OPEN
			| EventStatus.PLATFORM_VERIFICATION_REQUIRED
			| EventStatus.SKIPPED
			| EventStatus.QUEUED_FOR_REVIEW
			| EventStatus.QUEUED_PENDING_ACTION
		):
			return "pending"
