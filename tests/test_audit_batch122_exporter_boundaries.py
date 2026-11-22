"""审计批次 122：采集导出的三处边界（Issue #2490 / #2491 / #2492）。

- #2490：XLSX 容器字段内含超长整数时 `str()` 抛 `ValueError`，整份导出永久失败；
- #2491：预检后目标消失时，mkstemp 预建的空备份被恢复成 0 字节产物；
- #2492：序列化期间目标被换成符号链接 / FIFO，预检失效，链接被搬走并在成功后删除。

三处都必须在离线环境复现，不依赖 openpyxl（CI 没有 crawl 依赖）。
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from boss_agent_cli.crawler import exporter
from boss_agent_cli.crawler.controls import CrawlExportError
from boss_agent_cli.crawler.exporter import _xlsx_cell, write_run_outputs

_ROWS = [{"title": "示例岗位", "company": "示例公司"}]


def _seed_old_outputs(output_dir: Path) -> None:
	for name in ("jobs.json", "jobs.csv", "jobs.xlsx"):
		(output_dir / name).write_text(f"OLD-{name}", encoding="utf-8")


def _hidden_jobs_files(output_dir: Path) -> list[str]:
	return sorted(path.name for path in output_dir.iterdir() if path.name.startswith(".jobs."))


# ---------------------------------------------------------------------------
# Issue #2490：XLSX 单元格的 str() 兜底
# ---------------------------------------------------------------------------


def test_xlsx_container_with_oversized_int_degrades_to_missing() -> None:
	"""容器内超长整数让 str() 抛 ValueError；按缺失处理，不中止整份导出。"""
	assert _xlsx_cell({"n": 10**4400}) is None
	assert _xlsx_cell([10**4400, "ok"]) is None


def test_xlsx_normal_containers_still_render_as_text() -> None:
	"""兜底不能吞掉正常容器：保持既有「转文本保留原始信息」的行为（#1646）。"""
	assert _xlsx_cell({"a": 1}) == "{'a': 1}"
	assert _xlsx_cell(["x", "y"]) == "['x', 'y']"


def test_xlsx_scalar_oversized_int_still_missing() -> None:
	assert _xlsx_cell(10**4400) is None


# ---------------------------------------------------------------------------
# Issue #2491：目标在预检后消失，不能恢复空备份
# ---------------------------------------------------------------------------


def test_vanished_target_is_installed_fresh_not_recreated_empty(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	"""序列化期间目标被删除：按首次导出处理，产物必须是完整内容而不是 0 字节。"""
	_seed_old_outputs(tmp_path)

	def _removing_write_xlsx(path: Path, rows: list[dict[str, str]]) -> None:
		# 模拟另一个进程在预检之后、替换之前删除了 jobs.json。
		(tmp_path / "jobs.json").unlink()
		path.write_bytes(b"new-xlsx")

	monkeypatch.setattr(exporter, "_write_xlsx", _removing_write_xlsx)

	write_run_outputs(tmp_path, _ROWS)

	written = tmp_path / "jobs.json"
	assert written.stat().st_size > 0
	written_rows = json.loads(written.read_text(encoding="utf-8"))
	assert len(written_rows) == 1
	assert written_rows[0]["title"] == "示例岗位"
	assert written_rows[0]["company"] == "示例公司"
	assert "示例岗位" in (tmp_path / "jobs.csv").read_text(encoding="utf-8-sig")
	assert _hidden_jobs_files(tmp_path) == []


def test_backup_path_is_not_precreated_before_the_move(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""备份只借用唯一名字：搬运前它不能以空文件形式存在（#2491 的根因）。"""
	_seed_old_outputs(tmp_path)
	precreated: list[bool] = []
	real_replace = os.replace

	def _recording_replace(source, target, *args, **kwargs):
		destination = Path(target)
		if destination.name.startswith(".jobs."):
			# 目标是备份路径：旧实现由 mkstemp 预建空文件，搬运前就存在。
			precreated.append(destination.exists())
		return real_replace(source, target, *args, **kwargs)

	monkeypatch.setattr(exporter, "_write_xlsx", lambda path, rows: path.write_bytes(b"new-xlsx"))
	monkeypatch.setattr(exporter.os, "replace", _recording_replace)

	write_run_outputs(tmp_path, _ROWS)

	assert precreated == [False, False, False]


# ---------------------------------------------------------------------------
# Issue #2492：替换前复核目标类型
# ---------------------------------------------------------------------------


def test_target_swapped_to_symlink_after_precheck_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""预检后目标变成符号链接：拒绝替换，链接与指向的内容都必须保持原样。"""
	_seed_old_outputs(tmp_path)
	victim = tmp_path / "victim.txt"
	victim.write_text("KEEP", encoding="utf-8")
	link_target = tmp_path / "jobs.json"

	def _swapping_write_xlsx(path: Path, rows: list[dict[str, str]]) -> None:
		link_target.unlink()
		link_target.symlink_to(victim)
		path.write_bytes(b"new-xlsx")

	monkeypatch.setattr(exporter, "_write_xlsx", _swapping_write_xlsx)

	with pytest.raises(CrawlExportError):
		write_run_outputs(tmp_path, _ROWS)

	assert link_target.is_symlink()
	assert link_target.resolve() == victim.resolve()
	assert victim.read_text(encoding="utf-8") == "KEEP"
	assert _hidden_jobs_files(tmp_path) == []
	assert (tmp_path / "jobs.csv").read_text(encoding="utf-8") == "OLD-jobs.csv"
	assert (tmp_path / "jobs.xlsx").read_text(encoding="utf-8") == "OLD-jobs.xlsx"


def test_symlink_created_after_precheck_on_fresh_export_is_rejected(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	"""预检时目标不存在、序列化期间才出现的链接：同样拒绝，且回滚已安装的产物。"""
	victim = tmp_path / "victim.txt"
	victim.write_text("KEEP", encoding="utf-8")
	link_target = tmp_path / "jobs.csv"

	def _swapping_write_xlsx(path: Path, rows: list[dict[str, str]]) -> None:
		link_target.symlink_to(victim)
		path.write_bytes(b"new-xlsx")

	monkeypatch.setattr(exporter, "_write_xlsx", _swapping_write_xlsx)

	with pytest.raises(CrawlExportError):
		write_run_outputs(tmp_path, _ROWS)

	assert link_target.is_symlink()
	assert victim.read_text(encoding="utf-8") == "KEEP"
	# jobs.json 在发现链接之前已经安装：必须回滚到不存在的状态。
	with pytest.raises(FileNotFoundError):
		(tmp_path / "jobs.json").lstat()
	assert _hidden_jobs_files(tmp_path) == []


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="需要 FIFO 支持")
def test_target_swapped_to_fifo_after_precheck_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	_seed_old_outputs(tmp_path)
	fifo = tmp_path / "jobs.json"

	def _swapping_write_xlsx(path: Path, rows: list[dict[str, str]]) -> None:
		fifo.unlink()
		os.mkfifo(fifo)
		path.write_bytes(b"new-xlsx")

	monkeypatch.setattr(exporter, "_write_xlsx", _swapping_write_xlsx)

	with pytest.raises(CrawlExportError):
		write_run_outputs(tmp_path, _ROWS)

	assert stat.S_ISFIFO(fifo.lstat().st_mode)
	assert _hidden_jobs_files(tmp_path) == []
