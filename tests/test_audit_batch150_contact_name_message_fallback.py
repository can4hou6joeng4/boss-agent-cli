"""联系人名字、聊天消息与简历教育经历的「真值但不可用」回归（Issue #2748 #2749 #2750）。

三处同类缺陷：

1. 联系人 ``name`` 类型漂移时，向导 ``exchange`` 把 Python repr 发进写请求，
   ``mark`` / ``chat-summary`` / ``chatmsg`` 把容器原样写进信封（#2748）；
2. ``chatmsg`` 把发送方 uid 漂移的消息标成「我」，顶层 ``text`` 为真值非字符串
   时吞掉合法的 ``body.text``（#2749）；
3. ``hr resume`` 教育经历的主键漂移时吞掉合法的回退键（#2750）。
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.services.chat_utils import message_text_value
from boss_agent_cli.services.contact_lookup import friend_display_name
from boss_agent_cli.services.resume_parser import parse_resume
from boss_agent_cli.wizard.actions import DEFAULT_ACTIONS, ActionContext
from boss_agent_cli.wizard.runner import WorkflowActionError

_DRIFTED_NAMES = [{"n": 1}, ["x"], 42, True, None, float("nan"), "", "   "]
_DRIFTED_NAME_IDS = ["dict", "list", "int", "bool", "null", "nan", "empty", "blank"]


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: response.get("zpData") if "zpData" in response else response.get("data")
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	return instance


def _friend(**overrides):
	item = {
		"uid": 10001, "securityId": "sid_now", "name": "联系人甲",
		"friendSource": 0, "encryptJobId": "job_001", "relationType": 1,
	}
	item.update(overrides)
	return item


def _invoke(module, argv, tmp_path, *, friend, **platform_returns):
	with patch(f"boss_agent_cli.commands.{module}.AuthManager"), patch(
		f"boss_agent_cli.commands.{module}.get_platform_instance"
	) as mock_cls:
		client = _ctx_mock(mock_cls)
		client.friend_list.return_value = {"zpData": {"result": [friend], "friendList": [friend], "hasMore": False}}
		for name, value in platform_returns.items():
			getattr(client, name).return_value = value
		out = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", *argv])
	return out, client


def _chatmsg(tmp_path, messages, *, friend=None, extra=()):
	out, _ = _invoke(
		"chatmsg", ["chatmsg", "10001", *extra], tmp_path,
		friend=friend or _friend(),
		chat_history={"code": 0, "zpData": {"messages": messages}},
	)
	assert out.exit_code == 0, out.output
	return json.loads(out.stdout)["data"]


# ── #2748：联系人显示名只留一处判定 ─────────────────────────────


@pytest.mark.parametrize("name", _DRIFTED_NAMES, ids=_DRIFTED_NAME_IDS)
def test_friend_display_name_falls_back_to_placeholder(name):
	assert friend_display_name({"name": name}) == "-"


def test_friend_display_name_keeps_and_strips_real_name():
	assert friend_display_name({"name": " 联系人甲 "}) == "联系人甲"
	assert friend_display_name({}) == "-"


class _FakeCandidatePlatform:
	def __init__(self, name, *, exchange_code=0):
		self.calls: list[tuple] = []
		self._name = name
		self._exchange_code = exchange_code

	def __enter__(self):
		return self

	def __exit__(self, *args):
		return None

	def friend_list(self, *, page=1):
		return {
			"code": 0,
			"data": {
				"result": [{"uid": 10001, "securityId": "sid_current", "name": self._name, "friendSource": 0}],
				"hasMore": False,
			},
		}

	def exchange_contact(self, security_id, uid, name, exchange_type=1):
		self.calls.append(("exchange_contact", security_id, uid, name, exchange_type))
		return {"code": self._exchange_code, "data": {"requested": True}}

	def is_success(self, response):
		return response["code"] == 0

	def unwrap_data(self, response):
		return response["data"]

	def parse_error(self, response):
		return "RATE_LIMITED", "请求过于频繁"


def _action_context(tmp_path, platform):
	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
		candidate_factory=lambda name, auth, delay, cdp_url, browser_source=None: platform,
		recruiter_factory=lambda name, auth, delay, cdp_url, browser_source=None: None,
	)


@pytest.mark.parametrize("name", _DRIFTED_NAMES, ids=_DRIFTED_NAME_IDS)
def test_wizard_exchange_never_sends_python_repr_as_name(tmp_path, name):
	"""向导 exchange 的写请求与 CLI `exchange` 同口径（#1812）：漂移 name 回落 `-`。"""
	platform = _FakeCandidatePlatform(name)

	DEFAULT_ACTIONS["candidate_exchange"](
		_action_context(tmp_path, platform), {"security_id": "10001", "type": "wechat"}, {},
	)

	assert platform.calls == [("exchange_contact", "sid_current", "10001", "-", 2)]


