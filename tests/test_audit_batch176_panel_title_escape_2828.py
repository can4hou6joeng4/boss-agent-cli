"""面板标题按外部值转义（Issue #2828）。"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from boss_agent_cli import display


@pytest.fixture
def rendered(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
	stream = io.StringIO()
	monkeypatch.setattr(display, "console", Console(file=stream, force_terminal=False, width=200, emoji=False))
	return stream


def test_message_panel_title_markup_is_literal(rendered: io.StringIO) -> None:
	display.render_message_panel({"a": 1}, title="bad[/bold]")

	output = rendered.getvalue()
	assert "bad[/bold]" in output


def test_message_panel_title_link_is_literal(rendered: io.StringIO) -> None:
	display.render_message_panel({"a": 1}, title="[link=https://example.invalid]记录")

	output = rendered.getvalue()
	assert "[link=https://example.invalid]记录" in output


def test_message_panel_title_control_chars_stripped(rendered: io.StringIO) -> None:
	display.render_message_panel({"a": 1}, title="bad\x1b[31m")

	output = rendered.getvalue()
	assert "\x1b" not in output
	assert "bad" in output


def test_message_panel_normal_title_kept(rendered: io.StringIO) -> None:
	display.render_message_panel({"a": 1}, title="digest")

	assert "digest" in rendered.getvalue()


def test_sectioned_record_section_markup_is_literal(rendered: io.StringIO) -> None:
	display.render_sectioned_record({"bad[/bold]": {"a": 1}}, title="t")

	output = rendered.getvalue()
	assert "bad[/bold]" in output
	assert "a:" in output


def test_sectioned_record_section_link_is_literal(rendered: io.StringIO) -> None:
	display.render_sectioned_record({"[link=https://example.invalid]段": {"a": 1}}, title="t")

	output = rendered.getvalue()
	assert "[link=https://example.invalid]段" in output


def test_sectioned_record_normal_section_kept(rendered: io.StringIO) -> None:
	display.render_sectioned_record({"基本信息": {"a": 1}}, title="t")

	assert "基本信息" in rendered.getvalue()


def test_ai_result_title_markup_is_literal(rendered: io.StringIO) -> None:
	display.render_ai_result({"k": "v"}, title="bad[/bold]")

	output = rendered.getvalue()
	assert "bad[/bold]" in output
	assert "v" in output
