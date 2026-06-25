"""Autonomous recruiter decision policy."""

from __future__ import annotations

import re
from dataclasses import dataclass

from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.models import (
	CandidateKey,
	CandidateSnapshot,
	Conversation,
	Decision,
	PlatformAction,
)
from boss_agent_cli.automation.scoring import score_candidate


@dataclass(frozen=True, slots=True)
class ConversationStatus:
	has_questionnaire: bool
	has_follow_up: bool
	has_exchange: bool
	candidate_after_questionnaire: bool
	candidate_after_follow_up: bool
	interview_time: str


def decide_action(
	conversation: Conversation,
	config: AutomationConfig,
	prior: dict[str, str],
) -> Decision:
	"""Choose the next action for a candidate conversation."""
	candidate = conversation.candidate or snapshot_from_conversation(conversation)
	candidate_key = candidate.key
	escalation = _escalation_reason(conversation)
	if escalation:
		return Decision(
			action=PlatformAction.QUEUE_REVIEW,
			confidence=0.25,
			reason=escalation,
			candidate_key=candidate_key,
			requires_human=True,
			risk_flags=("candidate-escalation",),
		)

	matching = score_candidate(candidate)
	status = _conversation_status(conversation, prior, config)
	if status.has_exchange and status.interview_time:
		return Decision(
			action=PlatformAction.CREATE_INTERVIEW_LEAD,
			confidence=min(0.86, matching.score / 100),
			reason=f"candidate provided interview time; candidate score {matching.score}",
			candidate_key=candidate_key,
			interview_time=status.interview_time,
			matching=matching,
			risk_flags=matching.risk_flags,
		)
	if status.has_exchange:
		return _skip(candidate_key, "contact already exchanged", matching)
	if status.has_follow_up:
		if status.candidate_after_follow_up:
			return _scored_decision(
				PlatformAction.EXCHANGE_CONTACT,
				"",
				"candidate replied after follow-up",
				candidate_key,
				matching,
			)
		return _skip(candidate_key, "waiting for candidate reply after follow-up", matching)
	if status.has_questionnaire:
		if status.candidate_after_questionnaire:
			return _scored_decision(
				PlatformAction.SEND_FOLLOW_UP,
				config.follow_up_message,
				"candidate replied after questionnaire",
				candidate_key,
				matching,
			)
		return _skip(candidate_key, "waiting for candidate reply after questionnaire", matching)
	if not conversation.incoming_messages:
		return _skip(candidate_key, "no incoming candidate message", matching)
	return _scored_decision(
		PlatformAction.SEND_QUESTIONNAIRE,
		config.questionnaire_message,
		"new candidate needs questionnaire",
		candidate_key,
		matching,
	)


def snapshot_from_conversation(conversation: Conversation) -> CandidateSnapshot:
	text = "\n".join((
		conversation.title,
		conversation.item_title,
		*conversation.incoming_messages,
		*conversation.outgoing_messages,
		*conversation.all_messages,
	))
	return CandidateSnapshot(
		key=CandidateKey(
			conversation.title or str(conversation.fingerprint) or "unknown-candidate"
		),
		name=conversation.title or str(conversation.fingerprint) or "unknown-candidate",
		title=conversation.item_title,
		resume_text=text,
		city=_extract_first(
			text,
			("上海", "北京", "广州", "深圳", "杭州", "成都", "武汉"),
		),
		experience_years=_extract_years(text),
		education=_extract_first(
			text,
			("博士", "硕士", "本科", "大专", "中专", "高中", "初中"),
		),
		last_active_at=(
			"active"
			if any(token in text for token in ("刚刚", "今天", "今日", "在线"))
			else ""
		),
		intent_signals=tuple(
			token
			for token in ("想看机会", "有兴趣", "可面试", "近期到岗", "观望", "暂不考虑")
			if token in text
		),
		risk_flags=tuple(
			token for token in ("投诉", "举报", "骚扰", "隐私") if token in text
		),
		do_not_contact=any(
			token in text for token in ("不要再联系", "别联系", "拉黑", "骚扰")
		),
	)


def _scored_decision(
	action: PlatformAction,
	message: str,
	reason: str,
	candidate_key: CandidateKey,
	matching,
) -> Decision:
	confidence = min(0.9, matching.score / 100)
	return Decision(
		action=action,
		confidence=confidence,
		reason=f"{reason}; candidate score {matching.score}",
		candidate_key=candidate_key,
		message=message,
		requires_human=confidence < 0.82,
		matching=matching,
		risk_flags=matching.risk_flags,
	)


def _skip(candidate_key: CandidateKey, reason: str, matching) -> Decision:
	return Decision(
		action=PlatformAction.SKIP,
		confidence=0.9,
		reason=reason,
		candidate_key=candidate_key,
		matching=matching,
	)


def _conversation_status(
	conversation: Conversation,
	prior: dict[str, str],
	config: AutomationConfig,
) -> ConversationStatus:
	texts = [message[1] for message in conversation.ordered_messages] or [
		*conversation.outgoing_messages,
		*conversation.incoming_messages,
	]
	questionnaire_index = _last_index(texts, config.questionnaire_message)
	follow_up_index = _last_index(texts, config.follow_up_message)
	latest_incoming = "\n".join(conversation.incoming_messages)
	return ConversationStatus(
		has_questionnaire=questionnaire_index >= 0
		or bool(prior.get("questionnaire_sent_at")),
		has_follow_up=follow_up_index >= 0
		or bool(prior.get("follow_up_sent_at")),
		has_exchange=bool(prior.get("exchange_contact_at")) or any(
			"交换微信" in text or "已交换" in text for text in texts
		),
		candidate_after_questionnaire=_has_incoming_after(conversation, questionnaire_index)
		or bool(prior.get("questionnaire_sent_at") and conversation.incoming_messages),
		candidate_after_follow_up=_has_incoming_after(conversation, follow_up_index)
		or bool(prior.get("follow_up_sent_at") and conversation.incoming_messages),
		interview_time=_extract_interview_time(latest_incoming),
	)


def _last_index(items: list[str], marker: str) -> int:
	if not marker:
		return -1
	for index in range(len(items) - 1, -1, -1):
		if marker in items[index]:
			return index
	return -1


def _has_incoming_after(conversation: Conversation, marker_index: int) -> bool:
	if marker_index < 0:
		return False
	return any(
		index > marker_index and direction == "incoming" and text.strip()
		for index, (direction, text) in enumerate(conversation.ordered_messages)
	)


def _escalation_reason(conversation: Conversation) -> str:
	text = "\n".join((*conversation.incoming_messages, *conversation.all_messages))
	for token in ("不要再联系", "投诉", "举报", "骚扰", "隐私", "收费", "押金"):
		if token in text:
			return f"candidate escalation detected: {token}"
	return ""


def _extract_first(text: str, options: tuple[str, ...]) -> str:
	return next((item for item in options if item in text), "")


def _extract_years(text: str) -> float | None:
	match = re.search(r"(\d+(?:\.\d+)?)\s*年", text)
	return float(match.group(1)) if match else None


def _extract_interview_time(text: str) -> str:
	pattern = (
		r"((今天|明天|后天|周[一二三四五六日]|星期[一二三四五六日天])"
		r"\s*(上午|下午|晚上)?\s*\d{1,2}[:：点]\d{0,2})"
	)
	match = re.search(pattern, text)
	return match.group(1).replace("：", ":").replace("点", ":00").strip() if match else ""
