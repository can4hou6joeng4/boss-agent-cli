"""消息对象形状的候选人来信不展示招聘者发送状态（Issue #3003）。"""

import json
from copy import deepcopy

import pytest
from click.testing import CliRunner

from boss_agent_cli.commands.recruiter import chat
from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.services.recruiter_chat_summary import normalize_last_message
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS

FRIEND_ID = 880101
SELF_ID = 880909


def _message(incoming, *, status=2, text_uid=False):
	sender, recipient = (FRIEND_ID, SELF_ID) if incoming else (SELF_ID, FRIEND_ID)
	return {
		"from": {"uid": str(sender) if text_uid else sender},
		"to": {"uid": recipient},
		"body": {"text": "离线消息夹具"},
		"status": status,
	}


@pytest.mark.parametrize("incoming", [True, False])
@pytest.mark.parametrize("text_uid", [True, False])
@pytest.mark.parametrize("status,label", [(0, "发送中"), (1, "未读"), (2, "已读")])
def test_message_object_direction_controls_status(incoming, text_uid, status, label):
	result = normalize_last_message(_message(incoming, status=status, text_uid=text_uid), {FRIEND_ID})
	assert result["friendId"] == FRIEND_ID
	assert result["last_msg"] == "离线消息夹具"
	assert result["msg_status"] == (None if incoming else label)


@pytest.mark.parametrize("incoming,overrides,expected", [
	(True, {"lastIsSelf": True}, "已读"),
	(False, {"lastIsSelf": False}, None),
	(False, {"lastMsgInfo": {"fromId": FRIEND_ID}}, None),
	(True, {"lastMessageInfo": {"fromId": SELF_ID}}, "已读"),
])
def test_existing_direction_fields_keep_priority(incoming, overrides, expected):
	message = {**_message(incoming), **overrides}
	assert normalize_last_message(message, {FRIEND_ID})["msg_status"] == expected


@pytest.mark.parametrize("invalid_sender", [None, True, "invalid"])
def test_unusable_nested_sender_falls_back_to_message_object(invalid_sender):
	message = {**_message(True), "lastMsgInfo": {"fromId": invalid_sender}}
	assert normalize_last_message(message, {FRIEND_ID})["msg_status"] is None


@pytest.mark.parametrize("sender", [None, True, {}, "invalid"])
def test_unusable_message_sender_keeps_unknown_direction_contract(sender):
	message = {**_message(True), "friendId": FRIEND_ID, "from": {"uid": sender}}
	assert normalize_last_message(message)["msg_status"] == "已读"


class _Platform:
	def __init__(self, message):
		self.message = message

	def __enter__(self):
		return self

	def __exit__(self, *args):
		return None

	def friend_list(self, **kwargs):
		return {"code": 0, "zpData": {"friendList": [{"friendId": FRIEND_ID}], "hasMore": False}}

	def last_messages(self, friend_ids, **kwargs):
		assert friend_ids == [FRIEND_ID]
		return {"code": 0, "zpData": {"messages": [deepcopy(self.message)]}}

	def chat_list_snapshot(self):
		return None

	@staticmethod
	def is_success(response):
		return response.get("code") == 0

	@staticmethod
	def unwrap_data(response):
		return response.get("zpData")


@pytest.mark.parametrize("entry", ["chat", "last-messages", "wizard"])
@pytest.mark.parametrize("incoming", [True, False])
def test_cli_and_wizard_share_message_direction(tmp_path, monkeypatch, entry, incoming):
	platform = _Platform(_message(incoming, status=0 if incoming else 2))
	if entry == "wizard":
		context = ActionContext(
			data_dir=tmp_path, platform="zhipin", role="recruiter", logger=Logger(),
			recruiter_factory=lambda *args: platform,
		)
		data = DEFAULT_ACTIONS["recruiter_applications"](context, {}, {}).data
		row = data["items"][0]
	else:
		monkeypatch.setattr(chat, "AuthManager", lambda *args, **kwargs: None)
		monkeypatch.setattr(chat, "get_recruiter_platform_instance", lambda *args: platform)
		monkeypatch.setattr(chat, "require_compliance_allowed", lambda *args, **kwargs: True)
		result = CliRunner().invoke(cli, [
			"--data-dir", str(tmp_path), "--role", "recruiter", "--json", "hr", entry,
		])
		assert result.exit_code == 0, result.output
		envelope = json.loads(result.stdout)
		assert envelope["ok"] is True
		data = envelope["data"]
		row = data["friendList" if entry == "chat" else "messages"][0]
	assert row["msg_status"] == (None if incoming else "已读")
	assert row["last_msg"] == "离线消息夹具"
