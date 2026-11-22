"""Incremental JSON, CSV and formatted XLSX output for crawl runs."""

from __future__ import annotations

import csv
import math
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any

from boss_agent_cli.api.models import safe_text
from boss_agent_cli.crawler.controls import CrawlExportError, CrawlRuntimeUnavailable
from boss_agent_cli.services.chat_utils import sanitize_csv_cell
from boss_agent_cli.services.json_files import strict_json_text

FIELDS = (
	"query", "page", "rank", "title", "salary", "city", "district", "business_district",
	"company", "company_scale", "industry", "education", "experience", "labels", "benefits", "post_description",
	"address", "detail_status",
)

#: 会被 Excel / openpyxl 当成公式或特殊指令开头的字符。平台原文（JD 分隔线
#: `=====岗位职责=====`、以 `-` 开头的列表项等）经常以这里面的一个开头。
_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _csv_cell(value: Any) -> str:
	"""CSV 没有类型：一律按共享规则清洗后落盘，防止公式注入。

	非有限浮点与 JSON 分支同口径归一成空值：``nan`` / ``inf`` 不是可移植的
	文本值，也不该在产物里与合法数字混在一起（Issue #2107）。超长整数同样按
	缺失处理：裸 ``str()`` 会抛 ``ValueError``，整份导出中止（Issue #2406）。
	"""
	if isinstance(value, float) and not math.isfinite(value):
		value = None
	return sanitize_csv_cell("" if value is None else safe_text(value))


#: openpyxl 拒绝写入的控制字符（XML 1.0 不允许）。平台原文偶尔夹带，例如
#: 垂直制表符 `\x0b`；保留其余文字，只去掉这些字符。
_ILLEGAL_XLSX_CHARACTERS = re.compile(r"[\000-\010]|[\013-\014]|[\016-\037]|[\ud800-\udfff]|[\ufffe\uffff]")


