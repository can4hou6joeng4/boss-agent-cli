"""自动化适配器必须解析平台真实聊天记录结构（Issue #2582）。"""

from pathlib import Path
from unittest.mock import Mock

from boss_agent_cli.automation.boss_adapter import BossRecruiterAutomationPlatform
from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.models import (
	ConversationRef,
	EventStatus,
	PlatformAction,
)
from boss_agent_cli.automation.runner import run_automation_cycle
from boss_agent_cli.automation.storage import AutomationStore

FRIEND_ID = 9
SELF_UID = 777


def _adapter(history: dict) -> tuple[BossRecruiterAutomationPlatform, Mock]:
	recruiter = Mock()
	recruiter.chat_history.return_value = {"code": 0, "zpData": history}
	return BossRecruiterAutomationPlatform(recruiter), recruiter


def _read(history: dict) -> tuple[object, Mock]:
	adapter, recruiter = _adapter(history)
	return adapter.read_conversation(ConversationRef(id=str(FRIEND_ID), tab="scan")), recruiter


def test_real_messages_map_to_directions_and_body_text() -> None:
	conversation, recruiter = _read({"messages": [
		{"mid": 1, "from": {"uid": FRIEND_ID, "source": 0}, "to": {"uid": SELF_UID},
		 "body": {"type": 1, "text": "你好，还在看机会吗"}},
		{"mid": 2, "from": {"uid": SELF_UID, "source": 1}, "to": {"uid": FRIEND_ID},
		 "body": {"type": 1, "text": "在看，方便聊聊"}},
	]})

	recruiter.chat_history.assert_called_once_with(FRIEND_ID)
	assert conversation.ordered_messages == (
		("incoming", "你好，还在看机会吗"),
		("outgoing", "在看，方便聊聊"),
	)
	assert conversation.incoming_messages == ("你好，还在看机会吗",)
	assert conversation.outgoing_messages == ("在看，方便聊聊",)
	assert conversation.all_messages == ("你好，还在看机会吗", "在看，方便聊聊")


def test_string_uid_and_push_text_are_supported() -> None:
	conversation, _ = _read({"messages": [
		{"mid": 1, "from": {"uid": str(FRIEND_ID)}, "body": {"type": 1, "text": "你好"}},
		{"mid": 2, "from": {"uid": SELF_UID}, "body": {"type": 4},
		 "pushText": "请求交换联系方式"},
	]})

	assert conversation.incoming_messages == ("你好",)
	assert conversation.outgoing_messages == ("请求交换联系方式",)


def test_system_messages_do_not_look_like_candidate_replies() -> None:
	conversation, _ = _read({"messages": [
		{"mid": 1, "from": {"uid": 0}, "body": {"type": 4}, "pushText": "候选人已同意交换"},
		{"mid": 2, "from": {"uid": FRIEND_ID}, "body": {"type": 1, "text": "还在看机会吗"}},
	]})

	assert conversation.incoming_messages == ("还在看机会吗",)
	assert conversation.outgoing_messages == ()
	assert "候选人已同意交换" not in conversation.all_messages


def test_malformed_sender_uids_are_skipped_without_raising() -> None:
	conversation, _ = _read({"messages": [
		{"mid": 1, "from": {"uid": True}, "body": {"text": "布尔"}},
		{"mid": 2, "from": {"uid": {"nested": 1}}, "body": {"text": "容器"}},
		{"mid": 3, "from": {"uid": 1.5}, "body": {"text": "小数"}},
		{"mid": 4, "from": {"uid": "abc"}, "body": {"text": "非数字"}},
		{"mid": 5, "from": {"uid": "-9"}, "body": {"text": "负数"}},
	]})

	assert conversation.ordered_messages == ()


def test_legacy_synthetic_shape_is_unchanged() -> None:
	conversation, _ = _read({"result": [
		{"from": "boss", "content": "您好，我是招聘方"},
		{"from": "self", "text": "方便继续沟通吗"},
		{"content": "您好，在看机会"},
	]})

	assert conversation.ordered_messages == (
		("outgoing", "您好，我是招聘方"),
		("outgoing", "方便继续沟通吗"),
		("incoming", "您好，在看机会"),
	)


def test_empty_or_missing_history_yields_empty_conversation() -> None:
	for history in ({"messages": []}, {}):
		conversation, _ = _read(history)
		assert conversation.ordered_messages == ()
		assert conversation.incoming_messages == ()


def test_real_history_drives_questionnaire_in_dry_run(tmp_path: Path) -> None:
	recruiter = Mock()
	recruiter.greet_list.return_value = {
		"code": 0, "zpData": {"result": [{"friendId": FRIEND_ID, "name": "候选人"}]},
	}
	recruiter.friend_list.return_value = {"code": 0, "zpData": {"result": []}}
	recruiter.chat_history.return_value = {"code": 0, "zpData": {"messages": [
		{"mid": 1, "from": {"uid": FRIEND_ID},
		 "body": {"type": 1, "text": "你好，我在上海做过3年销售，擅长客户沟通，大专，想看机会"}},
	]}}
	store = AutomationStore(tmp_path)

	report = run_automation_cycle(
		BossRecruiterAutomationPlatform(recruiter),
		store,
		AutomationConfig(auto_execute_threshold=0.0),
		platform="zhipin",
		dry_run=True,
	)

	assert report.events[0].action == PlatformAction.SEND_QUESTIONNAIRE.value
	assert report.events[0].status == EventStatus.DRY_RUN.value
	recruiter.send_message_by_friend.assert_not_called()
