"""聊天列表真实数据形状的脱敏回归；不访问招聘平台。"""

import pytest

from boss_agent_cli.services.recruiter_chat_summary import merge_last_messages


def test_last_message_without_unread_does_not_overwrite_friend_count():
	rows = [{"friendId": 123, "unread": 4}]
	merge_last_messages(rows, [{"uid": 123, "lastMsgInfo": {"showText": "hello", "status": 1}}])
	assert rows[0]["unread"] == 4
	assert rows[0]["last_msg"] == "hello"


def test_missing_unread_remains_unknown():
	rows = [{"friendId": 123}]
	merge_last_messages(rows, [{"uid": 123, "lastMsgInfo": {"showText": "hello"}}])
	assert rows[0]["unread"] is None


def test_last_message_unread_does_not_replace_authoritative_friend_zero():
	rows = [{"friendId": 123, "unread": 0}]
	merge_last_messages(rows, [{"uid": 123, "unread": 4}])
	assert rows[0]["unread"] == 0


@pytest.mark.parametrize("unread", [-1, True, "", "invalid", 1.5])
def test_invalid_unread_is_not_zero(unread):
	rows = [{"friendId": 123, "unread": unread}]
	merge_last_messages(rows, [])
	assert rows[0]["unread"] is None
