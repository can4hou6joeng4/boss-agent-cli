"""同源在途行排在 pending 之后时不得重复派发（Issue #2586）。"""

from pathlib import Path

import pytest

from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.models import ActionResult, EventStatus, PendingAction, PlatformAction
from boss_agent_cli.automation.runner import run_automation_cycle
from boss_agent_cli.automation.storage import AutomationStore


class _Adapter:
	name = "zhipin"

	def __init__(self) -> None:
		self.sent: list[str] = []

	def ensure_session(self) -> None:
		pass

	def detect_safety_warning(self) -> str | None:
		return None

	def scan_conversations(self, tabs: list[str], max_per_tab: int) -> list:
		return []

	def execute_action(self, action, message, ref) -> ActionResult:
		self.sent.append(message)
		return ActionResult(status="executed", details={})


def _row(rid: str, candidate: str, status: str = "pending") -> PendingAction:
	return PendingAction(
		id=rid,
		ts="2026-11-23T00:00:00+00:00",
		platform="zhipin",
		candidate_key=candidate,
		action=PlatformAction.SEND_QUESTIONNAIRE.value,
		status=status,
		confidence=0.9,
		reason="approved",
		message=f"MESSAGE-{rid}",
	)


def _statuses(store: AutomationStore) -> list[tuple[str, str]]:
	return [(item.id, item.status) for item in store.read_pending()]


@pytest.mark.parametrize("order", ["pending-first", "executing-first"])
def test_same_candidate_pending_stays_when_executing_row_exists(tmp_path: Path, order: str) -> None:
	store = AutomationStore(tmp_path)
	pending = _row("first-pending", "C")
	executing = _row("second-executing", "C", "executing")
	if order == "pending-first":
		store.append_pending(pending)
		store.append_pending(executing)
	else:
		store.append_pending(executing)
		store.append_pending(pending)
	adapter = _Adapter()

	report = run_automation_cycle(adapter, store, AutomationConfig(), platform="zhipin", dry_run=False)

	assert adapter.sent == []
	assert ("first-pending", "pending") in _statuses(store)
	assert ("second-executing", "verification-required") in _statuses(store)
	assert [event.status for event in report.events] == [EventStatus.PLATFORM_VERIFICATION_REQUIRED.value]
	assert "was interrupted while executing" in report.events[0].reason


def test_other_candidate_is_not_blocked_by_same_candidate_inflight_row(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	store.append_pending(_row("claimed", "C"))
	store.append_pending(_row("other", "D"))
	store.append_pending(_row("unknown", "C", "executing"))
	adapter = _Adapter()

	run_automation_cycle(adapter, store, AutomationConfig(), platform="zhipin", dry_run=False)

	assert adapter.sent == ["MESSAGE-other"]
	assert _statuses(store) == [
		("claimed", "pending"),
		("other", "executed"),
		("unknown", "verification-required"),
	]


def test_verification_required_row_still_blocks_same_candidate_pending(tmp_path: Path) -> None:
	"""原有 verification-required 的认领语义保持不变。"""
	store = AutomationStore(tmp_path)
	store.append_pending(_row("claimed", "C", "verification-required"))
	store.append_pending(_row("newer", "C"))
	adapter = _Adapter()

	run_automation_cycle(adapter, store, AutomationConfig(), platform="zhipin", dry_run=False)

	assert adapter.sent == []
	assert _statuses(store) == [("claimed", "verification-required"), ("newer", "pending")]
