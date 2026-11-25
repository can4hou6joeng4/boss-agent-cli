"""向导损坏断点提示的 markup 转义（Issue #2823）。"""

from __future__ import annotations

import io

from rich.console import Console

from boss_agent_cli.commands.wizard import _corrupt_run_notice


def _render(notice: str) -> str:
	stream = io.StringIO()
	Console(file=stream, force_terminal=False, width=200, emoji=False).print(notice)
	return stream.getvalue()


def test_markup_in_run_id_is_literal() -> None:
	output = _render(_corrupt_run_notice(["bad[/bold]"]))

	assert "bad[/bold]" in output
	assert "跳过 1 条损坏的任务记录" in output


def test_markup_link_in_run_id_is_literal() -> None:
	output = _render(_corrupt_run_notice(["[link=https://example.invalid]记录"]))

	assert "[link=https://example.invalid]记录" in output


def test_control_chars_are_stripped() -> None:
	output = _render(_corrupt_run_notice(["bad\x1b[31m"]))

	assert "\x1b" not in output
	assert "bad" in output


def test_normal_run_ids_are_kept() -> None:
	output = _render(_corrupt_run_notice(["run-1", "run-2"]))

	assert "run-1, run-2" in output
	assert "跳过 2 条损坏的任务记录" in output
