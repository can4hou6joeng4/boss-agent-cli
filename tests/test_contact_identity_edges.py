"""联系人身份边界回归：list 容器漂移、uid 为空、分页上限的错误语义。

锁住三条不变量：

1. ``result`` 是真值但非列表（对象 / 数字 / 非空字符串）时，不能遮蔽合法的
   ``friendList``（Issue #1351）；
2. ``uid`` 键存在但为 null 时不能字符串化成 ``"None"`` 被当作 gid / uniqueId /
   friendId 使用（Issue #1352）；
3. 分页上限是确定性结果，恢复指引必须指向 ``boss chat`` 取稳定 uid，而不是
   让调用方盲目重试同一段按 securityId 的查询（Issue #1353）。
"""

import datetime
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.services.contact_lookup import FriendLookupLimitExceeded, find_friend


def _ctx_mock(mock_cls):
	"""让 mock 类支持 context manager，并模拟平台 envelope 解包。"""
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: (
		response.get("zpData") if "zpData" in response else response.get("data")
	)
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	return instance


def _friend_item(uid=10001, sid="sid_current", name="联系人甲", **overrides):
	item = {
		"uid": uid,
		"securityId": sid,
		"name": name,
		"brandName": "示例公司",
		"title": "招聘专员",
		"friendSource": 0,
		"encryptJobId": "job_001",
		"lastMsg": "你好",
		"lastTS": 1700000000000,
		"unreadMsgCount": 1,
		"relationType": 2,
		"lastMessageInfo": {"status": 2},
	}
	item.update(overrides)
	return item


def _friend_page(items, *, result=None, has_more=False):
	"""构造一页沟通列表；``result`` 用显式哨兵，避免和「没有 result」混淆。"""
	data = {"friendList": items, "hasMore": has_more}
	if result is not None:
		data["result"] = result
	return {"zpData": data}


def _invoke(module, argv, response, *, tmp_path=None, platform_returns=None):
	with patch(f"{module}.get_platform_instance") as platform_cls, patch(f"{module}.AuthManager"):
		client = _ctx_mock(platform_cls)
		client.friend_list.return_value = response
		for name, payload in (platform_returns or {}).items():
			getattr(client, name).return_value = payload
		full_argv = list(argv)
		if tmp_path is not None:
			full_argv = ["--data-dir", str(tmp_path), *full_argv]
		result = CliRunner().invoke(cli, full_argv)
	return result, client


def _success(payload):
	return {"zpData": payload}


# ── 1. result 是真值非列表时回退 friendList（Issue #1351） ────────────

_DRIFTED_RESULTS = [
	pytest.param({"unexpected": "object"}, id="truthy-dict"),
	pytest.param(1, id="truthy-int"),
	pytest.param("legacy-payload", id="truthy-string"),
]


@pytest.mark.parametrize("drifted_result", _DRIFTED_RESULTS)
def test_find_friend_reads_friend_list_when_result_is_truthy_non_list(drifted_result):
	"""result 是对象 / 数字 / 字符串时，find_friend 必须回退到合法的 friendList。"""
	response = _friend_page([_friend_item()], result=drifted_result)
	platform = SimpleNamespace(
		friend_list=lambda *, page: response,
		is_success=lambda resp: True,
		unwrap_data=lambda resp: resp.get("zpData"),
	)

	friend_item, error_response = find_friend(platform, "10001")

	assert friend_item == _friend_item()
	assert error_response is None


@pytest.mark.parametrize("drifted_result", _DRIFTED_RESULTS)
def test_chat_reads_friend_list_when_result_is_truthy_non_list(drifted_result, tmp_path: Path):
	response = _friend_page([_friend_item()], result=drifted_result)

	result, _ = _invoke(
		"boss_agent_cli.commands.chat", ["chat"], response, tmp_path=tmp_path,
	)

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert [(item["uid"], item["name"]) for item in parsed["data"]] == [(10001, "联系人甲")]


@pytest.mark.parametrize("drifted_result", _DRIFTED_RESULTS)
def test_digest_counts_contacts_when_result_is_truthy_non_list(drifted_result, tmp_path: Path):
	response = _friend_page([_friend_item()], result=drifted_result)

	result, _ = _invoke(
		"boss_agent_cli.commands.digest",
		["digest"],
		response,
		tmp_path=tmp_path,
		platform_returns={"interview_data": _success({"interviewList": []})},
	)

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert parsed["data"]["new_match_count"] == 1
	assert parsed["data"]["new_matches"][0]["company"] == "示例公司"


