"""向导聊天记录 TTY 预览使用归一化消息（Issue #2804）。"""

from __future__ import annotations

import io
from typing import Any

import pytest
from rich.console import Console

from boss_agent_cli import display
from boss_agent_cli.wizard import renderer

_RAW_MESSAGE = {
	"from": {"uid": 9, "name": "甲"},
	"body": {"type": 1, "text": "您好，看了简历想沟通"},
}
_NORMALIZED_MESSAGE = {"from": "甲", "type": "文本", "text": "您好，看了简历想沟通", "time": "11-25 22:00"}


@pytest.fixture
def rendered(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
	stream = io.StringIO()
	monkeypatch.setattr(display, "console", Console(file=stream, force_terminal=False, width=200, emoji=False))
	return stream


def _run(data: dict[str, Any]) -> dict[str, Any]:
	return {"status": "completed", "role": "candidate", "goal": "chat_history", "last_result": {"data": data}}


def test_chat_history_preview_prefers_normalized_messages(rendered: io.StringIO) -> None:
	renderer.render_run(_run({"items": [_RAW_MESSAGE], "messages": [_NORMALIZED_MESSAGE], "total": 1}))

	text = rendered.getvalue()
	assert "甲" in text
	assert "您好，看了简历想沟通" in text
	assert "{'" not in text


def test_chat_history_preview_falls_back_for_legacy_raw_messages(rendered: io.StringIO) -> None:
	# 旧 checkpoint 没有 messages，裸平台的 from / body 是对象时也不能崩或显示 repr。
	renderer.render_run(_run({"items": [_RAW_MESSAGE], "total": 1}))

	text = rendered.getvalue()
	assert "甲" in text
	assert "您好，看了简历想沟通" in text
	assert "{'" not in text


def test_last_messages_preview_keeps_contact_items(rendered: io.StringIO) -> None:
	data = {
		"items": [{"friendId": 9, "name": "甲", "lastMsg": "收到，明天聊"}],
		"messages": [{"friendId": 9, "last_msg": "收到，明天聊"}],
	}
	renderer.render_run({**_run(data), "goal": "last_messages"})

	text = rendered.getvalue()
	assert "甲" in text
	assert "收到，明天聊" in text


@pytest.mark.parametrize("item, expected", [
	({"from": 7, "text": 3.5}, "3.5"),
	({"body": "您好"}, "您好"),
])
def test_legacy_chat_history_keeps_scalar_text(rendered: io.StringIO, item: dict[str, Any], expected: str) -> None:
	renderer.render_run(_run({"items": [item], "total": 1}))

	text = rendered.getvalue()
	assert expected in text
	assert "{'" not in text
