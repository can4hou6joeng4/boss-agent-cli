"""automation 队列安全门控、--limit 全局上限与 status 缺失（Issue #2020–#2022）。"""

from __future__ import annotations

import json

import pytest

from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.models import (
	ActionResult,
	Conversation,
	ConversationRef,
	PendingAction,
	PlatformAction,
)
from boss_agent_cli.automation.runner import run_automation_cycle
from boss_agent_cli.automation.storage import AutomationStateError, AutomationStore


class _Adapter:
	name = "zhipin"

	def __init__(self, refs: list[ConversationRef] | None = None) -> None:
		self.sent: list[str] = []
		self.refs = refs or []

	def ensure_session(self) -> None:
		pass

	def detect_safety_warning(self) -> str | None:
		return None

	def scan_conversations(self, tabs: list[str], max_per_tab: int) -> list[ConversationRef]:
		return list(self.refs)

	def read_conversation(self, ref: ConversationRef) -> Conversation:
		return Conversation(title=f"候选人{ref.id}")

	def execute_action(self, action, message, ref) -> ActionResult:
		self.sent.append(message)
		return ActionResult(status="executed", details={})


def _seed(
	store: AutomationStore,
	*,
	confidence: float = 0.9,
	action: str = PlatformAction.SEND_QUESTIONNAIRE.value,
) -> None:
	store.append_pending(
		PendingAction(
			id="approved-1",
			ts="2026-11-16T00:00:00+00:00",
			platform="zhipin",
			candidate_key="候选人A",
			action=action,
			status="pending",
			confidence=confidence,
			reason="approved review",
			message="您好，想确认下近期是否看机会？",
		)
	)


# ── #2020：队列路径同样受 allowed_actions 与置信度阈值约束 ───────────


def test_empty_allowed_actions_skips_pending_queue_rows(tmp_path) -> None:
	store = AutomationStore(tmp_path)
	_seed(store)
	adapter = _Adapter()

	report = run_automation_cycle(
		adapter,
		store,
		AutomationConfig(allowed_actions=()),
		platform="zhipin",
		dry_run=False,
	)

	assert adapter.sent == [], "空白名单不得派发写动作"
	assert [item.status for item in store.read_pending()] == ["pending"], "跳过的行保持可执行"
	assert any(event.status == "SKIPPED" for event in report.events), report.events


def test_low_confidence_pending_queue_rows_are_skipped(tmp_path) -> None:
	store = AutomationStore(tmp_path)
	_seed(store, confidence=0.0)
	adapter = _Adapter()

	report = run_automation_cycle(adapter, store, AutomationConfig(), platform="zhipin", dry_run=False)

	assert adapter.sent == [], "低于阈值的队列行不得派发"
	assert any(event.status == "SKIPPED" for event in report.events), report.events


def test_allowed_action_still_dispatches_from_queue(tmp_path) -> None:
	"""护栏：配置允许的动作照常从队列派发。"""
	store = AutomationStore(tmp_path)
	_seed(store)
	adapter = _Adapter()

	run_automation_cycle(adapter, store, AutomationConfig(), platform="zhipin", dry_run=False)

	assert adapter.sent == ["您好，想确认下近期是否看机会？"]


# ── #2021：--limit 是全局会话上限，不按 tab 放大 ────────────────────


def test_limit_caps_total_conversations_across_tabs(tmp_path) -> None:
	store = AutomationStore(tmp_path)
	refs = [
		ConversationRef(id="1", tab="新招呼"),
		ConversationRef(id="101", tab="未读"),
		ConversationRef(id="2", tab="新招呼"),
		ConversationRef(id="102", tab="未读"),
	]
	adapter = _Adapter(refs)

	report = run_automation_cycle(
		adapter, store, AutomationConfig(), platform="zhipin", dry_run=True, limit=1
	)

	assert len(report.events) == 1, f"--limit 1 只能处理一个会话，实际 {len(report.events)}"


def test_no_limit_processes_all_scanned_conversations(tmp_path) -> None:
	store = AutomationStore(tmp_path)
	refs = [ConversationRef(id="1", tab="新招呼"), ConversationRef(id="101", tab="未读")]
	adapter = _Adapter(refs)

	report = run_automation_cycle(adapter, store, AutomationConfig(), platform="zhipin", dry_run=True)

	assert len(report.events) == 2


# ── #2022：缺 status 字段按状态损坏拒绝 ─────────────────────────────


def test_pending_row_without_status_is_rejected(tmp_path) -> None:
	store = AutomationStore(tmp_path)
	_seed(store)
	path = store.pending_path
	rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
	rows[0].pop("status")
	path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")

	with pytest.raises(AutomationStateError):
		store.read_pending()
