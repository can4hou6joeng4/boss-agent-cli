"""第九轮审计批次：Issue #2227–#2229 的回归测试。

- #2227：has_exchange 只看候选人一侧，我方话术里的「交换微信」不再触发跳过。
- #2228：面试时间按最近消息提取，候选人改期不再取到旧时间。
- #2229：没有我方消息锚点时不再把触发问卷的旧消息当成回复。
"""

from __future__ import annotations

from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.decision import decide_action
from boss_agent_cli.automation.models import Conversation, PlatformAction

_CONFIG = AutomationConfig()
_PRIOR_QUESTIONNAIRE = {"questionnaire_sent_at": "2026-11-20T00:00:00"}


def _decision(conversation: Conversation, prior: dict[str, str]):
	return decide_action(conversation, _CONFIG, prior)


# ── #2227：has_exchange 只看候选人 ──────────────────────────────────


def test_our_exchange_wording_does_not_skip_candidate() -> None:
	conversation = Conversation(
		title="t",
		incoming_messages=("您好",),
		outgoing_messages=("方便的话我们交换微信",),
		ordered_messages=(("incoming", "您好"), ("outgoing", "方便的话我们交换微信")),
	)

	decision = _decision(conversation, _PRIOR_QUESTIONNAIRE)

	assert decision.reason != "contact already exchanged"
	assert decision.action == PlatformAction.SKIP
	assert "waiting for candidate reply" in decision.reason


def test_candidate_exchange_wording_still_skips() -> None:
	conversation = Conversation(
		title="t",
		incoming_messages=("好的，我们已交换",),
		outgoing_messages=("您好，想确认下近期是否看机会？",),
	)

	decision = _decision(conversation, _PRIOR_QUESTIONNAIRE)

	assert decision.action == PlatformAction.SKIP
	assert decision.reason == "contact already exchanged"


# ── #2228：面试时间取最近一条消息 ───────────────────────────────────


def test_interview_time_prefers_latest_reschedule() -> None:
	conversation = Conversation(
		title="t",
		incoming_messages=("明天下午3点可以", "等等，改到后天上午10点"),
	)

	decision = _decision(conversation, {"exchange_contact_at": "2026-11-20T00:00:00"})

	assert decision.action == PlatformAction.CREATE_INTERVIEW_LEAD
	assert decision.interview_time == "后天上午10:00"


def test_interview_time_uses_ordered_messages_order() -> None:
	conversation = Conversation(
		title="t",
		incoming_messages=("明天下午3点可以", "后天上午10点吧"),
		outgoing_messages=("好的",),
		ordered_messages=(
			("incoming", "明天下午3点可以"),
			("outgoing", "好的"),
			("incoming", "后天上午10点吧"),
		),
	)

	decision = _decision(conversation, {"exchange_contact_at": "2026-11-20T00:00:00"})

	assert decision.interview_time == "后天上午10:00"


def test_stale_historical_time_not_used_when_no_candidate_time() -> None:
	conversation = Conversation(
		title="t",
		incoming_messages=(),
		outgoing_messages=("上周三 15:00 方便吗",),
	)

	decision = _decision(conversation, {"exchange_contact_at": "2026-11-20T00:00:00"})

	assert decision.interview_time == ""
	assert decision.action == PlatformAction.SKIP


# ── #2229：没有我方消息锚点时不推断已回复 ───────────────────────────


def test_without_outgoing_messages_questionnaire_is_not_a_reply() -> None:
	conversation = Conversation(title="t", incoming_messages=("触发问卷的旧消息",))

	decision = _decision(conversation, _PRIOR_QUESTIONNAIRE)

	assert decision.action == PlatformAction.SKIP
	assert "waiting for candidate reply after questionnaire" in decision.reason


def test_without_outgoing_messages_follow_up_is_not_a_reply() -> None:
	conversation = Conversation(title="t", incoming_messages=("触发问卷的旧消息",))

	decision = _decision(
		conversation,
		{"questionnaire_sent_at": "x", "follow_up_sent_at": "y"},
	)

	assert decision.action == PlatformAction.SKIP
	assert "waiting for candidate reply after follow-up" in decision.reason


def test_directionless_transcript_with_outgoing_still_follows_up() -> None:
	"""保留兼容：转录缺方向但确实有我方话术时，prior + incoming 仍算回复。"""
	conversation = Conversation(
		title="t",
		incoming_messages=("您好", "好的，可以聊"),
		outgoing_messages=("您好，想确认下近期是否看机会？",),
	)

	decision = _decision(conversation, _PRIOR_QUESTIONNAIRE)

	assert decision.action == PlatformAction.SEND_FOLLOW_UP