def test_wizard_exchange_sends_stripped_real_name(tmp_path):
	platform = _FakeCandidatePlatform(" 联系人甲 ")

	DEFAULT_ACTIONS["candidate_exchange"](
		_action_context(tmp_path, platform), {"security_id": "10001", "type": "phone"}, {},
	)

	assert platform.calls == [("exchange_contact", "sid_current", "10001", "联系人甲", 1)]


def test_wizard_exchange_platform_failure_keeps_placeholder_and_error_code(tmp_path):
	"""错误路径：平台拒绝时照常按平台错误码上报，发出的 name 仍是占位符。"""
	platform = _FakeCandidatePlatform({"n": 1}, exchange_code=9)

	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["candidate_exchange"](
			_action_context(tmp_path, platform), {"security_id": "10001", "type": "wechat"}, {},
		)

	assert exc_info.value.code == "RATE_LIMITED"
	assert platform.calls[0][3] == "-"


@pytest.mark.parametrize("name", _DRIFTED_NAMES, ids=_DRIFTED_NAME_IDS)
def test_mark_name_is_always_a_string(tmp_path, name):
	out, _ = _invoke(
		"mark", ["mark", "10001", "--label", "沟通中"], tmp_path,
		friend=_friend(name=name), friend_label={"code": 0, "zpData": {}},
	)

	assert out.exit_code == 0, out.output
	assert json.loads(out.stdout)["data"]["name"] == "-"


@pytest.mark.parametrize("name", _DRIFTED_NAMES, ids=_DRIFTED_NAME_IDS)
def test_chat_summary_name_is_always_a_string(tmp_path, name):
	out, _ = _invoke(
		"chat_summary", ["chat-summary", "10001"], tmp_path,
		friend=_friend(name=name),
		chat_history={"code": 0, "zpData": {"messages": [{"from": {"uid": 10001}, "text": "你好"}]}},
	)

	assert out.exit_code == 0, out.output
	assert json.loads(out.stdout)["data"]["name"] == "-"


def test_mark_and_chat_summary_keep_real_name(tmp_path):
	marked, _ = _invoke(
		"mark", ["mark", "10001", "--label", "沟通中"], tmp_path,
		friend=_friend(name=" 联系人甲 "), friend_label={"code": 0, "zpData": {}},
	)
	summary, _ = _invoke(
		"chat_summary", ["chat-summary", "10001"], tmp_path,
		friend=_friend(), chat_history={"code": 0, "zpData": {"messages": []}},
	)

	assert json.loads(marked.stdout)["data"]["name"] == "联系人甲"
	assert json.loads(summary.stdout)["data"]["name"] == "联系人甲"


def test_mark_platform_failure_with_drifted_name_reports_platform_error(tmp_path):
	"""错误路径：name 漂移不改变平台失败的分类，也不会落成 INTERNAL_ERROR。"""
	with patch("boss_agent_cli.commands.mark.AuthManager"), patch(
		"boss_agent_cli.commands.mark.get_platform_instance"
	) as mock_cls:
		client = _ctx_mock(mock_cls)
		friend = _friend(name={"n": 1})
		client.friend_list.return_value = {"zpData": {"result": [friend], "friendList": [friend], "hasMore": False}}
		client.friend_label.return_value = {"code": 9, "message": "请求过于频繁"}
		client.parse_error.return_value = ("RATE_LIMITED", "请求过于频繁")
		out = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "mark", "10001", "--label", "沟通中"])

	assert out.exit_code == 1, out.output
	payload = json.loads(out.stdout)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "RATE_LIMITED"


