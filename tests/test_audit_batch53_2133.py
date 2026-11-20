"""审计批次五十三：话术被改写 / 改模板后不得判「已回复」（Issue #2133）。

本地 AI 会改写问卷 / 追问的实际发送文本（#1511），操作者也可能中途修改模板。
此前 `_replied_after` 在转录里定位不到话术时退回 prior 时间戳兜底，而
``incoming_messages`` 至少含触发问卷的旧消息——下一轮无条件判「已回复」，
真实自动发出追问与交换联系方式。
"""

from __future__ import annotations

from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.decision import decide_action
from boss_agent_cli.automation.models import Conversation, ConversationFingerprint, PlatformAction

_RICH = "我在上海做过3年销售，本科，客服运营客户沟通面谈都可以，刚刚看到，想看机会，可面试"
_QUESTIONNAIRE_TS = "2026-06-01T00:00:00+00:00"
_FOLLOW_UP_TS = "2026-06-02T00:00:00+00:00"
_REWRITTEN_QUESTIONNAIRE = "您好，我们这边想和您确认一下最近是否考虑新的工作机会？"
_REWRITTEN_FOLLOW_UP = "感谢回复，岗位信息我这边同步给您，方便的话继续沟通面试时间。"


def _conversation(title: str, ordered: list[tuple[str, str]]) -> Conversation:
	return Conversation(
		title=title,
		incoming_messages=tuple(text for direction, text in ordered if direction == "incoming"),
		outgoing_messages=tuple(text for direction, text in ordered if direction == "outgoing"),
		ordered_messages=tuple(ordered),
		all_messages=tuple(text for _, text in ordered),
		fingerprint=ConversationFingerprint(title),
	)


def test_rewritten_questionnaire_without_reply_waits() -> None:
	conversation = _conversation(
		"401", [("incoming", _RICH), ("outgoing", _REWRITTEN_QUESTIONNAIRE)]
	)

	decision = decide_action(
		conversation, AutomationConfig(), {"questionnaire_sent_at": _QUESTIONNAIRE_TS}
	)

	assert decision.action == PlatformAction.SKIP
	assert "waiting for candidate reply after questionnaire" in decision.reason


def test_rewritten_questionnaire_with_reply_follows_up() -> None:
	conversation = _conversation(
		"402",
		[("incoming", _RICH), ("outgoing", _REWRITTEN_QUESTIONNAIRE), ("incoming", _RICH)],
	)

	decision = decide_action(
		conversation, AutomationConfig(), {"questionnaire_sent_at": _QUESTIONNAIRE_TS}
	)

	assert decision.action == PlatformAction.SEND_FOLLOW_UP


def test_rewritten_follow_up_without_reply_does_not_exchange() -> None:
	conversation = _conversation(
		"403",
		[
			("incoming", _RICH),
			("outgoing", _REWRITTEN_QUESTIONNAIRE),
			("outgoing", _REWRITTEN_FOLLOW_UP),
		],
	)

	decision = decide_action(
		conversation,
		AutomationConfig(),
		{"questionnaire_sent_at": _QUESTIONNAIRE_TS, "follow_up_sent_at": _FOLLOW_UP_TS},
	)

	assert decision.action == PlatformAction.SKIP
	assert "waiting for candidate reply after follow-up" in decision.reason


def test_changed_template_is_not_treated_as_reply() -> None:
	old_template = "您好，想确认下近期是否看机会？"
	config = AutomationConfig(questionnaire_message="您好，请问最近考虑换工作吗？")
	conversation = _conversation("404", [("incoming", _RICH), ("outgoing", old_template)])

	decision = decide_action(conversation, config, {"questionnaire_sent_at": _QUESTIONNAIRE_TS})

	assert decision.action == PlatformAction.SKIP


def test_outgoing_only_transcript_without_reply_waits() -> None:
	conversation = _conversation("405", [("outgoing", _REWRITTEN_QUESTIONNAIRE)])

	decision = decide_action(
		conversation, AutomationConfig(), {"questionnaire_sent_at": _QUESTIONNAIRE_TS}
	)

	assert decision.action == PlatformAction.SKIP


def test_no_direction_transcript_without_outgoing_waits() -> None:
	conversation = Conversation(
		title="406",
		incoming_messages=(_RICH,),
		all_messages=(_RICH,),
		fingerprint=ConversationFingerprint("406"),
	)

	decision = decide_action(
		conversation, AutomationConfig(), {"questionnaire_sent_at": _QUESTIONNAIRE_TS}
	)

	# 没有我方消息锚点时不再用 prior 兜底断言「已回复」：incoming 至少含触发问卷的
	# 旧消息，据此自动追问等于让历史消息成为写动作的依据（Issue #2229）。
	assert decision.action == PlatformAction.SKIP
	assert "waiting for candidate reply after questionnaire" in decision.reason


def test_direction_without_any_outgoing_waits() -> None:
	# 转录带方向但完全没有我方消息：无法证明候选人的消息在我方话术之后，
	# 保守按未回复处理，等待方向信息恢复或人工核对（Issue #2229）。
	conversation = _conversation("407", [("incoming", _RICH)])

	decision = decide_action(
		conversation, AutomationConfig(), {"questionnaire_sent_at": _QUESTIONNAIRE_TS}
	)

	assert decision.action == PlatformAction.SKIP
	assert "waiting for candidate reply after questionnaire" in decision.reason
