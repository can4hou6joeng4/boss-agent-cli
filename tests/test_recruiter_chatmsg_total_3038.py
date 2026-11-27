"""招聘者聊天记录的 pagination.total 必须等于实际渲染行数（Issue #3038）。

`hr chatmsg` 的 total 一直由 ``services.chat_utils.message_list`` 统计，而终端
渲染走 ``_render.message_rows`` 的候选键；两侧对「哪些容器算消息」口径不同，
``msgList`` / ``list`` / ``result`` 载荷会出现「屏幕有消息、total 为 0」。
"""

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.commands.recruiter._render import message_rows
from boss_agent_cli.main import cli

_MESSAGE = {"content": "您好", "createTime": 1, "mid": 7}

#: (容器键, 平台载荷, 期望 total)。期望值都是「实际渲染的行数」。
_CASES = [
	("msgList", {"msgList": [_MESSAGE], "hasMore": False}, 1),
	("list", {"list": [_MESSAGE], "hasMore": False}, 1),
	("result", {"result": [_MESSAGE], "hasMore": False}, 1),
	("historyMsgList", {"historyMsgList": [_MESSAGE], "hasMore": False}, 1),
	("non-object", {"messages": [_MESSAGE, None, "drift"], "hasMore": False}, 1),
	# 首选容器为空就是明确的空页：渲染不回退，total 也不能回退（Issue #3038）。
	("empty-first", {"messageList": [], "messages": [_MESSAGE], "hasMore": False}, 0),
]


def _invoke(tmp_path, payload: Any) -> dict[str, Any]:
	platform = MagicMock()
	platform.__enter__.return_value = platform
	platform.is_success.return_value = True
	platform.unwrap_data.side_effect = lambda response: response["zpData"]
	platform.chat_history.return_value = {"zpData": payload}
	with patch("boss_agent_cli.commands.recruiter.chat.AuthManager"), patch(
		"boss_agent_cli.commands.recruiter.chat.get_recruiter_platform_instance",
		return_value=platform,
	):
		result = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "hr", "chatmsg", "10001"])
	assert result.exit_code == 0, result.output
	parsed = json.loads(result.stdout)
	assert parsed["ok"] is True
	return parsed


@pytest.mark.parametrize(("label", "payload", "expected"), _CASES)
def test_chatmsg_total_matches_rendered_rows(tmp_path, label, payload, expected):
	parsed = _invoke(tmp_path, payload)
	rendered = message_rows(payload)
	assert len(rendered) == expected, label
	assert parsed["pagination"]["total"] == len(rendered), label
	assert parsed["data"] == payload
