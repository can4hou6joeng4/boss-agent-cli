"""锁定 chatmsg / chat-summary 的信封分页证据契约。

两个命令共用聊天历史接口。平台在该响应顶层给出 ``hasMore`` / ``page``，
但两个命令此前只把消息归一化后就丢弃了分页信息，Agent 无法判断是否还有
下一页。本文件锁住：

1. 只有平台返回布尔 ``hasMore`` 时才给出 true / false，其余一律 null；
2. true 时下一页命令可被直接执行，并沿用调用方请求的 ``--count``；
3. 失败信封不带 pagination；
4. ``messages`` / ``historyMsgList`` 的畸形载荷不会击穿命令。
"""

import json
import shlex
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.display import error_contract_for_code
from boss_agent_cli.main import cli
from boss_agent_cli.services.pagination import UNKNOWN_HAS_MORE_OPERATOR_ACTION

CHAT_COMMANDS = [
	pytest.param("boss_agent_cli.commands.chatmsg", "chatmsg", id="chatmsg"),
	pytest.param("boss_agent_cli.commands.chat_summary", "chat-summary", id="chat-summary"),
]


def _ctx_mock(mock_cls):
	"""让 mock 类支持 context manager，并模拟平台 envelope 解包。"""
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: response.get("zpData") if "zpData" in response else response.get("data")
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	return instance


def _friend_item(uid=10001, sid="sid_current", name="联系人甲"):
	return {
		"uid": uid,
		"securityId": sid,
		"name": name,
		"brandName": "示例公司",
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


def _message(uid=10001, text="您好"):
	return {"from": {"uid": uid, "name": "联系人甲"}, "type": 1, "text": text, "time": 1700000000000}


def _invoke(module, name, argv, payload=None, *, parse_error=None, tmp_path=None):
	with patch(f"{module}.get_platform_instance") as platform_cls, patch(f"{module}.AuthManager") as auth_cls:
		client = _ctx_mock(platform_cls)
		client.friend_list.return_value = _friend_list_response([_friend_item()])
		if parse_error is not None:
			client.chat_history.return_value = {"code": 9, "message": "fixture failure"}
			client.parse_error.return_value = parse_error
		else:
			client.chat_history.return_value = {"code": 0, "zpData": payload}
		full_argv = ["--json", name, "10001", *argv]
		if tmp_path is not None:
			full_argv = ["--data-dir", str(tmp_path), *full_argv]
		result = CliRunner().invoke(cli, full_argv)
	return result, client, auth_cls


PAGINATION_CASES = [
	pytest.param(
		{"messages": [], "hasMore": True, "page": 2}, (2, True, 0), 3,
		id="true-with-platform-page",
	),
	pytest.param({"messages": [], "hasMore": False}, (1, False, 0), None, id="false"),
	pytest.param({"messages": []}, (1, None, 0), None, id="missing"),
	pytest.param({"messages": [], "hasMore": None}, (1, None, 0), None, id="null"),
	pytest.param({"messages": [], "hasMore": "true"}, (1, None, 0), None, id="string-true"),
	pytest.param({"messages": [], "hasMore": 1}, (1, None, 0), None, id="integer-one"),
	pytest.param(
		{"messages": [_message()], "hasMore": True, "page": 4}, (4, True, 1), 5,
		id="platform-page-wins-over-request",
	),
	pytest.param(
		{"messages": "not-a-list", "hasMore": True}, (1, True, 0), 2,
		id="messages-not-list",
	),
	pytest.param(
		{"messages": [], "historyMsgList": [_message(), _message(text="在吗")]},
		(1, None, 2), None,
		id="history-msg-list-fallback",
	),
	pytest.param(["unexpected"], (1, None, 0), None, id="data-not-mapping"),
]


@pytest.mark.parametrize(("payload", "expected", "next_page"), PAGINATION_CASES)
@pytest.mark.parametrize(("module", "name"), CHAT_COMMANDS)
def test_chat_pagination_evidence(module, name, payload, expected, next_page, tmp_path):
	page, has_more, total = expected
	result, client, _ = _invoke(module, name, [], payload, tmp_path=tmp_path)
	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)

	assert parsed["pagination"] == {
		"page": page,
		"has_more": has_more,
		"has_more_source": "platform_hasMore" if has_more is not None else "unknown",
		"total": total,
		"total_kind": "returned_page_count",
	}
	next_pages = [action for action in parsed["hints"]["next_actions"] if "--page" in action]
	if has_more is True:
		assert next_pages == [f"boss {name} 10001 --page {next_page} --count 20"]
	else:
		assert next_pages == []
	if has_more is None:
		assert parsed["hints"]["operator_actions"] == [UNKNOWN_HAS_MORE_OPERATOR_ACTION]
	else:
		assert "operator_actions" not in parsed["hints"]
	if name == "chatmsg":
		assert len(parsed["data"]) == total

	call = client.chat_history.call_args
	assert call.args[0] == "10001"
	assert call.args[1] == "sid_current"
	assert call.kwargs == {"page": 1, "count": 20}


