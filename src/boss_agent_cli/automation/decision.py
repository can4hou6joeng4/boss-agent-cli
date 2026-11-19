"""Autonomous recruiter decision policy."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.models import (
	CandidateKey,
	CandidateSnapshot,
	Conversation,
	Decision,
	MatchScore,
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
	prior: dict[str, Any],
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
	matching: MatchScore,
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


def _skip(candidate_key: CandidateKey, reason: str, matching: MatchScore) -> Decision:
	return Decision(
		action=PlatformAction.SKIP,
		confidence=0.9,
		reason=reason,
		candidate_key=candidate_key,
		matching=matching,
	)


def _conversation_status(
	conversation: Conversation,
	prior: dict[str, Any],
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
		candidate_after_questionnaire=_replied_after(
			conversation, questionnaire_index, prior, "questionnaire_sent_at"
		),
		candidate_after_follow_up=_replied_after(
			conversation, follow_up_index, prior, "follow_up_sent_at"
		),
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


def _replied_after(
	conversation: Conversation,
	marker_index: int,
	prior: dict[str, Any],
	key: str,
) -> bool:
	"""候选人是否在我方话术之后回复过。

	有方向的转录里能定位到话术时以转录为准：prior 时间戳在发送成功后必然写入，
	而 ``incoming_messages`` 至少含触发问卷的旧消息，用它兜底会让下一轮无条件
	判成「已回复」并自动发出追问 / 交换联系方式（Issue #1944）。只有转录里
	找不到话术或没有方向信息时才退回 prior 标记。
	"""
	if marker_index >= 0 and conversation.ordered_messages:
		return _has_incoming_after(conversation, marker_index)
	sent = prior.get(key)
	return isinstance(sent, str) and bool(sent) and bool(conversation.incoming_messages)


def _candidate_texts(conversation: Conversation) -> tuple[str, ...]:
	"""候选人一侧的文本；完全没有方向信息时才退回全部消息。"""
	incoming = (
		*conversation.incoming_messages,
		*(text for direction, text in conversation.ordered_messages if direction == "incoming"),
	)
	if incoming or conversation.outgoing_messages or conversation.ordered_messages:
		return incoming
	return tuple(conversation.all_messages)


def _escalation_reason(conversation: Conversation) -> str:
	# 只看候选人说的话：我方话术里出现「隐私 / 收费」等词不是候选人升级（Issue #1945）。
	text = "\n".join(_candidate_texts(conversation))
	for token in ("不要再联系", "投诉", "举报", "骚扰", "隐私", "收费", "押金"):
		if token in text:
			return f"candidate escalation detected: {token}"
	return ""


def _extract_first(text: str, options: tuple[str, ...]) -> str:
	return next((item for item in options if item in text), "")


def _extract_years(text: str) -> float | None:
	match = re.search(r"(\d+(?:\.\d+)?)\s*年", text)
	return float(match.group(1)) if match else None


_INTERVIEW_DAY = r"(?:今天|明天|后天|周[一二三四五六日]|星期[一二三四五六日天])"
_INTERVIEW_PERIOD = r"(?:上午|下午|晚上|中午)?"
# H:MM / H：MM / H点MM / H点M / H点半 / H点一刻 / H点三刻
_INTERVIEW_MINUTES = (
	r"(?:(?P<colon>[：:])(?P<colon_minutes>\d{2})(?!\d)"
	r"|点(?:(?P<ban>半)|(?P<quarter>[一三])刻|(?P<point_minutes>\d{1,2})分?)?)"
)
_INTERVIEW_TIME = re.compile(
	r"(?P<prefix>" + _INTERVIEW_DAY + r"\s*" + _INTERVIEW_PERIOD + r"\s*\d{1,2})" + _INTERVIEW_MINUTES
)


def _extract_interview_time(text: str) -> str:
	"""Extract one spoken-time expression as ``<day><period> H:MM``.

	候选人说的是口语时间，可能是「3点30」「3点半」「3:30」「3点一刻」。旧实现
	把「点」一律替换成 `:00`，于是「3点30」变成 `3:0030`、「3点半」静默截成
	`3:00`——操作者会按 15:00 赴约而候选人说的是 15:30（Issue #891）。这里显式
	解析每种分钟形态；识别不出时返回空串，调用方据此不生成面试线索。
	"""
	match = _INTERVIEW_TIME.search(text)
	if match is None:
		return ""
	hour = match.group("prefix")
	hour_digits = re.search(r"\d{1,2}$", hour)
	if hour_digits is None or int(hour_digits.group()) > 23:
		# 「25点」不是时间；写进面试线索会让操作者按一个不存在的时刻赴约（Issue #1946）。
		return ""
	if match.group("colon"):
		minutes = match.group("colon_minutes")
	elif match.group("ban"):
		minutes = "30"
	elif match.group("quarter"):
		minutes = "15" if match.group("quarter") == "一" else "45"
	elif match.group("point_minutes"):
		minutes = match.group("point_minutes").zfill(2)
	else:
		minutes = "00"
	if int(minutes) > 59:
		return ""
	# 保留日期/时段原样（旧格式如「周二 14:00」继续带内部空格），仅去首尾空白。
	return f"{hour.strip()}:{minutes}"
