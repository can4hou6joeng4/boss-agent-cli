"""Recruiter automation fusion tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from boss_agent_cli.ai.config import AIConfigStore
from boss_agent_cli.automation.adapters import build_automation_adapter
from boss_agent_cli.automation.boss_adapter import BossRecruiterAutomationPlatform
from boss_agent_cli.automation.mock_adapter import MockRecruiterAutomationPlatform
from boss_agent_cli.automation.zhilian_adapter import ZhilianRecruiterAutomationPlatform
from boss_agent_cli.automation.config import AutomationConfig, ReplyStrategy
from boss_agent_cli.automation.decision import decide_action
from boss_agent_cli.automation.events import stable_action_id
from boss_agent_cli.automation.models import (
	ActionResult,
	Conversation,
	ConversationFingerprint,
	ConversationRef,
	EventStatus,
	ReviewItem,
	PlatformAction,
)
from boss_agent_cli.automation.runner import run_automation_cycle
from boss_agent_cli.automation.storage import AutomationStore
from boss_agent_cli.main import cli


class FakeBossRecruiter:
	def __init__(self) -> None:
		self.sent: list[tuple[int, str]] = []
		self.exchanges: list[tuple[int, int]] = []
		self.history_calls: list[int] = []

	def greet_list(self, page: int = 1, job_id: str | None = None) -> dict:
		return {"code": 0, "zpData": {"result": [{"friendId": 101, "name": "高分"}]}}

	def friend_list(
		self,
		page: int = 1,
		label_id: int = 0,
		job_id: str | None = None,
	) -> dict:
		return {"code": 0, "zpData": {"result": [{"friendId": 101, "name": "高分"}]}}

	def chat_history(
		self,
		gid: int,
		*,
		count: int = 20,
		max_msg_id: int | None = None,
	) -> dict:
		self.history_calls.append(gid)
		return {
			"code": 0,
			"zpData": {
				"result": [
					{
						"from": "geek",
						"content": "你好，我在上海做过3年销售，擅长客户沟通，大专，想看机会",
					}
				]
			},
		}

	def send_message_by_friend(self, friend_id: int, content: str) -> dict:
		self.sent.append((friend_id, content))
		return {"code": 0, "zpData": {"friendId": friend_id}}

	def exchange_request_by_friend(self, friend_id: int, exchange_type: int) -> dict:
		self.exchanges.append((friend_id, exchange_type))
		return {"code": 0, "zpData": {"friendId": friend_id}}


class RecordingMockRecruiterAutomationPlatform(MockRecruiterAutomationPlatform):
	def __init__(self, name: str, conversations: list[Conversation]) -> None:
		super().__init__(name, conversations)
		self.messages: list[str] = []

	def execute_action(
		self,
		action: PlatformAction,
		message: str,
		ref: ConversationRef,
	) -> ActionResult:
		self.messages.append(message)
		return super().execute_action(action, message, ref)


def _conversation(text: str, title: str = "张三") -> Conversation:
	return Conversation(
		title=title,
		incoming_messages=(text,),
		ordered_messages=(("incoming", text),),
		all_messages=(text,),
		fingerprint=ConversationFingerprint(title),
	)


def test_decision_sends_questionnaire_for_high_confidence_new_candidate() -> None:
	config = AutomationConfig()
	conversation = _conversation(
		"你好，我在上海做过3年销售，擅长客户沟通，大专，想看机会"
	)

	decision = decide_action(conversation, config, {})

	assert decision.action is PlatformAction.SEND_QUESTIONNAIRE
	assert decision.confidence >= config.auto_execute_threshold
	assert decision.requires_human is False


def test_hybrid_local_ai_rewrites_reply_without_changing_action(tmp_path: Path, monkeypatch) -> None:
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	store = AutomationStore(tmp_path)
	ai_store = AIConfigStore(tmp_path)
	ai_store.save_config(ai_provider="ollama", ai_model="qwen3:14b")
	ai_store.save_api_key("local")
	adapter = RecordingMockRecruiterAutomationPlatform(
		"zhilian",
		[_conversation("你好，我在上海做过3年销售，擅长客户沟通，大专，想看机会", "高分")],
	)
	response = MagicMock()
	response.raise_for_status = MagicMock()
	response.json.return_value = {
		"choices": [
			{
				"message": {
					"content": json.dumps({
						"action": "send_questionnaire",
						"confidence": 0.88,
						"reply": "您好，方便的话想确认下近期是否看新的销售机会？",
						"reason": "local ai polished",
						"risk_flags": [],
					}, ensure_ascii=False)
				}
			}
		]
	}

	with patch("boss_agent_cli.ai.service.httpx.post", return_value=response):
		report = run_automation_cycle(
			adapter,
			store,
			AutomationConfig(reply_strategy=ReplyStrategy.HYBRID),
			platform="zhilian",
			dry_run=False,
			limit=1,
		)

	assert report.events[0].action == PlatformAction.SEND_QUESTIONNAIRE.value
	assert report.events[0].status == EventStatus.AUTO_EXECUTED.value
	assert adapter.messages == ["您好，方便的话想确认下近期是否看新的销售机会？"]


def test_local_ai_parse_error_queues_review_without_sending(tmp_path: Path, monkeypatch) -> None:
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	store = AutomationStore(tmp_path)
	ai_store = AIConfigStore(tmp_path)
	ai_store.save_config(ai_provider="ollama", ai_model="qwen3:14b")
	ai_store.save_api_key("local")
	adapter = RecordingMockRecruiterAutomationPlatform(
		"zhilian",
		[_conversation("你好，我在上海做过3年销售，擅长客户沟通，大专，想看机会", "高分")],
	)
	response = MagicMock()
	response.raise_for_status = MagicMock()
	response.json.return_value = {"choices": [{"message": {"content": "not json"}}]}

	with patch("boss_agent_cli.ai.service.httpx.post", return_value=response):
		report = run_automation_cycle(
			adapter,
			store,
			AutomationConfig(reply_strategy=ReplyStrategy.LOCAL_AI),
			platform="zhilian",
			dry_run=False,
			limit=1,
		)

	assert report.events[0].status == EventStatus.QUEUED_FOR_REVIEW.value
	assert adapter.messages == []
	assert store.read_reviews()[0].message == "您好，想确认下近期是否看机会？"


def test_runner_dry_run_writes_events_and_review_queue(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	adapter = MockRecruiterAutomationPlatform(
		"zhilian",
		[
			_conversation(
				"你好，我在上海做过3年销售，擅长客户沟通，大专，想看机会",
				"高分",
			),
			_conversation("不要再联系", "风险"),
		],
	)

	report = run_automation_cycle(
		adapter,
		store,
		AutomationConfig(),
		platform="zhilian",
		dry_run=True,
	)

	assert report.status == "OK"
	assert any(event.status == EventStatus.DRY_RUN.value for event in report.events)
	assert store.stats()["human_reviews"] == 1
	assert store.stats()["dry_run"] >= 1


def test_runner_creates_interview_lead_after_contact_exchange(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	state = {
		"conversations": {
			"面试候选人": {"exchange_contact_at": "2026-06-23T00:00:00"}
		}
	}
	store.write_state(state)
	adapter = MockRecruiterAutomationPlatform(
		"zhipin",
		[
			_conversation(
				"我在上海做过3年销售，大专，想看机会，周二 14:00 面试",
				"面试候选人",
			)
		],
	)

	report = run_automation_cycle(
		adapter,
		store,
		AutomationConfig(),
		platform="zhipin",
		dry_run=True,
	)

	assert report.events[0].action == PlatformAction.CREATE_INTERVIEW_LEAD.value
	assert (tmp_path / "automation" / "interview-leads.csv").exists()


def test_agent_run_cli_returns_json_envelope(tmp_path: Path) -> None:
	runner = CliRunner()
	result = runner.invoke(
		cli,
		[
			"--data-dir",
			str(tmp_path),
			"--json",
			"--platform",
			"zhilian",
			"--role",
			"recruiter",
			"agent",
			"run",
			"--dry-run",
			"--limit",
			"1",
		],
	)

	assert result.exit_code == 0
	payload = json.loads(result.output)
	assert payload["ok"] is True
	assert payload["command"] == "agent.run"
	assert payload["data"]["platform"] == "zhilian"
	assert payload["data"]["events"][0]["status"] == "STOPPED_BY_SAFETY"


def test_agent_stats_review_and_pending_commands_are_available(tmp_path: Path) -> None:
	runner = CliRunner()
	for args, command in [
		(["agent", "stats"], "agent.stats"),
		(["agent", "review", "list"], "agent.review.list"),
		(["agent", "pending", "list"], "agent.pending.list"),
	]:
			result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])
			assert result.exit_code == 0
			payload = json.loads(result.output)
			assert payload["command"] == command


def test_review_approve_moves_item_to_pending_queue(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	review_id = stable_action_id("zhilian", "candidate-1", PlatformAction.SEND_FOLLOW_UP, "ts")
	store.append_review(
		ReviewItem(
			id=review_id,
			ts="ts",
			platform="zhilian",
			candidate_key="candidate-1",
			action=PlatformAction.SEND_FOLLOW_UP.value,
			status="review",
			confidence=0.7,
			reason="needs review",
			message="继续沟通",
		)
	)
	runner = CliRunner()

	result = runner.invoke(
		cli,
		[
			"--data-dir",
			str(tmp_path),
			"--json",
			"agent",
			"review",
			"approve",
			review_id,
		],
	)

	assert result.exit_code == 0
	payload = json.loads(result.output)
	assert payload["command"] == "agent.review.approve"
	assert store.read_pending()[0].approved_review_id == review_id


def test_review_reject_marks_item_and_writes_skip_event(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	review_id = stable_action_id("zhipin", "candidate-2", PlatformAction.EXCHANGE_CONTACT, "ts")
	store.append_review(
		ReviewItem(
			id=review_id,
			ts="ts",
			platform="zhipin",
			candidate_key="candidate-2",
			action=PlatformAction.EXCHANGE_CONTACT.value,
			status="review",
			confidence=0.68,
			reason="needs review",
		)
	)
	runner = CliRunner()

	result = runner.invoke(
		cli,
		[
			"--data-dir",
			str(tmp_path),
			"--json",
			"agent",
			"review",
			"reject",
			review_id,
			"--reason",
			"not a fit",
		],
	)

	assert result.exit_code == 0
	payload = json.loads(result.output)
	assert payload["command"] == "agent.review.reject"
	assert store.read_reviews()[0].status == "rejected"
	assert store.read_jsonl("action-log.jsonl")[0]["status"] == EventStatus.SKIPPED.value


def test_pending_actions_execute_before_new_conversation_scan(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	review_id = stable_action_id("zhilian", "pending-candidate", PlatformAction.SEND_FOLLOW_UP, "ts")
	store.append_review(
		ReviewItem(
			id=review_id,
			ts="ts",
			platform="zhilian",
			candidate_key="pending-candidate",
			action=PlatformAction.SEND_FOLLOW_UP.value,
			status="review",
			confidence=0.9,
			reason="approved follow-up",
			message="继续沟通",
		)
	)
	store.approve_review(review_id, "approved-ts")
	adapter = MockRecruiterAutomationPlatform(
		"zhilian",
		[_conversation("你好，我在上海做过3年销售，大专，想看机会", "新候选人")],
	)

	report = run_automation_cycle(
		adapter,
		store,
		AutomationConfig(),
		platform="zhilian",
		dry_run=True,
	)

	assert report.events[0].candidate_key == "pending-candidate"
	assert report.events[0].action == PlatformAction.SEND_FOLLOW_UP.value
	assert report.events[1].candidate_key == "新候选人"


def test_boss_adapter_uses_recruiter_platform_for_message_execution(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	recruiter = FakeBossRecruiter()
	adapter = BossRecruiterAutomationPlatform(recruiter)

	report = run_automation_cycle(
		adapter,
		store,
		AutomationConfig(),
		platform="zhipin",
		dry_run=False,
		limit=1,
	)

	assert report.events[0].status == EventStatus.AUTO_EXECUTED.value
	assert recruiter.history_calls == [101]
	assert recruiter.sent[0][0] == 101
	assert "近期是否看机会" in recruiter.sent[0][1]


def test_boss_adapter_uses_recruiter_platform_for_contact_exchange(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	store.write_state({"conversations": {"101": {"follow_up_sent_at": "ts"}}})
	recruiter = FakeBossRecruiter()
	adapter = BossRecruiterAutomationPlatform(recruiter)

	report = run_automation_cycle(
		adapter,
		store,
		AutomationConfig(),
		platform="zhipin",
		dry_run=False,
		limit=1,
	)

	assert report.events[0].action == PlatformAction.EXCHANGE_CONTACT.value
	assert report.events[0].status == EventStatus.AUTO_EXECUTED.value
	assert recruiter.exchanges == [(101, 2)]


def test_adapter_factory_returns_real_platform_adapters() -> None:
	assert isinstance(build_automation_adapter("zhilian"), ZhilianRecruiterAutomationPlatform)
	assert isinstance(build_automation_adapter("zhipin"), BossRecruiterAutomationPlatform)


def test_schema_and_mcp_expose_agent_automation(tmp_path: Path) -> None:
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "schema"])
	assert result.exit_code == 0
	payload = json.loads(result.output)
	assert "agent" in payload["data"]["commands"]
	assert payload["data"]["commands"]["agent"]["availability"]["recruiter_platforms"] == [
		"zhilian",
		"zhipin",
	]
	assert "CIRCUIT_BREAKER_OPEN" in payload["data"]["error_codes"]