@pytest.mark.parametrize("drifted_result", _DRIFTED_RESULTS)
def test_pipeline_contains_contacts_when_result_is_truthy_non_list(drifted_result, tmp_path: Path):
	response = _friend_page([_friend_item()], result=drifted_result)

	result, _ = _invoke(
		"boss_agent_cli.commands.pipeline",
		["pipeline"],
		response,
		tmp_path=tmp_path,
		platform_returns={"interview_data": _success({"interviewList": []})},
	)

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert [(item["source"], item["company"]) for item in parsed["data"]] == [("chat", "示例公司")]


@pytest.mark.parametrize("drifted_result", _DRIFTED_RESULTS)
def test_chat_export_snapshot_keeps_contacts_when_result_is_truthy_non_list(
	drifted_result, tmp_path: Path,
):
	"""空列表写进当日快照会让次日 diff 把全部联系人误报成「已消失」。"""
	response = _friend_page([_friend_item()], result=drifted_result)

	result, _ = _invoke(
		"boss_agent_cli.commands.chat",
		["chat", "--export", "json"],
		response,
		tmp_path=tmp_path,
	)

	assert result.exit_code == 0, result.output
	today = datetime.date.today().isoformat()
	snapshot_files = list(tmp_path.glob(f"chat-history/{today}.json"))
	assert len(snapshot_files) == 1
	snapshot = json.loads(snapshot_files[0].read_text(encoding="utf-8"))
	assert [(item["uid"], item["name"]) for item in snapshot] == [(10001, "联系人甲")]


# ── 1b. result 只含坏元素时回退 friendList（Issue #1402） ─────────────

_BAD_RESULT_ELEMENTS = [
	pytest.param([None], id="null-element"),
	pytest.param([5], id="numeric-element"),
	pytest.param([None, 5, "legacy-payload"], id="mixed-bad-elements"),
]


@pytest.mark.parametrize("bad_result", _BAD_RESULT_ELEMENTS)
def test_find_friend_reads_friend_list_when_result_has_only_bad_elements(bad_result):
	"""result 是非空列表但全是 null / 数字 / 字符串时，也要回退到合法的 friendList。"""
	response = _friend_page([_friend_item()], result=bad_result)
	platform = SimpleNamespace(
		friend_list=lambda *, page: response,
		is_success=lambda resp: True,
		unwrap_data=lambda resp: resp.get("zpData"),
	)

	friend_item, error_response = find_friend(platform, "10001")

	assert friend_item == _friend_item()
	assert error_response is None


@pytest.mark.parametrize("bad_result", _BAD_RESULT_ELEMENTS)
def test_chat_reads_friend_list_when_result_has_only_bad_elements(bad_result, tmp_path: Path):
	response = _friend_page([_friend_item()], result=bad_result)

	result, _ = _invoke(
		"boss_agent_cli.commands.chat", ["chat"], response, tmp_path=tmp_path,
	)

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert [(item["uid"], item["name"]) for item in parsed["data"]] == [(10001, "联系人甲")]


# ── 2. uid 为 null 不能被字符串化成 "None"（Issue #1352） ──────────────

_UID_NULL_CASES = [
	pytest.param(
		"boss_agent_cli.commands.chatmsg", ["chatmsg", "sid_current"], "chat_history",
		id="chatmsg",
	),
	pytest.param(
		"boss_agent_cli.commands.chat_summary", ["chat-summary", "sid_current"], "chat_history",
		id="chat-summary",
	),
	pytest.param(
		"boss_agent_cli.commands.exchange", ["exchange", "sid_current"], "exchange_contact",
		id="exchange",
	),
	pytest.param(
		"boss_agent_cli.commands.mark", ["mark", "sid_current", "--label", "沟通中"], "friend_label",
		id="mark",
	),
]


