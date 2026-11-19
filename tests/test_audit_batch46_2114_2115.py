"""审计批次：导出标量超大整数、job_card 容器非有限数（Issue #2114、#2115）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from boss_agent_cli.services.job_card import _text_item, text_items
from boss_agent_cli.services.job_export import _cell_text, render_html_export, write_export_file


# ── #2114：导出标量兜底不再让超大整数中止整份导出 ──────────────────


def test_cell_text_large_integer_is_missing_value() -> None:
	assert _cell_text(10**5000) == ""


def test_cell_text_keeps_normal_scalars() -> None:
	assert _cell_text(42) == "42"
	assert _cell_text("文本") == "文本"
	assert _cell_text(True) == "true"


def test_write_export_file_csv_tolerates_large_integer(tmp_path: Path) -> None:
	target = tmp_path / "jobs.csv"
	write_export_file([{"title": 10**5000, "company": "示例公司"}], "csv", str(target))
	body = target.read_text(encoding="utf-8-sig")
	assert "示例公司" in body
	assert "Exceeds the limit" not in body


def test_render_html_export_tolerates_large_integer() -> None:
	body = render_html_export([{"title": 10**5000, "company": "示例公司"}])
	assert "示例公司" in body


# ── #2115：job_card 容器序列化与导出口径一致，禁止 NaN / Infinity ──


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_text_item_normalizes_non_finite_numbers_in_containers(value: float) -> None:
	assert _text_item({"a": value}) == '{"a": null}'


def test_text_items_normalizes_nested_non_finite_numbers() -> None:
	assert text_items({"jobLabels": [{"score": float("nan")}]}, "jobLabels") == ['{"score": null}']


def test_text_item_keeps_normal_container_and_scalars() -> None:
	assert _text_item({"a": 1}) == '{"a": 1}'
	assert _text_item(10**5000) == ""
	assert _text_item(True) == "True"


def test_text_item_deep_nesting_is_missing_value() -> None:
	nested: list[object] = []
	cursor = nested
	for _ in range(2000):
		child: list[object] = []
		cursor.append(child)
		cursor = child
	assert _text_item(nested) == ""
