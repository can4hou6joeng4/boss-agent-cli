"""批次 113：预检拒绝不产生「可能已发出」证据（Issue #2451）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.execution import execute_or_dry_run
from boss_agent_cli.automation.mock_adapter import MockRecruiterAutomationPlatform
from boss_agent_cli.automation.models import (
	ActionResult,
	Conversation,
	ConversationFingerprint,
	ConversationRef,
	Decision,
	PlatformAction,
)
from boss_agent_cli.automation.runner import run_automation_cycle
from boss_agent_cli.automation.storage import AutomationControlBusy, AutomationControlBusyDispatched, AutomationStore

CANDIDATE = "候选人A"
INCOMING = "你好，我在上海做过3年销售，擅长客户沟通，大专，期望10-12k，想看机会"


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
	from boss_agent_cli.automation.models import PendingAction

	store.append_pending(
		PendingAction(
			id="approved-1",
			ts="2026-11-20T00:00:00+00:00",
			platform="zhipin",
			candidate_key=CANDIDATE,
			action=PlatformAction.SEND_QUESTIONNAIRE.value,
			status="pending",
			confidence=0.9,
			reason="approved review",
			message="APPROVED-MESSAGE",
		)
	)


def _seed_state(store: AutomationStore) -> None:
	(store.root / "state.json").write_text(
		json.dumps({"conversations": {}, "autonomy": {}, "safety": {}}, ensure_ascii=False),
		encoding="utf-8",
	)


def _inflight(store: AutomationStore) -> Any:
	state = json.loads((store.root / "state.json").read_text(encoding="utf-8"))
	return state.get("conversations", {}).get(CANDIDATE, {}).get("inflight_action")


def _busy_write(store: AutomationStore, monkeypatch: pytest.MonkeyPatch) -> None:
	def _raise(state: dict[str, Any]) -> None:
		raise AutomationControlBusy("control lock busy")

	monkeypatch.setattr(store, "write_state", _raise)


# ── 队列路径：预检拒绝后本地写失败不升级 ────────────────────────────


def test_queue_precheck_rejection_does_not_claim_dispatch(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	store = AutomationStore(tmp_path)
	_seed_pending(store)
	_seed_state(store)
	adapter = _Adapter()
	_busy_write(store, monkeypatch)

	with pytest.raises(AutomationControlBusy) as excinfo:
		run_automation_cycle(
			adapter,
			store,
			AutomationConfig(max_actions_per_run=0),
			platform="zhipin",
			dry_run=False,
		)

	assert not isinstance(excinfo.value, AutomationControlBusyDispatched)
	assert adapter.sent == []
	# 预检拒绝没有落 executing：行保持 pending，下一轮可安全重试。
	assert [item.status for item in store.read_pending()] == ["pending"]
	assert _inflight(store) is None


# ── 扫描路径：预检拒绝后 checkpoint 撞锁不升级 ──────────────────────


def test_scan_precheck_rejection_does_not_claim_dispatch(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	store = AutomationStore(tmp_path)
	_seed_state(store)
	adapter = _Adapter()
	_busy_write(store, monkeypatch)

	with pytest.raises(AutomationControlBusy) as excinfo:
		run_automation_cycle(
			adapter,
			store,
			AutomationConfig(max_actions_per_run=0),
			platform="zhipin",
			dry_run=False,
		)

	assert not isinstance(excinfo.value, AutomationControlBusyDispatched)
	assert adapter.sent == []
	assert _inflight(store) is None


# ── 正常派发：executing 行与在途标记先于适配器调用落盘 ──────────────


class _InspectingAdapter(_Adapter):
	def __init__(self, store: AutomationStore) -> None:
		super().__init__()
		self._store = store
		self.pending_status_at_dispatch: list[str] = []
		self.inflight_at_dispatch: list[Any] = []

	def execute_action(
		self,
		action: PlatformAction,
		message: str,
		ref: ConversationRef,
	) -> ActionResult:
		rows = [item.status for item in self._store.read_pending()]
		self.pending_status_at_dispatch.append(rows[0] if rows else None)
		self.inflight_at_dispatch.append(_inflight(self._store))
		return super().execute_action(action, message, ref)


def test_queue_dispatch_marks_executing_before_adapter(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	_seed_pending(store)
	_seed_state(store)
	adapter = _InspectingAdapter(store)

	run_automation_cycle(adapter, store, AutomationConfig(), platform="zhipin", dry_run=False)

	assert len(adapter.sent) == 1
	assert adapter.pending_status_at_dispatch == ["executing"]
	assert adapter.inflight_at_dispatch[0] is not None


def test_scan_dispatch_marks_inflight_before_adapter(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	_seed_state(store)
	adapter = _InspectingAdapter(store)

	run_automation_cycle(adapter, store, AutomationConfig(), platform="zhipin", dry_run=False)

	assert len(adapter.sent) == 1
	assert adapter.inflight_at_dispatch[0] is not None


# ── 预检拒绝返回停止状态，不调用适配器 ─────────────────────────────


def test_execute_or_dry_run_skips_callback_when_precheck_rejects() -> None:
	class _Guard:
		def stop_reason(self) -> str:
			return "stop requested"

		def before_action(self, decision: Decision, warning: str = "") -> Any:
			raise AssertionError("stop_reason 已拒绝时不应继续预检")

	called: list[bool] = []

	def _callback() -> None:
		called.append(True)

	decision = Decision(
		action=PlatformAction.SEND_QUESTIONNAIRE,
		confidence=0.9,
		reason="test",
		candidate_key=None,
		message="hi",
	)
	status, _reason = execute_or_dry_run(
		_Adapter(), _Guard(), decision, ConversationRef(id=CANDIDATE, tab="pending"), False,
		before_dispatch=_callback,
	)

	assert status.value == "CIRCUIT_BREAKER_OPEN"
	assert called == []


def test_execute_or_dry_run_skips_callback_in_dry_run(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	_seed_state(store)
	adapter = _Adapter()
	called: list[bool] = []

	def _callback() -> None:
		called.append(True)

	status, _reason = execute_or_dry_run(
		adapter,
		_guard_for(store),
		Decision(
			action=PlatformAction.SEND_QUESTIONNAIRE,
			confidence=0.9,
			reason="test",
			candidate_key=None,
			message="hi",
		),
		ConversationRef(id=CANDIDATE, tab="pending"),
		True,
		before_dispatch=_callback,
	)

	assert status.value == "DRY_RUN"
	assert called == []
	assert adapter.sent == []


def _guard_for(store: AutomationStore) -> Any:
	from boss_agent_cli.automation.safety import SafetyGuard

	return SafetyGuard(AutomationConfig(), store.read_state(), dry_run=True, store=store)
