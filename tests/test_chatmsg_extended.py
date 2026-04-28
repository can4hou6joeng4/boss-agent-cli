"""chatmsg.py 扩展测试 — 消息历史、空列表、无效好友、格式化逻辑。"""

import json
from unittest.mock import patch, MagicMock

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


# ── 正常消息历史返回 ────────────────────────────────────────────────


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_returns_correct_messages(mock_auth_cls, mock_client_cls):
	"""正常返回消息列表，包含正确的字段。
	注意：gid = friend.uid，from.uid == gid 表示好友发的，否则是自己发的。
	"""
	mock_client = _ctx_mock(mock_client_cls)
	# 好友 uid=12345
	mock_client.friend_list.return_value = _friend_list_response([_make_friend(uid=12345)])
	mock_client.chat_history.return_value = {
		"zpData": {
			"messages": [
				# from.uid=12345 == gid -> 好友发的
				{"from": {"uid": 12345, "name": "张HR"}, "type": 1, "text": "你好，看到你的简历", "time": 1700000000000},
				# from.uid=99999 != gid -> 自己发的
				{"from": {"uid": 99999, "name": "我本人"}, "type": 1, "text": "谢谢关注", "time": 1700000001000},
				# from.uid=12345 == gid -> 好友发的
				{"from": {"uid": 12345, "name": "张HR"}, "type": 1, "text": "方便聊聊吗", "time": 1700000002000},
			],
		},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["chatmsg", "sec_001"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert len(parsed["data"]) == 3
	# 验证消息内容和发送方
	assert parsed["data"][0]["text"] == "你好，看到你的简历"
	assert parsed["data"][0]["from"] == "张HR"
	assert parsed["data"][1]["from"] == "我"
	assert parsed["data"][2]["text"] == "方便聊聊吗"


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_message_fields_complete(mock_auth_cls, mock_client_cls):
	"""返回的每条消息应包含 from/type/text/time 四个字段"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	mock_client.chat_history.return_value = {
		"zpData": {
			"messages": [
				{"from": {"uid": 99, "name": "张HR"}, "type": 1, "text": "你好", "time": 1700000000000},
			],
		},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["chatmsg", "sec_001"])
	parsed = json.loads(result.output)
	msg = parsed["data"][0]
	assert "from" in msg
	assert "type" in msg
	assert "text" in msg
	assert "time" in msg


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_hints_present(mock_auth_cls, mock_client_cls):
	"""返回结果应包含 hints.next_actions"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	mock_client.chat_history.return_value = {
		"zpData": {"messages": []},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["chatmsg", "sec_001"])
	parsed = json.loads(result.output)
	assert "hints" in parsed
	assert "next_actions" in parsed["hints"]
	# hints 应包含 chat 和 detail 建议
	actions_text = " ".join(parsed["hints"]["next_actions"])
	assert "chat" in actions_text
	assert "detail" in actions_text


# ── 空消息列表边界 ──────────────────────────────────────────────────


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_empty_messages(mock_auth_cls, mock_client_cls):
	"""好友存在但消息列表为空时应返回空数组"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	mock_client.chat_history.return_value = {
		"zpData": {"messages": []},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["chatmsg", "sec_001"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"] == []


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_messages_key_missing(mock_auth_cls, mock_client_cls):
	"""API 返回中缺少 messages 键时应安全降级为空列表"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	mock_client.chat_history.return_value = {
		"zpData": {},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["chatmsg", "sec_001"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"] == []


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_uses_history_msg_list_fallback(mock_auth_cls, mock_client_cls):
	"""API 返回 historyMsgList 而非 messages 时应正确解析"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	mock_client.chat_history.return_value = {
		"zpData": {
			"historyMsgList": [
				{"from": {"uid": 99, "name": "张HR"}, "type": 1, "text": "备选字段", "time": 1700000000000},
			],
		},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["chatmsg", "sec_001"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert len(parsed["data"]) == 1
	assert parsed["data"][0]["text"] == "备选字段"


# ── 无效好友 ID 处理 ────────────────────────────────────────────────


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_invalid_security_id(mock_auth_cls, mock_client_cls):
	"""不存在的 security_id 应返回 JOB_NOT_FOUND 错误"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	runner = CliRunner()
	result = runner.invoke(cli, ["chatmsg", "sec_nonexistent"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "JOB_NOT_FOUND"
	assert "sec_nonexistent" in parsed["error"]["message"]


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_empty_friend_list(mock_auth_cls, mock_client_cls):
	"""好友列表为空时应返回 JOB_NOT_FOUND"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([])
	runner = CliRunner()
	result = runner.invoke(cli, ["chatmsg", "sec_001"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "JOB_NOT_FOUND"


# ── 消息格式化逻辑 ─────────────────────────────────────────────────


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_message_type_mapping(mock_auth_cls, mock_client_cls):
	"""消息类型应被正确映射为可读标签"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	mock_client.chat_history.return_value = {
		"zpData": {
			"messages": [
				{"from": {"uid": 99, "name": "张HR"}, "type": 1, "text": "文本消息", "time": 1700000000000},
				{"from": {"uid": 99, "name": "张HR"}, "type": 2, "text": "", "time": 1700000001000},
				{"from": {"uid": 99, "name": "张HR"}, "type": 3, "text": "打招呼", "time": 1700000002000},
				{"from": {"uid": 99, "name": "张HR"}, "type": 4, "text": "", "time": 1700000003000},
				{"from": {"uid": 99, "name": "张HR"}, "type": 5, "text": "系统通知", "time": 1700000004000},
			],
		},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["chatmsg", "sec_001"])
	parsed = json.loads(result.output)
	types = [m["type"] for m in parsed["data"]]
	assert types[0] == "文本"
	assert types[1] == "图片"
	assert types[2] == "招呼"
	assert types[3] == "简历"
	assert types[4] == "系统"


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_unknown_message_type(mock_auth_cls, mock_client_cls):
	"""未知消息类型应显示为 '其他(N)' 格式"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	mock_client.chat_history.return_value = {
		"zpData": {
			"messages": [
				{"from": {"uid": 99, "name": "张HR"}, "type": 99, "text": "未知类型", "time": 1700000000000},
			],
		},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["chatmsg", "sec_001"])
	parsed = json.loads(result.output)
	assert parsed["data"][0]["type"] == "其他(99)"


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_self_message_identified(mock_auth_cls, mock_client_cls):
	"""自己发的消息 from 应显示为 '我'。
	逻辑：gid = friend.uid，from.uid != gid 则为自己发的。
	"""
	mock_client = _ctx_mock(mock_client_cls)
	# 好友 uid=12345
	mock_client.friend_list.return_value = _friend_list_response([_make_friend(uid=12345)])
	mock_client.chat_history.return_value = {
		"zpData": {
			"messages": [
				# from.uid=99999 != gid(12345) -> 自己发的
				{"from": {"uid": 99999, "name": "我本人"}, "type": 1, "text": "我发的", "time": 1700000000000},
				# from.uid=12345 == gid(12345) -> 好友发的
				{"from": {"uid": 12345, "name": "张HR"}, "type": 1, "text": "好友发的", "time": 1700000001000},
			],
		},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["chatmsg", "sec_001"])
	parsed = json.loads(result.output)
	assert parsed["data"][0]["from"] == "我"
	assert parsed["data"][1]["from"] == "张HR"


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_time_formatting(mock_auth_cls, mock_client_cls):
	"""消息时间应被格式化为 MM-DD HH:MM 格式"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	# 2023-11-14 22:13:20 UTC = 1700000000000 ms
	mock_client.chat_history.return_value = {
		"zpData": {
			"messages": [
				{"from": {"uid": 99, "name": "张HR"}, "type": 1, "text": "你好", "time": 1700000000000},
			],
		},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["chatmsg", "sec_001"])
	parsed = json.loads(result.output)
	time_str = parsed["data"][0]["time"]
	# 时间应为 "MM-DD HH:MM" 格式
	assert len(time_str) > 0
	assert "-" in time_str
	assert ":" in time_str


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_no_time_field(mock_auth_cls, mock_client_cls):
	"""消息缺少 time 字段时应返回空字符串"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	mock_client.chat_history.return_value = {
		"zpData": {
			"messages": [
				{"from": {"uid": 99, "name": "张HR"}, "type": 1, "text": "无时间"},
			],
		},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["chatmsg", "sec_001"])
	parsed = json.loads(result.output)
	assert parsed["data"][0]["time"] == ""


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_text_fallback_to_body(mock_auth_cls, mock_client_cls):
	"""text 为空时应从 body.text 取值"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	mock_client.chat_history.return_value = {
		"zpData": {
			"messages": [
				{
					"from": {"uid": 99, "name": "张HR"},
					"type": 1,
					"text": None,
					"body": {"text": "来自body的内容"},
					"time": 1700000000000,
				},
			],
		},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["chatmsg", "sec_001"])
	parsed = json.loads(result.output)
	assert parsed["data"][0]["text"] == "来自body的内容"


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_text_both_empty(mock_auth_cls, mock_client_cls):
	"""text 和 body.text 都为空时应返回空字符串"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	mock_client.chat_history.return_value = {
		"zpData": {
			"messages": [
				{
					"from": {"uid": 99, "name": "张HR"},
					"type": 2,
					"text": None,
					"body": {},
					"time": 1700000000000,
				},
			],
		},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["chatmsg", "sec_001"])
	parsed = json.loads(result.output)
	assert parsed["data"][0]["text"] == ""


# ── 分页参数 ────────────────────────────────────────────────────────


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_page_and_count_params(mock_auth_cls, mock_client_cls):
	"""--page 和 --count 参数应传递给 chat_history"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	mock_client.chat_history.return_value = {"zpData": {"messages": []}}
	runner = CliRunner()
	result = runner.invoke(cli, ["chatmsg", "sec_001", "--page", "2", "--count", "50"])
	assert result.exit_code == 0
	mock_client.chat_history.assert_called_once()
	call_args = mock_client.chat_history.call_args
	# page 和 count 应作为关键字参数传入
	assert call_args.kwargs.get("page") == 2 or (len(call_args.args) >= 3 and call_args.args[2] == 2)
	assert call_args.kwargs.get("count") == 50 or (len(call_args.args) >= 4 and call_args.args[3] == 50)


# ── Context manager 验证 ──────────────────────────────────────────


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_uses_client_context_manager(mock_auth_cls, mock_client_cls):
	"""应使用 BossClient 的 context manager"""
	instance = mock_client_cls.return_value
	instance.__enter__ = MagicMock(return_value=instance)
	instance.__exit__ = MagicMock(return_value=None)
	instance.unwrap_data.side_effect = lambda response: response.get("zpData") if "zpData" in response else response.get("data")
	instance.friend_list.return_value = _friend_list_response([_make_friend()])
	instance.chat_history.return_value = {"zpData": {"messages": []}}
	runner = CliRunner()
	result = runner.invoke(cli, ["chatmsg", "sec_001"])
	assert result.exit_code == 0
	instance.__enter__.assert_called_once()
	instance.__exit__.assert_called_once()
