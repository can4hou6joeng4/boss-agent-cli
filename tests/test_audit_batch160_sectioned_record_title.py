"""分段记录面板统一显示命令标识（Issue #2783）。"""

import io

import pytest
from rich.console import Console

from boss_agent_cli import display


def _tty(monkeypatch: pytest.MonkeyPatch, *, width: int = 160) -> io.StringIO:
	stream = io.StringIO()
	monkeypatch.setattr(display, "console", Console(file=stream, width=width, emoji=False))
	return stream


def test_sectioned_record_shows_title(monkeypatch: pytest.MonkeyPatch) -> None:
	stream = _tty(monkeypatch)
	display.render_sectioned_record({"basic": {"name": "demo"}}, title="agent stats")
	out = stream.getvalue()
	lines = [line for line in out.splitlines() if line.strip()]
	assert lines[0] == "agent stats"
	assert sum("agent stats" in line for line in lines) == 1
	assert "basic" in out
	assert "demo" in out


def test_record_result_escapes_title_and_keeps_next_steps(monkeypatch: pytest.MonkeyPatch) -> None:
	stream = _tty(monkeypatch)
	next_step = "boss --data-dir /opt/demo/boss agent stats"
	display.render_record_result(
		{"basic": {"name": "[demo]"}},
		title="crawl [start]",
		next_steps=[next_step],
	)
	out = stream.getvalue()
	lines = [line for line in out.splitlines() if line.strip()]
	assert lines[0] == "crawl [start]"
	assert sum("crawl [start]" in line for line in lines) == 1
	assert "[demo]" in out
	assert next_step in out


def test_sectioned_record_title_type_drift_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
	stream = _tty(monkeypatch)
	display.render_sectioned_record({}, title=10**4300)  # type: ignore[arg-type]
	# 转换上限受 PYTHONINTMAXSTRDIGITS 影响：既能转出完整整数也能回落 info，
	# 这里只约束「不抛异常且有标题行」，不绑死具体回退文案。
	assert stream.getvalue().splitlines()[0].strip()


def test_batch_summary_shows_title_on_empty_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
	stream = _tty(monkeypatch)
	display.render_batch_operation_summary({"dry_run": True, "candidates": []}, title="batch-greet")
	out = stream.getvalue()
	assert out.splitlines()[0] == "batch-greet"
	assert "dry run" in out
