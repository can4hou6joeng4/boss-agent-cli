"""向导采集浏览行的载荷安全标量（Issue #2831）。"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from boss_agent_cli import display
from boss_agent_cli.wizard.live_crawl import _job_row
from boss_agent_cli.wizard.renderer import render_job_card_zh


@pytest.fixture
def rendered(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
	stream = io.StringIO()
	monkeypatch.setattr(display, "console", Console(file=stream, force_terminal=False, width=200, emoji=False))
	return stream


def test_normal_scalars_are_kept() -> None:
	row = _job_row(
		{
			"payload": {
				"title": "后端工程师",
				"company": "示例公司",
				"city": "北京",
				"salary": "20-30K",
				"experience": "3-5年",
				"education": "本科",
				"security_id": "sec-1",
				"job_id": "job-1",
			}
		}
	)

	assert row["title"] == "后端工程师"
	assert row["company"] == "示例公司"
	assert row["salary"] == "20-30K"
	assert row["security_id"] == "sec-1"


@pytest.mark.parametrize("value", [{"name": "x"}, ["A", "B"], True, None], ids=["dict", "list", "bool", "null"])
def test_container_and_bool_scalars_become_empty(value: object) -> None:
	row = _job_row({"payload": {"title": value, "company": value, "security_id": value}})

	assert row["title"] == ""
	assert row["company"] == ""
	assert row["security_id"] == ""


def test_huge_int_does_not_raise() -> None:
	row = _job_row({"payload": {"salary": 10**5000}})

	assert row["salary"] == ""


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")], ids=["nan", "inf", "-inf"])
def test_non_finite_floats_become_empty(value: float) -> None:
	assert _job_row({"payload": {"salary": value}})["salary"] == ""


def test_zero_number_is_kept() -> None:
	assert _job_row({"payload": {"experience": 0}})["experience"] == "0"


def test_description_falls_back_and_rejects_containers() -> None:
	assert _job_row({"payload": {"post_description": "", "description": "正文"}})["description"] == "正文"
	assert _job_row({"payload": {"post_description": {"bad": 1}, "description": "正文"}})["description"] == "正文"
	assert _job_row({"payload": {"post_description": 10**5000}})["description"] == ""


def test_labels_are_passed_through() -> None:
	row = _job_row({"payload": {"labels": ["双休", "远程"]}})

	assert row["labels"] == ["双休", "远程"]
	assert _job_row({"payload": {}})["labels"] == []


def test_job_card_renders_drifted_payload_without_repr(rendered: io.StringIO) -> None:
	row = _job_row(
		{
			"payload": {
				"title": {"name": "x"},
				"company": ["A", "B"],
				"salary": 10**5000,
				"experience": float("nan"),
			}
		}
	)
	render_job_card_zh(row)

	output = rendered.getvalue()
	assert "{'" not in output
	assert "['A'" not in output
	assert "nan" not in output
	assert "inf" not in output
