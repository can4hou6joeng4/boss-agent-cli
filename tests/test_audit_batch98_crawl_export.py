"""批次 98 审计修复：采集导出的超长整数 / 非对象行与 shortlist 主键归一（Issue #2405–#2408）。"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.crawler import exporter
from boss_agent_cli.crawler.exporter import _csv_cell, _xlsx_cell, write_run_outputs
from boss_agent_cli.crawler.operations import import_crawl_shortlist

_HUGE = 10**5000
_BIG = 10**309


# ── Issue #2405：XLSX 单元格的大整数降级 ─────────────────────────────


def test_xlsx_cell_turns_large_int_into_text() -> None:
	# 1024 bit 以上 openpyxl 转 float 会 OverflowError；按文本保留信息。
	assert _xlsx_cell(_BIG) == str(_BIG)
	assert _xlsx_cell(_HUGE) is None
	assert _xlsx_cell(42) == 42
	assert _xlsx_cell(True) is True


# ── Issue #2406：JSON / CSV 与批次 95 的超长整数口径一致 ─────────────


def test_csv_cell_degrades_oversized_int() -> None:
	assert _csv_cell(_HUGE) == ""
	assert _csv_cell(_BIG) == str(_BIG)
	assert _csv_cell(float("nan")) == ""
	assert _csv_cell(42) == "42"


def test_write_run_outputs_json_csv_survive_oversized_int(tmp_path: Path, monkeypatch) -> None:
	monkeypatch.setattr(exporter, "_write_xlsx", lambda path, rows: path.write_text("", encoding="utf-8"))

	paths = write_run_outputs(tmp_path, [{"title": "T", "page": _HUGE, "rank": _BIG}])

	payload = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
	assert payload[0]["page"] is None
	assert payload[0]["rank"] == _BIG
	assert payload[0]["title"] == "T"

	csv_text = Path(paths["csv"]).read_text(encoding="utf-8-sig")
	csv_rows = list(csv.DictReader(io.StringIO(csv_text)))
	assert csv_rows[0]["rank"] == str(_BIG)
	assert csv_rows[0]["page"] == ""


def test_write_run_outputs_xlsx_survives_large_int(tmp_path: Path) -> None:
	pytest.importorskip("openpyxl")

	paths = write_run_outputs(tmp_path, [{"title": "T", "rank": _BIG, "page": _HUGE}])

	assert Path(paths["xlsx"]).stat().st_size > 0


# ── Issue #2407：非对象行不绕过导出错误包装 ──────────────────────────


def test_write_run_outputs_ignores_non_object_rows(tmp_path: Path, monkeypatch) -> None:
	monkeypatch.setattr(exporter, "_write_xlsx", lambda path, rows: path.write_text("", encoding="utf-8"))

	paths = write_run_outputs(tmp_path, [None, "row", {"title": "T"}])  # type: ignore[list-item]

	payload = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
	assert len(payload) == 1
	assert payload[0]["title"] == "T"


# ── Issue #2408：shortlist 导入的 null 主键按缺失处理 ────────────────


def _seed_crawl_rows(tmp_path: Path, payloads: list[dict[str, object]]) -> Path:
	db_path = tmp_path / "cache" / "boss_agent.db"
	db_path.parent.mkdir(parents=True, exist_ok=True)
	with CacheStore(db_path) as cache:
		cache.create_crawl_run("run-1", {}, str(tmp_path / "out"), status="completed")
		for index, payload in enumerate(payloads):
			cache.put_crawl_job("run-1", f"job-{index}", 1, payload, detail_done=True)
	return db_path


def test_shortlist_import_skips_null_identifiers(tmp_path: Path) -> None:
	db_path = _seed_crawl_rows(
		tmp_path,
		[
			{"job_id": None, "security_id": None, "title": None},
			{"job_id": None, "security_id": None, "title": None},
		],
	)

	with CacheStore(db_path) as cache:
		result = import_crawl_shortlist(cache, "run-1", include_all=True)

		assert result["selected_count"] == 2
		assert result["imported_count"] == 0
		assert result["skipped_count"] == 2
		assert cache.list_shortlist() == []


def test_shortlist_import_keeps_real_identifiers(tmp_path: Path) -> None:
	db_path = _seed_crawl_rows(
		tmp_path,
		[{"job_id": "j-1", "security_id": "s-1", "title": None, "company": "C"}],
	)

	with CacheStore(db_path) as cache:
		result = import_crawl_shortlist(cache, "run-1", include_all=True)
		rows = cache.list_shortlist()

	assert result["imported_count"] == 1
	assert rows[0]["title"] == ""
	assert rows[0]["company"] == "C"
