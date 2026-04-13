import json
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	return instance


@patch("boss_agent_cli.commands.digest.BossClient")
@patch("boss_agent_cli.commands.digest.AuthManager")
def test_digest_command_returns_structured_sections(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = {
		"zpData": {
			"result": [
				{
					"name": "张HR",
					"securityId": "sec_1",
					"uid": 99,
					"title": "HR",
					"brandName": "TestCo",
					"friendSource": 0,
					"encryptJobId": "job_1",
					"lastMsg": "请尽快回复",
					"lastTS": 1700000001000,
					"unreadMsgCount": 1,
					"relationType": 1,
					"lastMessageInfo": {"status": 1},
				},
			],
		},
	}
	mock_client.interview_data.return_value = {
		"zpData": {
			"interviewList": [
				{"jobName": "Go 开发", "brandName": "TestCo", "interviewTime": "2026-04-14 10:00", "statusDesc": "待面试"},
			],
		},
	}

	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "digest"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["follow_up_count"] >= 1
	assert parsed["data"]["interview_count"] == 1


def test_digest_is_exposed_in_schema():
	runner = CliRunner()
	result = runner.invoke(cli, ["schema"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert "digest" in parsed["data"]["commands"]
