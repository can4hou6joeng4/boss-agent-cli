"""职位导出与 job_card 的字段漂移归一（Issue #1916 #1917）。"""

from __future__ import annotations

import csv
import json

import pytest

from boss_agent_cli.api.models import field_text
from boss_agent_cli.services.job_card import build_job_from_card
from boss_agent_cli.services.job_export import render_html_export, write_export_file


def _read_csv(path) -> dict[str, str]:
	with open(path, encoding="utf-8", newline="") as handle:
		return next(csv.DictReader(handle))


# ── Issue #1917：job_card / field_text 容器字段不再落 Python repr ─────


@pytest.mark.parametrize("value", [{"a": 1}, ["A"], ("A",), float("nan"), float("inf")])
def test_field_text_treats_containers_and_non_finite_as_missing(value: object) -> None:
	assert field_text({"field": value}, "field") == ""
	assert field_text({"field": value}, "field", "离线") == "离线"


def test_field_text_still_stringifies_scalars() -> None:
	assert field_text({"n": 4}, "n") == "4"
	assert field_text({"flag": True}, "flag") == "True"
	assert field_text({"text": "后端"}, "text") == "后端"


def test_job_card_drifted_text_fields_are_missing() -> None:
	job = build_job_from_card(
		{
			"encryptJobId": {"a": 1},
			"jobName": ["A"],
			"salaryDesc": {"low": 1},
			"brandName": ["示例"],
			"jobLabels": [None, {"x": 1}, "Python", 4],
			"jobType": {"code": 4},
		},
		security_id="s1",
		greeted=False,
	)

	assert job["job_id"] == ""
	assert job["title"] == ""
	assert job["salary"] == ""
	assert job["company"] == ""
	assert job["skills"] == ['{"x": 1}', "Python", "4"]
	assert job["raw_job_type"] is None
	assert job["employment_type"] == ""


def test_job_card_keeps_scalar_job_type_for_employment_type() -> None:
	job = build_job_from_card({"jobType": 4}, security_id="s1", greeted=False)

	assert job["raw_job_type"] == 4
	assert job["employment_type"] == "实习"


# ── Issue #1916：CSV / HTML 的列表元素与容器字段归一 ─────────────────


def test_csv_skips_null_list_elements(tmp_path) -> None:
	path = tmp_path / "out.csv"
	write_export_file(
		[{"title": "T", "skills": [None, "Python"], "welfare": [None, "五险一金"]}],
		"csv",
		str(path),
	)

	row = _read_csv(path)
	assert row["skills"] == "Python"
	assert row["welfare"] == "五险一金"
	assert "None" not in row.values()


def test_csv_serializes_container_cells_as_json(tmp_path) -> None:
	path = tmp_path / "out.csv"
	write_export_file(
		[{"title": "T", "skills": [{"name": "Python"}], "raw_job_type": {"code": 4}}],
		"csv",
		str(path),
	)

	row = _read_csv(path)
	assert row["skills"] == '{"name": "Python"}'
	assert row["raw_job_type"] == '{"code": 4}'
	assert "{'name'" not in row["skills"]
	assert "{'code'" not in row["raw_job_type"]


def test_csv_blank_non_finite_numbers(tmp_path) -> None:
	path = tmp_path / "out.csv"
	write_export_file(
		[{"title": "T", "raw_job_type": float("nan"), "least_month": float("inf")}],
		"csv",
		str(path),
	)

	row = _read_csv(path)
	assert row["raw_job_type"] == ""
	assert row["least_month"] == ""
	assert "nan" not in row.values()
	assert "inf" not in row.values()


def test_html_skips_null_list_elements() -> None:
	text = render_html_export([{"title": "T", "skills": [None, "Python"], "welfare": [None, "五险一金"]}])

	assert "Python" in text
	assert "五险一金" in text
	assert "None" not in text
	assert '<span class="tag sk"></span>' not in text


def test_html_serializes_container_cells_as_json() -> None:
	text = render_html_export([{"title": "T", "skills": [{"name": "Python"}]}])

	assert "&quot;name&quot;: &quot;Python&quot;" in text
	assert "{&#x27;name&#x27;" not in text


def test_json_export_keeps_non_finite_numbers_null(tmp_path) -> None:
	path = tmp_path / "out.json"
	write_export_file([{"title": "T", "raw_job_type": float("nan"), "least_month": float("inf")}], "json", str(path))

	row = json.loads(path.read_text(encoding="utf-8"))[0]
	assert row["raw_job_type"] is None
	assert row["least_month"] is None
