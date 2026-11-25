"""向导聊天步骤与 CLI 的消息归一化口径一致（Issue #2801）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from boss_agent_cli.output import Logger
from boss_agent_cli.services.recruiter_chat_summary import normalize_last_message
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS

_RAW_LAST = {
	"from": {"uid": 101},
	"to": {"uid": 9001},
	"content": "你好，方便聊聊吗",
	"lastTS": 1730000000000,
	"unreadMsgCount": "2",
}
_RAW_HISTORY = {
	"id": 7,
	"from": {"uid": 101, "name": "甲"},
	"type": 1,
	"time": 1730000000000,
	"text": "你好",
}


class _RecruiterPlatform:
	def __init__(self) -> None:
		self.message_calls: list[list[int]] = []

	def __enter__(self) -> "_RecruiterPlatform":
		return self

	def __exit__(self, *args: object) -> None:
		return None

	def friend_list(self, page: int = 1, label_id: int = 0, job_id: Any = None) -> dict[str, Any]:
		return {"code": 0, "zpData": {"hasMore": False, "friendList": [{"friendId": 101, "friendSource": 0}]}}

	def last_messages(self, friend_ids: list[int]) -> dict[str, Any]:
		self.message_calls.append(list(friend_ids))
		return {"code": 0, "zpData": {"messages": [_RAW_LAST]}}

	def is_success(self, response: dict[str, Any]) -> bool:
		return response.get("code") == 0

	def unwrap_data(self, response: dict[str, Any]) -> Any:
		return response.get("zpData") or {}

	def parse_error(self, response: dict[str, Any]) -> tuple[str, str]:
		return "UNKNOWN", ""


class _CandidatePlatform:
	def __enter__(self) -> "_CandidatePlatform":
		return self

	def __exit__(self, *args: object) -> None:
		return None

	def friend_list(self, page: int = 1) -> dict[str, Any]:
		return {
			"code": 0,
			"zpData": {
				"hasMore": False,
				"friendList": [{"uid": "101", "securityId": "sec-1", "name": "甲"}],
			},
		}

	def chat_history(self, gid: str, security_id: str, page: int = 1, count: int = 20) -> dict[str, Any]:
		return {"code": 0, "zpData": {"hasMore": False, "messageList": [_RAW_HISTORY]}}

	def is_success(self, response: dict[str, Any]) -> bool:
		return response.get("code") == 0

	def unwrap_data(self, response: dict[str, Any]) -> Any:
		return response.get("zpData") or {}

	def parse_error(self, response: dict[str, Any]) -> tuple[str, str]:
		return "UNKNOWN", ""


def _run(tmp_path: Any, action: str, platform: Any, inputs: dict[str, Any]) -> dict[str, Any]:
	if action == "recruiter_last_messages":
		factory = {"recruiter_factory": lambda *args: platform}
	else:
		factory = {"candidate_factory": lambda *args: platform}
	context = ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="recruiter" if action.startswith("recruiter_") else "candidate",
		logger=Logger(),
		**factory,
	)
	return dict(DEFAULT_ACTIONS[action](context, inputs, {}).data)


def test_recruiter_last_messages_expose_cli_summaries(tmp_path):
	data = _run(tmp_path, "recruiter_last_messages", _RecruiterPlatform(), {})

	assert data["messages"] == [normalize_last_message(_RAW_LAST, {101})]
	assert data["items"] == [_RAW_LAST]


def test_candidate_chat_history_exposes_cli_message_shape(tmp_path):
	data = _run(tmp_path, "candidate_chat_history", _CandidatePlatform(), {"gid": "101"})

	expected_time = datetime.fromtimestamp(1730000000000 / 1000).strftime("%m-%d %H:%M")
	assert data["messages"] == [{"from": "甲", "type": "文本", "text": "你好", "time": expected_time}]
	assert data["items"] == [_RAW_HISTORY]
	assert data["total"] == 1
