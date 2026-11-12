import json
import shlex
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
	uid=12345,
):
	return {
		"name": "张HR",
		"securityId": security_id,
		"uid": uid,
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
def test_pipeline_command_returns_aggregated_items(mock_auth_cls, mock_client_cls, legacy_args):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_chat_item(unread=1)])
	mock_client.interview_data.return_value = {"zpData": {"interviewList": [_interview_item()]}}

	runner = CliRunner()
	result = runner.invoke(cli, ["--json", *legacy_args, "pipeline"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	stages = {item["stage"] for item in parsed["data"]}
	assert "reply_needed" in stages
	assert "interview" in stages


@patch("boss_agent_cli.commands.pipeline.get_platform_instance")
@patch("boss_agent_cli.commands.pipeline.AuthManager")
def test_pipeline_command_reports_chat_page_truncation(mock_auth_cls, mock_client_cls, legacy_args):
	"""沟通列表翻满上限时信封必须带 truncated，否则 Agent 把前缀当全量（Issue #885）。"""
	mock_client = _ctx_mock(mock_client_cls)

	def endless_friend_list(*, page=1):
		# 每页 uid 都不同，避免重复页守卫提前结束。
		item = _chat_item(security_id=f"sec_{page}", job_id=f"job_{page}", uid=10000 + page)
		return {"zpData": {"result": [item], "hasMore": True}}

	mock_client.friend_list.side_effect = endless_friend_list
	mock_client.interview_data.return_value = {"zpData": {"interviewList": []}}

	runner = CliRunner()
	result = runner.invoke(cli, ["--json", *legacy_args, "pipeline"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["pagination"]["pages_scanned"] == 50
	assert parsed["pagination"]["truncated"] is True
	assert len(parsed["data"]) == 50


@patch("boss_agent_cli.commands.pipeline.get_platform_instance")
@patch("boss_agent_cli.commands.pipeline.AuthManager")
def test_pipeline_command_reports_complete_scan_as_not_truncated(mock_auth_cls, mock_client_cls, legacy_args):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = {"zpData": {"result": [_chat_item(unread=1)], "hasMore": False}}
	mock_client.interview_data.return_value = {"zpData": {"interviewList": []}}

	runner = CliRunner()
	result = runner.invoke(cli, ["--json", *legacy_args, "pipeline"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["pagination"]["truncated"] is False
	assert parsed["pagination"]["pages_scanned"] == 1


@patch("boss_agent_cli.commands.pipeline.get_platform_instance")
@patch("boss_agent_cli.commands.pipeline.AuthManager")
def test_pipeline_hints_preserve_data_dir(mock_auth_cls, mock_client_cls, tmp_path):
	"""后继命令必须保留自定义数据目录，否则 Agent 会读取另一份本地库（Issue #913）。"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_chat_item(unread=1)])
	mock_client.interview_data.return_value = {"zpData": {"interviewList": []}}

	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "pipeline"])
	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	expected = ["boss", "--data-dir", str(tmp_path.resolve())]
	commands = parsed["hints"]["next_actions"]
	assert commands
	for command in commands:
		assert shlex.split(command)[:3] == [*expected]


@patch("boss_agent_cli.commands.pipeline.get_platform_instance")
@patch("boss_agent_cli.commands.pipeline.AuthManager")
def test_follow_up_hints_preserve_data_dir(mock_auth_cls, mock_client_cls, tmp_path):
	"""follow-up 的 chat / mark 提示同样落在原数据目录（Issue #913）。"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_chat_item(unread=1)])
	mock_client.interview_data.return_value = {"zpData": {"interviewList": []}}

	result = CliRunner().invoke(
		cli, ["--json", "--data-dir", str(tmp_path), "follow-up", "--now-ts-ms", "1800000000000"]
	)
	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	for command in parsed["hints"]["next_actions"]:
		assert shlex.split(command)[:3] == ["boss", "--data-dir", str(tmp_path.resolve())]


@patch("boss_agent_cli.commands.pipeline.get_platform_instance")
@patch("boss_agent_cli.commands.pipeline.AuthManager")
def test_follow_up_command_filters_actionable_items(mock_auth_cls, mock_client_cls, legacy_args):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response(
		[
			_chat_item(unread=1),
			_chat_item(security_id="sec_old", job_id="job_old", unread=0, last_ts=1700000000000),
		]
	)
	mock_client.interview_data.return_value = {"zpData": {"interviewList": [_interview_item()]}}

	runner = CliRunner()
	result = runner.invoke(cli, ["--json", *legacy_args, "follow-up", "--now-ts-ms", str(1700000000000 + 5 * 24 * 3600 * 1000)])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	stages = [item["stage"] for item in parsed["data"]]
	assert "reply_needed" in stages
	assert "follow_up" in stages
	assert "interview" in stages


@patch("boss_agent_cli.commands.pipeline.get_platform_instance")
@patch("boss_agent_cli.commands.pipeline.AuthManager")
def test_follow_up_command_aggregates_items_from_second_page(mock_auth_cls, mock_client_cls, legacy_args):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.side_effect = [
		_friend_list_response([_chat_item(unread=1, security_id="sec_001", job_id="job_001")]),
		_friend_list_response([_chat_item(unread=0, security_id="sec_page2", job_id="job_page2", last_ts=1700000000000, uid=67890)]),
		_friend_list_response([]),
	]
	mock_client.interview_data.return_value = {"zpData": {"interviewList": []}}

	runner = CliRunner()
	result = runner.invoke(cli, ["--json", *legacy_args, "follow-up", "--now-ts-ms", str(1700000000000 + 5 * 24 * 3600 * 1000)])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	security_ids = {item["security_id"] for item in parsed["data"]}
	assert "sec_001" in security_ids
	assert "sec_page2" in security_ids
	assert mock_client.friend_list.call_args_list[0].kwargs == {"page": 1}
	assert mock_client.friend_list.call_args_list[1].kwargs == {"page": 2}
	assert mock_client.friend_list.call_args_list[2].kwargs == {"page": 3}


@patch("boss_agent_cli.commands.pipeline.get_platform_instance")
@patch("boss_agent_cli.commands.pipeline.AuthManager")
def test_pipeline_reports_friend_list_error(mock_auth_cls, mock_client_cls, legacy_args):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = {"code": 37, "message": "stoken expired"}
	mock_client.parse_error.return_value = ("TOKEN_REFRESH_FAILED", "stoken expired")
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", *legacy_args, "pipeline"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "TOKEN_REFRESH_FAILED"
	assert parsed["error"]["message"] == "stoken expired"


@patch("boss_agent_cli.commands.pipeline.get_platform_instance")
@patch("boss_agent_cli.commands.pipeline.AuthManager")
def test_pipeline_reports_second_page_friend_list_error(mock_auth_cls, mock_client_cls, legacy_args):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.side_effect = [
		_friend_list_response([_chat_item(unread=1)]),
		{"code": 37, "message": "stoken expired"},
	]
	mock_client.parse_error.return_value = ("TOKEN_REFRESH_FAILED", "stoken expired")
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", *legacy_args, "pipeline"])
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
