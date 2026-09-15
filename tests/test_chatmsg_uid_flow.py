"""回归测试：security_id 是每请求轮换的令牌，联系人必须按稳定 uid 关联。

背景：``security_id`` 由 BOSS 服务端每次请求重新加密生成，同一个联系人在
``chat`` 与 ``chatmsg`` 两次调用中拿到的值不同。修复前
``find_friend_by_security_id`` 用 ``securityId`` 精确匹配，导致
``boss chatmsg`` 必然报 JOB_NOT_FOUND。

本文件锁住三条不变量：
1. ``boss chat`` 输出稳定标识 ``uid``；
2. ``boss chatmsg`` 能按 uid 命中，并把**本次**返回的 securityId 传给消息接口；
3. ``chat-summary`` / ``exchange`` 同样使用本次返回的 securityId。

快照 / diff 的 uid 主键不变量见 ``test_chat_snapshot_extended.py``。
"""

import json
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli


def _ctx_mock(mock_cls):
	"""让 mock 类支持 context manager。"""
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: response.get("zpData") if "zpData" in response else response.get("data")
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	return instance


def _friend_item(uid=117661469, sid="sid_this_request", name="郝女士", brand="万联智链"):
	return {
		"uid": uid,
		"securityId": sid,
		"name": name,
		"brandName": brand,
		"title": "招聘专员",
		"friendSource": 0,
		"encryptJobId": "job_001",
		"lastMsg": "你好",
		"lastTS": 1700000000000,
		"unreadMsgCount": 0,
		"relationType": 2,
		"lastMessageInfo": {"status": 2},
	}


def _friend_list_response(items):
	return {"zpData": {"result": items, "friendList": items}}


# ── 1. boss chat 输出稳定标识 uid ────────────────────────────────────


@patch("boss_agent_cli.commands.chat.get_platform_instance")
@patch("boss_agent_cli.commands.chat.AuthManager")
def test_chat_output_exposes_uid(mock_auth_cls, mock_client_cls):
	"""沟通列表必须给出 uid，否则调用方没有任何可跨请求复用的句柄。"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_friend_item()])

	result = CliRunner().invoke(cli, ["chat"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"][0]["uid"] == 117661469
	# security_id 仍然保留（仅作参考，不是稳定句柄）
	assert parsed["data"][0]["security_id"] == "sid_this_request"


@patch("boss_agent_cli.commands.chat.get_platform_instance")
@patch("boss_agent_cli.commands.chat.AuthManager")
def test_chat_hint_points_to_uid(mock_auth_cls, mock_client_cls):
	"""下一步提示应引导用 uid，而不是已失效的 security_id。"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_friend_item()])

	result = CliRunner().invoke(cli, ["chat"])
	parsed = json.loads(result.output)
	actions = " ".join(parsed["hints"]["next_actions"])
	assert "chatmsg <uid>" in actions


# ── 2. boss chatmsg 按 uid 命中，并传本次的 securityId ───────────────


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_by_uid_uses_fresh_security_id(mock_auth_cls, mock_client_cls):
	"""核心回归：按 uid 命中后，必须把本次 friend_list 返回的 securityId
	传给 chat_history；直接透传调用方入参（uid）或旧的 security_id 都不对。
	"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response(
		[_friend_item(uid=117661469, sid="sid_current_request")]
	)
	mock_client.chat_history.return_value = {
		"zpData": {"messages": [{"from": {"uid": 117661469, "name": "郝女士"}, "type": 1, "text": "你好", "time": 1700000000000}]},
	}

	result = CliRunner().invoke(cli, ["chatmsg", "117661469"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"][0]["text"] == "你好"

	call = mock_client.chat_history.call_args
	gid, security_id = call.args[0], call.args[1]
	assert str(gid) == "117661469", "gid 必须是 uid"
	assert security_id == "sid_current_request", "必须使用本次请求返回的 securityId"


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_by_security_id_still_works(mock_auth_cls, mock_client_cls):
	"""向后兼容：security_id 仍可匹配（只要与本次返回的值一致）。

	注意：由于该值每请求轮换，真实场景下调用方几乎不可能持有当前值——
	这只是保证匹配逻辑没把旧入参路径删掉。
	"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response(
		[_friend_item(uid=117661469, sid="sid_current_request")]
	)
	mock_client.chat_history.return_value = {"zpData": {"messages": []}}

	result = CliRunner().invoke(cli, ["chatmsg", "sid_current_request"])
	assert result.exit_code == 0
	assert mock_client.chat_history.call_args.args[1] == "sid_current_request"


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_stale_security_id_still_fails_but_suggests_uid(mock_auth_cls, mock_client_cls):
	"""传已轮换掉的 security_id 必然失配——报错需引导改用 uid。"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response(
		[_friend_item(uid=117661469, sid="sid_current_request")]
	)

	result = CliRunner().invoke(cli, ["chatmsg", "sid_previous_request"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "JOB_NOT_FOUND"
	assert "uid" in parsed["error"]["message"]


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_not_found_message_mentions_uid(mock_auth_cls, mock_client_cls):
	"""找不到时要提示可用 uid，避免用户继续在 security_id 上打转。"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_friend_item()])

	result = CliRunner().invoke(cli, ["chatmsg", "nobody"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "JOB_NOT_FOUND"
	assert "uid" in parsed["error"]["message"]
	assert "nobody" in parsed["error"]["message"]


# ── 3. chat-summary / exchange 同样使用本次的 securityId ─────────────


@patch("boss_agent_cli.commands.chat_summary.get_platform_instance")
@patch("boss_agent_cli.commands.chat_summary.AuthManager")
def test_chat_summary_by_uid_uses_fresh_security_id(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response(
		[_friend_item(uid=117661469, sid="sid_current_request")]
	)
	mock_client.chat_history.return_value = {"zpData": {"messages": []}}

	result = CliRunner().invoke(cli, ["chat-summary", "117661469"])
	assert result.exit_code == 0
	assert mock_client.chat_history.call_args.args[1] == "sid_current_request"


@patch("boss_agent_cli.commands.exchange.get_platform_instance")
@patch("boss_agent_cli.commands.exchange.AuthManager")
def test_exchange_by_uid_uses_fresh_security_id(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response(
		[_friend_item(uid=117661469, sid="sid_current_request")]
	)
	mock_client.exchange_contact.return_value = {"zpData": {}}

	result = CliRunner().invoke(cli, ["exchange", "117661469"])
	assert result.exit_code == 0
	assert mock_client.exchange_contact.call_args.args[0] == "sid_current_request"
