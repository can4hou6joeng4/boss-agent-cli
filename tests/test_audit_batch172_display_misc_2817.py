"""CLI 风控锁与通用键值面板的安全标量（Issue #2817）。"""

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


def _assert_no_repr(text: str) -> None:
	assert "{'" not in text
	assert "nan" not in text
	assert "inf" not in text
	assert "None" not in text
	assert "True" not in text
	assert "False" not in text


@pytest.mark.parametrize(
	"locked_at",
	[10**5000, {"a": 1}, ["a"], True, float("nan"), float("inf")],
	ids=["huge", "dict", "list", "bool", "nan", "inf"],
)
def test_status_lock_at_drift_falls_back(rendered: io.StringIO, locked_at: Any) -> None:
	display.render_status({"logged_in": True, "cdp_risk_lock": {"locked": True, "locked_at": locked_at}})

	text = rendered.getvalue()
	_assert_no_repr(text)
	assert "unknown 起" in text


@pytest.mark.parametrize(
	"reason",
	[10**5000, {"a": 1}, ["a"], False, float("nan")],
	ids=["huge", "dict", "list", "bool", "nan"],
)
def test_status_lock_reason_drift_falls_back(rendered: io.StringIO, reason: Any) -> None:
	display.render_status({"logged_in": True, "cdp_risk_lock": {"locked": True, "reason": reason}})

	text = rendered.getvalue()
	_assert_no_repr(text)
	assert "ENVIRONMENT_RISK" in text


def test_status_lock_normal_values_are_kept(rendered: io.StringIO) -> None:
	display.render_status({
		"logged_in": True,
		"cdp_risk_lock": {"locked": True, "locked_at": "2026-11-26", "reason": "ENVIRONMENT_RISK"},
	})

	text = rendered.getvalue()
	assert "2026-11-26 起" in text
	assert "ENVIRONMENT_RISK" in text


def test_status_without_lock_has_no_lock_line(rendered: io.StringIO) -> None:
	display.render_status({"logged_in": True})

	assert "风控锁定中" not in rendered.getvalue()


def test_panel_boolean_kept_as_before(rendered: io.StringIO) -> None:
	"""布尔沿用既有 TTY 契约（True / False），本次只收口非有限浮点。"""
	display.render_message_panel({"dry_run": True, "fine": False})

	text = rendered.getvalue()
	assert "dry_run: True" in text
	assert "fine: False" in text


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")], ids=["nan", "inf", "-inf"])
def test_panel_non_finite_float_falls_back(rendered: io.StringIO, value: float) -> None:
	display.render_message_panel({"score": value})

	text = rendered.getvalue()
	_assert_no_repr(text)
	assert "score: -" in text


def test_panel_list_booleans_and_non_finite(rendered: io.StringIO) -> None:
	display.render_message_panel({"flags": [True, False, float("nan")]})

	text = rendered.getvalue()
	assert "flags: True, False, -" in text


def test_panel_nested_bool_kept(rendered: io.StringIO) -> None:
	display.render_message_panel({"inner": {"done": True}})

	assert "True" in rendered.getvalue()


def test_panel_normal_values_are_kept(rendered: io.StringIO) -> None:
	display.render_message_panel({"done": 2, "ok": "yes", "missing": None, "score": 1.5})

	text = rendered.getvalue()
	assert "done: 2" in text
	assert "ok: yes" in text
	assert "missing: -" in text
	assert "score: 1.5" in text
