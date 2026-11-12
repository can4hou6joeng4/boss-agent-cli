"""锁定招聘者 ``hr chatmsg`` 的游标分页证据。

平台聊天历史用 ``maxMsgId`` 向更早消息翻页，响应顶层的 ``hasMore`` 是唯一
可靠的「还有没有更早消息」信号。本文件锁住：

1. 只有布尔 ``hasMore`` 才给出 true / false，其余一律 null；
2. true 时用本页最旧的 ``mid`` 作为下一页游标，命令保留本次 ``--count``；
3. 失败信封不带 pagination；
4. 畸形消息数组或缺失 ``mid`` 不会击穿命令，也不会伪造游标。
"""

import json
import shlex
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.display import error_contract_for_code
from boss_agent_cli.main import cli
from boss_agent_cli.services.pagination import UNKNOWN_HAS_MORE_OPERATOR_ACTION


def _hint(command: str) -> str:
	"""默认数据目录下的招聘者后继命令。"""
	return f"boss --data-dir {(Path.home() / '.boss-agent').resolve()} {command}"


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: response.get("zpData") if "zpData" in response else response.get("data")
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	return instance


def _message(mid, text="您好"):
	return {"mid": mid, "from": {"uid": 99}, "content": text}


def _invoke(payload, *args, parse_error=None):
	with patch("boss_agent_cli.commands.recruiter.chat.get_recruiter_platform_instance") as platform_cls, patch(
		"boss_agent_cli.commands.recruiter.chat.AuthManager"
	):
		platform = _ctx_mock(platform_cls)
		if parse_error is not None:
			platform.chat_history.return_value = {"code": 9, "message": "fixture failure"}
			platform.parse_error.return_value = parse_error
		else:
			platform.chat_history.return_value = {"code": 200, "data": payload}
		result = CliRunner().invoke(cli, ["--json", "hr", "chatmsg", "12345", *args])
	return result, platform


PAGINATION_CASES = [
	pytest.param(
		{"messages": [_message(100), _message(99)], "hasMore": True},
		(True, 2, 99),
		id="true-uses-oldest-mid",
	),
	pytest.param(
		{"messages": [_message("100"), _message("99")], "hasMore": True},
		(True, 2, 99),
		id="true-accepts-numeric-string-mid",
	),
	pytest.param({"messages": [], "hasMore": False}, (False, 0, None), id="false"),
	pytest.param({"messages": []}, (None, 0, None), id="missing"),
	pytest.param({"messages": [], "hasMore": None}, (None, 0, None), id="null"),
	pytest.param({"messages": [], "hasMore": "true"}, (None, 0, None), id="string-true"),
	pytest.param({"messages": [], "hasMore": 1}, (None, 0, None), id="integer-one"),
	pytest.param(
		{"messages": "not-a-list", "hasMore": True}, (True, 0, None),
		id="messages-not-list",
	),
	pytest.param(
		{"messages": [{"mid": None}, {"mid": 0}], "hasMore": True}, (True, 2, None),
		id="missing-usable-mid",
	),
	pytest.param(["unexpected"], (None, 0, None), id="data-not-mapping"),
]


@pytest.mark.parametrize(("payload", "expected"), PAGINATION_CASES)
def test_hr_chatmsg_pagination_evidence(payload, expected):
	has_more, total, next_max_msg_id = expected
	result, platform = _invoke(payload)
	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)

	assert parsed["pagination"] == {
		"has_more": has_more,
		"has_more_source": "platform_hasMore" if has_more is not None else "unknown",
		"total": total,
		"total_kind": "returned_page_count",
		"next_max_msg_id": next_max_msg_id,
	}
	assert "page" not in parsed["pagination"]
	next_cursors = [action for action in parsed["hints"]["next_actions"] if "--max-msg-id" in action]
	if has_more is True and next_max_msg_id is not None:
		assert next_cursors == [_hint(f"hr chatmsg 12345 --max-msg-id {next_max_msg_id} --count 20")]
	else:
		assert next_cursors == []
	if has_more is None:
		assert parsed["hints"]["operator_actions"] == [UNKNOWN_HAS_MORE_OPERATOR_ACTION]
	else:
		assert "operator_actions" not in parsed["hints"]
	platform.chat_history.assert_called_once_with(12345, count=20, max_msg_id=None)


def test_hr_chatmsg_next_cursor_is_executable(tmp_path):
	"""下一页指引要能直接交给 CLI 执行，并沿用调用方本次的 --count。"""
	responses = [
		{"code": 200, "data": {"messages": [_message(100), _message(99)], "hasMore": True}},
		{"code": 200, "data": {"messages": [_message(98)], "hasMore": False}},
	]
	with patch("boss_agent_cli.commands.recruiter.chat.get_recruiter_platform_instance") as platform_cls, patch(
		"boss_agent_cli.commands.recruiter.chat.AuthManager"
	) as auth_cls:
		platform = _ctx_mock(platform_cls)
		platform.chat_history.side_effect = responses
		runner = CliRunner()
		first = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "hr", "chatmsg", "12345", "--count", "7"])
		assert first.exit_code == 0, first.output
		actions = [action for action in json.loads(first.output)["hints"]["next_actions"] if "--max-msg-id" in action]
		assert actions == [f"boss --data-dir {tmp_path.resolve()} hr chatmsg 12345 --max-msg-id 99 --count 7"]

		second = runner.invoke(cli, ["--json", *shlex.split(actions[0])[1:]])
		assert second.exit_code == 0, second.output
		parsed = json.loads(second.output)
		assert parsed["pagination"]["has_more"] is False
		assert parsed["pagination"]["next_max_msg_id"] == 98
		assert [call.kwargs for call in platform.chat_history.call_args_list] == [
			{"count": 7, "max_msg_id": None},
			{"count": 7, "max_msg_id": 99},
		]
		assert [call.args[0].resolve() for call in auth_cls.call_args_list] == [
			tmp_path.resolve(),
			tmp_path.resolve(),
		]


@pytest.mark.parametrize(("code", "recoverable"), [("RATE_LIMITED", True), ("ACCOUNT_RISK", False)])
def test_hr_chatmsg_failure_has_no_pagination(code, recoverable):
	result, platform = _invoke(None, parse_error=(code, "fixture failure"))
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["pagination"] is None
	assert parsed["error"]["code"] == code
	assert parsed["error"]["recoverable"] is recoverable
	assert parsed["error"]["recovery_action"] == error_contract_for_code(code)[1]
	platform.unwrap_data.assert_not_called()
