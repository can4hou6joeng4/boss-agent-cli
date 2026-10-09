"""userLastMsg 实测形状 + 分批：一次塞 356 个 friendId 会被拒（「未知的非法参数」），改为小批串行并限量。"""
import datetime
import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from boss_agent_cli.api import recruiter_endpoints as ep
from boss_agent_cli.api.recruiter_client import BossRecruiterClient
from boss_agent_cli.commands.recruiter.chat import (
	DEFAULT_SUMMARY_LIMIT,
	LAST_MESSAGES_BATCH_SIZE,
	_fetch_last_messages,
)
from boss_agent_cli.main import cli

LAST_TS = 1791518413490


def _expected(ms):
	return datetime.datetime.fromtimestamp(ms / 1000).strftime("%m-%d %H:%M")


def _friend(fid, source=0):
	return {"friendId": fid, "friendSource": source, "encryptFriendId": f"e{fid}", "name": "x", "updateTime": LAST_TS - 1000}


def _live_msg(uid, *, from_id=None, status=0, text="您好，方便聊聊吗"):
	# 实测 userLastMsg zpData 条目形状（脱敏）
	return {
		"lastTime": "12:00", "uid": uid, "encryptUid": f"e{uid}", "lastTS": LAST_TS,
		"lastMsgInfo": {
			"msgId": 1, "encryptMsgId": "m", "showText": text,
			"fromId": uid if from_id is None else from_id, "toId": 1, "status": status, "msgTime": LAST_TS,
		},
	}


def _platform(friends, responder):
	platform = MagicMock()
	platform.__enter__ = lambda self: self
	platform.__exit__ = lambda self, *a: None
	platform.unwrap_data.side_effect = lambda response: response.get("zpData")
	platform.is_success.side_effect = lambda response: response.get("code") == 0
	platform.parse_error.side_effect = lambda response: (response.get("code"), response.get("message"))
	platform.friend_list.return_value = {"code": 0, "zpData": {"friendList": friends}}
	platform.chat_list_snapshot.return_value = None
	platform.last_messages.side_effect = responder
	return platform


def _ok(ids, **_kw):
	return {"code": 0, "zpData": [_live_msg(fid) for fid in ids]}


def _invoke(platform, *args):
	with patch("boss_agent_cli.commands.recruiter.chat.get_recruiter_platform_instance", return_value=platform), \
		patch("boss_agent_cli.commands.recruiter.chat.AuthManager"):
		result = CliRunner().invoke(cli, ["--role", "recruiter", "--json", "hr", *args])
	assert result.exit_code == 0, result.output
	return json.loads(result.output)


def test_live_shape_maps_text_timestamp_and_hides_status_for_candidate_message():
	platform = _platform([_friend(1)], _ok)
	parsed = _invoke(platform, "chat")
	item = parsed["data"]["friendList"][0]
	assert item["summary_source"] == "last_messages"
	assert item["last_msg"] == "您好，方便聊聊吗"
	# 用 lastTS 毫秒时间，不用「12:00」展示串
	assert item["last_time"] == _expected(LAST_TS)
	# 候选人发来的消息 status 恒为 0，不能显示成「发送中」
	assert item["msg_status"] is None
	assert item["unread"] is None
	assert parsed["hints"]["summary_sources"]["last_messages_requests"] == 1


def test_self_sent_message_keeps_read_status():
	platform = _platform([_friend(1)], lambda ids, **kw: {"code": 0, "zpData": [_live_msg(1, from_id=999, status=2)]})
	item = _invoke(platform, "chat")["data"]["friendList"][0]
	assert item["msg_status"] == "已读"


def test_large_list_is_batched_sequentially_and_capped_by_default():
	friends = [_friend(i) for i in range(1, 357)]
	platform = _platform(friends, _ok)
	parsed = _invoke(platform, "chat")
	calls = platform.last_messages.call_args_list
	assert len(calls) == DEFAULT_SUMMARY_LIMIT // LAST_MESSAGES_BATCH_SIZE == 2
	assert all(len(c.args[0]) <= LAST_MESSAGES_BATCH_SIZE for c in calls)
	assert calls[0].args[0] == list(range(1, 51))
	assert calls[1].args[0] == list(range(51, 101))
	hint = parsed["hints"]["summary_sources"]
	assert hint["counts"] == {"last_messages": 100, "friend_list": 256}
	assert hint["last_messages_skipped"] == 256
	assert hint["summary_limit"] == DEFAULT_SUMMARY_LIMIT
	assert "last_messages_error" not in hint


def test_summary_limit_zero_sends_no_request():
	platform = _platform([_friend(1), _friend(2)], _ok)
	parsed = _invoke(platform, "chat", "--summary-limit", "0")
	platform.last_messages.assert_not_called()
	assert parsed["hints"]["summary_sources"]["counts"] == {"friend_list": 2}


def test_failed_batch_stops_further_requests():
	friends = [_friend(i) for i in range(1, 101)]
	platform = _platform(friends, lambda ids, **kw: {"code": 37, "message": "环境异常"})
	parsed = _invoke(platform, "chat")
	assert platform.last_messages.call_count == 1
	hint = parsed["hints"]["summary_sources"]
	assert hint["last_messages_error"] == "环境异常"
	assert hint["last_messages_error_code"] == 37
	assert hint["last_messages_requests"] == 1
	assert hint["counts"] == {"friend_list": 100}


def test_friend_source_one_goes_to_src_1():
	platform = _platform([], _ok)
	items, stats = _fetch_last_messages(platform, [1, 2, 3], sources={1: 0, 2: 1, 3: 0})
	assert stats == {"requests": 2}
	calls = platform.last_messages.call_args_list
	assert calls[0].args[0] == [1, 3] and calls[0].kwargs == {}
	assert calls[1].args[0] == [2] and calls[1].kwargs == {"src": 1}
	assert {item["uid"] for item in items} == {1, 2, 3}


def test_last_messages_command_batches_and_limits():
	friends = [_friend(i) for i in range(1, 121)]
	platform = _platform(friends, _ok)
	parsed = _invoke(platform, "last-messages", "--limit", "60")
	data = parsed["data"]
	assert data["requests"] == 2
	assert data["skipped"] == 60
	assert len(data["messages"]) == 60
	assert data["messages"][0]["last_msg"] == "您好，方便聊聊吗"


def test_last_messages_command_errors_when_first_batch_fails():
	platform = _platform([_friend(1)], lambda ids, **kw: {"code": 5, "message": "未知的非法参数"})
	with patch("boss_agent_cli.commands.recruiter.chat.get_recruiter_platform_instance", return_value=platform), \
		patch("boss_agent_cli.commands.recruiter.chat.AuthManager"):
		result = CliRunner().invoke(cli, ["--role", "recruiter", "--json", "hr", "last-messages"])
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert "未知的非法参数" in parsed["error"]["message"]


def test_client_posts_src_and_comma_joined_ids():
	client = BossRecruiterClient.__new__(BossRecruiterClient)
	with patch.object(BossRecruiterClient, "_request", return_value={"code": 0}) as req:
		client.last_messages([1, 2, 3], src=1)
	req.assert_called_once_with("POST", ep.BOSS_LAST_MESSAGES_URL, data={"friendIds": "1,2,3", "src": 1})
