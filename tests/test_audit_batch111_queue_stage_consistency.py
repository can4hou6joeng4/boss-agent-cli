"""批次 111：队列阶段判定与决策路径共用 ISO 校验（Issue #2450）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.decision import prior_sent
from boss_agent_cli.automation.execution import process_pending
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
from boss_agent_cli.automation.safety import SafetyGuard
from boss_agent_cli.automation.storage import AutomationStore

CANDIDATE = "候选人A"
INCOMING = "你好，我在上海做过3年销售，擅长客户沟通，大专，期望10-12k，想看机会"
APPROVED_MESSAGE = "APPROVED-MESSAGE"
ISO_TS = "2026-11-20T00:00:00+00:00"


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


def _seed_pending(store: AutomationStore) -> None:
	store.append_pending(
		PendingAction(
			id="approved-1",
			ts=ISO_TS,
			platform="zhipin",
			candidate_key=CANDIDATE,
			action=PlatformAction.SEND_QUESTIONNAIRE.value,
			status="pending",
			confidence=0.9,
			reason="approved review",
			message=APPROVED_MESSAGE,
		)
	)


def _seed_state(store: AutomationStore, questionnaire_sent_at: Any) -> None:
	(store.root / "state.json").write_text(
		json.dumps(
			{
				"conversations": {CANDIDATE: {"questionnaire_sent_at": questionnaire_sent_at}},
				"autonomy": {},
				"safety": {},
			},
			ensure_ascii=False,
		),
		encoding="utf-8",
	)


def _run(store: AutomationStore, adapter: _Adapter, *, dry_run: bool = False):
	return run_automation_cycle(
		adapter,
		store,
		AutomationConfig(),
		platform="zhipin",
		dry_run=dry_run,
	)


# ── 共享判定：只认可解析的 ISO 时间戳 ───────────────────────────────


@pytest.mark.parametrize(
	"value",
	["false", "yesterday", "1", 1, True, {}, [], "  ", "2026-13-40T00:00:00+00:00"],
)
def test_prior_sent_rejects_drifted_values(value: object) -> None:
	assert prior_sent({"questionnaire_sent_at": value}, "questionnaire_sent_at") is False


@pytest.mark.parametrize("value", [ISO_TS, " 2026-11-20T00:00:00+00:00 "])
def test_prior_sent_accepts_iso_timestamps(value: str) -> None:
	assert prior_sent({"questionnaire_sent_at": value}, "questionnaire_sent_at") is True


def test_prior_sent_missing_key_is_false() -> None:
	assert prior_sent({}, "questionnaire_sent_at") is False


# ── 队列路径：漂移值按缺失处理，批准消息不被丢弃 ───────────────────


def test_drifted_stage_value_keeps_approved_message(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	_seed_pending(store)
	_seed_state(store, "false")
	adapter = _Adapter()

	report = _run(store, adapter)

	# 漂移值不能证明问卷已发：队列行照常派发，且发的是操作者批准的消息，
	# 不是同轮扫描路径的模板话术（Issue #2450）。
	assert adapter.sent == [(PlatformAction.SEND_QUESTIONNAIRE, APPROVED_MESSAGE, CANDIDATE)]
	assert [item.status for item in store.read_pending()] == ["executed"]
	assert any(event.status == EventStatus.AUTO_EXECUTED.value for event in report.events)
	template = AutomationConfig().questionnaire_message
	assert all(template not in event.reason for event in report.events)


def test_drifted_stage_value_dry_run_keeps_plan(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	_seed_pending(store)
	_seed_state(store, "false")
	adapter = _Adapter()

	report = _run(store, adapter, dry_run=True)

	assert adapter.sent == []
	assert [item.status for item in store.read_pending()] == ["pending"]
	assert [event.status for event in report.events] == [EventStatus.DRY_RUN.value]


# ── 合法 ISO：队列行标记已执行，扫描路径不再补位 ────────────────────


def test_valid_iso_stage_value_skips_queue_row(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	_seed_pending(store)
	_seed_state(store, ISO_TS)
	adapter = _Adapter()

	report = _run(store, adapter)

	assert adapter.sent == []
	assert [item.status for item in store.read_pending()] == ["executed"]
	assert report.events[0].status == EventStatus.SKIPPED.value


def test_valid_iso_stage_value_dry_run_does_not_mutate(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	_seed_pending(store)
	_seed_state(store, ISO_TS)
	adapter = _Adapter()

	report = _run(store, adapter, dry_run=True)

	assert adapter.sent == []
	assert [item.status for item in store.read_pending()] == ["pending"]
	assert report.events[0].status == EventStatus.DRY_RUN.value


def test_stage_skip_claims_candidate_from_scan_path(tmp_path: Path) -> None:
	"""队列把行标成 executed 时，该候选人必须进入本轮已派发集合（Issue #2450）。"""
	store = AutomationStore(tmp_path)
	_seed_pending(store)
	_seed_state(store, ISO_TS)
	adapter = _Adapter()
	state = store.read_state()
	guard = SafetyGuard(AutomationConfig(), state, dry_run=False, store=store)

	events, dispatched = process_pending(adapter, store, guard, "zhipin", False, state=state)

	assert adapter.sent == []
	assert [event.status for event in events] == [EventStatus.SKIPPED.value]
	assert CANDIDATE in dispatched
