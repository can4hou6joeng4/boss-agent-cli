"""线上数据形状的脱敏回归；不访问招聘平台。"""

from unittest.mock import MagicMock

import pytest

from boss_agent_cli.api.recruiter_client import BossRecruiterClient
from boss_agent_cli.commands.recruiter.chat import _merge_last_messages
from boss_agent_cli.commands.recruiter.recommendations import _read_state_after_greet
from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.platforms.zhipin_recruiter import BossRecruiterPlatform


def test_live_friend_list_shape_can_resolve_zero_unread():
	client = MagicMock()
	client.friend_list.return_value = {"code": 0, "zpData": {"result": [
		{"encryptFriendId": "geek", "friendId": 123, "unread": 0},
	]}}
	state = _read_state_after_greet(BossRecruiterPlatform(client), start_result={"code": 0}, geek_id="geek", job_id="job", security_id="s")
	assert state == {"status": "not_needed", "unread": 0}
	client.mark_read.assert_not_called()


def test_unread_does_not_open_mqtt_without_separate_permission():
	client = MagicMock()
	client.friend_list.return_value = {"code": 0, "zpData": {"result": [
		{"encryptFriendId": "geek", "friendId": 123, "unread": 1, "messageId": 456},
	]}}
	state = _read_state_after_greet(BossRecruiterPlatform(client), start_result={"code": 0}, geek_id="geek", job_id="job", security_id="s")
	assert state["status"] == "deferred"
	assert state["reason"] == "mqtt_session_not_authorized"
	client.mark_read.assert_not_called()
	client.last_messages.assert_not_called()


def test_auth_expiry_response_is_not_flattened_to_unknown():
	client = MagicMock()
	client.friend_list.return_value = {"code": 7, "message": "当前登录状态已失效"}
	state = _read_state_after_greet(BossRecruiterPlatform(client), start_result={"code": 0}, geek_id="g", job_id="j", security_id="s")
	assert state["error_code"] == "AUTH_REQUIRED"


def test_last_message_without_unread_does_not_overwrite_friend_count():
	rows = [{"friendId": 123, "unread": 4}]
	_merge_last_messages(rows, [{"uid": 123, "lastMsgInfo": {"showText": "hello", "status": 1}}])
	assert rows[0]["unread"] == 4
	assert rows[0]["last_msg"] == "hello"


def test_missing_unread_remains_unknown():
	rows = [{"friendId": 123}]
	_merge_last_messages(rows, [{"uid": 123, "lastMsgInfo": {"showText": "hello"}}])
	assert rows[0]["unread"] is None


def test_last_message_unread_does_not_replace_authoritative_friend_zero():
	rows = [{"friendId": 123, "unread": 0}]
	_merge_last_messages(rows, [{"uid": 123, "unread": 4}])
	assert rows[0]["unread"] == 0


@pytest.mark.parametrize("permission", [False, None, "true", 1])
def test_mqtt_client_requires_literal_permission_before_bootstrap(permission):
	client = object.__new__(BossRecruiterClient)
	client._request = MagicMock()
	with pytest.raises(PermissionError):
		client.mark_read(peer_uid=123, message_id=456, allow_mqtt_session=permission)
	client._request.assert_not_called()


@pytest.mark.parametrize("permission", [None, False, "false", "true", 1, True])
def test_mcp_mqtt_permission_is_not_inferred_from_yes(permission):
	arguments = {"geek_id": "g", "job_id": "j", "expect_id": "e", "lid": "l", "security_id": "s", "message": "hello", "yes": True}
	if permission is not None:
		arguments["allow_mqtt_session"] = permission
	assert ("--allow-mqtt-session" in _build_args("boss_hr_greet", arguments)) is (permission is True)


@pytest.mark.parametrize("unread", [-1, True, "", "invalid", 1.5])
def test_invalid_unread_is_not_zero(unread):
	rows = [{"friendId": 123, "unread": unread}]
	_merge_last_messages(rows, [])
	assert rows[0]["unread"] is None
