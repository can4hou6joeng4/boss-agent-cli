"""Issue #2938：chatmsg 的 detail 后继命令必须保护位置参数。"""

from __future__ import annotations

import json
import shlex
from typing import Any
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.commands.detail import detail_cmd
from boss_agent_cli.main import cli


def _ctx_mock(mock_cls: Any) -> Any:
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *args: None
	instance.unwrap_data.side_effect = lambda response: response.get("zpData") if "zpData" in response else response.get("data")
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	return instance


def _messages_page() -> dict[str, Any]:
	return {"zpData": {"messages": [{
		"mid": 1, "from": {"uid": 42, "name": "演示用户"},
		"type": 1, "text": "你好", "time": 1700000000000,
	}], "hasMore": False}}


def _hint_for(token: str) -> str:
	with patch("boss_agent_cli.commands.chatmsg.AuthManager") as auth, \
		patch("boss_agent_cli.commands.chatmsg.get_platform_instance") as factory:
		auth.return_value.check_status.return_value = {"cookies": {}}
		platform = _ctx_mock(factory)
		platform.chat_history.return_value = _messages_page()
		result = CliRunner().invoke(cli, ["chatmsg", "--friend-uid", "42", "--", token])
	assert result.exit_code == 0, result.output
	envelope = json.loads(result.output)
	return next(action for action in envelope["hints"]["next_actions"] if " detail " in action)


def _assert_replayable(command: str, token: str) -> None:
	argv = shlex.split(command)
	detail_index = argv.index("detail")
	with detail_cmd.make_context("detail", argv[detail_index + 1:]) as ctx:
		assert ctx.params["security_id"] == token


def test_hyphen_token_is_protected_in_detail_hint() -> None:
	token = "-offline-token"
	command = _hint_for(token)

	assert "detail -- -offline-token" in command
	_assert_replayable(command, token)


def test_token_with_whitespace_is_quoted_in_detail_hint() -> None:
	token = "offline token"
	command = _hint_for(token)

	assert "detail 'offline token'" in command
	_assert_replayable(command, token)
