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
	# 单条平台字段漂移成 dict/list 时 openpyxl 会抛 ValueError，让整套产物无法
	# 替换且 run 永远停在 stopped（Issue #1646）。这里兜成文本，保留原始信息。
	if isinstance(value, str):
		return _ILLEGAL_XLSX_CHARACTERS.sub("", value)
	if isinstance(value, (int, float, bool)) or value is None:
		return value
	return _ILLEGAL_XLSX_CHARACTERS.sub("", str(value))


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
	json_path = output_dir / "jobs.json"
	csv_path = output_dir / "jobs.csv"
	xlsx_path = output_dir / "jobs.xlsx"
	temporaries: list[Path] = []
	# 替换只能发生在三种序列化都成功之后；记录当前目标，替换失败时错误信息
	# 才能指向具体产物。三个目标逐次替换，已替换的文件在后续失败时回滚到旧内容，
	# 避免失败后磁盘上留着不同批次的产物（Issue #1617）。
	current_target: Path | None = None
	replaced: list[tuple[Path, Path | None]] = []
	try:
		# mkdir 也必须在 try 内：输出目录被同名文件占用时 FileExistsError /
		# NotADirectoryError 会原样上抛，被 _run 兜成 ok:true 的 stopped 并提示
		# resume（Issue #1409）。
		output_dir.mkdir(parents=True, exist_ok=True)
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
		# 替换也必须留在 try 内：目标已是目录（或权限 / 磁盘问题）时 os.replace
		# 抛 OSError，原样上抛会被 _run 兜成 status=stopped 的 ok:true 信封，
		# 且 `.jobs.*` 临时文件残留（Issue #1346）。
		for temporary, current_target in ((json_tmp, json_path), (csv_tmp, csv_path), (xlsx_tmp, xlsx_path)):
			backup: Path | None = None
			if current_target.is_file():
				backup = _temporary_for(current_target)
				os.replace(current_target, backup)
			try:
				os.replace(temporary, current_target)
			except OSError:
				if backup is not None:
					os.replace(backup, current_target)
				raise
			replaced.append((current_target, backup))
	except CrawlRuntimeUnavailable:
		_rollback_replacements(replaced)
		for temporary in temporaries:
			temporary.unlink(missing_ok=True)
		raise
	except OSError as exc:
		_rollback_replacements(replaced)
		for temporary in temporaries:
			temporary.unlink(missing_ok=True)
		raise CrawlExportError(
			f"采集结果无法写出到 {current_target or output_dir}：{type(exc).__name__}: {exc}"
		) from exc
	except Exception as exc:
		_rollback_replacements(replaced)
		for temporary in temporaries:
			temporary.unlink(missing_ok=True)
		raise CrawlExportError(f"采集结果无法写出到 {output_dir}：{type(exc).__name__}: {exc}") from exc
	# 全部替换成功后才清理备份：三份产物要么都是新批次，要么保持旧批次。
	for _, backup in replaced:
		if backup is not None:
			backup.unlink(missing_ok=True)
	return {"json": str(json_path), "csv": str(csv_path), "xlsx": str(xlsx_path)}


def _rollback_replacements(replaced: list[tuple[Path, Path | None]]) -> None:
	"""把本次导出已经替换的目标恢复到旧内容，失败不吞掉原始异常。"""
	for target, backup in reversed(replaced):
		try:
			if backup is not None:
				os.replace(backup, target)
			else:
				target.unlink(missing_ok=True)
		except OSError:
			# 回滚失败时保留备份文件，操作者可据此恢复；原始错误仍然上抛。
			continue


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