@pytest.mark.parametrize("name", [{"n": 1}, ["x"], True, 42, float("nan"), "   "])
def test_chatmsg_from_never_carries_drifted_names(tmp_path, name):
	"""联系人名与消息里的发送方名字漂移时，from 仍是字符串。"""
	rows = _chatmsg(
		tmp_path,
		[
			# 联系人名漂移、消息没带名字：回落占位符。
			{"from": {"uid": 10001}, "type": 1, "text": "a"},
			# 消息里的名字漂移：回落联系人名（此处同样漂移，故为占位符）。
			{"from": {"uid": 10001, "name": name}, "type": 1, "text": "b"},
		],
		friend=_friend(name=name),
	)

	assert [row["from"] for row in rows] == ["-", "-"]


def test_chatmsg_drifted_sender_name_falls_back_to_friend_name(tmp_path):
	rows = _chatmsg(
		tmp_path,
		[
			{"from": {"uid": 10001, "name": {"n": 1}}, "type": 1, "text": "a"},
			{"from": {"uid": 10001, "name": " 张HR "}, "type": 1, "text": "b"},
		],
	)

	assert [row["from"] for row in rows] == ["联系人甲", "张HR"]


# ── #2749：chatmsg 的发送方与正文 ───────────────────────────────


@pytest.mark.parametrize(
	"uid", [True, False, {"n": 1}, [10001], 1.5, 10001.0, float("nan")],
	ids=["true", "false", "dict", "list", "float", "integral-float", "nan"],
)
def test_chatmsg_drifted_sender_uid_is_unknown_not_me(tmp_path, uid):
	"""只有明确的非空 uid 才能判定发送方（#1352）：漂移值不能落进「我」。"""
	rows = _chatmsg(tmp_path, [{"from": {"uid": uid, "name": "某人"}, "type": 1, "text": "hi"}])

	assert rows[0]["from"] == "未知"


def test_chatmsg_real_sender_uids_keep_their_labels(tmp_path):
	rows = _chatmsg(
		tmp_path,
		[
			{"from": {"uid": 10001, "name": "张HR"}, "type": 1, "text": "整数 uid"},
			{"from": {"uid": " 10001 "}, "type": 1, "text": "字符串 uid"},
			{"from": {"uid": 20002}, "type": 1, "text": "自己"},
			{"from": {"uid": "20002"}, "type": 1, "text": "自己（字符串）"},
			{"from": {"uid": None}, "type": 1, "text": "null"},
			{"type": 1, "text": "缺失"},
		],
	)

	assert [row["from"] for row in rows] == ["张HR", "联系人甲", "我", "我", "未知", "未知"]


@pytest.mark.parametrize(
	"top_text", [{"k": 1}, ["a"], True, float("nan"), float("inf"), 7, 0, "", None],
	ids=["dict", "list", "bool", "nan", "inf", "int", "zero", "empty", "null"],
)
def test_chatmsg_unusable_top_level_text_does_not_shadow_body_text(tmp_path, top_text):
	rows = _chatmsg(
		tmp_path,
		[{"from": {"uid": 10001}, "type": 1, "text": top_text, "body": {"text": "正文在 body"}}],
	)

	assert rows[0]["text"] == "正文在 body"


@pytest.mark.parametrize(
	("message", "expected"),
	[
		pytest.param({"text": "顶层", "body": {"text": "body"}}, "顶层", id="top-level-wins"),
		pytest.param({"text": 7}, "7", id="number-only"),
		pytest.param({"text": None, "body": {"text": 3.5}}, "3.5", id="body-number-only"),
		pytest.param({"text": None, "body": {"text": {"k": 1}}}, "", id="body-dict"),
		pytest.param({"body": {"text": ["x"]}}, "", id="body-list"),
		pytest.param({"text": True, "body": {"text": False}}, "", id="both-bool"),
		pytest.param({"text": float("nan"), "body": "不是对象"}, "", id="nan-and-bad-body"),
	],
)
def test_chatmsg_text_is_always_a_string(tmp_path, message, expected):
	rows = _chatmsg(tmp_path, [{"from": {"uid": 10001}, "type": 1, **message}])

	assert rows[0]["text"] == expected
	assert isinstance(rows[0]["text"], str)


