"""招聘者沟通列表摘要：真实列表只有基础字段时不再输出「-」「未知」占位，并按来源补全。"""
import datetime
import json
import shutil
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.api import recruiter_client as rc
from boss_agent_cli.api.recruiter_client import BossRecruiterClient
from boss_agent_cli.commands.recruiter.chat import _merge_last_messages, _message_items, _normalize_last_message
from boss_agent_cli.main import cli

UPDATE_TIME = 1791516891255
# 实测 filterByLabel 返回的条目形状（脱敏）
LIVE_ITEM = {
	"friendId": 66001, "friendSource": 0, "encryptFriendId": "enc", "name": "候选人A",
	"updateTime": UPDATE_TIME, "score": 0, "waterLevel": 0, "puffinLabelId": 0,
}


def _expected_time(ms):
	return datetime.datetime.fromtimestamp(ms / 1000).strftime("%m-%d %H:%M")


def _platform(friend_items, *, last_messages=None, snapshot=None):
	platform = MagicMock()
	platform.__enter__ = lambda self: self
	platform.__exit__ = lambda self, *a: None
	platform.unwrap_data.side_effect = lambda response: response.get("zpData")
	platform.is_success.side_effect = lambda response: response.get("code") == 0
	platform.parse_error.side_effect = lambda response: ("UNKNOWN", response.get("message"))
	platform.friend_list.return_value = {"code": 0, "zpData": {"friendList": friend_items}}
	platform.last_messages.return_value = last_messages if last_messages is not None else {"code": 0, "zpData": {}}
	platform.chat_list_snapshot.return_value = snapshot
	return platform


def _run(platform):
	with patch("boss_agent_cli.commands.recruiter.chat.get_recruiter_platform_instance", return_value=platform), \
		patch("boss_agent_cli.commands.recruiter.chat.AuthManager"):
		result = CliRunner().invoke(cli, ["--role", "recruiter", "--json", "hr", "chat"])
	assert result.exit_code == 0, result.output
	return json.loads(result.output)


def test_basic_friend_items_yield_null_not_placeholders_and_update_time():
	platform = _platform([dict(LIVE_ITEM)], last_messages={"code": 5, "message": "参数错误"})
	parsed = _run(platform)
	item = parsed["data"]["friendList"][0]
	assert item["unread"] is None
	assert item["msg_status"] is None
	assert item["last_msg"] is None
	assert item["last_time"] == _expected_time(UPDATE_TIME)
	assert item["last_time_source"] == "updateTime"
	assert item["summary_source"] == "friend_list"
	hint = parsed["hints"]["summary_sources"]
	assert hint["counts"] == {"friend_list": 1}
	assert hint["last_messages_error"] == "参数错误"


def test_page_snapshot_covers_everything_without_extra_request():
	snapshot = {"ok": True, "items": [{
		"friendId": 66001, "newMsgCount": 2,
		"lastMessageInfo": {"showText": "在吗", "status": 1, "msgTime": UPDATE_TIME},
	}]}
	platform = _platform([dict(LIVE_ITEM)], snapshot=snapshot)
	parsed = _run(platform)
	item = parsed["data"]["friendList"][0]
	assert item["unread"] == 2
	assert item["last_msg"] == "在吗"
	assert item["msg_status"] == "未读"
	assert item["last_time"] == _expected_time(UPDATE_TIME)
	assert "last_time_source" not in item
	assert item["summary_source"] == "page"
	platform.last_messages.assert_not_called()


def test_only_friends_missing_from_page_hit_last_messages():
	other = {**LIVE_ITEM, "friendId": 66002}
	snapshot = {"ok": True, "items": [{"friendId": 66001, "lastMsg": "你好", "newMsgCount": 0}]}
	last = {"code": 0, "zpData": {"lastMsgList": [{"friendId": 66002, "lastMsg": "简历发你了", "unreadMsgCount": 1}]}}
	platform = _platform([dict(LIVE_ITEM), other], snapshot=snapshot, last_messages=last)
	parsed = _run(platform)
	rows = {row["friendId"]: row for row in parsed["data"]["friendList"]}
	assert rows[66001]["summary_source"] == "page"
	assert rows[66002]["summary_source"] == "last_messages"
	assert rows[66002]["last_msg"] == "简历发你了"
	assert rows[66002]["unread"] == 1
	platform.last_messages.assert_called_once_with([66002])


