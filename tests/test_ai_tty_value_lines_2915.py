"""Issue #2915：AI 结果的终端渲染应与 cell_text 同口径，不泄漏 None / repr。"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rich.console import Console

from boss_agent_cli import display


@pytest.fixture
def rendered(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
	stream = io.StringIO()
	monkeypatch.setattr(display, "console", Console(file=stream, force_terminal=False, width=200, emoji=False))
	return stream


def test_ai_dict_values_use_cell_text(rendered: io.StringIO) -> None:
	display.render_ai_result(
		{"assessment": {"skills": ["Python", "SQL"], "details": {"strength": "代码能力"}, "note": None}},
		title="audit",
	)

	output = rendered.getvalue()
	assert "skills: Python, SQL" in output
	assert 'details: {"strength": "代码能力"}' in output
	assert "note: -" in output
	assert "['Python'" not in output
	assert "{'strength'" not in output
	assert "note: None" not in output


def test_ai_list_scalars_use_cell_text(rendered: io.StringIO) -> None:
	display.render_ai_result({"items": [None, ["甲", "乙"], {"tag": "远程"}]}, title="audit")

	output = rendered.getvalue()
	assert "  · -" in output
	assert "  · 甲, 乙" in output
	assert "tag=远程" in output
	assert "  · ['甲'" not in output


def test_json_envelope_keeps_original_ai_values() -> None:
	data = {"assessment": {"skills": ["Python", "SQL"], "details": {"strength": "代码能力"}, "note": None}}
	stdout = io.StringIO()
	ctx = SimpleNamespace(obj={"json_output": True})

	with redirect_stdout(stdout), patch.object(stdout, "isatty", return_value=False):
		display.handle_output(ctx, "ai", data, render=lambda x: display.render_ai_result(x, title="audit"))

	envelope = json.loads(stdout.getvalue())
	assert envelope["ok"] is True
	assert envelope["data"] == data
