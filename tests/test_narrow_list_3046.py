"""窄终端列表渲染降级（Issue #3046）。

Rich 把列压到 1 格时，2 宽的中文字符无处可放，整列内容（含表头）会消失；
这里要求窄终端改用逐条纵向布局，宽终端保持表格。
"""

import io

import pytest
from rich.console import Console

from boss_agent_cli import display
from boss_agent_cli.commands.recruiter._render import render_friend_list
from boss_agent_cli.display import render_simple_list

COLUMNS = [
	("姓名", "name", "bold cyan"),
	("公司", "company", "green"),
	("职位", "job", "yellow"),
	("未读", "unread", "red"),
	("最近时间", "time", "dim"),
	("最近消息", "message", ""),
]
ROW = {
	"name": "候选人甲",
	"company": "示例公司",
	"job": "后端工程师",
	"unread": 2,
	"time": "11-27 10:00",
	"message": "您好",
}


def _tty(monkeypatch: pytest.MonkeyPatch, *, width: int) -> io.StringIO:
	stream = io.StringIO()
	monkeypatch.setattr(display, "console", Console(file=stream, width=width, emoji=False))
	return stream


def test_narrow_terminal_keeps_every_field(monkeypatch: pytest.MonkeyPatch) -> None:
	stream = _tty(monkeypatch, width=30)
	render_simple_list([ROW], "hr applications", COLUMNS)
	out = stream.getvalue()
	for text in (
		"hr applications (1)",
		"姓名",
		"公司",
		"职位",
		"未读",
		"最近时间",
		"最近消息",
		"候选人甲",
		"示例公司",
		"后端工程师",
		"11-27 10:00",
		"您好",
		"2",
	):
		assert text in out, out
	for border in ("┃", "│", "┏", "┡"):
		assert border not in out, out


def test_wide_terminal_still_renders_table(monkeypatch: pytest.MonkeyPatch) -> None:
	stream = _tty(monkeypatch, width=120)
	render_simple_list([ROW], "hr applications", COLUMNS)
	out = stream.getvalue()
	assert "│" in out
	assert "候选人甲" in out


def test_narrow_terminal_keeps_external_values_literal(monkeypatch: pytest.MonkeyPatch) -> None:
	stream = _tty(monkeypatch, width=30)
	render_simple_list([{**ROW, "name": "[bold]不是样式[/bold]"}], "hr applications", COLUMNS)
	assert "[bold]不是样式[/bold]" in stream.getvalue()


def test_recruiter_friend_list_falls_back_on_narrow_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
	stream = _tty(monkeypatch, width=30)
	render_friend_list(
		{
			"friendList": [
				{
					"name": "候选人甲",
					"brandName": "示例公司",
					"jobName": "后端工程师",
					"unreadMsgCount": 2,
					"lastMsg": "您好",
					"lastTS": 1_764_300_000_000,
				},
			],
			"hasMore": False,
		},
		title="hr applications",
		next_steps=[],
	)
	out = stream.getvalue()
	for text in ("候选人甲", "示例公司", "后端工程师", "您好"):
		assert text in out, out
	assert "│" not in out