def test_last_messages_message_object_shape_maps_by_from_to_uid():
	rows = [dict(LIVE_ITEM)]
	message = {"mid": 1, "from": {"uid": 999}, "to": {"uid": 66001}, "body": {"type": 1, "text": "方便聊聊吗"},
		"time": UPDATE_TIME, "status": 2}
	counts = _merge_last_messages(rows, [message])
	assert counts == {"last_messages": 1}
	assert rows[0]["last_msg"] == "方便聊聊吗"
	assert rows[0]["msg_status"] == "已读"
	assert rows[0]["last_time"] == _expected_time(UPDATE_TIME)


def test_last_messages_map_shape():
	items = _message_items({"66001": {"lastMsg": "好的"}, "66002": "收到", "hasMore": False})
	assert {item["friendId"]: item["lastMsg"] for item in items} == {66001: "好的", 66002: "收到"}


def test_empty_summary_from_api_does_not_override_friend_fallback():
	rows = [dict(LIVE_ITEM)]
	_merge_last_messages(rows, [{"friendId": 66001}])
	assert rows[0]["summary_source"] == "friend_list"
	assert rows[0]["last_time_source"] == "updateTime"


def test_normalize_unknown_status_is_null():
	assert _normalize_last_message({"friendId": 1, "status": 42})["msg_status"] is None
	assert _normalize_last_message({"friendId": 1, "lastTime": "0"})["last_time"] is None


# ── client.chat_list_snapshot ─────────────────────────────────


def test_snapshot_not_attempted_outside_cdp_mode():
	client = BossRecruiterClient(MagicMock())
	client._get_browser = MagicMock()
	with patch.object(BossRecruiterClient, "is_browser_only", return_value=False):
		assert client.chat_list_snapshot() is None
	client._get_browser.assert_not_called()
	client.close()


def test_snapshot_uses_page_eval_in_cdp_mode_and_swallows_errors():
	client = BossRecruiterClient(MagicMock())
	browser = MagicMock()
	browser.evaluate_js.return_value = {"ok": True, "items": [{"friendId": 1}]}
	client._get_browser = MagicMock(return_value=browser)
	with patch.object(BossRecruiterClient, "is_browser_only", return_value=True):
		assert client.chat_list_snapshot() == {"ok": True, "items": [{"friendId": 1}]}
		assert browser.evaluate_js.call_args[0][0] is rc._CHAT_LIST_SNAPSHOT_JS
		browser.evaluate_js.side_effect = RuntimeError("no chat tab")
		assert client.chat_list_snapshot() is None
		browser.evaluate_js.side_effect = None
		browser.evaluate_js.return_value = {"ok": False, "error": "x"}
		assert client.chat_list_snapshot() is None
	client.close()


_FAKE_PAGE_JS = r"""
const friends = [
	{friendId: 1, newMsgCount: 3, lastMsg: '你好', updateTime: 5, resume: {big: 'x'},
		lastMessageInfo: {showText: '你好', status: 1, msgTime: 7, extra: {deep: 1}}},
	{friendId: 2, name: '不应带出', unreadMsgCount: 0},
];
const vm = {$data: {tab: 0, wrapper: {list: friends}}, _computedWatchers: {short: 1}, short: [{friendId: 9}]};
global.document = {querySelector: (sel) => sel === '.chat-user' ? {__vue__: vm} : null};
const fn = __SCRIPT__;
console.log(JSON.stringify(fn()));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 运行页面脚本")
def test_snapshot_script_picks_largest_friend_list_and_whitelists_fields(tmp_path):
	script = tmp_path / "snap.js"
	script.write_text(_FAKE_PAGE_JS.replace("__SCRIPT__", rc._CHAT_LIST_SNAPSHOT_JS), encoding="utf-8")
	out = json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, check=True, timeout=30).stdout)
	assert out["ok"] is True
	assert out["source"] == "data.wrapper.list"
	assert out["items"][0] == {
		"friendId": 1, "newMsgCount": 3, "lastMsg": "你好", "updateTime": 5,
		"lastMessageInfo": {"showText": "你好", "status": 1, "msgTime": 7},
	}
	assert out["items"][1] == {"friendId": 2, "unreadMsgCount": 0}
