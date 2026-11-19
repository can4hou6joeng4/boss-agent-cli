"""采集导出备份判定复用预检的 lstat 结果（Issue #2085）。

``write_run_outputs`` 先用 ``lstat`` 预检三个产物目标（符号链接 / FIFO / 目录），
替换阶段却用 ``current_target.is_file()`` 再判一次是否备份。3.14 起
``is_file()`` 把权限等 OSError 吞成 False，备份被跳过，安装新产物后旧内容没有
可回滚副本；替换中途失败时，回滚只能删除新产物，旧产物彻底丢失。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from boss_agent_cli.crawler.controls import CrawlExportError
from boss_agent_cli.crawler.exporter import write_run_outputs


def _job(job_id: str) -> dict[str, str]:
	return {"title": job_id, "salary": "20-30K", "city": "杭州"}


def _seed_old_outputs(output_dir: Path) -> None:
	(output_dir / "jobs.json").write_text("OLD-JSON", encoding="utf-8")
	(output_dir / "jobs.csv").write_text("OLD-CSV", encoding="utf-8")
	(output_dir / "jobs.xlsx").write_text("OLD-XLSX", encoding="utf-8")


def test_backup_survives_swallowed_is_file_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""is_file() 吞掉元数据错误时，旧产物仍要备份，替换失败才能回滚。"""
	_seed_old_outputs(tmp_path)
	# 模拟 3.14 的 Path.is_file()：权限等 OSError 被吞成 False。
	monkeypatch.setattr(Path, "is_file", lambda self: False)
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

	with pytest.raises(CrawlExportError):
		write_run_outputs(tmp_path, [_job("new")])

	# 旧代码没有备份 jobs.json，回滚只能删掉新产物；新代码把旧内容放回。
	assert (tmp_path / "jobs.json").read_text(encoding="utf-8") == "OLD-JSON"
