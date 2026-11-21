"""批次 90 审计修复：自动化决策的时间/状态证据与本地 AI 草稿编码边界（Issue #2364–#2367）。"""

from __future__ import annotations

import json

import pytest

from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.decision import (
	_conversation_status,
	_extract_interview_time,
	_latest_interview_time,
	decide_action,
)
from boss_agent_cli.automation.models import Conversation, PlatformAction
from boss_agent_cli.automation.reply_ai import parse_reply_draft

_CONFIG = AutomationConfig()


# ── Issue #2364：单条消息内的改期取最新时间 ─────────────────────────


def test_single_message_reschedule_uses_latest_time() -> None:
	conversation = Conversation(
		title="t",
		incoming_messages=("明天下午3点不行，改到周五下午4点可以吗",),
	)

	assert _latest_interview_time(conversation) == "周五下午4:00"


def test_decision_uses_latest_time_in_single_message() -> None:
	conversation = Conversation(
		title="t",
		incoming_messages=("明天下午3点不行，改到周五下午4点可以吗",),
	)

	decision = decide_action(
		conversation, _CONFIG, {"exchange_contact_at": "2026-11-20T00:00:00"}
	)

	assert decision.action == PlatformAction.CREATE_INTERVIEW_LEAD
	assert decision.interview_time == "周五下午4:00"


def test_invalid_latest_time_falls_back_to_earlier_valid_match() -> None:
	# 越界时刻跳过而不是让整条消息失效：前一处合法时间仍是唯一约定。
	assert _extract_interview_time("明天下午3点可以，明天25点不行") == "明天下午3:00"
	assert _extract_interview_time("明天25点不行，改到周五下午4点") == "周五下午4:00"


# ── Issue #2365：prior 时间戳类型漂移按缺失处理 ─────────────────────

_PRIOR_STATUS_FIELDS = {
	"questionnaire_sent_at": "has_questionnaire",
	"follow_up_sent_at": "has_follow_up",
	"exchange_contact_at": "has_exchange",
}


@pytest.mark.parametrize("key", sorted(_PRIOR_STATUS_FIELDS))
@pytest.mark.parametrize(
	"value",
	["false", "yesterday", "2026-13-40", 0, 1, True, {"at": "2026-11-20T00:00:00"}, ["2026-11-20T00:00:00"], "   "],
	ids=["str-false", "words", "bad-date", "zero", "one", "true", "dict", "list", "blank"],
)
def test_drifted_prior_timestamp_is_treated_as_missing(key: str, value: object) -> None:
	conversation = Conversation(
		title="t",
		incoming_messages=("您好",),
		outgoing_messages=("有空聊一下",),
	)

	status = _conversation_status(conversation, {key: value}, _CONFIG)

	assert getattr(status, _PRIOR_STATUS_FIELDS[key]) is False


def test_string_prior_timestamp_still_counts() -> None:
	conversation = Conversation(
		title="t",
		incoming_messages=("您好",),
		outgoing_messages=("有空聊一下",),
	)

	status = _conversation_status(
		conversation, {"questionnaire_sent_at": "2026-11-20T00:00:00"}, _CONFIG
	)

	assert status.has_questionnaire is True


def test_drifted_questionnaire_timestamp_does_not_trigger_follow_up() -> None:
	conversation = Conversation(
		title="t",
		incoming_messages=("您好",),
		outgoing_messages=("有空聊一下",),
	)

	decision = decide_action(conversation, _CONFIG, {"questionnaire_sent_at": "false"})

	assert decision.action == PlatformAction.SEND_QUESTIONNAIRE


def test_drifted_exchange_timestamp_does_not_skip() -> None:
	conversation = Conversation(
		title="t",
		incoming_messages=("您好",),
		outgoing_messages=("有空聊一下",),
	)

	decision = decide_action(conversation, _CONFIG, {"exchange_contact_at": "false"})

	assert decision.reason != "contact already exchanged"
	assert decision.action == PlatformAction.SEND_QUESTIONNAIRE


# ── Issue #2366：本地 AI 草稿拒绝孤立代理项 ─────────────────────────


@pytest.mark.parametrize("field", ["action", "reply", "reason"])
def test_parse_rejects_lone_surrogate_in_text_fields(field: str) -> None:
	payload = {
		"action": "send_follow_up",
		"reply": "ok",
		"confidence": 0.9,
		"reason": "ok",
	}
	payload[field] = "bad \ud800"

	with pytest.raises(ValueError):
		parse_reply_draft(json.dumps(payload))


def test_parse_rejects_lone_surrogate_in_risk_flags() -> None:
	payload = {
		"action": "send_follow_up",
		"reply": "ok",
		"confidence": 0.9,
		"reason": "ok",
		"risk_flags": ["bad \ud800"],
	}

	with pytest.raises(ValueError):
		parse_reply_draft(json.dumps(payload))


def test_parse_accepts_valid_draft() -> None:
	draft = parse_reply_draft(
		json.dumps(
			{
				"action": "send_follow_up",
				"reply": "您好，方便确认下近期的求职意向吗？",
				"confidence": 0.9,
				"reason": "candidate replied",
				"risk_flags": ["低风险"],
			}
		)
	)

	assert draft.reply == "您好，方便确认下近期的求职意向吗？"
	assert draft.risk_flags == ("低风险",)


# ── Issue #2367：否定 / 疑问句不算已交换 ────────────────────────────


@pytest.mark.parametrize(
	("text", "expected"),
	[
		("我们还没交换微信呢", False),
		("可以交换微信吗", False),
		("要不要加微信方便沟通", False),
		("好的，我们已交换", True),
		("我们加了微信", True),
		("微信已加，后续聊", True),
	],
)
def test_exchange_completion_heuristic(text: str, expected: bool) -> None:
	conversation = Conversation(title="t", incoming_messages=(text,))

	status = _conversation_status(conversation, {}, _CONFIG)

	assert status.has_exchange is expected


def test_negated_candidate_exchange_wording_does_not_skip() -> None:
	conversation = Conversation(
		title="t",
		incoming_messages=("我们还没交换微信呢",),
		outgoing_messages=("有空聊一下",),
	)

	decision = decide_action(conversation, _CONFIG, {})

	assert decision.reason != "contact already exchanged"
	assert decision.action == PlatformAction.SEND_QUESTIONNAIRE
