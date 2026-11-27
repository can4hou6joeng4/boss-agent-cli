"""chat 家族字段类型漂移必须按缺失归一（Issue #3075）。

``boss chat`` 的 ``_friend_record`` 此前用 ``or`` / ``.get(key, "-")`` 组装
字段：容器 / 布尔 / ``null`` 会原样进入成功信封，``uid`` 不是字符串时快照
身份为空、下游 ``chatmsg`` / ``mark`` 拿到不可用标识。``chatmsg --raw`` 的
``_first_present`` 则把 ``0`` / ``False`` / ``[]`` 当成「在场」，遮蔽 body
里的合法字符串。
"""

import csv
import io
import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.services.chat_export import prepare_render_data, render_export
from boss_agent_cli.services.chat_utils import normalize_chat_message


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: response.get("zpData")
	instance.is_success.side_effect = lambda response: response.get("code", 0) == 0
	return instance


def _drifted_friend_item() -> dict:
	return {
		"uid": {"nested": "not-an-id"},
		"securityId": ["sid-as-list"],
		"name": {"x": 1},
		"brandName": ["brand-list"],
		"title": True,
		"encryptJobId": ["job-as-list"],
		"lastMsg": {"text": "hi"},
		"lastTS": 0,
		"lastTime": None,
		"unreadMsgCount": 0,
		"relationType": 2,
		"lastMessageInfo": {"status": 2},
	}


@patch("boss_agent_cli.commands.chat.get_platform_instance")
@patch("boss_agent_cli.commands.chat.AuthManager")
def test_chat_normalizes_drifted_contact_fields(mock_auth_cls, mock_platform_cls, tmp_path: Path):
	"""容器 / 布尔 / null 字段按缺失回退，成功信封里不再出现不可用类型。"""
	platform = _ctx_mock(mock_platform_cls)
	item = _drifted_friend_item()
	platform.friend_list.return_value = {"code": 0, "zpData": {"result": [item], "friendList": [item], "hasMore": False}}

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "chat"])

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.stdout)
	assert parsed["ok"] is True
	record = parsed["data"][0]
	assert record["name"] == "-"
	assert record["title"] == "-"
	assert record["brand_name"] == "-"
	assert record["last_msg"] == "-"
	assert record["last_time"] == "-"
	assert record["uid"] == ""
	assert record["security_id"] == ""
	assert record["encrypt_job_id"] == ""


@patch("boss_agent_cli.commands.chat.get_platform_instance")
@patch("boss_agent_cli.commands.chat.AuthManager")
def test_chat_preserves_valid_string_fields(mock_auth_cls, mock_platform_cls, tmp_path: Path):
	"""合法字符串字段保持原值，归一化不能误伤正常响应。"""
	platform = _ctx_mock(mock_platform_cls)
	item = {
		"uid": "u_curr",
		"securityId": "sid_curr",
		"name": "联系人甲",
		"brandName": "示例公司",
		"title": "招聘专员",
		"encryptJobId": "job_001",
		"lastMsg": "你好",
		"lastTS": 0,
		"lastTime": "昨天",
		"unreadMsgCount": 0,
		"relationType": 2,
		"lastMessageInfo": {"status": 2},
	}
	platform.friend_list.return_value = {"code": 0, "zpData": {"result": [item], "friendList": [item], "hasMore": False}}

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "chat"])

	assert result.exit_code == 0, result.output
	record = json.loads(result.stdout)["data"][0]
	assert record["name"] == "联系人甲"
	assert record["title"] == "招聘专员"
	assert record["brand_name"] == "示例公司"
	assert record["last_msg"] == "你好"
	assert record["last_time"] == "昨天"
	assert record["uid"] == "u_curr"
	assert record["security_id"] == "sid_curr"
	assert record["encrypt_job_id"] == "job_001"


def test_raw_identifiers_fall_back_to_body_when_top_level_is_falsy_non_string():
	"""顶层 0 / False 不是可用标识，必须继续读 body 里的合法字符串。"""
	msg = {
		"from": {"uid": "u1"},
		"securityId": 0,
		"jobId": False,
		"body": {"securityId": "valid-sid", "jobId": "valid-jid"},
	}

	out = normalize_chat_message(msg, gid="u1", friend_name="n", include_raw=True)

	assert out["security_id"] == "valid-sid"
	assert out["job_id"] == "valid-jid"


def test_raw_identifiers_reject_container_values():
	"""容器不是标识：没有合法回退时按缺失（None），不能原样进入信封。"""
	msg = {"from": {"uid": "u1"}, "securityId": [], "jobId": {}}

	out = normalize_chat_message(msg, gid="u1", friend_name="n", include_raw=True)

	assert out["security_id"] is None
	assert out["job_id"] is None


def test_csv_unread_bool_matches_markdown_normalization():
	"""同一份数据里 bool 未读数在 CSV 与 Markdown 都要按 0 处理（Issue #2886 同口径）。"""
	friend = {"uid": "u1", "name": "A", "msg_status": "未读", "initiated_by": "我主动", "unread": True}

	row = prepare_render_data([friend], None, {})["sections"][0]["rows"][0]
	assert row["unread"] == 0

	csv_text = render_export([friend], "csv", None, None, {})
	rows = list(csv.DictReader(io.StringIO(csv_text)))
	assert rows and rows[0]["unread"] == "0"


def test_null_initiated_by_groups_as_unknown():
	"""``initiated_by`` 键存在但为 null 时按「未知」分组，不能落成空分组名。"""
	friend = {"uid": "u1", "name": "A", "initiated_by": None, "unread": 0}

	data = prepare_render_data([friend], None, {})

	subtitles = [section["subtitle"] for section in data["sections"]]
	assert any(subtitle.startswith("未知（1 条") for subtitle in subtitles)
