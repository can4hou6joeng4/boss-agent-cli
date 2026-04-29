import json
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: response.get("zpData") if "zpData" in response else response.get("data")
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	return instance


def _friend_list_response(items):
	return {"zpData": {"result": items, "friendList": items}}


def _chat_item(
	*,
	security_id="sec_001",
	job_id="job_001",
	relation_type=1,
	unread=0,
	last_ts=1700000000000,
):
	return {
		"name": "张HR",
		"securityId": security_id,
		"uid": 12345,
		"title": "HR",
		"brandName": "TestCo",
		"friendSource": 0,
		"encryptJobId": job_id,
		"lastMsg": "你好",
		"lastTS": last_ts,
		"unreadMsgCount": unread,
		"relationType": relation_type,
		"lastMessageInfo": {"status": 1 if unread else 2},
	}


def _interview_item():
	return {
		"jobName": "Go 开发",
		"brandName": "TestCo",
		"interviewTime": "2026-04-12 10:00",
		"address": "线上",
		"statusDesc": "待面试",
	}


@patch("boss_agent_cli.commands.pipeline.get_platform_instance")
@patch("boss_agent_cli.commands.pipeline.AuthManager")
def test_pipeline_command_returns_aggregated_items(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_chat_item(unread=1)])
	mock_client.interview_data.return_value = {"zpData": {"interviewList": [_interview_item()]}}

	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "pipeline"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	stages = {item["stage"] for item in parsed["data"]}
	assert "reply_needed" in stages
	assert "interview" in stages


@patch("boss_agent_cli.commands.pipeline.get_platform_instance")
@patch("boss_agent_cli.commands.pipeline.AuthManager")
def test_follow_up_command_filters_actionable_items(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response(
		[
			_chat_item(unread=1),
			_chat_item(security_id="sec_old", job_id="job_old", unread=0, last_ts=1700000000000),
		]
	)
	mock_client.interview_data.return_value = {"zpData": {"interviewList": [_interview_item()]}}

	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "follow-up", "--now-ts-ms", str(1700000000000 + 5 * 24 * 3600 * 1000)])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	stages = [item["stage"] for item in parsed["data"]]
	assert "reply_needed" in stages
	assert "follow_up" in stages
	assert "interview" in stages


@patch("boss_agent_cli.commands.pipeline.get_platform_instance")
@patch("boss_agent_cli.commands.pipeline.AuthManager")
def test_pipeline_reports_friend_list_error(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = {"code": 37, "message": "stoken expired"}
	mock_client.parse_error.return_value = ("TOKEN_REFRESH_FAILED", "stoken expired")
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "pipeline"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "TOKEN_REFRESH_FAILED"
	assert parsed["error"]["message"] == "stoken expired"


def test_pipeline_and_follow_up_are_exposed_in_schema():
	runner = CliRunner()
	result = runner.invoke(cli, ["schema"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert "pipeline" in parsed["data"]["commands"]
	assert "follow-up" in parsed["data"]["commands"]