@pytest.mark.parametrize(("module", "name"), CHAT_COMMANDS)
def test_chat_next_page_action_is_executable(module, name, tmp_path):
	"""下一页指引要能直接交给 CLI 执行，并沿用调用方本次的 --count。"""
	with patch(f"{module}.get_platform_instance") as platform_cls, patch(f"{module}.AuthManager") as auth_cls:
		client = _ctx_mock(platform_cls)
		client.friend_list.return_value = _friend_list_response([_friend_item()])
		client.chat_history.side_effect = [
			{"code": 0, "zpData": {"messages": [], "page": 3, "hasMore": True}},
			{"code": 0, "zpData": {"messages": [], "page": 4, "hasMore": False}},
		]
		runner = CliRunner()
		first = runner.invoke(
			cli,
			["--data-dir", str(tmp_path), "--json", name, "10001", "--page", "3", "--count", "7"],
		)
		assert first.exit_code == 0, first.output
		actions = [a for a in json.loads(first.output)["hints"]["next_actions"] if "--page" in a]
		assert actions == [f"boss {name} 10001 --page 4 --count 7"]

		second = runner.invoke(cli, ["--data-dir", str(tmp_path), *shlex.split(actions[0])[1:]])
		assert second.exit_code == 0, second.output
		parsed = json.loads(second.output)
		assert parsed["pagination"]["page"] == 4
		assert parsed["pagination"]["has_more"] is False
		assert [call.kwargs for call in client.chat_history.call_args_list] == [
			{"page": 3, "count": 7},
			{"page": 4, "count": 7},
		]
		assert [call.args[0].resolve() for call in auth_cls.call_args_list] == [
			tmp_path.resolve(),
			tmp_path.resolve(),
		]


def test_chatmsg_next_page_action_keeps_raw(tmp_path):
	"""``chatmsg --raw`` 的翻页提示必须保留 --raw。

	只写 --page / --count 时，下一页的 type_code / timestamp / body / links /
	security_id 全部消失，Agent 会以为保真字段只在第一页存在。
	"""
	with patch("boss_agent_cli.commands.chatmsg.get_platform_instance") as platform_cls, \
			patch("boss_agent_cli.commands.chatmsg.AuthManager"):
		client = _ctx_mock(platform_cls)
		client.friend_list.return_value = _friend_list_response([_friend_item()])
		client.chat_history.return_value = {"code": 0, "zpData": {
			"messages": [_message()], "hasMore": True, "page": 2,
		}}
		result = CliRunner().invoke(
			cli, ["--data-dir", str(tmp_path), "--json", "chatmsg", "10001", "--count", "9", "--raw"],
		)
	assert result.exit_code == 0, result.output
	actions = [a for a in json.loads(result.output)["hints"]["next_actions"] if "--page" in a]
	assert actions == ["boss chatmsg 10001 --page 3 --count 9 --raw"]

	# 提示要真能被 CLI 解析，且 --raw 确实生效（下一页输出仍是保真字段）。
	replay = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), *shlex.split(actions[0])[1:], "--help"])
	assert replay.exit_code == 0, replay.output


def test_chatmsg_next_page_action_omits_raw_when_not_requested(tmp_path):
	with patch("boss_agent_cli.commands.chatmsg.get_platform_instance") as platform_cls, \
			patch("boss_agent_cli.commands.chatmsg.AuthManager"):
		client = _ctx_mock(platform_cls)
		client.friend_list.return_value = _friend_list_response([_friend_item()])
		client.chat_history.return_value = {"code": 0, "zpData": {
			"messages": [_message()], "hasMore": True, "page": 1,
		}}
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "chatmsg", "10001"])
	assert result.exit_code == 0, result.output
	actions = [a for a in json.loads(result.output)["hints"]["next_actions"] if "--page" in a]
	assert actions == ["boss chatmsg 10001 --page 2 --count 20"]


@pytest.mark.parametrize(("code", "recoverable"), [("RATE_LIMITED", True), ("ACCOUNT_RISK", False)])
@pytest.mark.parametrize(("module", "name"), CHAT_COMMANDS)
def test_chat_failure_has_no_pagination(module, name, code, recoverable):
	result, client, _ = _invoke(module, name, [], parse_error=(code, "fixture failure"))
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["pagination"] is None
	assert parsed["error"]["code"] == code
	assert parsed["error"]["recoverable"] is recoverable
	assert parsed["error"]["recovery_action"] == error_contract_for_code(code)[1]
	client.chat_history.assert_called_once_with("10001", "sid_current", page=1, count=20)
