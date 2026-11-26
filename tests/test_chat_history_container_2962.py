"""聊天历史的容器键在 CLI 与向导之间必须一致（Issue #2962）。

同一个 ``chat_history`` 响应只替换消息容器键时，旧实现让两个入口读出不同
条数：CLI 共用 ``message_list`` 只认 ``messages`` / ``historyMsgList``，向导
的通用提取器只认 ``messageList`` / ``messages`` 等。平台返回 ``messageList``
时 CLI 静默返回空历史，返回 ``historyMsgList`` 时向导静默返回空历史。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.services.chat_utils import message_list
from boss_agent_cli.wizard.actions import DEFAULT_ACTIONS, ActionContext

NOW_MS = 1_780_000_000_000
RAW_MESSAGE = {"from": {"uid": 12345, "name": "甲"}, "type": 1, "text": "你好", "time": NOW_MS}
CONTAINERS = ("messages", "historyMsgList", "messageList")


class _Platform:
	def __init__(self, container: str) -> None:
		self.container = container

	def __enter__(self) -> "_Platform":
		return self

	def __exit__(self, *args: object) -> None:
		return None

	def friend_list(self, page: int = 1, **kwargs: Any) -> dict[str, Any]:
		return {
			"code": 0,
			"zpData": {
				"result": [{
					"uid": 12345,
					"securityId": "sec-1",
					"name": "甲",
					"encryptJobId": "job-1",
					"relationType": 1,
					"unreadMsgCount": 0,
					"lastMsg": "你好",
					"lastTS": NOW_MS,
				}],
				"hasMore": False,
			},
		}

	def chat_history(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
		return {"code": 0, "zpData": {self.container: [dict(RAW_MESSAGE)], "hasMore": False}}

	@staticmethod
	def is_success(response: dict[str, Any]) -> bool:
		return response.get("code") == 0

	@staticmethod
	def unwrap_data(response: dict[str, Any]) -> Any:
		return response.get("zpData") or {}


def _invoke_candidate(command: str, container: str, tmp_path: Path) -> Any:
	platform = _Platform(container)
	module = "chatmsg" if command == "chatmsg" else "chat_summary"
	with patch(f"boss_agent_cli.commands.{module}.AuthManager"), patch(
		f"boss_agent_cli.commands.{module}.get_platform_instance", return_value=platform
	):
		return CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), command, "sec-1"])


def _invoke_recruiter(container: str, tmp_path: Path) -> Any:
	platform = _Platform(container)
	with patch("boss_agent_cli.commands.recruiter.chat.AuthManager"), patch(
		"boss_agent_cli.commands.recruiter.chat.get_recruiter_platform_instance", return_value=platform
	):
		return CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "hr", "chatmsg", "12345"])


def _wizard_context(tmp_path: Path, platform: _Platform, *, role: str) -> ActionContext:
	factory = lambda *args: platform  # noqa: E731
	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role=role,
		logger=Logger(),
		candidate_factory=factory if role == "candidate" else None,
		recruiter_factory=factory if role == "recruiter" else None,
	)


# ── 共享解析器 ──────────────────────────────────────────────────────


@pytest.mark.parametrize("container", CONTAINERS)
def test_message_list_accepts_each_known_container(container: str) -> None:
	assert message_list({container: [dict(RAW_MESSAGE)]}) == [RAW_MESSAGE]


def test_message_list_falls_back_in_documented_order() -> None:
	assert message_list({"messages": [], "historyMsgList": [{"text": "b"}]}) == [{"text": "b"}]
	assert message_list({"messages": [], "historyMsgList": [], "messageList": [{"text": "c"}]}) == [
		{"text": "c"}
	]
	assert message_list({"messages": [{"text": "a"}], "messageList": [{"text": "c"}]}) == [{"text": "a"}]


@pytest.mark.parametrize(
	"payload",
	[
		{"messages": [None, 1, "x", {"text": "ok"}]},
		{"messageList": [None, 1, "x", {"text": "ok"}]},
	],
)
def test_message_list_skips_drifted_elements(payload: dict[str, Any]) -> None:
	assert message_list(payload) == [{"text": "ok"}]


@pytest.mark.parametrize("payload", [None, [], "messages", {"messages": "nope"}, {}])
def test_message_list_rejects_malformed_payload(payload: Any) -> None:
	assert message_list(payload) == []


# ── CLI 入口 ────────────────────────────────────────────────────────


@pytest.mark.parametrize("container", CONTAINERS)
def test_chatmsg_reads_every_known_container(container: str, tmp_path: Path) -> None:
	result = _invoke_candidate("chatmsg", container, tmp_path)

	assert result.exit_code == 0, result.output
	data = json.loads(result.stdout)["data"]
	assert [message["text"] for message in data] == ["你好"]


@pytest.mark.parametrize("container", CONTAINERS)
def test_chat_summary_reads_every_known_container(container: str, tmp_path: Path) -> None:
	result = _invoke_candidate("chat-summary", container, tmp_path)

	assert result.exit_code == 0, result.output
	envelope = json.loads(result.stdout)
	assert envelope["data"]["message_count"] == 1
	assert envelope["pagination"]["total"] == 1


@pytest.mark.parametrize("container", CONTAINERS)
def test_recruiter_chatmsg_pagination_counts_known_containers(container: str, tmp_path: Path) -> None:
	result = _invoke_recruiter(container, tmp_path)

	assert result.exit_code == 0, result.output
	assert json.loads(result.stdout)["pagination"]["total"] == 1


# ── 向导入口 ────────────────────────────────────────────────────────


@pytest.mark.parametrize("container", CONTAINERS)
def test_wizard_candidate_chat_history_reads_every_known_container(
	container: str, tmp_path: Path
) -> None:
	data = DEFAULT_ACTIONS["candidate_chat_history"](
		_wizard_context(tmp_path, _Platform(container), role="candidate"), {"gid": "12345"}, {}
	).data

	assert len(data["messages"]) == 1
	assert len(data["items"]) == 1
	assert data["total"] == 1


@pytest.mark.parametrize("container", CONTAINERS)
def test_wizard_recruiter_chat_history_reads_every_known_container(
	container: str, tmp_path: Path
) -> None:
	data = DEFAULT_ACTIONS["recruiter_chat_history"](
		_wizard_context(tmp_path, _Platform(container), role="recruiter"), {"friend_id": 12345}, {}
	).data

	assert len(data["items"]) == 1
	assert data["total"] == 1
