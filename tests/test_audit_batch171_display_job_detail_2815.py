"""CLI 职位详情的 TTY 渲染安全标量（Issue #2815）。"""

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
	"description",
	[10**5000, {"a": 1}, ["a"], True, float("nan"), float("inf")],
	ids=["huge", "dict", "list", "bool", "nan", "inf"],
)
def test_description_drift_does_not_crash(rendered: io.StringIO, description: Any) -> None:
	display.render_job_detail({"title": "职位", "description": description})

	text = rendered.getvalue()
	_assert_no_repr(text)
	assert "description:" not in text


def test_description_text_is_kept(rendered: io.StringIO) -> None:
	display.render_job_detail({"title": "职位", "description": "负责后端服务"})

	assert "负责后端服务" in rendered.getvalue()


def test_long_description_still_truncated(rendered: io.StringIO) -> None:
	display.render_job_detail({"title": "职位", "description": "z" * 800})

	text = rendered.getvalue()
	assert text.count("z") == 500
	assert "已省略 300 字" in text


def test_skills_drift_keeps_plain_values(rendered: io.StringIO) -> None:
	display.render_job_detail(
		{"title": "职位", "skills": [{"a": 1}, 10**5000, True, float("nan"), "Python", 7]}
	)

	text = rendered.getvalue()
	_assert_no_repr(text)
	assert "Python" in text
	assert "7" in text


def test_skills_empty_after_filter_falls_back(rendered: io.StringIO) -> None:
	display.render_job_detail({"title": "职位", "skills": [{"a": 1}, True, 10**5000]})

	text = rendered.getvalue()
	assert "skills: -" in text


@pytest.mark.parametrize(
	"snapshot",
	[
		{"count": 10**5000, "source": "search", "saved_at": 0},
		{"page": 10**5000, "source": "search", "saved_at": 0},
		{"source": {"a": 1}, "saved_at": 0},
		{"count": True, "page": False, "source": "search", "saved_at": 0},
	],
	ids=["count-huge", "page-huge", "source-dict", "bool-flags"],
)
def test_snapshot_drift_does_not_crash(rendered: io.StringIO, snapshot: dict[str, Any]) -> None:
	display.render_job_detail({"title": "职位", "index": 3, "index_snapshot": snapshot})

	text = rendered.getvalue()
	_assert_no_repr(text)
	assert "#3" in text
	assert "共" in text
	assert "保存于" in text


def test_snapshot_normal_values_are_kept(rendered: io.StringIO) -> None:
	display.render_job_detail({
		"title": "职位",
		"index": 3,
		"index_snapshot": {"source": "search:Golang", "page": 2, "count": 30, "saved_at": 1700000000.0},
	})

	text = rendered.getvalue()
	assert "#3" in text
	assert "第 2 页" in text
	assert "search:Golang" in text
	assert "共 30 条" in text


def test_snapshot_zero_count_is_kept(rendered: io.StringIO) -> None:
	display.render_job_detail({"title": "职位", "index": 1, "index_snapshot": {"count": 0, "source": "search"}})

	text = rendered.getvalue()
	assert "共 0 条" in text


def test_snapshot_markup_is_escaped(rendered: io.StringIO) -> None:
	display.render_job_detail({
		"title": "职位",
		"index": "[/dim]x",
		"index_snapshot": {"source": "[link=https://example.invalid]来源", "count": 1, "saved_at": 0},
	})

	text = rendered.getvalue()
	assert "共 1 条" in text


@pytest.mark.parametrize(
	"security_id, job_id, expected",
	[
		("sec1", "job1", True),
		({"a": 1}, "job1", False),
		("sec1", {"a": 1}, False),
		(10**5000, "job1", False),
	],
	ids=["normal", "sid-dict", "jid-dict", "sid-huge"],
)
def test_greet_hint_only_with_safe_ids(
	rendered: io.StringIO, security_id: Any, job_id: Any, expected: bool
) -> None:
	display.render_job_detail({"title": "职位", "security_id": security_id, "job_id": job_id})

	assert ("next:" in rendered.getvalue()) is expected


@pytest.mark.parametrize("value", [True, False, float("nan"), float("inf"), float("-inf")], ids=["true", "false", "nan", "inf", "-inf"])
def test_job_field_text_rejects_bool_and_non_finite(value: Any) -> None:
	assert display.job_field_text({"title": value}, "title") == "-"


def test_job_field_text_container_json_kept() -> None:
	assert display.job_field_text({"title": ["A", "B"]}, "title") == "A, B"
