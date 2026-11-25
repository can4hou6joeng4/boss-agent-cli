"""exchange / mark 结果未知时使用求职者侧核对指引（Issue #2849）。

登记表的 ``ACTION_UNCONFIRMED`` 恢复文案是给招聘者的 ``boss hr chatmsg
<friend_id>``；求职者侧写命令必须像 greet / apply / 向导一样改用自己的
``boss chat``，否则 Agent 会被引向一个不可能完成的核对动作。
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli


def _ctx_mock(mock_cls: Any) -> Any:
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *args: None
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	instance.unwrap_data.side_effect = lambda response: response.get("zpData")
	return instance


_FRIEND = {"uid": 10001, "securityId": "sid_now", "name": "联系人甲", "friendSource": 0}
_FRIENDS = {"zpData": {"result": [_FRIEND], "friendList": [_FRIEND]}}
_INTERRUPTED = {
	"code": -1,
	"message": "请求期间页面或响应中断；操作可能已生效，请先核对结果，勿直接重试",
	"zpData": {},
	"__cli_error_code__": "ACTION_UNCONFIRMED",
	"__cli_error_details__": {"outcome": "unknown", "replay_safe": False, "__private": "ignored"},
}
_NOT_SENT = {
	"code": -1,
	"message": "浏览器通道在请求发出前中断，平台侧未收到请求；可安全重试",
	"zpData": {},
	"__cli_error_code__": "NETWORK_ERROR",
	"__cli_error_details__": {"outcome": "unknown", "replay_safe": True, "request_sent": False},
}

_COMMANDS = {
	"exchange": (["exchange", "--", "10001"], "exchange_contact", "chat"),
	"mark": (["mark", "--label", "沟通中", "--", "10001"], "friend_label", "chat"),
}


def _invoke(tmp_path: Any, command: str, response: dict[str, Any], error_code: str) -> dict[str, Any]:
	argv, method, _ = _COMMANDS[command]
	with patch(f"boss_agent_cli.commands.{command}.AuthManager"), patch(
		f"boss_agent_cli.commands.{command}.get_platform_instance"
	) as mock_cls:
		client = _ctx_mock(mock_cls)
		client.friend_list.return_value = _FRIENDS
		client.parse_error.return_value = (error_code, "请求中断")
		getattr(client, method).return_value = response
		out = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", *argv])
	assert out.exit_code == 1, out.output
	return json.loads(out.output)


@pytest.mark.parametrize("command", sorted(_COMMANDS))
def test_candidate_unconfirmed_uses_candidate_side_verification(tmp_path: Any, command: str) -> None:
	payload = _invoke(tmp_path, command, _INTERRUPTED, "ACTION_UNCONFIRMED")
	error = payload["error"]
	assert error["code"] == "ACTION_UNCONFIRMED"
	assert error["recoverable"] is False
	recovery = error["recovery_action"]
	# 同一数据目录的 boss chat 是求职者唯一能执行的核对入口。
	assert f"boss --data-dir {tmp_path} chat" in recovery
	assert "hr chatmsg" not in recovery
	assert "<friend_id>" not in recovery
	assert "不要直接重试" in recovery
	# 语义字段保留：结果未知、不可重放；内部命名空间的键不进入信封。
	assert error["details"]["outcome_unknown"] is True
	assert error["details"]["replay_safe"] is False
	assert "__private" not in error["details"]
	next_actions = payload["hints"]["next_actions"]
	assert next_actions == [f"boss --data-dir {tmp_path} chat"]
	assert any("BOSS 直聘" in action for action in payload["hints"]["operator_actions"])


@pytest.mark.parametrize("command", sorted(_COMMANDS))
def test_candidate_provably_unsent_keeps_retryable_network_error(tmp_path: Any, command: str) -> None:
	payload = _invoke(tmp_path, command, _NOT_SENT, "NETWORK_ERROR")
	error = payload["error"]
	# 确定未发出的请求仍按可重试的网络错误输出，不能被升级成结果未知。
	assert error["code"] == "NETWORK_ERROR"
	assert error["recoverable"] is True
	assert "hr chatmsg" not in error["recovery_action"]


def test_cli_error_details_filters_internal_keys_and_non_dicts() -> None:
	# 延迟导入：未修复源码没有这个共享 helper 时，行为用例仍能先跑出真实差异。
	from boss_agent_cli.services.candidate_actions import cli_error_details

	assert cli_error_details(_INTERRUPTED) == {"outcome": "unknown", "replay_safe": False}
	assert cli_error_details({"__cli_error_details__": {"__private": 1, "reason": "unparsed"}}) == {"reason": "unparsed"}
	assert cli_error_details({"__cli_error_details__": "not-a-dict"}) == {}
	assert cli_error_details(None) == {}
