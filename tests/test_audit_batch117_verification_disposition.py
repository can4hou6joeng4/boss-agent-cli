"""人工核对在途动作后的处置协议（Issue #2452）。

`verification-required` 的队列行是操作者批准过的动作，在人工处置完成前必须
继续认领该候选人：只按旧指引删掉 `inflight_action` 标记，会让扫描路径把批准
文案当成从未发出，用模板问卷补位。

恢复指引同时区分两种结果：

- 确认已发出：先写该会话的阶段时间（`questionnaire_sent_at` /
  `follow_up_sent_at` / `exchange_contact_at`）并把队列行标为 `executed`，
  最后删除标记；删标记先于写事实的窗口里，扫描路径仍不能补位；
- 确认未发出：把队列行恢复 `pending`，再删除标记，队列重新派发批准文案。
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.execution import InflightAction, _inflight_reason
from boss_agent_cli.automation.mock_adapter import MockRecruiterAutomationPlatform
from boss_agent_cli.automation.models import (
	ActionResult,
	Conversation,
	ConversationFingerprint,
	ConversationRef,
	EventStatus,
	PendingAction,
	PlatformAction,
)
from boss_agent_cli.automation.runner import run_automation_cycle
from boss_agent_cli.automation.storage import AutomationStore

CANDIDATE = "候选人A"
INCOMING = "你好，我在上海做过3年销售，擅长客户沟通，大专，期望10-12k，想看机会"
APPROVED_MESSAGE = "您好，方便发一份简历吗？我们这边有匹配的岗位。"


def _conversation() -> Conversation:
	return Conversation(
		title=CANDIDATE,
		incoming_messages=(INCOMING,),
		outgoing_messages=(),
		ordered_messages=(("incoming", INCOMING),),
		all_messages=(INCOMING,),
		fingerprint=ConversationFingerprint(CANDIDATE),
	)


class _Adapter(MockRecruiterAutomationPlatform):
	def __init__(self) -> None:
		super().__init__("zhipin", [_conversation()])
		self.sent: list[tuple[PlatformAction, str, str]] = []

	def execute_action(
		self,
		action: PlatformAction,
		message: str,
		ref: ConversationRef,
	) -> ActionResult:
		self.sent.append((action, message, ref.id))
		return ActionResult(status="executed", details={})


def _cycle(adapter: _Adapter, store: AutomationStore):
	return run_automation_cycle(
		adapter,
		store,
		AutomationConfig(),
		platform="zhipin",
		dry_run=False,
		limit=1,
	)


def _seed_row(store: AutomationStore, status: str, row_id: str = "approved-1") -> None:
	store.append_pending(
		PendingAction(
			id=row_id,
			ts="2026-11-22T00:00:00+00:00",
			platform="zhipin",
			candidate_key=CANDIDATE,
			action=PlatformAction.SEND_QUESTIONNAIRE.value,
			status=status,
			confidence=0.9,
			reason="approved review",
			message=APPROVED_MESSAGE,
		)
	)


def _seed_state(store: AutomationStore, prior: dict[str, Any]) -> None:
	state = {"conversations": {CANDIDATE: prior}, "autonomy": {}, "safety": {}}
	(store.root / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _inflight(status: str, action: PlatformAction) -> InflightAction:
	return InflightAction(
		status=status,
		action=action,
		started_at="2026-11-22T00:00:00+00:00",
		conversation_ref=CANDIDATE,
		message_hash="sha256:test",
	)


def test_verification_required_row_claims_candidate_after_marker_removed(tmp_path: Path) -> None:
	"""#2452 复现：按旧指引删掉标记后，队列行仍挡住扫描路径的模板补位。"""
	store = AutomationStore(tmp_path)
	_seed_row(store, "verification-required")
	_seed_state(store, {})
	adapter = _Adapter()

	report = _cycle(adapter, store)

	assert adapter.sent == []
	assert [event.candidate_key for event in report.events] == []
	assert [row.status for row in store.read_pending()] == ["verification-required"]


def test_verification_required_row_blocks_earlier_pending_action_for_same_candidate(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	_seed_row(store, "pending", "earlier")
	_seed_row(store, "verification-required")
	_seed_state(store, {})
	adapter = _Adapter()

	_cycle(adapter, store)

	assert adapter.sent == []
	assert [row.status for row in store.read_pending()] == ["pending", "verification-required"]


def test_confirmed_sent_disposition_prevents_template_after_row_is_settled(tmp_path: Path) -> None:
	"""确认已发出：阶段时间 + executed 之后，扫描路径等待回复而不是补发模板。"""
	store = AutomationStore(tmp_path)
	_seed_row(store, "executed")
	_seed_state(store, {"questionnaire_sent_at": datetime.now(timezone.utc).isoformat()})
	adapter = _Adapter()

	report = _cycle(adapter, store)

	assert adapter.sent == []
	assert [event.status for event in report.events] == [EventStatus.SKIPPED.value]
	assert "waiting for candidate reply" in report.events[0].reason


def test_confirmed_unsent_disposition_redelivers_the_approved_message(tmp_path: Path) -> None:
	"""确认未发出：队列行恢复 pending、标记删除后，队列重新派发批准文案。"""
	store = AutomationStore(tmp_path)
	_seed_row(store, "pending")
	_seed_state(store, {})
	adapter = _Adapter()

	_cycle(adapter, store)

	assert [(action, message) for action, message, _ in adapter.sent] == [
		(PlatformAction.SEND_QUESTIONNAIRE, APPROVED_MESSAGE)
	]


def test_recovery_guidance_separates_sent_and_unsent_disposition() -> None:
	reason = _inflight_reason(CANDIDATE, _inflight("verification-required", PlatformAction.SEND_QUESTIONNAIRE))

	assert "questionnaire_sent_at" in reason
	assert "确认已发出" in reason and "确认未发出" in reason
	assert "executed" in reason and "pending" in reason
	# 写事实必须先于删标记：反过来会重新打开模板补位的窗口。
	assert reason.index("questionnaire_sent_at") < reason.index("最后再删除该标记")
	assert "inflight_action" in reason


def test_recovery_guidance_covers_every_stage_action() -> None:
	for action, stage_key in (
		(PlatformAction.SEND_QUESTIONNAIRE, "questionnaire_sent_at"),
		(PlatformAction.SEND_FOLLOW_UP, "follow_up_sent_at"),
		(PlatformAction.EXCHANGE_CONTACT, "exchange_contact_at"),
	):
		assert stage_key in _inflight_reason(CANDIDATE, _inflight("verification-required", action))


def test_recovery_guidance_for_an_unknown_stage_requires_identifying_the_action() -> None:
	reason = _inflight_reason(CANDIDATE, _inflight("executing", PlatformAction.SKIP))

	assert "先核对动作类型和结果" in reason
	assert "pending" not in reason
	assert "questionnaire_sent_at" not in reason
