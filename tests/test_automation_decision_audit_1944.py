"""automation 决策与预算停止的边界（Issue #1944–#1947）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from boss_agent_cli.api.request_budget import RequestBudgetExceeded
from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.decision import _extract_interview_time, decide_action
from boss_agent_cli.automation.execution import process_pending
from boss_agent_cli.automation.mock_adapter import MockRecruiterAutomationPlatform
from boss_agent_cli.automation.models import (
	Conversation,
	ConversationFingerprint,
	PendingAction,
	PlatformAction,
)
from boss_agent_cli.automation.safety import SafetyGuard
from boss_agent_cli.automation.storage import AutomationStore

_RICH = "我在上海做过3年销售，本科，客服运营客户沟通面谈都可以，刚刚看到，想看机会，可面试"


def _conversation(title: str, ordered: list[tuple[str, str]]) -> Conversation:
	return Conversation(
		title=title,
		incoming_messages=tuple(text for direction, text in ordered if direction == "incoming"),
		outgoing_messages=tuple(text for direction, text in ordered if direction == "outgoing"),
		ordered_messages=tuple(ordered),
		all_messages=tuple(text for _, text in ordered),
		fingerprint=ConversationFingerprint(title),
	)


# ── Issue #1944：转录证明未回复时不得自动追问 ───────────────────────


def test_no_follow_up_when_transcript_shows_no_reply() -> None:
	config = AutomationConfig()
	conversation = _conversation(
		"201", [("incoming", _RICH), ("outgoing", config.questionnaire_message)]
	)

	decision = decide_action(
		conversation, config, {"questionnaire_sent_at": "2026-06-01T00:00:00+00:00"}
	)

	assert decision.action not in {PlatformAction.SEND_FOLLOW_UP, PlatformAction.EXCHANGE_CONTACT}


def test_no_exchange_when_transcript_shows_no_reply_after_follow_up() -> None:
	config = AutomationConfig()
	conversation = _conversation(
		"202",
		[
			("incoming", _RICH),
			("outgoing", config.questionnaire_message),
			("outgoing", config.follow_up_message),
		],
	)
	prior = {
		"questionnaire_sent_at": "2026-06-01T00:00:00+00:00",
		"follow_up_sent_at": "2026-06-02T00:00:00+00:00",
	}

	decision = decide_action(conversation, config, prior)

	assert decision.action != PlatformAction.EXCHANGE_CONTACT


def test_follow_up_still_sent_when_candidate_replied() -> None:
	config = AutomationConfig()
	conversation = _conversation(
		"203",
		[("incoming", _RICH), ("outgoing", config.questionnaire_message), ("incoming", _RICH)],
	)

	decision = decide_action(
		conversation, config, {"questionnaire_sent_at": "2026-06-01T00:00:00+00:00"}
	)

	assert decision.action == PlatformAction.SEND_FOLLOW_UP


# ── Issue #1945：升级检测只看候选人一侧 ─────────────────────────────


def test_own_outgoing_wording_is_not_candidate_escalation() -> None:
	conversation = _conversation(
		"302",
		[
			("incoming", "您好，我在上海做过3年销售，本科，想看机会"),
			("outgoing", "我们这边不收取任何费用，也会保护您的隐私。"),
		],
	)

	decision = decide_action(conversation, AutomationConfig(), {})

	assert "candidate-escalation" not in decision.risk_flags


def test_candidate_escalation_still_detected() -> None:
	conversation = _conversation("303", [("incoming", "不要再联系我了，再发我就投诉")])

	decision = decide_action(conversation, AutomationConfig(), {})

	assert decision.action == PlatformAction.QUEUE_REVIEW
	assert "candidate-escalation" in decision.risk_flags


def test_escalation_falls_back_to_all_messages_without_direction() -> None:
	conversation = Conversation(
		title="304", all_messages=("我要举报",), fingerprint=ConversationFingerprint("304")
	)

	decision = decide_action(conversation, AutomationConfig(), {})

	assert "candidate-escalation" in decision.risk_flags


# ── Issue #1946：越界时刻不生成面试时间 ─────────────────────────────


@pytest.mark.parametrize("text", ["明天25点99面试", "明天下午3点99", "周二 99:99 面试", "明天24点"])
def test_out_of_range_interview_time_is_rejected(text: str) -> None:
	assert _extract_interview_time(text) == ""


@pytest.mark.parametrize(
	("text", "expected"),
	[
		("明天下午3点30面试", "明天下午3:30"),
		("明天下午3点半", "明天下午3:30"),
		("周二 14:00 面试", "周二 14:00"),
		("明天10点", "明天10:00"),
		("后天23点59", "后天23:59"),
	],
)
def test_valid_interview_time_is_kept(text: str, expected: str) -> None:
	assert _extract_interview_time(text) == expected


# ── Issue #1947：还原写盘失败不得掩盖预算停止 ───────────────────────


def test_budget_stop_survives_pending_restore_failure(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	store.append_pending(
		PendingAction(
			id="p1",
			ts="t",
			platform="zhipin",
			candidate_key="101",
			action="send_questionnaire",
			status="pending",
			confidence=0.9,
			reason="approved",
			message="m",
		)
	)
	state = store.read_state()
	store.write_state(state)
	guard = SafetyGuard(AutomationConfig(), state, dry_run=False, store=store)

	class BudgetAdapter(MockRecruiterAutomationPlatform):
		def execute_action(self, action, message, ref):  # type: ignore[no-untyped-def]
			store.pending_path.unlink()
			store.pending_path.mkdir()
			raise RequestBudgetExceeded("工作流已超过截止时间，请求未发出", code="WORKFLOW_TIMEOUT")

	adapter = BudgetAdapter("zhipin", [Conversation(title="101")])

	with pytest.raises(RequestBudgetExceeded):
		process_pending(adapter, store, guard, "zhipin", False, state=state, dispatched=[False])
