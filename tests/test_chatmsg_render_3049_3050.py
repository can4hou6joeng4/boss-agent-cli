"""聊天记录 TTY 的行号宽度与类型标签（Issue #3049 / #3050）。

行号列固定 3 格时第 1000 行起会被 Rich 截成 `10…`；消息 `type` 为 null /
容器时类型列会把 `None` 与 Python repr 打给真人。两处都只改展示。
"""

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from rich.console import Console

from boss_agent_cli import display
from boss_agent_cli.display import render_simple_list
from boss_agent_cli.main import cli
from boss_agent_cli.services.chat_utils import message_type_label

FRIEND = {
	"uid": 10001,
	"securityId": "sid_now",
	"name": "联系人甲",
	"friendSource": 0,
	"encryptJobId": "job_001",
	"relationType": 1,
}


def _tty(monkeypatch: pytest.MonkeyPatch, *, width: int = 120) -> io.StringIO:
	stream = io.StringIO()
	monkeypatch.setattr(display, "console", Console(file=stream, width=width, emoji=False))
	monkeypatch.setattr(display, "is_json_mode", display.json_forced)
	return stream


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: (
		response.get("zpData") if "zpData" in response else response.get("data")
	)
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	return instance


def _invoke_chatmsg(tmp_path: Path, messages: list[dict], *, json_mode: bool = False):
	argv = ["--data-dir", str(tmp_path)]
	if json_mode:
		argv.append("--json")
	argv += ["chatmsg", "10001"]
	with (
		patch("boss_agent_cli.commands.chatmsg.AuthManager"),
		patch("boss_agent_cli.commands.chatmsg.get_platform_instance") as mock_cls,
	):
		client = _ctx_mock(mock_cls)
		client.friend_list.return_value = {"zpData": {"result": [FRIEND], "friendList": [FRIEND], "hasMore": False}}
		client.chat_history.return_value = {"code": 0, "zpData": {"messages": messages}}
		return CliRunner().invoke(cli, argv)


# ── #3049：行号列按条数自适应 ─────────────────────────────────────


def test_thousand_rows_keep_full_row_index(monkeypatch: pytest.MonkeyPatch) -> None:
	stream = _tty(monkeypatch)
	items = [{"content": f"消息{i}"} for i in range(1, 1002)]

	render_simple_list(items, "聊天记录", [("内容", "content", "")])

	out = stream.getvalue()
	assert "│ 999  │" in out
	assert "│ 1000 │" in out
	assert "│ 1001 │" in out
	assert "10…" not in out
	assert len([line for line in out.splitlines() if line.strip().startswith("│")]) == 1001


def test_small_tables_keep_existing_row_index_width(monkeypatch: pytest.MonkeyPatch) -> None:
	stream = _tty(monkeypatch)

	render_simple_list([{"content": "a"}, {"content": "b"}], "items", [("内容", "content", "")])

	assert "│ 1   │" in stream.getvalue()


# ── #3050：缺失 / 容器类型标签 ────────────────────────────────────


def test_message_type_label_normalizes_missing_and_containers() -> None:
	assert message_type_label(None) == "其他"
	assert message_type_label({"a": 1}) == "其他"
	assert message_type_label([1]) == "其他"
	# 既有口径不变：未知整数编码与布尔仍保留原值。
	assert message_type_label(99) == "其他(99)"
	assert message_type_label(True) == "其他(True)"
	assert message_type_label(1) == "文本"


def test_chatmsg_tty_does_not_print_repr_for_missing_or_container_type(
	monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	stream = _tty(monkeypatch)
	messages = [
		{"from": {"uid": 10001, "name": "联系人甲"}, "type": None, "text": "缺 type"},
		{"from": {"uid": 10001, "name": "联系人甲"}, "type": {"a": 1}, "text": "容器 type"},
	]

	result = _invoke_chatmsg(tmp_path, messages)

	assert result.exit_code == 0, result.output
	out = stream.getvalue()
	assert "其他" in out
	assert "None" not in out
	assert '{"a": 1}' not in out and "{'a': 1}" not in out


def test_chatmsg_envelope_normalizes_type_string(tmp_path: Path) -> None:
	result = _invoke_chatmsg(
		tmp_path,
		[{"from": {"uid": 10001, "name": "联系人甲"}, "type": None, "text": "缺 type"}],
		json_mode=True,
	)

	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	assert payload["data"][0]["type"] == "其他"
