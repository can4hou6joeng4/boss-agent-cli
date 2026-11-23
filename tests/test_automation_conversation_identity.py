"""候选人会话标识未经确认时不得发出平台读取或写入（Issue #2580）。"""

from pathlib import Path
from unittest.mock import Mock

import pytest

from boss_agent_cli.automation.boss_adapter import BossRecruiterAutomationPlatform
from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.models import ConversationRef, EventStatus, PendingAction, PlatformAction
from boss_agent_cli.automation.runner import run_automation_cycle
from boss_agent_cli.automation.storage import AutomationStore


def _recruiter() -> Mock:
	recruiter = Mock()
	recruiter.greet_list.return_value = {"code": 0, "zpData": {"result": []}}
	recruiter.friend_list.return_value = {"code": 0, "zpData": {"result": []}}
	recruiter.chat_history.return_value = {"code": 0, "zpData": {"result": []}}
	recruiter.send_message_by_friend.return_value = {"code": 0}
	recruiter.exchange_request_by_friend.return_value = {"code": 0}
	return recruiter


@pytest.mark.parametrize("value", ["invalid", "0", "-42", "1.0", "True", "", " ", "４２"])
@pytest.mark.parametrize("action", [PlatformAction.SEND_QUESTIONNAIRE, PlatformAction.EXCHANGE_CONTACT])
def test_invalid_reference_never_reaches_platform(value: str, action: PlatformAction) -> None:
	recruiter = _recruiter()
	adapter = BossRecruiterAutomationPlatform(recruiter)
	ref = ConversationRef(id=value, tab="pending")

	conversation = adapter.read_conversation(ref)
	result = adapter.execute_action(action, "示例消息", ref)

	assert conversation.all_messages == ()
	assert result.status == "blocked"
	assert "friend_id" in result.details["reason"]
	recruiter.chat_history.assert_not_called()
	recruiter.send_message_by_friend.assert_not_called()
	recruiter.exchange_request_by_friend.assert_not_called()


@pytest.mark.parametrize("bad_id", [True, {"id": 42}, [42], 1.0, 0, -1, None])
def test_malformed_list_identity_does_not_use_numeric_display_name(bad_id: object) -> None:
	recruiter = _recruiter()
	recruiter.greet_list.return_value = {
		"code": 0, "zpData": {"result": [{"friendId": bad_id, "name": "42"}, {"friendId": 43}]},
	}
	adapter = BossRecruiterAutomationPlatform(recruiter)

	refs = adapter.scan_conversations(["新招呼"], 20)

	assert refs[0].diagnostic
	assert refs[0].id != "42"
	assert refs[1].id == "43" and not refs[1].diagnostic


@pytest.mark.parametrize("value", [42, "42", "0042"])
def test_valid_identifiers_keep_existing_reference_and_recipient(value: str | int) -> None:
	recruiter = _recruiter()
	recruiter.greet_list.return_value = {"code": 0, "zpData": {"result": [{"uid": value}]}}
	adapter = BossRecruiterAutomationPlatform(recruiter)
	ref = adapter.scan_conversations(["新招呼"], 1)[0]

	assert ref.id == str(value)
	assert not ref.diagnostic
	adapter.read_conversation(ref)
	assert adapter.execute_action(PlatformAction.SEND_FOLLOW_UP, "示例消息", ref).status == "executed"
	recruiter.chat_history.assert_called_once_with(42)
	recruiter.send_message_by_friend.assert_called_once_with(42, "示例消息")


@pytest.mark.parametrize("empty", [None, "", 0])
def test_empty_primary_identity_still_falls_back_to_next_field(empty: object) -> None:
	"""0 / 空串 / null 表示「这个字段没填」，沿用原先按顺序回退的后继字段。"""
	recruiter = _recruiter()
	recruiter.greet_list.return_value = {
		"code": 0, "zpData": {"result": [{"friendId": empty, "uid": 43}]},
	}
	ref = BossRecruiterAutomationPlatform(recruiter).scan_conversations(["新招呼"], 1)[0]

	assert ref.id == "43"
	assert not ref.diagnostic


def test_oversized_numeric_identity_never_reaches_platform() -> None:
	"""超过 Python 整数转换上限的数字串不能让读取路径抛异常或发出请求。"""
	recruiter = _recruiter()
	adapter = BossRecruiterAutomationPlatform(recruiter)
	ref = ConversationRef(id="9" * 5000, tab="pending")

	assert adapter.read_conversation(ref).all_messages == ()
	assert adapter.execute_action(PlatformAction.SEND_QUESTIONNAIRE, "示例消息", ref).status == "blocked"
	recruiter.chat_history.assert_not_called()
	recruiter.send_message_by_friend.assert_not_called()


def test_invalid_approved_action_stays_pending_without_inflight_marker(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	store.append_pending(PendingAction(
		id="sample-action", ts="2026-11-23T00:00:00+00:00", platform="zhipin", candidate_key="invalid",
		action=PlatformAction.SEND_QUESTIONNAIRE.value, status="pending", confidence=0.9,
		reason="已批准的示例", message="示例消息",
	))
	recruiter = _recruiter()

	report = run_automation_cycle(
		BossRecruiterAutomationPlatform(recruiter), store, AutomationConfig(), platform="zhipin", dry_run=False,
	)

	assert store.read_pending()[0].status == "pending"
	assert report.events[0].status == EventStatus.STOPPED_BY_SAFETY.value
	assert "friend_id" in report.events[0].reason
	assert not store.read_state()["conversations"]["invalid"].get("inflight")
	recruiter.send_message_by_friend.assert_not_called()
	recruiter.exchange_request_by_friend.assert_not_called()
