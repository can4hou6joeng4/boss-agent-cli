"""沟通 / 面试列表的元素级漂移：坏元素只跳过自己，不拖垮整条命令。

平台把 ``result`` / ``interviewList`` 返回成 ``[null, 123, "x", {...}]`` 时，
``build_pipeline_items``、``_friend_record`` 等下游会直接 ``item.get``；
``zpData`` / ``interview_data`` 本身漂移成字符串时取子字段同样会崩。
这里统一按 ``api.models.dict_items`` 的口径：非对象元素跳过，容器不是对象按空处理。
"""

from __future__ import annotations

import importlib
import json

import pytest
from click.testing import CliRunner

from boss_agent_cli.api.models import dict_items
from boss_agent_cli.api.recruiter_client import BossRecruiterClient
from boss_agent_cli.chat_summary import summarize_messages
from boss_agent_cli.services.chat_utils import message_list
from boss_agent_cli.main import cli
from boss_agent_cli.pipeline_state import build_pipeline_items
from boss_agent_cli.services.contact_lookup import find_friend
from boss_agent_cli.services.friend_list_pages import collect_friend_list_page

FRIEND = {
	"uid": "u1",
	"name": "甲",
	"lastTS": 1700000000000,
	"relationType": 1,
	"unreadMsgCount": 2,
	"securityId": "s1",
	"encryptJobId": "j1",
}
INTERVIEW = {"jobName": "后端", "brandName": "乙公司", "interviewTime": "2026-11-20 10:00"}


class _FakePlatform:
	def __init__(
		self,
		*friend_pages: object,
		interview: object = None,
		messages: object = None,
	):
		self._friend_pages = list(friend_pages)
		self._interview = interview if interview is not None else {"interviewList": []}
		self._messages = messages if messages is not None else []
		self.friend_calls = 0

	def __enter__(self):
		return self

	def __exit__(self, *exc):
		return None

	def friend_list(self, page: int = 1):
		index = min(page - 1, len(self._friend_pages) - 1)
		self.friend_calls += 1
		payload = self._friend_pages[index]
		return {"code": 0, "zpData": payload}

	def interview_data(self):
		return {"code": 0, "zpData": self._interview}

	def chat_history(self, gid, security_id, page: int = 1, count: int = 20):
		return {"code": 0, "zpData": {"messages": self._messages, "hasMore": False}}

	def is_success(self, response):
		return isinstance(response, dict) and response.get("code") == 0

	def unwrap_data(self, response):
		return response.get("zpData")


DRIFTED_FRIENDS = {
	"result": [None, 123, "x", FRIEND],
	"hasMore": False,
}


def _invoke_command(module: str, platform: _FakePlatform, *args: str):
	command_module = importlib.import_module(f"boss_agent_cli.commands.{module.replace('-', '_')}")
	with pytest.MonkeyPatch.context() as patch:
		patch.setattr(command_module, "get_platform_instance", lambda *a, **kw: platform)
		patch.setattr(command_module, "AuthManager", lambda *a, **kw: None)
		return CliRunner().invoke(cli, ["--json", module, *args])


# ── 命令级：chat / pipeline / digest ─────────────────────────────


@pytest.mark.parametrize("module", ["chat", "pipeline", "digest"])
def test_commands_skip_non_object_friend_and_interview_items(module):
	platform = _FakePlatform(
		{"result": [None, 123, "x", FRIEND], "hasMore": False},
		interview={"interviewList": [None, 5, "x", INTERVIEW]},
	)
	result = _invoke_command(module, platform)

	assert result.exit_code == 0, result.output
	envelope = json.loads(result.stdout)
	assert envelope["ok"] is True
	assert envelope["error"] is None
	if module == "chat":
		assert len(envelope["data"]) == 1
		assert envelope["data"][0]["uid"] == "u1"
	elif module == "pipeline":
		assert len(envelope["data"]) == 2
		assert envelope["pagination"]["total"] == 2
	else:
		# 一条沟通 + 一条面试；坏元素不计入任何分组。
		assert len(envelope["data"]["interviews"]) == 1
		assert envelope["data"]["interview_count"] == 1


