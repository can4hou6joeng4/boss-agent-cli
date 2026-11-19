"""采集导出失败 / 清理路径的产物完整性（Issue #1924–#1926）。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from boss_agent_cli.crawler.controls import CrawlExportError, CrawlRuntimeUnavailable
from boss_agent_cli.crawler.exporter import write_run_outputs


def _job(job_id: str) -> dict[str, str]:
	return {"title": job_id, "salary": "20-30K", "city": "杭州"}


def _seed_old_outputs(output_dir: Path) -> None:
	(output_dir / "jobs.json").write_text("OLD-JSON", encoding="utf-8")
	(output_dir / "jobs.csv").write_text("OLD-CSV", encoding="utf-8")
	(output_dir / "jobs.xlsx").write_text("OLD-XLSX", encoding="utf-8")


def _leftovers(output_dir: Path, prefix: str) -> list[Path]:
	return sorted(path for path in output_dir.glob(f".{prefix}.*") if path.is_file())


def test_rollback_failure_keeps_backup_as_last_copy(tmp_path, monkeypatch) -> None:
	_seed_old_outputs(tmp_path)
	real_replace = os.replace
	csv_attempts = 0

	def flaky_replace(source, destination, *args, **kwargs):
		nonlocal csv_attempts
		if Path(destination) == tmp_path / "jobs.csv":
			csv_attempts += 1
			# 第一次是安装新产物，第二次是回滚旧产物：两次都拒绝写入。
			raise PermissionError(13, "Permission denied", str(destination))
		return real_replace(source, destination, *args, **kwargs)

	monkeypatch.setattr("boss_agent_cli.crawler.exporter.os.replace", flaky_replace)

	with pytest.raises(CrawlExportError) as caught:
		write_run_outputs(tmp_path, [_job("new")])

	assert "旧产物备份保留在" in str(caught.value)
	retained = _leftovers(tmp_path, "jobs.csv")
	assert len(retained) == 1
	assert retained[0].read_text(encoding="utf-8") == "OLD-CSV"
	assert not (tmp_path / "jobs.csv").exists()
	# 已成功替换的 json 必须回滚到旧内容，不能停留在新批次。
	assert (tmp_path / "jobs.json").read_text(encoding="utf-8") == "OLD-JSON"


def test_cleanup_failure_does_not_replace_original_error(tmp_path, monkeypatch) -> None:
	_seed_old_outputs(tmp_path)
	real_unlink = Path.unlink

	def flaky_unlink(self, *args, **kwargs):
		if self.name.startswith(".jobs."):
			raise PermissionError(13, "Permission denied", str(self))
		return real_unlink(self, *args, **kwargs)

	def unavailable(path, rows):
		raise CrawlRuntimeUnavailable("openpyxl missing (simulated)")

	monkeypatch.setattr(Path, "unlink", flaky_unlink)
	monkeypatch.setattr("boss_agent_cli.crawler.exporter._write_xlsx", unavailable)

	with pytest.raises(CrawlRuntimeUnavailable):
		write_run_outputs(tmp_path, [_job("new")])


def test_backup_cleanup_failure_after_success_is_not_an_error(tmp_path, monkeypatch) -> None:
	_seed_old_outputs(tmp_path)
	real_unlink = Path.unlink

	def flaky_unlink(self, *args, **kwargs):
		if self.name.startswith(".jobs."):
			raise PermissionError(13, "Permission denied", str(self))
		return real_unlink(self, *args, **kwargs)

	monkeypatch.setattr(Path, "unlink", flaky_unlink)

	written = write_run_outputs(tmp_path, [_job("new")])

	assert Path(written["json"]).is_file()
	assert Path(written["csv"]).is_file()
	assert Path(written["xlsx"]).is_file()
	assert '"new"' in (tmp_path / "jobs.json").read_text(encoding="utf-8")
	assert "new" in (tmp_path / "jobs.csv").read_text(encoding="utf-8-sig")
	# 备份清理失败只留下残留文件，不影响成功结果。
	assert _leftovers(tmp_path, "jobs.json")
