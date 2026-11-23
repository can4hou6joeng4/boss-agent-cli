"""审计批次 140：联系人身份口径与预设入口校验（Issue #2543）。

- wizard 的身份解析此前对 ``securityId`` / ``uid`` 直接 ``str()``：类型漂移成
  布尔 / 容器时会把 ``"True"`` / ``"{'a': 1}"`` 当身份发给 ``exchange_contact``
  等写请求；CLI 侧已 fail-closed，wizard 侧没有。
- ``current_friend_uid_or_emit`` 对超过 4300 位的整数在 ``str()`` 抛 ``ValueError``，
  被兜成可重试的 ``NETWORK_ERROR``。
- ``preset add`` / ``watch add`` 只校验关键词的可编码性，读取侧
  ``validate_stored_search_params`` 却校验全部参数键：``--welfare "\\udcff"``
  能保存成功，随后每次运行都被拒绝。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
import pytest

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands._contact_lookup import current_friend_uid_or_emit
from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import DEFAULT_ACTIONS, ActionContext
from boss_agent_cli.wizard.runner import WorkflowActionError

_BAD = "\udcff"
_GOOD_FRIEND = {"uid": 10001, "securityId": "sid_current", "name": "联系人甲", "friendSource": 0}


class _FakePlatform:
	def __init__(self, friend: dict[str, Any]):
		self.friend = friend
		self.calls: list[tuple[Any, ...]] = []

	def __enter__(self):
		return self

	def __exit__(self, *args):
		return None

	def friend_list(self, *, page: int = 1):
		self.calls.append(("friend_list", page))
		return {"code": 0, "data": {"result": [self.friend], "hasMore": False}}

	def exchange_contact(self, security_id, uid, name, exchange_type=1):
		self.calls.append(("exchange_contact", security_id, uid, name, exchange_type))
		return {"code": 0, "data": {"requested": True}}

	def friend_label(self, uid, label_id, friend_source=0, remove=False):
		self.calls.append(("friend_label", uid, label_id, friend_source, remove))
		return {"code": 0, "data": {"updated": True}}

	def chat_history(self, gid, security_id, *, page=1, count=20):
		self.calls.append(("chat_history", gid, security_id, page, count))
		return {"code": 0, "data": {"messages": []}}

	def is_success(self, response):
		return response["code"] == 0

	def unwrap_data(self, response):
		return response["data"]

	def parse_error(self, response):
		return "ACCOUNT_RISK", "blocked"


def _context(tmp_path: Path, platform: _FakePlatform) -> ActionContext:
	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger("error"),
		candidate_factory=lambda *args: platform,
	)


def _assert_stopped(exc_info: pytest.ExceptionInfo[WorkflowActionError]) -> None:
	error = exc_info.value
	assert error.code == "NETWORK_ERROR"
	assert error.recoverable is False


def test_exchange_rejects_container_security_id_before_write(tmp_path: Path) -> None:
	# 用 uid 定位联系人；securityId 漂移成容器。
	platform = _FakePlatform({**_GOOD_FRIEND, "securityId": {"a": 1}})
	context = _context(tmp_path, platform)

	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["candidate_exchange"](context, {"security_id": "10001"}, {})

	_assert_stopped(exc_info)
	assert "securityId" in str(exc_info.value)
	assert not [call for call in platform.calls if call[0] == "exchange_contact"]


def test_exchange_rejects_boolean_uid_before_write(tmp_path: Path) -> None:
	# 用 securityId 定位联系人；uid 漂移成布尔。
	platform = _FakePlatform({**_GOOD_FRIEND, "uid": True})
	context = _context(tmp_path, platform)

	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["candidate_exchange"](context, {"security_id": "sid_current"}, {})

	_assert_stopped(exc_info)
	assert "uid" in str(exc_info.value)
	assert not [call for call in platform.calls if call[0] == "exchange_contact"]


def test_mark_rejects_container_uid_before_write(tmp_path: Path) -> None:
	platform = _FakePlatform({**_GOOD_FRIEND, "uid": {"x": 1}})
	context = _context(tmp_path, platform)

	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["candidate_mark"](
			context, {"security_id": "sid_current", "label": "沟通中"}, {}
		)

	_assert_stopped(exc_info)
	assert not [call for call in platform.calls if call[0] == "friend_label"]


def test_chat_history_rejects_boolean_uid_before_read(tmp_path: Path) -> None:
	platform = _FakePlatform({**_GOOD_FRIEND, "uid": True})
	context = _context(tmp_path, platform)

	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["candidate_chat_history"](
			context, {"gid": "sid_current"}, {}
		)

	_assert_stopped(exc_info)
	assert not [call for call in platform.calls if call[0] == "chat_history"]


def test_mark_step_result_does_not_echo_drifted_security_id(tmp_path: Path) -> None:
	# mark 只需要 uid；securityId 漂移不影响写请求，但结果 data 不能回显 repr 文本。
	platform = _FakePlatform({**_GOOD_FRIEND, "securityId": {"a": 1}})
	context = _context(tmp_path, platform)

	result = DEFAULT_ACTIONS["candidate_mark"](
		context, {"security_id": "10001", "label": "沟通中"}, {}
	)

	assert result.data["uid"] == "10001"
	assert result.data["security_id"] == ""
	label_calls = [call for call in platform.calls if call[0] == "friend_label"]
	assert len(label_calls) == 1
	assert label_calls[0][1] == "10001"


def test_exchange_still_sends_normal_values(tmp_path: Path) -> None:
	platform = _FakePlatform(dict(_GOOD_FRIEND))
	context = _context(tmp_path, platform)

	result = DEFAULT_ACTIONS["candidate_exchange"](
		context, {"security_id": "sid_current"}, {}
	)

	assert result.data["uid"] == "10001"
	assert result.data["security_id"] == "sid_current"
	assert ("exchange_contact", "sid_current", "10001", "联系人甲", 1) in platform.calls


def test_current_friend_uid_or_emit_treats_oversized_integer_as_missing() -> None:
	ctx = MagicMock()
	ctx.obj = {"json_output": True, "data_dir": Path("/tmp/boss-2543"), "platform": "zhipin"}

	with patch("boss_agent_cli.display.handle_error_output") as mock_err:
		uid = current_friend_uid_or_emit(ctx, "chatmsg", {"uid": 10**5000, "securityId": "sid"})

	assert uid is None
	kwargs = mock_err.call_args.kwargs
	assert kwargs["code"] == "NETWORK_ERROR"
	assert kwargs["recoverable"] is False


def _invoke(tmp_path: Path, *args: str):
	return CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), *args])


def _error(result) -> dict:
	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["ok"] is False
	return payload["error"]


def _saved_names(tmp_path: Path) -> list[str]:
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		return [item["name"] for item in cache.list_saved_searches()]


def test_preset_add_rejects_unencodable_welfare(tmp_path: Path) -> None:
	error = _error(_invoke(tmp_path, "preset", "add", "p1", "kw", "--welfare", _BAD))

	assert error["code"] == "INVALID_PARAM"
	assert "welfare" in error["message"]
	assert _saved_names(tmp_path) == []


def test_watch_add_rejects_unencodable_welfare(tmp_path: Path) -> None:
	error = _error(_invoke(tmp_path, "watch", "add", "w1", "kw", "--welfare", _BAD))

	assert error["code"] == "INVALID_PARAM"
	assert "welfare" in error["message"]
	assert _saved_names(tmp_path) == []


def test_preset_and_watch_add_accept_encodable_filters(tmp_path: Path) -> None:
	preset = _invoke(tmp_path, "preset", "add", "p1", "kw", "--welfare", "五险一金")
	watch = _invoke(tmp_path, "watch", "add", "w1", "kw", "--welfare", "五险一金")

	assert preset.exit_code == 0, preset.output
	assert watch.exit_code == 0, watch.output
	assert set(_saved_names(tmp_path)) == {"p1", "w1"}