def test_chatmsg_raw_still_preserves_platform_values(tmp_path):
	"""--raw 的保真字段不归一：raw / body 仍是平台原值，只有摘要列 text 归一。"""
	rows = _chatmsg(
		tmp_path,
		[{"from": {"uid": True}, "type": 1, "text": {"k": 1}, "body": {"text": "正文在 body", "extra": [1]}}],
		extra=("--raw",),
	)

	assert rows[0]["text"] == "正文在 body"
	assert rows[0]["from"] == "未知"
	assert rows[0]["raw"]["text"] == {"k": 1}
	assert rows[0]["raw"]["from"] == {"uid": True}
	assert rows[0]["body"] == {"text": "正文在 body", "extra": [1]}


def test_chatmsg_platform_failure_still_reports_platform_error(tmp_path):
	"""错误路径：平台失败照常出错误信封，归一逻辑不参与。"""
	with patch("boss_agent_cli.commands.chatmsg.AuthManager"), patch(
		"boss_agent_cli.commands.chatmsg.get_platform_instance"
	) as mock_cls:
		client = _ctx_mock(mock_cls)
		friend = _friend(name={"n": 1})
		client.friend_list.return_value = {"zpData": {"result": [friend], "friendList": [friend], "hasMore": False}}
		client.chat_history.return_value = {"code": 9, "message": "请求过于频繁"}
		client.parse_error.return_value = ("RATE_LIMITED", "请求过于频繁")
		out = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "chatmsg", "10001"])

	assert out.exit_code == 1, out.output
	payload = json.loads(out.stdout)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "RATE_LIMITED"


@pytest.mark.parametrize(
	("value", "expected"),
	[
		("你好", "你好"), ("", None), (7, "7"), (0, "0"), (1.5, "1.5"),
		(True, None), (False, None), (None, None), ({"k": 1}, None), (["a"], None),
		(float("nan"), None), (float("inf"), None), (float("-inf"), None),
	],
)
def test_message_text_value_contract(value, expected):
	assert message_text_value(value) == expected


# ── #2750：简历教育经历的回退链 ─────────────────────────────────


def _education(entry):
	return parse_resume({"zpData": {"geekDetailInfo": {"geekEduExpList": [entry]}}})["education"][0]


def test_education_drifted_primary_keys_do_not_shadow_fallbacks():
	edu = _education({
		"school": "某大学", "major": "计算机",
		"degreeDesc": ["本科"], "degreeName": "本科",
		"startYearMonStr": {"y": 2018}, "startDateDesc": "2018.09",
		"endYearMonStr": 7, "endDateDesc": "2022.06",
	})

	assert edu == {"school": "某大学", "major": "计算机", "degree": "本科", "start": "2018.09", "end": "2022.06"}


@pytest.mark.parametrize("primary", [True, 3, 1.5, {"k": 1}, ["x"]], ids=["bool", "int", "float", "dict", "list"])
def test_education_each_drifted_primary_type_falls_back(primary):
	edu = _education({"degreeDesc": primary, "degreeName": "硕士", "startYearMonStr": primary, "startDateDesc": "2020.09"})

	assert edu["degree"] == "硕士"
	assert edu["start"] == "2020.09"


def test_education_primary_string_still_wins():
	edu = _education({
		"degreeDesc": "本科", "degreeName": "大专",
		"startYearMonStr": "2018.09", "startDateDesc": "2017",
		"endYearMonStr": "2022.06", "endDateDesc": "2021",
	})

	assert (edu["degree"], edu["start"], edu["end"]) == ("本科", "2018.09", "2022.06")


def test_education_both_keys_unusable_stays_empty_string():
	"""错误路径：两个键都不可用时仍是空串，不泄漏 Python repr（#1808 的契约）。"""
	edu = _education({
		"degreeDesc": {"k": 1}, "degreeName": ["x"],
		"startYearMonStr": 0, "startDateDesc": None,
		"endYearMonStr": True,
	})

	assert (edu["degree"], edu["start"], edu["end"]) == ("", "", "")
