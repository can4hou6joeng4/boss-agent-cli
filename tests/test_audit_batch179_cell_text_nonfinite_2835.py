"""通用表格单元格的非有限浮点口径（Issue #2835）。"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from boss_agent_cli import display
from boss_agent_cli.commands.recruiter import _render
from boss_agent_cli.display import cell_text, render_simple_list


@pytest.fixture
def rendered(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
	stream = io.StringIO()
	monkeypatch.setattr(display, "console", Console(file=stream, force_terminal=False, width=200, emoji=False))
	return stream


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")], ids=["nan", "inf", "-inf"])
def test_non_finite_scalar_is_missing(value: float) -> None:
	assert cell_text(value) == "-"


def test_non_finite_list_element_is_missing() -> None:
	assert cell_text([1, float("nan")]) == "1, -"


def test_normal_scalars_and_containers_unchanged() -> None:
	assert cell_text(1.5) == "1.5"
	assert cell_text(0) == "0"
	assert cell_text(True) == "True"
	assert cell_text(False) == "False"
	assert cell_text("") == ""
	assert cell_text({"a": 1}) == '{"a": 1}'
	assert cell_text([1, 2.5]) == "1, 2.5"


def test_simple_list_never_prints_non_finite(rendered: io.StringIO) -> None:
	render_simple_list(
		[{"name": float("nan"), "unread": float("inf"), "ratio": [1, float("nan")]}],
		"t",
		[("名称", "name", ""), ("未读", "unread", ""), ("比例", "ratio", "")],
	)

	output = rendered.getvalue()
	assert "nan" not in output
	assert "inf" not in output
	assert "-" in output


def test_recruiter_list_never_prints_non_finite(rendered: io.StringIO) -> None:
	_render.render_friend_list(
		{
			"friendList": [
				{
					"name": float("nan"),
					"brandName": float("inf"),
					"jobName": float("-inf"),
					"unread": float("inf"),
					"lastMsg": float("nan"),
				}
			]
		},
		title="t",
		next_steps=[],
	)

	output = rendered.getvalue()
	assert "nan" not in output
	assert "inf" not in output
