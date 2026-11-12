"""补齐缺失的命令测试：chatmsg / mark / exchange / detail / me / show / history / interviews / login / logout"""
import json
import shlex
from pathlib import Path
from unittest.mock import patch, MagicMock

from click.testing import CliRunner
import pytest

from boss_agent_cli.display import error_contract_for_code, risk_error_message
from boss_agent_cli.main import cli

_DEFAULT_DATA_DIR = str((Path.home() / ".boss-agent").resolve())


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: response.get("zpData") if "zpData" in response else response.get("data")
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	return instance


def _friend_list_response(items):
	return {"zpData": {"result": items, "friendList": items}}


def _make_friend(name="张HR", sid="sec_001", uid=12345):
	return {
		"name": name, "securityId": sid, "uid": uid,
		"title": "HR", "brandName": "TestCo",
		"friendSource": 0, "encryptJobId": "job_001",
		"lastMsg": "你好", "lastTS": 1700000000000,
		"unreadMsgCount": 0, "relationType": 1,
		"lastMessageInfo": {"status": 2},
	}


# ── chatmsg ──────────────────────────────────────────────────────────


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_success(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	mock_client.chat_history.return_value = {
		"zpData": {
			"messages": [
				{"from": {"uid": 12345, "name": "张HR"}, "type": 1, "text": "你好", "time": 1700000000000},
				{"from": {"uid": 99, "name": "我"}, "type": 1, "text": "谢谢", "time": 1700000001000},
			],
		},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["chatmsg", "sec_001"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert len(parsed["data"]) == 2
	assert parsed["data"][0]["from"] == "张HR"
	assert parsed["data"][1]["from"] == "我"
	assert parsed["data"][0]["text"] == "你好"


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_finds_contact_on_second_page(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.side_effect = [
		_friend_list_response([_make_friend(sid="sec_other", uid=999)]),
		_friend_list_response([_make_friend()]),
	]
	mock_client.chat_history.return_value = {
		"zpData": {
			"messages": [
				{"from": {"uid": 12345, "name": "张HR"}, "type": 1, "text": "第二页找到你了", "time": 1700000000000},
			],
		},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["chatmsg", "sec_001"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"][0]["from"] == "张HR"
	assert parsed["data"][0]["text"] == "第二页找到你了"
	assert mock_client.friend_list.call_args_list[0].kwargs == {"page": 1}
	assert mock_client.friend_list.call_args_list[1].kwargs == {"page": 2}


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_not_found(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([])
	runner = CliRunner()
	result = runner.invoke(cli, ["chatmsg", "sec_nonexistent"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "CONTACT_NOT_FOUND"


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_not_found_after_second_page_keeps_contact_not_found(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.side_effect = [
		_friend_list_response([_make_friend(sid="sec_other", uid=999)]),
		{"zpData": {"result": [], "friendList": [], "hasMore": False}},
	]
	runner = CliRunner()
	result = runner.invoke(cli, ["chatmsg", "sec_nonexistent"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "CONTACT_NOT_FOUND"


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_supports_data_envelope(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = {"code": 200, "data": {"result": [_make_friend()]}}
	mock_client.chat_history.return_value = {
		"code": 200,
		"data": {
			"messages": [
				{"from": {"uid": 12345, "name": "张HR"}, "type": 1, "text": "你好", "time": 1700000000000},
			],
		},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["chatmsg", "sec_001"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"][0]["from"] == "张HR"
	assert parsed["data"][0]["text"] == "你好"


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_reports_friend_list_error(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = {"code": 37, "message": "stoken expired"}
	mock_client.is_success.side_effect = None
	mock_client.is_success.return_value = False
	mock_client.parse_error.return_value = ("TOKEN_REFRESH_FAILED", "stoken expired")
	runner = CliRunner()
	result = runner.invoke(cli, ["chatmsg", "sec_001"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "TOKEN_REFRESH_FAILED"
	assert parsed["error"]["message"] == "stoken expired"
	assert parsed["error"]["recoverable"] is True
	assert shlex.split(parsed["error"]["recovery_action"]) == ["boss", "--data-dir", _DEFAULT_DATA_DIR, "login"]


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_reports_chat_history_error(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	mock_client.chat_history.return_value = {"code": 9, "message": "too fast"}
	mock_client.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	mock_client.parse_error.return_value = ("RATE_LIMITED", "too fast")
	runner = CliRunner()
	result = runner.invoke(cli, ["chatmsg", "sec_001"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "RATE_LIMITED"
	assert parsed["error"]["message"] == "too fast"
	assert parsed["error"]["recoverable"] is True
	assert parsed["error"]["recovery_action"] == "等待后重试"


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_reports_not_supported_when_friend_list_missing(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.side_effect = NotImplementedError("friend_list is not supported")
	runner = CliRunner()
	result = runner.invoke(cli, ["chatmsg", "sec_001"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "NOT_SUPPORTED"
	assert parsed["error"]["message"] == "friend_list is not supported"
	assert parsed["error"]["recoverable"] is True
	assert parsed["error"]["recovery_action"] == "切换平台或调整命令参数后重试"


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_reports_not_supported_when_chat_history_missing(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	mock_client.chat_history.side_effect = NotImplementedError("chat_history is not supported")
	runner = CliRunner()
	result = runner.invoke(cli, ["chatmsg", "sec_001"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "NOT_SUPPORTED"
	assert parsed["error"]["message"] == "chat_history is not supported"
	assert parsed["error"]["recoverable"] is True
	assert parsed["error"]["recovery_action"] == "切换平台或调整命令参数后重试"


# ── mark ─────────────────────────────────────────────────────────────


@patch("boss_agent_cli.commands.mark.get_platform_instance")
@patch("boss_agent_cli.commands.mark.AuthManager")
def test_mark_add_label(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	mock_client.friend_label.return_value = {"code": 0, "zpData": {}}
	runner = CliRunner()
	result = runner.invoke(cli, ["mark", "sec_001", "--label", "沟通中"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert "沟通中" in parsed["data"]["message"]


@patch("boss_agent_cli.commands.mark.get_platform_instance")
@patch("boss_agent_cli.commands.mark.AuthManager")
def test_mark_finds_contact_on_second_page(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.side_effect = [
		_friend_list_response([_make_friend(sid="sec_other", uid=999)]),
		_friend_list_response([_make_friend()]),
	]
	mock_client.friend_label.return_value = {"code": 0, "zpData": {}}
	runner = CliRunner()
	result = runner.invoke(cli, ["mark", "sec_001", "--label", "沟通中"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["name"] == "张HR"
	assert mock_client.friend_list.call_args_list[0].kwargs == {"page": 1}
	assert mock_client.friend_list.call_args_list[1].kwargs == {"page": 2}


@patch("boss_agent_cli.commands.mark.get_platform_instance")
@patch("boss_agent_cli.commands.mark.AuthManager")
def test_mark_remove_label(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	mock_client.friend_label.return_value = {"code": 0, "zpData": {}}
	runner = CliRunner()
	result = runner.invoke(cli, ["mark", "sec_001", "--label", "不合适", "--remove"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["action"] == "移除"


@patch("boss_agent_cli.commands.mark.get_platform_instance")
@patch("boss_agent_cli.commands.mark.AuthManager")
def test_mark_reports_error_when_platform_rejects(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	mock_client.friend_label.return_value = {"code": 36, "message": "account risk"}
	mock_client.is_success.return_value = False
	mock_client.parse_error.return_value = ("ACCOUNT_RISK", "account risk")
	runner = CliRunner()
	result = runner.invoke(cli, ["mark", "sec_001", "--label", "沟通中"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "ACCOUNT_RISK"
	assert parsed["error"]["message"] == risk_error_message("ACCOUNT_RISK")
	assert parsed["error"]["recoverable"] is False
	assert parsed["error"]["recovery_action"] == "停止自动化访问，回到平台官网手动处理；确认目标业务页风险解除后，由用户执行 boss clean --risk-lock 并显式恢复任务，必要时联系客服"


@patch("boss_agent_cli.commands.mark.get_platform_instance")
@patch("boss_agent_cli.commands.mark.AuthManager")
def test_mark_reports_friend_list_error(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = {"code": 37, "message": "stoken expired"}
	mock_client.is_success.side_effect = None
	mock_client.is_success.return_value = False
	mock_client.parse_error.return_value = ("TOKEN_REFRESH_FAILED", "stoken expired")
	runner = CliRunner()
	result = runner.invoke(cli, ["mark", "sec_001", "--label", "沟通中"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "TOKEN_REFRESH_FAILED"
	assert parsed["error"]["message"] == "stoken expired"
	assert parsed["error"]["recoverable"] is True
	assert shlex.split(parsed["error"]["recovery_action"]) == ["boss", "--data-dir", _DEFAULT_DATA_DIR, "login"]


@patch("boss_agent_cli.commands.mark.get_platform_instance")
@patch("boss_agent_cli.commands.mark.AuthManager")
def test_mark_not_found(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([])
	runner = CliRunner()
	result = runner.invoke(cli, ["mark", "sec_none", "--label", "收藏"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "CONTACT_NOT_FOUND"


@patch("boss_agent_cli.commands.mark.get_platform_instance")
@patch("boss_agent_cli.commands.mark.AuthManager")
def test_mark_not_found_after_second_page_keeps_contact_not_found(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.side_effect = [
		_friend_list_response([_make_friend(sid="sec_other", uid=999)]),
		{"zpData": {"result": [], "friendList": [], "hasMore": False}},
	]
	runner = CliRunner()
	result = runner.invoke(cli, ["mark", "sec_none", "--label", "收藏"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "CONTACT_NOT_FOUND"


@patch("boss_agent_cli.commands.mark.get_platform_instance")
@patch("boss_agent_cli.commands.mark.AuthManager")
def test_mark_reports_not_supported_when_friend_list_missing(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.side_effect = NotImplementedError("friend_list is not supported")
	runner = CliRunner()
	result = runner.invoke(cli, ["mark", "sec_001", "--label", "沟通中"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "NOT_SUPPORTED"
	assert parsed["error"]["message"] == "friend_list is not supported"
	assert parsed["error"]["recoverable"] is True
	assert parsed["error"]["recovery_action"] == "切换平台或调整命令参数后重试"


@patch("boss_agent_cli.commands.mark.get_platform_instance")
@patch("boss_agent_cli.commands.mark.AuthManager")
def test_mark_reports_not_supported_when_friend_label_missing(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	mock_client.friend_label.side_effect = NotImplementedError("friend_label is not supported")
	runner = CliRunner()
	result = runner.invoke(cli, ["mark", "sec_001", "--label", "沟通中"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "NOT_SUPPORTED"
	assert parsed["error"]["message"] == "friend_label is not supported"
	assert parsed["error"]["recoverable"] is True
	assert parsed["error"]["recovery_action"] == "切换平台或调整命令参数后重试"


# ── exchange ─────────────────────────────────────────────────────────


@patch("boss_agent_cli.commands.exchange.get_platform_instance")
@patch("boss_agent_cli.commands.exchange.AuthManager")
def test_exchange_phone(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	mock_client.exchange_contact.return_value = {"code": 0, "zpData": {}}
	runner = CliRunner()
	result = runner.invoke(cli, ["exchange", "sec_001"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert "手机号" in parsed["data"]["message"]


@patch("boss_agent_cli.commands.exchange.get_platform_instance")
@patch("boss_agent_cli.commands.exchange.AuthManager")
def test_exchange_finds_contact_on_second_page(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.side_effect = [
		_friend_list_response([_make_friend(sid="sec_other", uid=999)]),
		_friend_list_response([_make_friend()]),
	]
	mock_client.exchange_contact.return_value = {"code": 0, "zpData": {}}
	runner = CliRunner()
	result = runner.invoke(cli, ["exchange", "sec_001"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["name"] == "张HR"
	assert mock_client.friend_list.call_args_list[0].kwargs == {"page": 1}
	assert mock_client.friend_list.call_args_list[1].kwargs == {"page": 2}


@patch("boss_agent_cli.commands.exchange.get_platform_instance")
@patch("boss_agent_cli.commands.exchange.AuthManager")
def test_exchange_not_found_after_second_page_keeps_contact_not_found(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.side_effect = [
		_friend_list_response([_make_friend(sid="sec_other", uid=999)]),
		{"zpData": {"result": [], "friendList": [], "hasMore": False}},
	]
	runner = CliRunner()
	result = runner.invoke(cli, ["exchange", "sec_none"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "CONTACT_NOT_FOUND"


@patch("boss_agent_cli.commands.exchange.get_platform_instance")
@patch("boss_agent_cli.commands.exchange.AuthManager")
def test_exchange_wechat(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	mock_client.exchange_contact.return_value = {"code": 0, "zpData": {}}
	runner = CliRunner()
	result = runner.invoke(cli, ["exchange", "sec_001", "--type", "wechat"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert "微信" in parsed["data"]["message"]


@patch("boss_agent_cli.commands.exchange.get_platform_instance")
@patch("boss_agent_cli.commands.exchange.AuthManager")
def test_exchange_reports_error_when_platform_rejects(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	mock_client.exchange_contact.return_value = {"code": 9, "message": "too fast"}
	mock_client.is_success.return_value = False
	mock_client.parse_error.return_value = ("RATE_LIMITED", "too fast")
	runner = CliRunner()
	result = runner.invoke(cli, ["exchange", "sec_001", "--type", "wechat"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "RATE_LIMITED"
	assert parsed["error"]["message"] == "too fast"
	assert parsed["error"]["recoverable"] is True
	assert parsed["error"]["recovery_action"] == "等待后重试"


@patch("boss_agent_cli.commands.exchange.get_platform_instance")
@patch("boss_agent_cli.commands.exchange.AuthManager")
def test_exchange_reports_friend_list_error(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = {"code": 36, "message": "account risk"}
	mock_client.is_success.side_effect = None
	mock_client.is_success.return_value = False
	mock_client.parse_error.return_value = ("ACCOUNT_RISK", "account risk")
	runner = CliRunner()
	result = runner.invoke(cli, ["exchange", "sec_001"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "ACCOUNT_RISK"
	assert parsed["error"]["message"] == risk_error_message("ACCOUNT_RISK")
	assert parsed["error"]["recoverable"] is False
	assert parsed["error"]["recovery_action"] == "停止自动化访问，回到平台官网手动处理；确认目标业务页风险解除后，由用户执行 boss clean --risk-lock 并显式恢复任务，必要时联系客服"


@patch("boss_agent_cli.commands.exchange.get_platform_instance")
@patch("boss_agent_cli.commands.exchange.AuthManager")
def test_exchange_reports_not_supported_when_friend_list_missing(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.side_effect = NotImplementedError("friend_list is not supported")
	runner = CliRunner()
	result = runner.invoke(cli, ["exchange", "sec_001"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "NOT_SUPPORTED"
	assert parsed["error"]["message"] == "friend_list is not supported"
	assert parsed["error"]["recoverable"] is True
	assert parsed["error"]["recovery_action"] == "切换平台或调整命令参数后重试"


@patch("boss_agent_cli.commands.exchange.get_platform_instance")
@patch("boss_agent_cli.commands.exchange.AuthManager")
def test_exchange_reports_not_supported_when_exchange_contact_missing(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	mock_client.exchange_contact.side_effect = NotImplementedError("exchange_contact is not supported")
	runner = CliRunner()
	result = runner.invoke(cli, ["exchange", "sec_001"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "NOT_SUPPORTED"
	assert parsed["error"]["message"] == "exchange_contact is not supported"
	assert parsed["error"]["recoverable"] is True
	assert parsed["error"]["recovery_action"] == "切换平台或调整命令参数后重试"


# ── detail ───────────────────────────────────────────────────────────


@patch("boss_agent_cli.commands.detail.CacheStore")
@patch("boss_agent_cli.commands.detail.get_platform_instance")
@patch("boss_agent_cli.commands.detail.AuthManager")
def test_detail_with_job_id(mock_auth_cls, mock_client_cls, mock_cache_cls):
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.job_detail.return_value = {
		"code": 0,
		"zpData": {
			"jobInfo": {
				"jobName": "Go 开发",
				"salaryDesc": "30K",
				"experienceName": "3-5年",
				"degreeName": "本科",
				"locationName": "北京",
				"postDescription": "职位描述",
				"showSkills": ["Golang", "K8s"],
				"jobLabels": ["Golang", "K8s"],
				"encryptId": "enc_001",
			},
			"bossInfo": {
				"name": "张总", "title": "CTO",
				"activeTimeDesc": "刚刚活跃",
			},
			"brandComInfo": {
				"brandName": "TestCo",
				"industryName": "互联网",
				"scaleName": "100-499",
				"stageName": "A轮",
				"labels": ["五险一金", "双休"],
			},
		},
	}
	mock_client.unwrap_data.return_value = mock_client.job_detail.return_value["zpData"]
	runner = CliRunner()
	result = runner.invoke(cli, ["detail", "sec_001", "--job-id", "enc_001"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["title"] == "Go 开发"
	assert "Golang" in parsed["data"]["skills"]


@patch("boss_agent_cli.commands.detail.CacheStore")
@patch("boss_agent_cli.commands.detail.get_platform_instance")
@patch("boss_agent_cli.commands.detail.AuthManager")
def test_detail_reports_platform_error(mock_auth_cls, mock_client_cls, mock_cache_cls):
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.job_detail.return_value = {"code": 37, "message": "stoken expired"}
	mock_client.parse_error.return_value = ("TOKEN_REFRESH_FAILED", "stoken expired")
	runner = CliRunner()
	result = runner.invoke(cli, ["detail", "sec_001", "--job-id", "enc_001"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "TOKEN_REFRESH_FAILED"
	assert parsed["error"]["message"] == "stoken expired"
	assert parsed["error"]["recoverable"] is True
	assert shlex.split(parsed["error"]["recovery_action"]) == ["boss", "--data-dir", _DEFAULT_DATA_DIR, "login"]


@patch("boss_agent_cli.commands.detail.CacheStore")
@patch("boss_agent_cli.commands.detail.get_platform_instance")
@patch("boss_agent_cli.commands.detail.AuthManager")
def test_detail_reports_not_supported_when_browser_fallback_missing(mock_auth_cls, mock_client_cls, mock_cache_cls):
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_cache.get_job_id.return_value = ""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.job_card.side_effect = NotImplementedError("job_card is not supported")
	runner = CliRunner()
	result = runner.invoke(cli, ["detail", "sec_001"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "NOT_SUPPORTED"
	assert parsed["error"]["message"] == "job_card is not supported"
	assert parsed["error"]["recoverable"] is True
	assert parsed["error"]["recovery_action"] == "切换平台或调整命令参数后重试"


@patch("boss_agent_cli.commands.detail.CacheStore")
@patch("boss_agent_cli.commands.detail.get_platform_instance")
@patch("boss_agent_cli.commands.detail.AuthManager")
def test_detail_empty_job_card_reports_not_found_with_next_steps(mock_auth_cls, mock_client_cls, mock_cache_cls):
	"""「下架」不是死胡同：恢复指引与下一步必须能直接换线索。"""
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_cache.get_job_id.return_value = ""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.job_card.return_value = {"zpData": {"jobCard": {}}}
	mock_client.unwrap_data.return_value = {"jobCard": {}}
	runner = CliRunner()
	result = runner.invoke(cli, ["detail", "sec_001"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "JOB_NOT_FOUND"
	assert parsed["error"]["recovery_action"]
	assert parsed["hints"]["next_actions"] == ["boss search <关键词>", "boss recommend"]


@patch("boss_agent_cli.commands.detail.CacheStore")
@patch("boss_agent_cli.commands.detail.get_platform_instance")
@patch("boss_agent_cli.commands.detail.AuthManager")
def test_detail_preserves_httpx_platform_error_when_browser_fallback_not_supported(mock_auth_cls, mock_client_cls, mock_cache_cls):
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_cache.get_job_id.return_value = "enc_cached_001"
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.job_detail.return_value = {"code": 9, "message": "too fast"}
	mock_client.parse_error.return_value = ("RATE_LIMITED", "too fast")
	mock_client.job_card.side_effect = NotImplementedError("job_card is not supported")
	runner = CliRunner()
	result = runner.invoke(cli, ["detail", "sec_001"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "RATE_LIMITED"
	assert parsed["error"]["message"] == "too fast"
	assert parsed["error"]["recoverable"] is True
	assert parsed["error"]["recovery_action"] == "等待后重试"


@patch("boss_agent_cli.commands.show.CacheStore")
@patch("boss_agent_cli.commands.show.get_job_by_index")
@patch("boss_agent_cli.commands.show.get_platform_instance")
@patch("boss_agent_cli.commands.show.AuthManager")
def test_show_reports_platform_error(mock_auth_cls, mock_client_cls, mock_get_job_by_index, mock_cache_cls):
	mock_get_job_by_index.return_value = {"security_id": "sec_001"}
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.job_card.return_value = {"code": 9, "message": "too fast"}
	mock_client.parse_error.return_value = ("RATE_LIMITED", "too fast")
	runner = CliRunner()
	result = runner.invoke(cli, ["show", "1"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "RATE_LIMITED"
	assert parsed["error"]["message"] == "too fast"
	assert parsed["error"]["recoverable"] is True
	assert parsed["error"]["recovery_action"] == "等待后重试"


@patch("boss_agent_cli.commands.show.CacheStore")
@patch("boss_agent_cli.commands.show.get_job_by_index")
@patch("boss_agent_cli.commands.show.get_platform_instance")
@patch("boss_agent_cli.commands.show.AuthManager")
def test_show_reports_not_supported_when_job_card_missing(mock_auth_cls, mock_client_cls, mock_get_job_by_index, mock_cache_cls):
	mock_get_job_by_index.return_value = {"security_id": "sec_001"}
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.job_card.side_effect = NotImplementedError("job_card is not supported")
	runner = CliRunner()
	result = runner.invoke(cli, ["show", "1"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "NOT_SUPPORTED"
	assert parsed["error"]["message"] == "job_card is not supported"
	assert parsed["error"]["recoverable"] is True
	assert parsed["error"]["recovery_action"] == "切换平台或调整命令参数后重试"


# ── me ───────────────────────────────────────────────────────────────


@patch("boss_agent_cli.commands.me.get_platform_instance")
@patch("boss_agent_cli.commands.me.AuthManager")
def test_me_basic(mock_auth_cls, mock_client_cls, tmp_path):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.user_info.return_value = {"code": 0, "zpData": {"name": "测试用户", "userId": 123}}
	mock_client.resume_baseinfo.return_value = {"code": 0, "zpData": {"degree": "本科"}}
	mock_client.resume_expect.return_value = {"code": 0, "zpData": {"city": "北京"}}
	mock_client.deliver_list.return_value = {"code": 0, "zpData": {"list": [], "hasMore": True, "page": 2}}
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "me"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert "测试用户" in str(parsed["data"])
	# 查全部 section 时顶层混有四段载荷，不能把投递列表的页码说成整体页码。
	assert parsed["pagination"] is None
	assert not any("--deliver-page" in action for action in parsed["hints"]["next_actions"])
	assert "operator_actions" not in parsed["hints"]
	mock_client.deliver_list.assert_called_once_with(page=1)


@pytest.mark.parametrize(
	("platform_data", "expected", "page", "total"),
	[
		({"list": [{"jobName": "后端工程师"}], "hasMore": True}, True, 2, 1),
		({"list": [{"jobName": "后端工程师"}], "hasMore": False}, False, 2, 1),
		({"list": [{"jobName": "后端工程师"}]}, None, 2, 1),
		({"list": [], "hasMore": None}, None, 2, 0),
		({"list": [], "hasMore": "true", "page": "4"}, None, 2, 0),
		({"list": [], "hasMore": 1, "page": True}, None, 2, 0),
		({"list": [], "hasMore": True, "page": 4}, True, 4, 0),
		({"list": "unexpected", "hasMore": False, "page": 0}, False, 2, None),
		(["unexpected"], None, 2, None),
	],
	ids=["more", "last", "missing", "null", "string", "integer", "empty-with-more", "invalid-list", "non-mapping"],
)
@patch("boss_agent_cli.commands.me.get_platform_instance")
@patch("boss_agent_cli.commands.me.AuthManager")
def test_me_deliver_pagination_evidence(mock_auth_cls, mock_client_cls, tmp_path, platform_data, expected, page, total):
	from boss_agent_cli.services.pagination import UNKNOWN_HAS_MORE_OPERATOR_ACTION

	mock_client = _ctx_mock(mock_client_cls)
	mock_client.deliver_list.return_value = {"code": 0, "zpData": platform_data}
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "me", "--section", "deliver", "--deliver-page", "2"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"] == {"deliver": platform_data}
	assert parsed["pagination"] == {
		"page": page,
		"has_more": expected,
		"has_more_source": "platform_hasMore" if expected is not None else "unknown",
		"total": total,
		"total_kind": "returned_page_count",
	}
	next_pages = [action for action in parsed["hints"]["next_actions"] if "--deliver-page" in action]
	if expected is True:
		assert next_pages == [f"boss me --section deliver --deliver-page {page + 1}"]
	else:
		assert next_pages == []
	if expected is None:
		assert parsed["hints"]["operator_actions"] == [UNKNOWN_HAS_MORE_OPERATOR_ACTION]
	else:
		assert "operator_actions" not in parsed["hints"]
	mock_client.deliver_list.assert_called_once_with(page=2)
	mock_client.user_info.assert_not_called()
	mock_client.resume_baseinfo.assert_not_called()
	mock_client.resume_expect.assert_not_called()


@patch("boss_agent_cli.commands.me.get_platform_instance")
@patch("boss_agent_cli.commands.me.AuthManager")
def test_me_deliver_next_action_is_executable(mock_auth_cls, mock_client_cls, tmp_path):
	"""下一页指引要能直接交给 CLI 执行，并沿用调用方当前的数据目录。"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.deliver_list.side_effect = [
		{"code": 0, "zpData": {"list": [], "page": 4, "hasMore": True}},
		{"code": 0, "zpData": {"list": [], "page": 5, "hasMore": False}},
	]
	runner = CliRunner()
	first = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "me", "--section", "deliver", "--deliver-page", "4"])
	assert first.exit_code == 0
	action = next(action for action in json.loads(first.output)["hints"]["next_actions"] if "--deliver-page" in action)
	second = runner.invoke(cli, ["--data-dir", str(tmp_path), *shlex.split(action)[1:]])
	assert second.exit_code == 0
	assert json.loads(second.output)["pagination"]["page"] == 5
	assert [call.kwargs for call in mock_client.deliver_list.call_args_list] == [{"page": 4}, {"page": 5}]
	assert [call.args[0].resolve() for call in mock_auth_cls.call_args_list] == [tmp_path.resolve(), tmp_path.resolve()]


@pytest.mark.parametrize(("code", "recoverable"), [("AUTH_EXPIRED", True), ("ACCOUNT_RISK", False)])
@patch("boss_agent_cli.commands.me.get_platform_instance")
@patch("boss_agent_cli.commands.me.AuthManager")
def test_me_deliver_failure_has_no_pagination(mock_auth_cls, mock_client_cls, tmp_path, code, recoverable):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.deliver_list.return_value = {"code": 32, "message": "fixture failure"}
	mock_client.parse_error.return_value = (code, "fixture failure")
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "me", "--section", "deliver"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["pagination"] is None
	assert parsed["error"]["code"] == code
	assert parsed["error"]["recoverable"] is recoverable
	if code in {"AUTH_REQUIRED", "AUTH_EXPIRED", "TOKEN_REFRESH_FAILED"}:
		# 认证错误在运行目录内重新登录，不再回显登记表的通用示例。
		assert shlex.split(parsed["error"]["recovery_action"]) == [
			"boss", "--data-dir", str(tmp_path.resolve()), "login",
		]
	else:
		assert parsed["error"]["recovery_action"] == error_contract_for_code(code)[1]
	mock_client.deliver_list.assert_called_once_with(page=1)
	mock_client.unwrap_data.assert_not_called()


@patch("boss_agent_cli.commands.me.get_platform_instance")
@patch("boss_agent_cli.commands.me.AuthManager")
def test_me_user_section_supports_data_envelope(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.user_info.return_value = {"code": 200, "data": {"name": "示例用户", "email": "z@demo.dev"}}
	runner = CliRunner()
	result = runner.invoke(cli, ["me", "--section", "user"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["user"]["name"] == "示例用户"
	assert parsed["pagination"] is None
	mock_client.deliver_list.assert_not_called()


@patch("boss_agent_cli.commands.me.get_platform_instance")
@patch("boss_agent_cli.commands.me.AuthManager")
def test_me_reports_user_info_error(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.user_info.return_value = {"code": 37, "message": "stoken expired"}
	mock_client.parse_error.return_value = ("TOKEN_REFRESH_FAILED", "stoken expired")
	runner = CliRunner()
	result = runner.invoke(cli, ["me", "--section", "user"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "TOKEN_REFRESH_FAILED"
	assert parsed["error"]["message"] == "stoken expired"
	assert parsed["error"]["recoverable"] is True
	assert shlex.split(parsed["error"]["recovery_action"]) == ["boss", "--data-dir", _DEFAULT_DATA_DIR, "login"]


@patch("boss_agent_cli.commands.me.get_platform_instance")
@patch("boss_agent_cli.commands.me.AuthManager")
@pytest.mark.parametrize("error_cls,code", [
	("AccountRiskError", "ACCOUNT_RISK"),
	("EnvironmentRiskError", "ENVIRONMENT_RISK"),
	("EnvironmentRiskLockedError", "ENVIRONMENT_RISK_LOCKED"),
])
def test_me_risk_uses_stop_contract(mock_auth_cls, mock_client_cls, error_cls, code):
	"""me 没有 @handle_auth_errors：风控必须显式按停止契约输出，不能变成 NETWORK_ERROR。"""
	from boss_agent_cli.api.client import AccountRiskError, EnvironmentRiskError, EnvironmentRiskLockedError

	errors = {
		"AccountRiskError": AccountRiskError,
		"EnvironmentRiskError": EnvironmentRiskError,
		"EnvironmentRiskLockedError": EnvironmentRiskLockedError,
	}
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.user_info.side_effect = errors[error_cls]("private-token")
	runner = CliRunner()
	result = runner.invoke(cli, ["me", "--section", "user"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == code
	assert parsed["error"]["recoverable"] is False
	assert parsed["error"]["recovery_action"] == error_contract_for_code(code)[1]
	assert parsed["hints"]["next_actions"]
	assert "private-token" not in result.output


@patch("boss_agent_cli.commands.me.get_platform_instance")
@patch("boss_agent_cli.commands.me.AuthManager")
def test_me_budget_stop_uses_contract(mock_auth_cls, mock_client_cls):
	"""me 没有 @handle_auth_errors：预算/时限停止不能降级成可重试的 NETWORK_ERROR。"""
	from boss_agent_cli.api.request_budget import RequestBudgetExceeded

	mock_client = _ctx_mock(mock_client_cls)
	mock_client.user_info.side_effect = RequestBudgetExceeded("fixture budget stop")
	runner = CliRunner()
	result = runner.invoke(cli, ["me", "--section", "user"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "REQUEST_BUDGET_EXHAUSTED"
	assert (parsed["error"]["recoverable"], parsed["error"]["recovery_action"]) == error_contract_for_code(
		"REQUEST_BUDGET_EXHAUSTED"
	)


@patch("boss_agent_cli.commands.me.get_platform_instance")
@patch("boss_agent_cli.commands.me.AuthManager")
def test_me_resume_reports_not_supported(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.resume_baseinfo.side_effect = NotImplementedError("resume_baseinfo is not supported")
	runner = CliRunner()
	result = runner.invoke(cli, ["me", "--section", "resume"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "NOT_SUPPORTED"
	assert parsed["error"]["message"] == "resume_baseinfo is not supported"


@patch("boss_agent_cli.commands.me.get_platform_instance")
@patch("boss_agent_cli.commands.me.AuthManager")
def test_me_expect_reports_not_supported(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.resume_expect.side_effect = NotImplementedError("resume_expect is not supported")
	runner = CliRunner()
	result = runner.invoke(cli, ["me", "--section", "expect"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "NOT_SUPPORTED"
	assert parsed["error"]["message"] == "resume_expect is not supported"
	assert parsed["error"]["recoverable"] is True
	assert parsed["error"]["recovery_action"] == "切换平台或调整命令参数后重试"


@patch("boss_agent_cli.commands.me.get_platform_instance")
@patch("boss_agent_cli.commands.me.AuthManager")
def test_me_deliver_reports_not_supported(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.deliver_list.side_effect = NotImplementedError("deliver_list is not supported")
	runner = CliRunner()
	result = runner.invoke(cli, ["me", "--section", "deliver"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "NOT_SUPPORTED"
	assert parsed["error"]["message"] == "deliver_list is not supported"


@patch("boss_agent_cli.commands.me.get_platform_instance")
@patch("boss_agent_cli.commands.me.AuthManager")
def test_me_default_sequence_stops_on_expect_not_supported(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.user_info.return_value = {"code": 0, "zpData": {"name": "测试用户"}}
	mock_client.resume_baseinfo.return_value = {"code": 0, "zpData": {"degree": "本科"}}
	mock_client.resume_expect.side_effect = NotImplementedError("resume_expect is not supported")
	runner = CliRunner()
	result = runner.invoke(cli, ["me"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "NOT_SUPPORTED"
	assert parsed["error"]["message"] == "resume_expect is not supported"
	assert parsed["error"]["recoverable"] is True
	assert parsed["error"]["recovery_action"] == "切换平台或调整命令参数后重试"
	mock_client.user_info.assert_called_once_with()
	mock_client.resume_baseinfo.assert_called_once_with()
	mock_client.resume_expect.assert_called_once_with()
	mock_client.deliver_list.assert_not_called()


# ── history ──────────────────────────────────────────────────────────


@patch("boss_agent_cli.commands.history.get_platform_instance")
@patch("boss_agent_cli.commands.history.AuthManager")
def test_history_success(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.job_history.return_value = {
		"code": 0,
		"zpData": {
			"hasMore": False,
			"jobList": [
				{
					"encryptJobId": "j1", "jobName": "测试岗位",
					"brandName": "公司A", "salaryDesc": "20K",
					"cityName": "北京", "jobExperience": "3-5年",
					"jobDegree": "本科", "bossName": "HR",
					"bossTitle": "招聘", "bossOnline": True,
					"securityId": "sec_h1",
				},
			],
		},
	}
	mock_client.unwrap_data.return_value = mock_client.job_history.return_value["zpData"]
	runner = CliRunner()
	result = runner.invoke(cli, ["history"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True


@patch("boss_agent_cli.commands.history.get_platform_instance")
@patch("boss_agent_cli.commands.history.AuthManager")
def test_history_hints_omit_next_page_when_no_more(mock_auth_cls, mock_client_cls):
	"""hasMore=false 时不能建议 --page N+1：Agent 照着执行只会拿到空页。"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.job_history.return_value = {"code": 0, "zpData": {"hasMore": False, "jobList": []}}
	runner = CliRunner()
	result = runner.invoke(cli, ["history", "--page", "3"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert all("--page" not in action for action in parsed["hints"]["next_actions"])


@patch("boss_agent_cli.commands.history.get_platform_instance")
@patch("boss_agent_cli.commands.history.AuthManager")
def test_history_hints_include_next_page_when_has_more(mock_auth_cls, mock_client_cls):
	"""hasMore=true 时保留下一页提示，页码基于当前页 +1。"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.job_history.return_value = {"code": 0, "zpData": {"hasMore": True, "jobList": []}}
	runner = CliRunner()
	result = runner.invoke(cli, ["history", "--page", "2"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert any("history --page 3" in action for action in parsed["hints"]["next_actions"])


@pytest.mark.parametrize(
	("platform_has_more", "expected"),
	[(True, True), (False, False), ("missing", None)],
)
@patch("boss_agent_cli.commands.history.get_platform_instance")
@patch("boss_agent_cli.commands.history.AuthManager")
def test_history_pagination_reports_platform_evidence(mock_auth_cls, mock_client_cls, platform_has_more, expected):
	"""缺失 hasMore 时返回 null，不能伪造成 false；三态都必须记录来源。"""
	mock_client = _ctx_mock(mock_client_cls)
	zp_data = {"jobList": []}
	if platform_has_more != "missing":
		zp_data["hasMore"] = platform_has_more
	mock_client.job_history.return_value = {"code": 0, "zpData": zp_data}
	result = CliRunner().invoke(cli, ["--json", "history", "--page", "2"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	pagination = parsed["pagination"]
	assert pagination["page"] == 2
	assert pagination["has_more"] is expected
	assert pagination["has_more_source"] == ("platform_hasMore" if expected is not None else "unknown")
	assert pagination["total"] == 0
	assert pagination["total_kind"] == "returned_page_count"
	next_pages = [action for action in parsed["hints"]["next_actions"] if "history --page" in action]
	if expected is True:
		assert next_pages == ["boss history --page 3"]
	else:
		assert next_pages == []
	if expected is None:
		assert parsed["hints"]["operator_actions"] == [
			"平台未提供可靠的下一页标记，不能仅凭本页数量判断是否还有记录"
		]
	else:
		assert "operator_actions" not in parsed["hints"]


@patch("boss_agent_cli.commands.history.get_platform_instance")
@patch("boss_agent_cli.commands.history.AuthManager")
def test_history_uses_client_context_manager(mock_auth_cls, mock_client_cls):
	instance = mock_client_cls.return_value
	instance.__enter__ = MagicMock(return_value=instance)
	instance.__exit__ = MagicMock(return_value=None)
	instance.job_history.return_value = {"code": 0, "zpData": {"hasMore": False, "jobList": []}}
	instance.unwrap_data.return_value = instance.job_history.return_value["zpData"]
	runner = CliRunner()
	result = runner.invoke(cli, ["history"])
	assert result.exit_code == 0
	instance.__enter__.assert_called_once()
	instance.__exit__.assert_called_once()


@patch("boss_agent_cli.commands.history.get_platform_instance")
@patch("boss_agent_cli.commands.history.AuthManager")
def test_history_reports_platform_error(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.job_history.return_value = {"code": 9, "message": "too fast"}
	mock_client.parse_error.return_value = ("RATE_LIMITED", "too fast")
	runner = CliRunner()
	result = runner.invoke(cli, ["history"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "RATE_LIMITED"
	assert parsed["error"]["message"] == "too fast"
	assert parsed["error"]["recoverable"] is True
	assert parsed["error"]["recovery_action"] == "等待后重试"


@patch("boss_agent_cli.commands.history.get_platform_instance")
@patch("boss_agent_cli.commands.history.AuthManager")
def test_history_reports_not_supported_when_job_history_missing(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.job_history.side_effect = NotImplementedError("job_history is not supported")
	runner = CliRunner()
	result = runner.invoke(cli, ["history"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "NOT_SUPPORTED"
	assert parsed["error"]["message"] == "job_history is not supported"
	assert parsed["error"]["recoverable"] is True
	assert parsed["error"]["recovery_action"] == "切换平台或调整命令参数后重试"


# ── interviews ───────────────────────────────────────────────────────


@patch("boss_agent_cli.commands.interviews.get_platform_instance")
@patch("boss_agent_cli.commands.interviews.AuthManager")
def test_interviews_success(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.interview_data.return_value = {
		"code": 0,
		"zpData": {"interviewList": []},
	}
	mock_client.unwrap_data.return_value = mock_client.interview_data.return_value["zpData"]
	runner = CliRunner()
	result = runner.invoke(cli, ["interviews"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["hints"]["next_actions"][0] == "boss search <query>"


@patch("boss_agent_cli.commands.interviews.get_platform_instance")
@patch("boss_agent_cli.commands.interviews.AuthManager")
def test_interviews_supports_data_envelope(mock_auth_cls, mock_client_cls):
	mock_auth_cls.return_value.check_status.return_value = {"cookies": {"wt2": "x"}}
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.interview_data.return_value = {
		"code": 200,
		"data": {"interviewList": [{"jobName": "测试岗位"}]},
	}
	mock_client.unwrap_data.return_value = mock_client.interview_data.return_value["data"]
	runner = CliRunner()
	result = runner.invoke(cli, ["interviews"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"][0]["jobName"] == "测试岗位"
	assert parsed["hints"]["next_actions"][0] == "boss search <query>"


@patch("boss_agent_cli.commands.interviews.get_platform_instance")
@patch("boss_agent_cli.commands.interviews.AuthManager")
def test_interviews_reports_platform_error(mock_auth_cls, mock_client_cls):
	mock_auth_cls.return_value.check_status.return_value = {"cookies": {"wt2": "x"}}
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.interview_data.return_value = {"code": 36, "message": "account risk"}
	mock_client.parse_error.return_value = ("ACCOUNT_RISK", "account risk")
	runner = CliRunner()
	result = runner.invoke(cli, ["interviews"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "ACCOUNT_RISK"
	assert parsed["error"]["message"] == risk_error_message("ACCOUNT_RISK")
	assert parsed["error"]["recoverable"] is False
	assert parsed["error"]["recovery_action"] == "停止自动化访问，回到平台官网手动处理；确认目标业务页风险解除后，由用户执行 boss clean --risk-lock 并显式恢复任务，必要时联系客服"


@patch("boss_agent_cli.commands.interviews.get_platform_instance")
@patch("boss_agent_cli.commands.interviews.AuthManager")
def test_interviews_reports_not_supported_when_interview_data_missing(mock_auth_cls, mock_client_cls):
	mock_auth_cls.return_value.check_status.return_value = {"cookies": {"wt2": "x"}}
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.interview_data.side_effect = NotImplementedError("interview_data is not supported")
	runner = CliRunner()
	result = runner.invoke(cli, ["interviews"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "NOT_SUPPORTED"
	assert parsed["error"]["message"] == "interview_data is not supported"
	assert parsed["error"]["recoverable"] is True
	assert parsed["error"]["recovery_action"] == "切换平台或调整命令参数后重试"


@patch("boss_agent_cli.commands.interviews.get_platform_instance")
@patch("boss_agent_cli.commands.interviews.AuthManager")
def test_interviews_real_data_has_no_stub_hint(mock_auth_cls, mock_client_cls):
	"""面试邀请端点已接通（zhipin），命令不应再输出占位 capability/note。"""
	mock_auth_cls.return_value.check_status.return_value = {"cookies": {"wt2": "x"}}
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.interview_data.return_value = {
		"code": 200,
		"data": {"interviewList": [{"jobName": "测试岗位"}]},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["interviews"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert "capability" not in parsed["hints"]
	assert "note" not in parsed["hints"]


@patch("boss_agent_cli.commands.detail.CacheStore")
@patch("boss_agent_cli.commands.detail.get_platform_instance")
@patch("boss_agent_cli.commands.detail.AuthManager")
def test_detail_uses_client_context_manager(mock_auth_cls, mock_client_cls, mock_cache_cls):
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	instance = mock_client_cls.return_value
	instance.__enter__ = MagicMock(return_value=instance)
	instance.__exit__ = MagicMock(return_value=None)
	instance.job_detail.return_value = {
		"code": 0,
		"zpData": {
			"jobInfo": {"jobName": "Go 开发", "salaryDesc": "30K", "experienceName": "3-5年", "degreeName": "本科"},
			"bossInfo": {"name": "张总", "title": "CTO"},
			"brandComInfo": {"brandName": "TestCo"},
		},
	}
	instance.unwrap_data.return_value = instance.job_detail.return_value["zpData"]
	runner = CliRunner()
	result = runner.invoke(cli, ["detail", "sec_001", "--job-id", "enc_001"])
	assert result.exit_code == 0
	instance.__enter__.assert_called_once()
	instance.__exit__.assert_called_once()


@patch("boss_agent_cli.commands.detail.CacheStore")
@patch("boss_agent_cli.commands.detail.get_platform_instance")
@patch("boss_agent_cli.commands.detail.AuthManager")
def test_detail_httpx_fallback_to_browser_on_auth_error(mock_auth_cls, mock_client_cls, mock_cache_cls):
	"""httpx 快速通道因 AuthError 失败时，应降级到浏览器通道而非直接报错"""
	from boss_agent_cli.api.client import AuthError
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_cache.get_job_id.return_value = ""
	mock_client = _ctx_mock(mock_client_cls)
	# httpx 通道抛 AuthError（stoken 过期刷新失败）
	mock_client.job_detail.side_effect = AuthError("stoken refresh failed")
	# 浏览器通道返回正常数据
	mock_client.job_card.return_value = {
		"zpData": {
			"jobCard": {
				"encryptJobId": "enc_001",
				"jobName": "Go 开发",
				"brandName": "TestCo",
				"salaryDesc": "30K",
				"cityName": "北京",
				"experienceName": "3-5年",
				"degreeName": "本科",
				"postDescription": "JD 描述",
				"bossName": "张总",
				"bossTitle": "CTO",
				"activeTimeDesc": "刚刚活跃",
			}
		}
	}
	def unwrap_data_side_effect(payload):
		return payload.get("zpData")
	mock_client.unwrap_data.side_effect = unwrap_data_side_effect
	runner = CliRunner()
	result = runner.invoke(cli, ["detail", "sec_001", "--job-id", "enc_001"])
	assert result.exit_code == 0, f"exit_code={result.exit_code}, output={result.output}"
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["title"] == "Go 开发"
	# 确认 httpx 被调用后又降级到了浏览器通道
	mock_client.job_detail.assert_called_once()
	mock_client.job_card.assert_called_once()


@patch("boss_agent_cli.commands.detail.CacheStore")
@patch("boss_agent_cli.commands.detail.get_platform_instance")
@patch("boss_agent_cli.commands.detail.AuthManager")
def test_detail_httpx_fallback_to_browser_on_none(mock_auth_cls, mock_client_cls, mock_cache_cls):
	"""httpx 快速通道返回空 jobInfo 时，应降级到浏览器通道"""
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_cache.get_job_id.return_value = ""
	mock_client = _ctx_mock(mock_client_cls)
	# httpx 返回无 jobInfo（解析后 result=None）
	mock_client.job_detail.return_value = {"zpData": {"jobInfo": {}}}
	# 浏览器通道返回正常数据
	mock_client.job_card.return_value = {
		"zpData": {
			"jobCard": {
				"encryptJobId": "enc_001",
				"jobName": "Go 开发",
				"brandName": "FallbackCo",
				"salaryDesc": "25K",
				"cityName": "深圳",
				"experienceName": "1-3年",
				"degreeName": "大专",
				"postDescription": "降级 JD",
				"bossName": "李总",
				"bossTitle": "HR",
				"activeTimeDesc": "在线",
			}
		}
	}
	def unwrap_data_side_effect(payload):
		return payload.get("zpData")
	mock_client.unwrap_data.side_effect = unwrap_data_side_effect
	runner = CliRunner()
	result = runner.invoke(cli, ["detail", "sec_001", "--job-id", "enc_001"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["company"] == "FallbackCo"


@patch("boss_agent_cli.commands.show.CacheStore")
@patch("boss_agent_cli.commands.show.get_job_by_index")
@patch("boss_agent_cli.commands.show.get_platform_instance")
@patch("boss_agent_cli.commands.show.AuthManager")
def test_show_uses_client_context_manager(mock_auth_cls, mock_client_cls, mock_get_job_by_index, mock_cache_cls):
	mock_get_job_by_index.return_value = {"security_id": "sec_001"}
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	instance = mock_client_cls.return_value
	instance.__enter__ = MagicMock(return_value=instance)
	instance.__exit__ = MagicMock(return_value=None)
	instance.job_card.return_value = {
		"zpData": {
			"jobCard": {
				"encryptJobId": "enc_001",
				"jobName": "Go 开发",
				"brandName": "TestCo",
				"salaryDesc": "30K",
				"cityName": "北京",
				"experienceName": "3-5年",
				"degreeName": "本科",
			},
		},
	}
	instance.unwrap_data.return_value = instance.job_card.return_value["zpData"]
	runner = CliRunner()
	result = runner.invoke(cli, ["show", "1"])
	assert result.exit_code == 0
	instance.__enter__.assert_called_once()
	instance.__exit__.assert_called_once()


@patch("boss_agent_cli.commands.interviews.get_platform_instance")
@patch("boss_agent_cli.commands.interviews.AuthManager")
def test_interviews_uses_client_context_manager(mock_auth_cls, mock_client_cls):
	mock_auth_cls.return_value.check_status.return_value = {"cookies": {"wt2": "ok"}}
	instance = mock_client_cls.return_value
	instance.__enter__ = MagicMock(return_value=instance)
	instance.__exit__ = MagicMock(return_value=None)
	instance.interview_data.return_value = {"code": 0, "zpData": {"interviewList": []}}
	instance.unwrap_data.return_value = instance.interview_data.return_value["zpData"]
	runner = CliRunner()
	result = runner.invoke(cli, ["interviews"])
	assert result.exit_code == 0
	instance.__enter__.assert_called_once()
	instance.__exit__.assert_called_once()


# ── show ─────────────────────────────────────────────────────────────


def test_show_no_cache(tmp_path):
	"""没有缓存时 show 应返回错误"""
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "show", "1"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False


# ── login / logout ───────────────────────────────────────────────────


@patch("boss_agent_cli.commands.login.AuthManager")
def test_login_auth_required(mock_auth_cls):
	"""login 命令基本调用（mock 不实际启动浏览器）"""
	from boss_agent_cli.auth.manager import AuthRequired
	mock_auth_cls.return_value.login.side_effect = AuthRequired("test")
	runner = CliRunner()
	result = runner.invoke(cli, ["login"])
	# login 失败应返回错误
	assert result.exit_code != 0


@patch("boss_agent_cli.commands.logout.AuthManager")
def test_logout_success(mock_auth_cls):
	mock_auth_cls.return_value.logout.return_value = None
	runner = CliRunner()
	result = runner.invoke(cli, ["logout"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True


# ── schema 包含新命令 ────────────────────────────────────────────────


def test_schema_includes_new_commands_v2():
	runner = CliRunner()
	result = runner.invoke(cli, ["schema"])
	parsed = json.loads(result.output)
	commands = parsed["data"]["commands"]
	assert "chatmsg" in commands
	assert "mark" in commands
	assert "exchange" in commands


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_missing_contact_recovery_points_to_chat(mock_auth_cls, mock_client_cls):
	"""联系人不在沟通列表里不是「职位下架」：错误码与恢复动作都要指向会话列表。"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([])

	result = CliRunner().invoke(cli, ["chatmsg", "sec_nonexistent"])

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "CONTACT_NOT_FOUND"
	assert parsed["error"]["recovery_action"] == "boss chat"
	assert parsed["hints"]["next_actions"] == ["boss chat"]
