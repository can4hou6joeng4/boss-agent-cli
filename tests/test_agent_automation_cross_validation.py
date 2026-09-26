"""Cross-platform recruiter automation validation tests."""

from __future__ import annotations

from pathlib import Path

from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.mock_adapter import MockRecruiterAutomationPlatform
from boss_agent_cli.automation.models import (
	ActionResult,
	ConversationRef,
	EventStatus,
	PlatformAction,
	ReviewItem,
)
from boss_agent_cli.automation.runner import run_automation_cycle
from boss_agent_cli.automation.storage import AutomationStore


class FailingMockRecruiterAutomationPlatform(MockRecruiterAutomationPlatform):
	def execute_action(
		self,
		action: PlatformAction,
		message: str,
		ref: ConversationRef,
	) -> ActionResult:
		self.executed.append((action, ref.id))
		return ActionResult("blocked", {"reason": "platform verification required"})


def test_pending_actions_are_isolated_by_platform(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	legacy_review = "legacy-platform-legacy-pending-send_follow_up"
	zhipin_review = "zhipin-zhipin-pending-send_follow_up"
	for platform, review_id, candidate_key in [
		("legacy-platform", legacy_review, "legacy-pending"),
		("zhipin", zhipin_review, "zhipin-pending"),
	]:
		store.append_review(
			ReviewItem(
				id=review_id,
				ts="ts",
				platform=platform,
				candidate_key=candidate_key,
				action=PlatformAction.SEND_FOLLOW_UP.value,
				status="review",
				confidence=0.9,
				reason="approved follow-up",
				message="继续沟通",
			)
		)
		store.approve_review(review_id, "approved-ts")
	adapter = MockRecruiterAutomationPlatform("zhipin", [])

	report = run_automation_cycle(
		adapter,
		store,
		AutomationConfig(),
		platform="zhipin",
		dry_run=True,
	)

	assert report.events[0].candidate_key == "zhipin-pending"
	assert "legacy-pending" not in {event.candidate_key for event in report.events}
	statuses = {item.candidate_key: item.status for item in store.read_pending()}
	assert statuses["legacy-pending"] == "pending"
	assert statuses["zhipin-pending"] == "dry-run"


def test_pending_action_stays_pending_when_execution_is_blocked(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	review_id = "zhipin-blocked-pending-send_follow_up"
	store.append_review(
		ReviewItem(
			id=review_id,
			ts="ts",
			platform="zhipin",
			candidate_key="blocked-pending",
			action=PlatformAction.SEND_FOLLOW_UP.value,
			status="review",
			confidence=0.9,
			reason="approved follow-up",
			message="继续沟通",
		)
	)
	store.approve_review(review_id, "approved-ts")
	adapter = FailingMockRecruiterAutomationPlatform("zhipin", [])

	report = run_automation_cycle(
		adapter,
		store,
		AutomationConfig(),
		platform="zhipin",
		dry_run=False,
	)

	assert report.events[0].status == EventStatus.STOPPED_BY_SAFETY.value
	pending = store.read_pending()[0]
	assert pending.status == "pending"
	assert pending.updated_at
