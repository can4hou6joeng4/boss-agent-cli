"""Cross-platform recruiter automation validation tests."""

from __future__ import annotations

from pathlib import Path

from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.events import stable_action_id
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
from boss_agent_cli.automation.zhilian_adapter import ZhilianRecruiterAutomationPlatform


class FailingMockRecruiterAutomationPlatform(MockRecruiterAutomationPlatform):
	def execute_action(
		self,
		action: PlatformAction,
		message: str,
		ref: ConversationRef,
	) -> ActionResult:
		self.executed.append((action, ref.id))
		return ActionResult("blocked", {"reason": "platform verification required"})


class FakeZhilianClient:
	def __init__(self) -> None:
		self.reads: list[str] = []
		self.sent: list[tuple[str, str]] = []
		self.exchanges: list[str] = []

	def user_info(self) -> dict:
		return {"code": 200, "data": {"name": "recruiter"}}

	def recruiter_conversations(self, tabs: list[str], max_per_tab: int) -> dict:
		return {
			"code": 200,
			"data": {
				"items": [
					{"id": "zl-101", "candidateName": "智联高分"},
				][:max_per_tab]
			},
		}

	def recruiter_conversation(self, ref_id: str) -> dict:
		self.reads.append(ref_id)
		return {
			"code": 200,
			"data": {
				"items": [
					{
						"from": "candidate",
						"content": "你好，我在上海做过3年销售，擅长客户沟通，大专，想看机会",
					}
				]
			},
		}

	def send_recruiter_message(self, ref_id: str, message: str) -> dict:
		self.sent.append((ref_id, message))
		return {"code": 200, "data": {"id": ref_id}}

	def exchange_recruiter_contact(self, ref_id: str) -> dict:
		self.exchanges.append(ref_id)
		return {"code": 200, "data": {"id": ref_id}}


class FakeZhilianClientWithoutWriteSelectors(FakeZhilianClient):
	send_recruiter_message = None
	exchange_recruiter_contact = None


class RaisingZhilianClient(FakeZhilianClient):
	def send_recruiter_message(self, ref_id: str, message: str) -> dict:
		raise RuntimeError("input is hidden")


def test_pending_actions_are_isolated_by_platform(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	zhilian_review = stable_action_id("zhilian", "zhilian-pending", PlatformAction.SEND_FOLLOW_UP, "ts")
	zhipin_review = stable_action_id("zhipin", "zhipin-pending", PlatformAction.SEND_FOLLOW_UP, "ts")
	for platform, review_id, candidate_key in [
		("zhilian", zhilian_review, "zhilian-pending"),
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
	assert "zhilian-pending" not in {event.candidate_key for event in report.events}
	statuses = {item.candidate_key: item.status for item in store.read_pending()}
	assert statuses["zhilian-pending"] == "pending"
	assert statuses["zhipin-pending"] == "dry-run"


def test_pending_action_stays_pending_when_execution_is_blocked(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	review_id = stable_action_id("zhipin", "blocked-pending", PlatformAction.SEND_FOLLOW_UP, "ts")
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


def test_pending_action_stays_pending_when_zhilian_write_raises(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	review_id = stable_action_id("zhilian", "zl-raises", PlatformAction.SEND_QUESTIONNAIRE, "ts")
	store.append_review(
		ReviewItem(
			id=review_id,
			ts="ts",
			platform="zhilian",
			candidate_key="zl-raises",
			action=PlatformAction.SEND_QUESTIONNAIRE.value,
			status="review",
			confidence=0.9,
			reason="approved questionnaire",
			message="继续沟通",
		)
	)
	store.approve_review(review_id, "approved-ts")
	adapter = ZhilianRecruiterAutomationPlatform(RaisingZhilianClient())

	report = run_automation_cycle(
		adapter,
		store,
		AutomationConfig(),
		platform="zhilian",
		dry_run=False,
	)

	assert report.events[0].status == EventStatus.STOPPED_BY_SAFETY.value
	assert "input is hidden" in report.events[0].reason
	pending = store.read_pending()[0]
	assert pending.status == "pending"
	assert pending.updated_at



def test_zhilian_adapter_uses_client_for_message_execution(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	client = FakeZhilianClient()
	adapter = ZhilianRecruiterAutomationPlatform(client)

	report = run_automation_cycle(
		adapter,
		store,
		AutomationConfig(),
		platform="zhilian",
		dry_run=False,
		limit=1,
	)

	assert report.events[0].status == EventStatus.AUTO_EXECUTED.value
	assert client.reads == ["zl-101"]
	assert client.sent[0][0] == "zl-101"
	assert "近期是否看机会" in client.sent[0][1]


def test_zhilian_adapter_uses_client_for_contact_exchange(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	store.write_state({"conversations": {"zl-101": {"follow_up_sent_at": "ts"}}})
	client = FakeZhilianClient()
	adapter = ZhilianRecruiterAutomationPlatform(client)

	report = run_automation_cycle(
		adapter,
		store,
		AutomationConfig(),
		platform="zhilian",
		dry_run=False,
		limit=1,
	)

	assert report.events[0].action == PlatformAction.EXCHANGE_CONTACT.value
	assert report.events[0].status == EventStatus.AUTO_EXECUTED.value
	assert client.exchanges == ["zl-101"]


def test_zhilian_adapter_blocks_when_write_selector_is_missing(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	client = FakeZhilianClientWithoutWriteSelectors()
	adapter = ZhilianRecruiterAutomationPlatform(client)

	report = run_automation_cycle(
		adapter,
		store,
		AutomationConfig(),
		platform="zhilian",
		dry_run=False,
		limit=1,
	)

	assert report.events[0].status == EventStatus.STOPPED_BY_SAFETY.value
	assert "write selector" in store.stats()["recent_errors"][0]["reason"]
