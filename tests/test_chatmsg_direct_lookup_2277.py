"""Issue #2277：已知 uid + security_id 时 chatmsg 直接读消息，不再从头扫描沟通列表。

``find_friend`` 每次从第 1 页开始遍历沟通列表，联系人越靠后、重复读取越多，
请求开销越大。新增 ``--friend-uid``：位置参数按本次请求的 security_id 使用，
直接调用消息接口；失败按实际错误返回，不隐式退回全列表扫描。
"""

import json
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.mcp_args import _build_args


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: response.get("zpData") if "zpData" in response else response.get("data")
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	return instance


def _messages_page(*, has_more: bool = False) -> dict:
	return {
		"zpData": {
			"messages": [
				{"mid": 1, "from": {"uid": 10001, "name": "联系人甲"}, "type": 1, "text": "你好", "time": 1700000000000},
			],
			"hasMore": has_more,
		}
	}


def _invoke(args: list[str]):
	return CliRunner().invoke(cli, args)


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_friend_uid_reads_messages_without_friend_list(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.chat_history.return_value = _messages_page()
	mock_client.friend_list.side_effect = AssertionError("直读路径不应遍历沟通列表")

	result = _invoke(["chatmsg", "sid_this_request", "--friend-uid", "10001"])

	assert result.exit_code == 0, result.output
	assert mock_client.friend_list.call_count == 0
	mock_client.chat_history.assert_called_once_with("10001", "sid_this_request", page=1, count=20)
	parsed = json.loads(result.output)
	assert parsed["data"][0]["text"] == "你好"


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_friend_uid_strips_surrounding_whitespace(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.chat_history.return_value = _messages_page()

	result = _invoke(["chatmsg", "  sid_this_request  ", "--friend-uid", " 10001 "])

	assert result.exit_code == 0, result.output
	mock_client.chat_history.assert_called_once_with("10001", "sid_this_request", page=1, count=20)


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_blank_friend_uid_is_invalid_param_not_a_silent_scan(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.side_effect = AssertionError("空 --friend-uid 不能退回全列表扫描")

	result = _invoke(["chatmsg", "sid_this_request", "--friend-uid", "   "])

	assert result.exit_code == 1, result.output
	assert json.loads(result.output)["error"]["code"] == "INVALID_PARAM"
	assert mock_client.friend_list.call_count == 0


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_direct_lookup_surfaces_platform_error_without_fallback(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.chat_history.return_value = {"code": 37, "message": "环境存在异常"}
	mock_client.parse_error.return_value = ("ENVIRONMENT_RISK", "环境存在异常")
	mock_client.friend_list.side_effect = AssertionError("直读失败不能退回全列表扫描")

	result = _invoke(["chatmsg", "sid_stale", "--friend-uid", "10001"])

	assert result.exit_code == 1, result.output
	assert json.loads(result.output)["error"]["code"] == "ENVIRONMENT_RISK"
	assert mock_client.friend_list.call_count == 0


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_next_page_hint_keeps_direct_mode(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.chat_history.return_value = _messages_page(has_more=True)

	result = _invoke(["chatmsg", "sid_this_request", "--friend-uid", "10001", "--count", "20"])

	assert result.exit_code == 0, result.output
	next_actions = json.loads(result.output)["hints"]["next_actions"]
	pages = [action for action in next_actions if "--page 2" in action]
	assert len(pages) == 1, next_actions
	assert "--friend-uid 10001" in pages[0]
	assert "sid_this_request" in pages[0]


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_next_page_hint_protects_dash_leading_security_id(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.chat_history.return_value = _messages_page(has_more=True)

	result = _invoke(["chatmsg", "--friend-uid", "10001", "--", "-dash_sid"])

	assert result.exit_code == 0, result.output
	next_actions = json.loads(result.output)["hints"]["next_actions"]
	pages = [action for action in next_actions if "--page 2" in action]
	assert len(pages) == 1, next_actions
	assert pages[0].rstrip().endswith("-- -dash_sid"), pages[0]


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_without_friend_uid_still_scans_and_uses_fresh_security_id(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = {
		"zpData": {"result": [{"uid": 10001, "securityId": "sid_fresh", "name": "联系人甲"}]}
	}
	mock_client.chat_history.return_value = _messages_page()

	result = _invoke(["chatmsg", "10001"])

	assert result.exit_code == 0, result.output
	assert mock_client.friend_list.call_count == 1
	# securityId 是每请求轮换的令牌：必须用本次列表返回的新值。
	mock_client.chat_history.assert_called_once_with("10001", "sid_fresh", page=1, count=20)


def test_mcp_maps_friend_uid_into_cli_argv():
	args = _build_args("boss_chatmsg", {"security_id": "sid_this_request", "friend_uid": "10001"})
	assert args == ["chatmsg", "sid_this_request", "--friend-uid", "10001"]


def test_mcp_passes_explicit_empty_friend_uid_through():
	# 显式空串必须交给 CLI 拒绝，不能被真值判断化成「没传」。
	args = _build_args("boss_chatmsg", {"security_id": "sid_this_request", "friend_uid": ""})
	assert args == ["chatmsg", "sid_this_request", "--friend-uid", ""]