@pytest.mark.parametrize("module", ["chat", "pipeline", "digest"])
def test_commands_treat_non_object_platform_data_as_empty(module):
	platform = _FakePlatform("oops", interview="oops")
	result = _invoke_command(module, platform)

	assert result.exit_code == 0, result.output
	envelope = json.loads(result.stdout)
	assert envelope["ok"] is True
	assert envelope["error"] is None


# ── 服务层 ───────────────────────────────────────────────────────


def test_dict_items_skips_drifted_elements():
	assert dict_items(None) == []
	assert dict_items("x") == []
	assert dict_items({"a": 1}) == []
	assert dict_items([None, 1, "x", {"a": 1}]) == [{"a": 1}]


def test_collect_friend_list_page_skips_bad_elements_and_stops_on_drifted_payload():
	platform = _FakePlatform({"result": [None, FRIEND], "hasMore": False})
	items, error, pages, truncated = collect_friend_list_page(platform)
	assert error is None
	assert items == [FRIEND]
	assert pages == 1
	assert truncated is False

	drifted = _FakePlatform("oops")
	items, error, pages, truncated = collect_friend_list_page(drifted)
	assert (items, error, pages, truncated) == ([], None, 1, False)
	assert drifted.friend_calls == 1


def test_find_friend_skips_bad_elements_and_stops_on_drifted_payload():
	platform = _FakePlatform({"result": [None, FRIEND], "hasMore": False})
	assert find_friend(platform, "u1") == (FRIEND, None)

	drifted = _FakePlatform("oops")
	assert find_friend(drifted, "u1") == (None, None)
	assert drifted.friend_calls == 1


def test_build_pipeline_items_skips_non_object_elements():
	items = build_pipeline_items(
		chat_items=[None, FRIEND],  # type: ignore[list-item]
		interview_items=[123, INTERVIEW],  # type: ignore[list-item]
		now_ts_ms=1700000000000,
	)
	assert [item["source"] for item in items] == ["chat", "interview"]


MESSAGE = {"text": "你好", "from": {"uid": "u1"}, "time": 1700000000000}


@pytest.mark.parametrize("module", ["chatmsg", "chat-summary"])
def test_message_commands_skip_non_object_messages(module):
	platform = _FakePlatform(
		{"result": [FRIEND], "hasMore": False},
		messages=[None, 5, "x", MESSAGE],
	)
	result = _invoke_command(module, platform, "u1")

	assert result.exit_code == 0, result.output
	envelope = json.loads(result.stdout)
	assert envelope["ok"] is True
	assert envelope["error"] is None
	if module == "chatmsg":
		assert len(envelope["data"]) == 1
	else:
		assert envelope["data"]["message_count"] == 1


def test_message_list_and_summary_tolerate_drifted_message_fields():
	assert message_list({"messages": [None, 1, {"a": 1}]}) == [{"a": 1}]
	summary = summarize_messages(
		[{"text": 7, "from": "oops", "body": "oops"}],  # type: ignore[list-item]
		friend_uid="u1",
	)
	assert summary["message_count"] == 1
	assert summary["stage"] in {"new", "waiting", "reply_needed", "interview"}


def test_require_chat_friend_data_rejects_non_object_zpdata():
	client = BossRecruiterClient.__new__(BossRecruiterClient)
	client.friend_detail = lambda ids: {"code": 0, "zpData": "oops"}  # type: ignore[method-assign]
	client._check_cdp_risk_lock = lambda: None  # type: ignore[method-assign]
	client._raise_platform_risk_response = lambda *a, **kw: None  # type: ignore[method-assign]

	with pytest.raises(LookupError):
		client._require_chat_friend_data(42)
