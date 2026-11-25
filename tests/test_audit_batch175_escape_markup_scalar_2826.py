"""``escape_markup`` 对超大整数与深嵌套外部值的兜底（Issue #2826）。"""

from __future__ import annotations

import io
from typing import Any

import pytest
from rich.console import Console

from boss_agent_cli import display


@pytest.fixture
def rendered(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
	stream = io.StringIO()
	monkeypatch.setattr(display, "console", Console(file=stream, force_terminal=False, width=200, emoji=False))
	return stream


def test_huge_int_returns_empty_without_raising() -> None:
	assert display.escape_markup(10**5000) == ""


def test_digit_limit_boundary() -> None:
	# 4300 位十进制仍在转换上限内，4301 位才触发 ValueError（Issue #2736 同口径）。
	assert len(display.escape_markup(10**4299)) == 4300
	assert display.escape_markup(10**4300) == ""


def test_deeply_nested_value_does_not_raise() -> None:
	value: Any = []
	for _ in range(10_000):
		value = [value]

	text = display.escape_markup(value)

	assert isinstance(text, str)
	assert "\x1b" not in text


def test_normal_scalars_keep_existing_shape() -> None:
	assert display.escape_markup(42) == "42"
	assert display.escape_markup(True) == "True"
	assert display.escape_markup(None) == "None"
	assert display.escape_markup("工程师") == "工程师"


def test_markup_still_escaped_for_strings() -> None:
	text = display.escape_markup("[bold]x[/bold]")

	assert text == "[none]\\[bold]x\\[/bold][/none]"


def test_render_ai_result_with_huge_int_value(rendered: io.StringIO) -> None:
	display.render_ai_result({"result": 10**5000, "reason": "ok"}, title="ai")

	output = rendered.getvalue()
	assert "reason" in output
	assert "ok" in output


def test_render_ai_result_with_huge_int_in_list(rendered: io.StringIO) -> None:
	display.render_ai_result({"items": [10**5000, "正常"]}, title="ai")

	output = rendered.getvalue()
	assert "正常" in output


def test_render_ai_result_with_huge_int_in_dict_value(rendered: io.StringIO) -> None:
	display.render_ai_result({"detail": {"score": 10**5000, "note": "备注"}}, title="ai")

	output = rendered.getvalue()
	assert "备注" in output


def test_render_ai_result_with_huge_int_in_list_of_dicts(rendered: io.StringIO) -> None:
	display.render_ai_result({"items": [{"score": 10**5000, "note": "ok"}]}, title="ai")

	output = rendered.getvalue()
	assert "note=ok" in output


def test_render_ai_result_inline_dict_containers_use_text_not_repr(rendered: io.StringIO) -> None:
	display.render_ai_result({"items": [{"tags": ["远程", "双休"]}]}, title="ai")

	output = rendered.getvalue()
	assert "tags=远程, 双休" in output
	assert "['远程'" not in output


def test_render_batch_operation_summary_with_huge_int_title(rendered: io.StringIO) -> None:
	display.render_batch_operation_summary(
		{"greeted": [{"title": 10**5000, "company": "示例公司"}], "failed": [], "dry_run": False}
	)

	output = rendered.getvalue()
	assert "success: 1" in output
	assert "示例公司" in output
