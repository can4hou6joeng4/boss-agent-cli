"""客户端明确未派发证据同时保护批准队列与直接扫描（Issue #3019）。"""

from pathlib import Path
from unittest.mock import Mock

import pytest

from boss_agent_cli.api.client import EnvironmentRiskError
from boss_agent_cli.api.recruiter_client import BossRecruiterClient
from boss_agent_cli.automation.boss_adapter import BossRecruiterAutomationPlatform
from boss_agent_cli.automation.config import AutomationConfig, ReplyStrategy
from boss_agent_cli.automation.models import (
	CandidateKey,
	Conversation,
	ConversationRef,
	Decision,
	PendingAction,
	PlatformAction,
)
from boss_agent_cli.automation.runner import run_automation_cycle
from boss_agent_cli.automation.storage import AutomationStore


class _OfflineAdapter(BossRecruiterAutomationPlatform):
	def __init__(self, client: Mock, source: str) -> None:
		super().__init__(client)
		self.source = source

	def scan_conversations(self, tabs: list[str], max_per_tab: int) -> list[ConversationRef]:
		return [ConversationRef(id="42", tab="offline")] if self.source == "scan" else []

	def read_conversation(self, ref: ConversationRef) -> Conversation:
		return Conversation(title=ref.id)


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str):
	store = AutomationStore(tmp_path)
	if source == "queue":
		store.append_pending(PendingAction(
			id="offline-approved", ts="2026-11-27T00:00:00+00:00", platform="zhipin",
			candidate_key="42", action=PlatformAction.SEND_QUESTIONNAIRE.value,
			status="pending", confidence=0.99, reason="offline approval", message="测试问卷",
		))
	decision = Decision(
		action=PlatformAction.SEND_QUESTIONNAIRE, confidence=0.99,
		reason="offline decision", candidate_key=CandidateKey("42"), message="测试问卷",
	)
	monkeypatch.setattr("boss_agent_cli.automation.execution.decide_action", lambda *args: decision)
	client = Mock(spec=BossRecruiterClient)
	adapter = _OfflineAdapter(client, source)
	config = AutomationConfig(reply_strategy=ReplyStrategy.TEMPLATE)
	return store, client, adapter, config


@pytest.mark.parametrize("source", ["queue", "scan"])
def test_client_prewrite_failure_releases_marker_and_allows_later_retry(tmp_path, monkeypatch, source):
	store, client, adapter, config = _setup(tmp_path, monkeypatch, source)
	error = RuntimeError("offline pre-write read failure")
	client._require_chat_friend_data.side_effect = error
	# 使用真实客户端的写前边界，证明属性来自客户端而非测试随意构造。
	client.send_message_by_friend.side_effect = lambda friend_id, content: (
		BossRecruiterClient.send_message_by_friend(client, friend_id, content)
	)

	with pytest.raises(RuntimeError) as caught:
		run_automation_cycle(adapter, store, config, platform="zhipin", dry_run=False)

	assert caught.value is error
	assert error.request_sent is False
	client._run_chat_frontend_action.assert_not_called()
	assert "inflight_action" not in store.read_state()["conversations"]["42"]
	if source == "queue":
		assert store.read_pending()[0].status == "pending"

	client.send_message_by_friend.side_effect = None
	client.send_message_by_friend.return_value = {"code": 0, "data": {"sent": True}}
	report = run_automation_cycle(adapter, store, config, platform="zhipin", dry_run=False)

	assert report.status == "OK"
	assert client.send_message_by_friend.call_count == 2
	if source == "queue":
		assert store.read_pending()[0].status == "executed"
	assert "inflight_action" not in store.read_state()["conversations"]["42"]


@pytest.mark.parametrize("source", ["queue", "scan"])
@pytest.mark.parametrize("evidence", [None, True, 0, "false"])
def test_unknown_or_invalid_evidence_keeps_replay_barrier(tmp_path, monkeypatch, source, evidence):
	store, client, adapter, config = _setup(tmp_path, monkeypatch, source)
	error = RuntimeError("offline failure without proof")
	if evidence is not None:
		setattr(error, "request_sent", evidence)
	client.send_message_by_friend.side_effect = error

	with pytest.raises(RuntimeError) as caught:
		run_automation_cycle(adapter, store, config, platform="zhipin", dry_run=False)
	assert caught.value is error
	assert store.read_state()["conversations"]["42"]["inflight_action"]["status"] == "executing"
	if source == "queue":
		assert store.read_pending()[0].status == "executing"

	report = run_automation_cycle(adapter, store, config, platform="zhipin", dry_run=False)

	assert report.status == "CIRCUIT_BREAKER_OPEN"
	assert client.send_message_by_friend.call_count == 1
	assert store.read_state()["conversations"]["42"]["inflight_action"]["status"] == "verification-required"


@pytest.mark.parametrize("source", ["queue", "scan"])
def test_risk_still_stops_and_preserves_inflight_even_with_false_attribute(tmp_path, monkeypatch, source):
	store, client, adapter, config = _setup(tmp_path, monkeypatch, source)
	error = EnvironmentRiskError("offline environment risk")
	setattr(error, "request_sent", False)
	client.send_message_by_friend.side_effect = error

	with pytest.raises(EnvironmentRiskError) as caught:
		run_automation_cycle(adapter, store, config, platform="zhipin", dry_run=False)

	assert caught.value is error
	assert store.read_stop()["open"] is True
	assert store.read_state()["conversations"]["42"]["inflight_action"]["status"] == "executing"
	if source == "queue":
		assert store.read_pending()[0].status == "executing"


def test_failed_pending_restore_does_not_replace_original_unsent_error(tmp_path, monkeypatch):
	store, client, adapter, config = _setup(tmp_path, monkeypatch, "queue")
	error = RuntimeError("offline explicitly unsent")
	setattr(error, "request_sent", False)
	client.send_message_by_friend.side_effect = error
	write_pending = store.write_pending
	calls = 0

	def fail_restore(items):
		nonlocal calls
		calls += 1
		if calls == 2:
			raise OSError("offline disk failure")
		return write_pending(items)

	monkeypatch.setattr(store, "write_pending", fail_restore)
	with pytest.raises(RuntimeError) as caught:
		run_automation_cycle(adapter, store, config, platform="zhipin", dry_run=False)

	assert caught.value is error
	assert store.read_pending()[0].status == "executing"
	assert client.send_message_by_friend.call_count == 1