def _xlsx_cell(value: Any) -> Any:
	# 单条平台字段漂移成 dict/list 时 openpyxl 会抛 ValueError，让整套产物无法
	# 替换且 run 永远停在 stopped（Issue #1646）。这里兜成文本，保留原始信息。
	if isinstance(value, float) and not math.isfinite(value):
		# openpyxl 会为非有限数写出空的 <v></v> 数值单元格（Issue #2107）。
		return None
	if isinstance(value, int) and not isinstance(value, bool) and value.bit_length() > 1000:
		# 保存时 openpyxl 会把数值转 float：超过 1024 bit 的整数抛
		# ``OverflowError: int too large to convert to float``，整份导出失败
		# （Issue #2405）。按文本保留；4300 位以上连 ``str()`` 都不可用，
		# 与展示字段同口径按缺失处理（Issue #2065）。
		try:
			return str(value)
		except ValueError:
			return None
	if isinstance(value, str):
		return _ILLEGAL_XLSX_CHARACTERS.sub("", value)
	if isinstance(value, (int, float, bool)) or value is None:
		return value
	try:
		return _ILLEGAL_XLSX_CHARACTERS.sub("", str(value))
	except (ValueError, RecursionError):
		# 容器内含 4300 位以上整数时 `str()` 抛 ValueError；深嵌套容器抛
		# RecursionError。单条漂移字段按缺失处理，不能中止整份导出
		# （Issue #2490，与 safe_text 的 #2065 / #2319 同口径）。
		return None


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
	# 非对象行直接丢弃：``row.get`` 会抛 AttributeError，绕过下面的导出错误包装，
	# 让命令层拿到裸异常（与 services/job_export 的边界口径一致，Issue #2407）。
	# 缓存读取本身已要求 payload 是 object，这里是公共边界的纵深防御。
	export_rows = [
		{field: row.get(field, "") for field in FIELDS} for row in rows if isinstance(row, dict)
	]
	json_path = output_dir / "jobs.json"
	csv_path = output_dir / "jobs.csv"
	xlsx_path = output_dir / "jobs.xlsx"
	temporaries: list[Path] = []
	# 替换只能发生在三种序列化都成功之后；记录当前目标，替换失败时错误信息
	# 才能指向具体产物。三个目标逐次替换，已替换的文件在后续失败时回滚到旧内容，
	# 避免失败后磁盘上留着不同批次的产物（Issue #1617）。
	current_target: Path | None = None
	# 每项是（目标，旧产物备份，新内容的临时文件）：回滚要靠临时文件是否还在原位
	# 判断这次替换有没有生效。
	replaced: list[tuple[Path, Path | None, Path]] = []
	# 备份搬运被中断时，备份可能是旧产物的唯一副本；必须在外层 try 之前初始化，
	# 序列化阶段就中断时也要能安全读取。备份不预建空文件（Issue #2491）。
	pending_backup: tuple[Path, Path] | None = None
	# 回滚也失败时，备份是旧产物的唯一副本：清理临时文件必须跳过它，
	# 并把路径写进错误消息（Issue #1924）。
	retained: set[Path] = set()
	try:
		# mkdir 也必须在 try 内：输出目录被同名文件占用时 FileExistsError /
		# NotADirectoryError 会原样上抛，被 _run 兜成 ok:true 的 stopped 并提示
		# resume（Issue #1409）。
		output_dir.mkdir(parents=True, exist_ok=True)
		# 产物目标直接 lstat：is_file() 会跟随符号链接，把链接移到备份再删掉，
		# 静默破坏用户的链接布局；FIFO / 设备则会被 os.replace 替换掉（Issue #1755）。
		# 输出目录本身是链接时也会让三份产物落到数据目录之外。
		_dir_mode = output_dir.lstat().st_mode
		if not stat.S_ISDIR(_dir_mode):
			raise CrawlExportError(f"采集结果目录 {output_dir} 不是普通目录（符号链接或特殊文件）")
		# 预检结果同时决定是否备份：替换阶段不再用 is_file() 判一次，3.14 起
		# 它把权限等 OSError 吞成 False，跳过备份后替换失败时回滚只能删除新
		# 产物，旧产物没有副本（Issue #2085）。
		existing_regular: set[Path] = set()
		for target in (json_path, csv_path, xlsx_path):
			try:
				mode = target.lstat().st_mode
			except FileNotFoundError:
				continue
			if stat.S_ISLNK(mode):
				raise CrawlExportError(f"采集产物 {target} 是符号链接，拒绝替换链接或写穿目标")
			if stat.S_ISDIR(mode):
				# 同名目录留给下面的 os.replace 抛 IsADirectoryError：保留既有
				# 回滚与临时文件清理契约（Issue #1616）。
				continue
			if not stat.S_ISREG(mode):
				raise CrawlExportError(f"采集产物 {target} 不是普通文件（FIFO / 设备），拒绝替换")
			existing_regular.add(target)
		json_tmp = _temporary_for(json_path)
		temporaries.append(json_tmp)
		with json_tmp.open("w", encoding="utf-8", errors="backslashreplace") as file:
			# 与职位 / 聊天 JSON 导出同口径：超长整数按 null 降级，单个数值漂移
			# 不再让整份产物失败（Issue #2387、#2406）。
			file.write(strict_json_text(export_rows, null_oversized_ints=True))
		csv_tmp = _temporary_for(csv_path)
		temporaries.append(csv_tmp)
		with csv_tmp.open("w", encoding="utf-8-sig", newline="", errors="backslashreplace") as file:
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
			# 序列化可能耗时，期间目标可以被换成符号链接 / FIFO：预检结果在替换
			# 阶段必须复核（Issue #2492）。
			regular_now = _replacement_target_state(current_target)
			backup: Path | None = None
			if regular_now and current_target in existing_regular:
				# 备份只借用唯一文件名，不预建空文件：预建的空备份在「搬运前
				# 目标消失」的窗口里会被当成旧产物恢复成 0 字节产物
				# （Issue #2491）。搬运失败时名字仍参与临时文件清理，不残留
				# `.jobs.*`（Issue #1875）。
				backup = _backup_path_for(current_target)
				temporaries.append(backup)
				# 搬运前登记：中断可能发生在 os.replace 已生效但还未返回时。
				pending_backup = (current_target, backup)
				os.replace(current_target, backup)
			# 替换前登记回滚项，覆盖调用前 / 调用后两个中断窗口；首次导出没有
			# 旧产物时，也要删除已经写入的新文件，恢复原来不存在的状态。
			replaced.append((current_target, backup, temporary))
			pending_backup = None
			os.replace(temporary, current_target)
	except CrawlRuntimeUnavailable:
		_rollback_replacements(replaced, retained)
		_restore_pending_backup(pending_backup, retained)
		_discard(temporaries, retained)
		raise
	except OSError as exc:
		_rollback_replacements(replaced, retained)
		_restore_pending_backup(pending_backup, retained)
		_discard(temporaries, retained)
		raise CrawlExportError(
			f"采集结果无法写出到 {current_target or output_dir}：{type(exc).__name__}: {exc}{_retained_hint(retained)}"
		) from exc
	except Exception as exc:
		_rollback_replacements(replaced, retained)
		_restore_pending_backup(pending_backup, retained)
		_discard(temporaries, retained)
		raise CrawlExportError(
			f"采集结果无法写出到 {output_dir}：{type(exc).__name__}: {exc}{_retained_hint(retained)}"
		) from exc
	except BaseException:
		# 用户中断（Ctrl-C / SIGTERM 转成的 KeyboardInterrupt、SystemExit）不是
		# Exception：不回滚就会让三份产物落成不同批次，`.jobs.*` 临时 / 备份文件
		# 也会永久残留（其中含整份采集数据）。回滚与清理本身失败时不吞掉原始
		# 中断异常（Issue #2274）。
		_rollback_replacements(replaced, retained)
		_restore_pending_backup(pending_backup, retained)
		_discard(temporaries, retained)
		raise
	# 全部替换成功后才清理备份：三份产物要么都是新批次，要么保持旧批次。
	# 清理失败不能把已成功的导出报成失败（Issue #1926）。
	_discard([backup for _, backup, _ in replaced if backup is not None], retained)
	return {"json": str(json_path), "csv": str(csv_path), "xlsx": str(xlsx_path)}


