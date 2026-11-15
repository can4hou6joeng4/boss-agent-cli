"""Incremental JSON, CSV and formatted XLSX output for crawl runs."""

from __future__ import annotations

import csv
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from boss_agent_cli.crawler.controls import CrawlExportError, CrawlRuntimeUnavailable
from boss_agent_cli.services.chat_utils import sanitize_csv_cell

FIELDS = (
	"query", "page", "rank", "title", "salary", "city", "district", "business_district",
	"company", "company_scale", "industry", "education", "experience", "labels", "benefits", "post_description",
	"address", "detail_status",
)

#: 会被 Excel / openpyxl 当成公式或特殊指令开头的字符。平台原文（JD 分隔线
#: `=====岗位职责=====`、以 `-` 开头的列表项等）经常以这里面的一个开头。
_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _csv_cell(value: Any) -> str:
	"""CSV 没有类型：一律按共享规则清洗后落盘，防止公式注入。"""
	return sanitize_csv_cell(str(value if value is not None else ""))


#: openpyxl 拒绝写入的控制字符（XML 1.0 不允许）。平台原文偶尔夹带，例如
#: 垂直制表符 `\x0b`；保留其余文字，只去掉这些字符。
_ILLEGAL_XLSX_CHARACTERS = re.compile(r"[\000-\010]|[\013-\014]|[\016-\037]")


def _xlsx_cell(value: Any) -> Any:
	if isinstance(value, str):
		return _ILLEGAL_XLSX_CHARACTERS.sub("", value)
	return value


def _force_text_cell(cell: Any) -> None:
	"""把以公式字符开头的字符串单元格固定为文本。

	openpyxl 对以 `=` 开头的字符串默认按公式保存（写 `<f>` 节点），Excel 打开
	时会计算或报错。CSV 用前置单引号解决，XLSX 则应保留原值并显式声明文本——
	单引号在 xlsx 里会作为可见字符留在单元格里。
	"""
	value = cell.value
	if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
		cell.data_type = "s"


def write_run_outputs(output_dir: Path, rows: list[dict[str, Any]]) -> dict[str, str]:
	"""Overwrite the three deterministic artifacts after each completed page.

	Three files must move together: each is written to a sibling temporary file
	first and only replaced once every format succeeded, so a failure in one
	serializer cannot leave the others half-updated (Issue #1135).
	"""
	export_rows = [{field: row.get(field, "") for field in FIELDS} for row in rows]
	output_dir.mkdir(parents=True, exist_ok=True)
	json_path = output_dir / "jobs.json"
	csv_path = output_dir / "jobs.csv"
	xlsx_path = output_dir / "jobs.xlsx"
	temporaries: list[Path] = []
	try:
		json_tmp = _temporary_for(json_path)
		temporaries.append(json_tmp)
		json_tmp.write_text(json.dumps(export_rows, ensure_ascii=False, indent=2), encoding="utf-8")
		csv_tmp = _temporary_for(csv_path)
		temporaries.append(csv_tmp)
		with csv_tmp.open("w", encoding="utf-8-sig", newline="") as file:
			writer = csv.DictWriter(file, fieldnames=FIELDS, extrasaction="ignore")
			writer.writeheader()
			writer.writerows({field: _csv_cell(row.get(field)) for field in FIELDS} for row in export_rows)
		xlsx_tmp = _temporary_for(xlsx_path)
		temporaries.append(xlsx_tmp)
		_write_xlsx(xlsx_tmp, export_rows)
	except CrawlRuntimeUnavailable:
		for temporary in temporaries:
			temporary.unlink(missing_ok=True)
		raise
	except Exception as exc:
		for temporary in temporaries:
			temporary.unlink(missing_ok=True)
		raise CrawlExportError(f"采集结果无法写出：{type(exc).__name__}: {exc}") from exc
	for temporary, target in ((json_tmp, json_path), (csv_tmp, csv_path), (xlsx_tmp, xlsx_path)):
		os.replace(temporary, target)
	return {"json": str(json_path), "csv": str(csv_path), "xlsx": str(xlsx_path)}


def _temporary_for(target: Path) -> Path:
	fd, name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
	os.close(fd)
	return Path(name)


def _write_xlsx(path: Path, rows: list[dict[str, Any]]) -> None:
	try:
		from openpyxl import Workbook
		from openpyxl.styles import Alignment
		from openpyxl.worksheet.table import Table, TableStyleInfo
		from openpyxl.utils import get_column_letter
	except ImportError as exc:  # pragma: no cover - exercised through CLI dependency error
		raise CrawlRuntimeUnavailable("crawl XLSX 导出需要 openpyxl；请安装 boss-agent-cli[crawl]") from exc

	workbook = Workbook()
	sheet = workbook.active
	sheet.title = "jobs"
	for index, field in enumerate(FIELDS, 1):
		cell = sheet.cell(row=1, column=index, value=field)
		cell.alignment = Alignment(horizontal="center", vertical="center")
	for row in rows:
		start = sheet.max_row + 1
		sheet.append([_xlsx_cell(row.get(field, "")) for field in FIELDS])
		for cell in sheet[start]:
			_force_text_cell(cell)

	sheet.freeze_panes = "A2"
	sheet.auto_filter.ref = f"A1:{get_column_letter(len(FIELDS))}{max(1, len(rows) + 1)}"
	sheet.row_dimensions[1].height = 22
	for index, field in enumerate(FIELDS, 1):
		letter = get_column_letter(index)
		width = 18
		if field == "post_description":
			width = 54
		elif field in {"labels", "benefits", "address"}:
			width = 28
		sheet.column_dimensions[letter].width = width
	for row_index in range(2, len(rows) + 2):
		sheet.row_dimensions[row_index].height = 20
		for cell in sheet[row_index]:
			# 保留完整值，但在表格中固定为单行，长内容不拉高整行。
			cell.alignment = Alignment(vertical="center", wrap_text=False)

	if rows:
		table = Table(displayName="JobsTable", ref=f"A1:{get_column_letter(len(FIELDS))}{len(rows) + 1}")
		table.tableStyleInfo = TableStyleInfo(
			name="TableStyleMedium2",
			showFirstColumn=False,
			showLastColumn=False,
			showRowStripes=True,
			showColumnStripes=False,
		)
		sheet.add_table(table)
	workbook.save(path)