@pytest.mark.parametrize(("module", "argv", "platform_method"), _UID_NULL_CASES)
def test_uid_null_stops_before_platform_write_or_history(
	module, argv, platform_method, tmp_path: Path,
):
	"""uid 键存在且为 null 时，不得把 "None" / 空串当作 gid 发请求。"""
	response = _friend_page([_friend_item(uid=None)])

	result, client = _invoke(module, argv, response, tmp_path=tmp_path)

	assert result.exit_code == 1, result.output
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "NETWORK_ERROR"
	assert parsed["error"]["recoverable"] is False

	chat_cmd = f"boss --data-dir {tmp_path.resolve()} chat"
	assert chat_cmd in parsed["error"]["recovery_action"]
	assert parsed["hints"]["next_actions"] == [chat_cmd]
	# 不在 uid 缺失时生成必然失败的 chatmsg 后继命令。
	assert not any("chatmsg" in action for action in parsed["hints"]["next_actions"])
	getattr(client, platform_method).assert_not_called()


def test_exchange_passes_real_uid_to_platform(tmp_path: Path):
	response = _friend_page([_friend_item()])

	result, client = _invoke(
		"boss_agent_cli.commands.exchange",
		["exchange", "10001"],
		response,
		tmp_path=tmp_path,
		platform_returns={"exchange_contact": _success({})},
	)

	assert result.exit_code == 0, result.output
	# exchange_contact(security_id, uid, name, exchange_type=...)
	args = client.exchange_contact.call_args.args
	assert args[:3] == ("sid_current", "10001", "联系人甲")
	assert "None" not in args


def test_mark_passes_real_uid_to_platform(tmp_path: Path):
	response = _friend_page([_friend_item()])

	result, client = _invoke(
		"boss_agent_cli.commands.mark",
		["mark", "10001", "--label", "沟通中"],
		response,
		tmp_path=tmp_path,
		platform_returns={"friend_label": _success({})},
	)

	assert result.exit_code == 0, result.output
	args = client.friend_label.call_args.args
	assert args[0] == "10001"
	assert "None" not in args


def test_chatmsg_normalizes_null_sender_identity(tmp_path: Path):
	response = _friend_page([_friend_item()])
	messages = [
		{"from": {"uid": None, "name": "某人"}, "type": 1, "text": "null uid", "time": 1700000000000},
		{"from": {"uid": 10001, "name": None}, "type": 1, "text": "null name", "time": 1700000001000},
		{"from": {"uid": 10001}, "type": 1, "text": "missing name", "time": 1700000002000},
		{"type": 1, "text": "missing from", "time": 1700000003000},
		{"from": {"uid": 20002, "name": None}, "type": 1, "text": "self", "time": 1700000004000},
	]

	result, client = _invoke(
		"boss_agent_cli.commands.chatmsg",
		["chatmsg", "10001"],
		response,
		tmp_path=tmp_path,
		platform_returns={"chat_history": _success({"messages": messages})},
	)

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert [item["from"] for item in parsed["data"]] == [
		"未知", "联系人甲", "联系人甲", "未知", "我",
	]
	# gid 必须是稳定的真实 uid，不能是 "None" 或空串。
	call = client.chat_history.call_args
	assert call.args[:2] == ("10001", "sid_current")
	assert "None" not in call.args


# ── 3. 分页上限的恢复指引指向稳定 uid（Issue #1353） ──────────────────


def test_friend_lookup_limit_recovery_points_to_chat_uid(tmp_path: Path):
	chat_cmd = f"boss --data-dir {tmp_path.resolve()} chat"
	with patch(
		"boss_agent_cli.commands._contact_lookup.find_friend",
		side_effect=FriendLookupLimitExceeded(
			"沟通列表分页遍历超过上限，未能确认联系人是否存在，请重试"
		),
	), patch("boss_agent_cli.commands.chatmsg.get_platform_instance") as platform_cls, \
			patch("boss_agent_cli.commands.chatmsg.AuthManager"):
		client = _ctx_mock(platform_cls)
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "chatmsg", "10001"])

	assert result.exit_code == 1, result.output
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "NETWORK_ERROR"
	recovery = parsed["error"]["recovery_action"]
	# 裸「重试」必然得到同一结果；必须给出取稳定 uid 的可执行路径。
	assert recovery != "重试"
	assert chat_cmd in recovery
	assert "按会轮换的 securityId 查询超过分页上限时，重试同样的查询不会改变结果" in recovery
	assert parsed["hints"]["next_actions"] == [chat_cmd]
	client.chat_history.assert_not_called()
