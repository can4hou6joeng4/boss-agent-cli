import json
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	return instance


def _friend_list_response(items):
	return {"zpData": {"result": items, "friendList": items}}


def _friend():
	return {
		"name": "张HR",
		"securityId": "sec_001",
		"uid": 99,
		"title": "HR",
		"brandName": "TestCo",
		"friendSource": 0,
		"encryptJobId": "job_001",
		"lastMsg": "发我一份简历",
		"lastTS": 1700000001000,
		"unreadMsgCount": 1,
		"relationType": 1,
		"lastMessageInfo": {"status": 1},
	}


@patch("boss_agent_cli.commands.chat_summary.BossClient")
@patch("boss_agent_cli.commands.chat_summary.AuthManager")
def test_chat_summary_command_success(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_friend()])
	mock_client.chat_history.return_value = {
		"zpData": {
			"messages": [
				{"from": {"uid": 12345, "name": "我"}, "type": 1, "text": "您好", "time": 1700000000000},
				{"from": {"uid": 99, "name": "张HR"}, "type": 1, "text": "发我一份简历", "time": 1700000001000},
			]
		}
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "chat-summary", "sec_001"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["stage"] == "reply_needed"
	assert parsed["data"]["security_id"] == "sec_001"


def test_chat_summary_is_exposed_in_schema():
	runner = CliRunner()
	result = runner.invoke(cli, ["schema"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert "chat-summary" in parsed["data"]["commands"]
