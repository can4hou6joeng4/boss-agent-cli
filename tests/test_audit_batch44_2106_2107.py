"""审计批次：请求租约本地 OSError 归一、crawl 导出非有限数（Issue #2106、#2107）。"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
from pathlib import Path

import pytest

from boss_agent_cli.api import request_budget as request_budget_module
from boss_agent_cli.api.request_budget import RequestBudget
from boss_agent_cli.crawler import exporter
from boss_agent_cli.crawler.exporter import write_run_outputs


# ── #2106：租约目录 / 文件路径异常按确定性本地失败上报 ───────────────


def test_lease_mkdir_failure_is_deterministic_local_error(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	root = tmp_path / "readonly" / "cache"
	root.parent.mkdir()
	budget = RequestBudget(tmp_path / "data", root=root)
	real_mkdir = Path.mkdir

	def fake_mkdir(self: Path, *args, **kwargs):
		if self == root:
			raise PermissionError(13, "permission denied", str(self))
		return real_mkdir(self, *args, **kwargs)

	monkeypatch.setattr(Path, "mkdir", fake_mkdir)

	with pytest.raises(sqlite3.OperationalError, match="无法创建请求预算目录"):
		with budget.attempt():
			pass


def test_directory_lease_open_failure_is_deterministic_local_error(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	root = tmp_path / "cache"
	budget = RequestBudget(tmp_path / "data", root=root)
	real_open = os.open

	def fake_open(path, flags, *args, **kwargs):
		if str(path) == str(root):
			raise PermissionError(13, "permission denied", str(path))
		return real_open(path, flags, *args, **kwargs)

	monkeypatch.setattr(request_budget_module.os, "open", fake_open)

	with pytest.raises(sqlite3.OperationalError, match="无法打开请求预算目录"):
		with budget.attempt():
			pass


def test_lease_file_open_failure_is_deterministic_local_error(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	root = tmp_path / "cache"
	budget = RequestBudget(tmp_path / "data", root=root)
	monkeypatch.setattr(RequestBudget, "_try_directory_lease", staticmethod(lambda path: (False, None)))
	lease_path = root / "request-budget.lease"
	real_open = Path.open

	def fake_open(self: Path, *args, **kwargs):
		if self == lease_path:
			raise PermissionError(13, "permission denied", str(self))
		return real_open(self, *args, **kwargs)

	monkeypatch.setattr(Path, "open", fake_open)

	with pytest.raises(sqlite3.OperationalError, match="无法打开请求租约文件"):
		with budget.attempt():
			pass


# ── #2107：导出对非有限浮点使用同一归一规则 ──────────────────────────


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_csv_cell_normalizes_non_finite_floats(value: float) -> None:
	assert exporter._csv_cell(value) == ""


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_xlsx_cell_normalizes_non_finite_floats(value: float) -> None:
	assert exporter._xlsx_cell(value) is None


def test_run_outputs_normalize_non_finite_floats_consistently(tmp_path: Path) -> None:
	openpyxl = pytest.importorskip("openpyxl")
	rows = [{"title": float("nan"), "salary": float("inf"), "page": 1}]

	write_run_outputs(tmp_path, rows)

	with (tmp_path / "jobs.csv").open(encoding="utf-8-sig", newline="") as handle:
		parsed = list(csv.reader(handle))
	header = parsed[0]
	row = dict(zip(header, parsed[1]))
	assert row["title"] == ""
	assert row["salary"] == ""
	assert row["page"] == "1"

	payload = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))
	assert payload[0]["title"] is None
	assert payload[0]["salary"] is None
	assert payload[0]["page"] == 1

	workbook = openpyxl.load_workbook(tmp_path / "jobs.xlsx")
	values = [cell.value for cell in workbook.active[2]]
	title_column = exporter.FIELDS.index("title")
	salary_column = exporter.FIELDS.index("salary")
	assert values[title_column] is None
	assert values[salary_column] is None
	assert values[exporter.FIELDS.index("page")] == 1