def _discard(temporaries: list[Path], retained: set[Path]) -> None:
	"""best-effort 清理临时文件：失败只跳过，绝不覆盖正在传播的原始异常（Issue #1925）。"""
	for temporary in temporaries:
		if temporary in retained:
			continue
		try:
			temporary.unlink(missing_ok=True)
		except OSError:
			continue


def _retained_hint(retained: set[Path]) -> str:
	if not retained:
		return ""
	paths = "、".join(str(path) for path in sorted(retained))
	return f"；旧产物备份保留在 {paths}"


def _rollback_replacements(replaced: list[tuple[Path, Path | None, Path]], retained: set[Path]) -> None:
	"""把本次导出已经替换的目标恢复到旧内容，失败不吞掉原始异常。"""
	for target, backup, temporary in reversed(replaced):
		try:
			if backup is not None:
				os.replace(backup, target)
			elif _replacement_took_effect(temporary):
				# 没有旧产物时只删除本次确实搬上去的新文件。回滚项在替换调用之前
				# 就登记了，替换没有生效时目标位置上的东西（同名目录、别的进程
				# 刚写的文件）不属于本次导出，不能删。
				target.unlink(missing_ok=True)
		except OSError:
			# 回滚失败时保留备份文件，操作者可据此恢复；原始错误仍然上抛。
			if backup is not None:
				retained.add(backup)
			continue


