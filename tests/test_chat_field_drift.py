"""平台字段漂移不能打断 chat 家族命令（Issue #1317）。

三处残余：

1. ``lastMessageInfo`` 漂移成字符串 / 数组时 ``boss chat`` 的 ``.get`` 抛
   ``AttributeError``，被兜成 ``INTERNAL_ERROR``；
2. 上一份快照的 ``last_ts`` 是字符串时，``boss chat --export`` 的筛选比较
   抛 ``TypeError``，整次导出丢失信封；
3. 超过 4300 位的 ASCII 数字串 ``mid`` 会触发 ``int_max_str_digits`` 上限，
   ``boss hr chatmsg`` 落成可重试的 ``NETWORK_ERROR``。
"""

import datetime
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.commands.recruiter.chat import _message_cursor_id
from boss_agent_cli.main import cli


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: response.get("zpData") if "zpData" in response else response.get("data")
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	return instance


def _friend_item(uid="u_curr", sid="sid_curr", name="联系人甲"):
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


# ── 1. lastMessageInfo 形状漂移 ─────────────────────────────────────


@pytest.mark.parametrize("value", ["drifted", ["status"], 1], ids=["string", "list", "int"])
@patch("boss_agent_cli.commands.chat.get_platform_instance")
@patch("boss_agent_cli.commands.chat.AuthManager")
def test_chat_last_message_info_drift_falls_back_to_unknown(mock_auth_cls, mock_platform_cls, value, tmp_path: Path):
	"""lastMessageInfo 不是对象时按缺失处理，消息状态显示「未知」并照常输出信封。"""
	platform = _ctx_mock(mock_platform_cls)
	item = _friend_item()
	item["lastMessageInfo"] = value
	platform.friend_list.return_value = {"zpData": {"result": [item], "friendList": [item]}}

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "chat"])

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.stdout)
	assert parsed["ok"] is True
	assert parsed["data"][0]["msg_status"] == "未知"


# ── 2. 快照 last_ts 漂移不打断导出 ──────────────────────────────────


@patch("boss_agent_cli.commands.chat.get_platform_instance")
@patch("boss_agent_cli.commands.chat.AuthManager")
def test_chat_export_tolerates_string_last_ts_in_previous_snapshot(mock_auth_cls, mock_platform_cls, tmp_path: Path):
	"""上一份快照里的 last_ts 是数字字符串时，导出仍要完成并保留该联系人。"""
	snapshot_dir = tmp_path / "chat-history"
	snapshot_dir.mkdir()
	yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
	prev_item = {
		"name": "旧联系人",
		"title": "-",
		"brand_name": "-",
		"initiated_by": "对方主动",
		"last_msg": "旧消息",
		"last_time": "昨天",
		# 旧格式 / 手工改过的快照：last_ts 不是整数。
		"last_ts": "1700000000000",
		"msg_status": "已读",
		"uid": "u_prev",
		"security_id": "sid_prev",
		"encrypt_job_id": "job_prev",
		"unread": 0,
	}
	(snapshot_dir / f"{yesterday}.json").write_text(
		json.dumps([prev_item], ensure_ascii=False), encoding="utf-8",
	)

	platform = _ctx_mock(mock_platform_cls)
	platform.friend_list.return_value = {
		"zpData": {"result": [_friend_item()], "friendList": [_friend_item()], "hasMore": False},
	}
	out_path = tmp_path / "out.md"

	result = CliRunner().invoke(cli, [
		"--data-dir", str(tmp_path), "--json", "chat", "--export", "md", "--days", "3",
		"-o", str(out_path),
	])

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.stdout)
	assert parsed["ok"] is True
	removed = parsed["data"]["diff"]["removed"]
	assert [item["uid"] for item in removed] == ["u_prev"]
	assert out_path.exists()


# ── 3. 超长 ASCII 数字 mid 不能当游标 ───────────────────────────────


def test_message_cursor_id_rejects_overlong_ascii_digits():
	"""4301 位 ASCII 数字串会触发 int_max_str_digits 上限，按不可用处理。"""
	assert _message_cursor_id("9" * 4301) is None
	assert _message_cursor_id("123") == 123


@patch("boss_agent_cli.commands.recruiter.chat.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.chat.AuthManager")
def test_hr_chatmsg_skips_overlong_mid_when_choosing_cursor(mock_auth_cls, mock_platform_cls):
	platform = _ctx_mock(mock_platform_cls)
	platform.chat_history.return_value = {
		"code": 200,
		"data": {
			"messages": [
				{"mid": "9" * 4301, "from": {"uid": 99}, "content": "漂移"},
				{"mid": 100, "from": {"uid": 99}, "content": "正常"},
			],
			"hasMore": True,
		},
	}

	result = CliRunner().invoke(cli, ["--json", "hr", "chatmsg", "12345"])

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.stdout)
	assert parsed["pagination"]["next_max_msg_id"] == 100