def _replacement_took_effect(temporary: Path) -> bool:
	"""新内容的临时文件已经不在原位，说明 ``os.replace`` 已经把它搬到目标上。

	状态无法确认时按没有生效处理：宁可留下一份新产物，也不删除不属于本次
	导出的文件。
	"""
	try:
		temporary.lstat()
	except FileNotFoundError:
		return True
	except OSError:
		return False
	return False


def _replacement_target_state(target: Path) -> bool:
	"""替换阶段复核目标仍是预检时的普通文件（Issue #2492）。

	序列化可能耗时，期间目标可以被换成符号链接或 FIFO。``os.replace`` 搬运的
	是链接本身而不是它指向的内容：不复核就会把用户的链接搬进备份，安装成功后
	清理备份时静默删除链接（预检时目标不存在、期间被换成链接的窗口同样如此），
	预检承诺的「拒绝替换链接或写穿目标」（Issue #1755）随之失效。目标已消失
	返回 False，按首次导出处理；同名目录同样返回 False，交给 ``os.replace``
	抛 ``IsADirectoryError``，保留既有回滚契约（Issue #1616）。
	"""
	try:
		mode = target.lstat().st_mode
	except FileNotFoundError:
		return False
	if stat.S_ISREG(mode):
		return True
	if stat.S_ISDIR(mode):
		return False
	if stat.S_ISLNK(mode):
		raise CrawlExportError(f"采集产物 {target} 变成了符号链接，拒绝替换链接或写穿目标")
	raise CrawlExportError(f"采集产物 {target} 变成了非普通文件（FIFO / 设备），拒绝替换")


def _restore_pending_backup(pending: tuple[Path, Path] | None, retained: set[Path]) -> None:
	"""备份搬运本身被中断：目标还在就丢弃备份，目标已消失就把备份放回去。

	只有搬运中断时 ``replaced`` 里还没有这一项，备份可能是旧产物的唯一副本；
	此时不能把它当临时文件清理掉（Issue #2274）。备份路径不预建文件：它不存在
	就说明搬运调用没有生效，旧产物（若还在）仍在目标位置，不能凭空放回空文件
	（Issue #2491）。
	"""
	if pending is None:
		return
	target, backup = pending
	try:
		backup.lstat()
	except FileNotFoundError:
		return
	except OSError:
		# 状态无法确认：保留备份，宁可多一个隐藏文件也不丢旧产物。
		retained.add(backup)
		return
	try:
		target.lstat()
	except FileNotFoundError:
		pass
	except OSError:
		# 状态无法确认：保留备份，宁可多一个隐藏文件也不丢旧产物。
		retained.add(backup)
		return
	else:
		return
	try:
		os.replace(backup, target)
	except OSError:
		retained.add(backup)


def _temporary_for(target: Path) -> Path:
	# 前缀只用截断后的目标名（最多 32 字符）：导出目标名可能接近 NAME_MAX，
	# 拼完整名会让 mkstemp 直接 ENAMETOOLONG（Issue #2080）。截断仍让残留的
	# 临时 / 备份文件能看出属于哪份产物。
	fd, name = tempfile.mkstemp(prefix=f".{target.name[:32]}.", dir=target.parent)
	os.close(fd)
	return Path(name)


def _backup_path_for(target: Path) -> Path:
	"""为旧产物生成备份路径，但不预建文件（Issue #2491）。

	预建的空备份在「搬运前目标消失」的窗口里会被 ``_restore_pending_backup``
	当成旧产物恢复成 0 字节产物。名字沿用 ``_temporary_for`` 的截断前缀
	（Issue #2080），后缀用 128 bit 随机数保证唯一；备份只在 ``os.replace``
	真正把旧产物搬进来后才存在，因此不存在需要清理的空占位文件。
	"""
	return target.parent / f".{target.name[:32]}.{os.urandom(16).hex()}"


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
