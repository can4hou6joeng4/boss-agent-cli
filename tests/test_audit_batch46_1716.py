"""stats 与 `-o` 预检的路径状态 fail-closed 回归测试（Issue #1716）。

两处都曾在元数据错误时把路径当成「不存在」：stats 报没有缓存，`-o`
预检报告未发现问题，把失败留到平台分页与写入之后。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.services.output_path import (
	OutputPathProblem,
	inspect_output_path,
)


def _invoke(data_dir: Path, *args: str) -> tuple[int, dict[str, Any]]:
	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(data_dir), *args])
	return result.exit_code, json.loads(result.stdout)


def _fail_lstat_for(paths: set[Path], monkeypatch: pytest.MonkeyPatch) -> None:
	real_lstat = Path.lstat

	def lstat_fails(self: Path) -> Any:
		if self in paths:
			raise PermissionError("permission denied")
		return real_lstat(self)

	monkeypatch.setattr(Path, "lstat", lstat_fails)


def test_stats_db_metadata_error_is_local_io(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	db_path = tmp_path / "cache" / "boss_agent.db"
	db_path.parent.mkdir(parents=True)
	db_path.write_bytes(b"not-a-real-db")
	_fail_lstat_for({db_path}, monkeypatch)

	exit_code, payload = _invoke(tmp_path, "stats")

	assert exit_code == 1
	assert payload["ok"] is False
	assert payload["error"]["code"] == "LOCAL_IO_ERROR"


def test_stats_fifo_db_is_rejected_without_connecting(tmp_path: Path) -> None:
	if os.name == "nt":
		pytest.skip("Windows 不提供 os.mkfifo")
	db_path = tmp_path / "cache" / "boss_agent.db"
	db_path.parent.mkdir(parents=True)
	os.mkfifo(db_path)

	exit_code, payload = _invoke(tmp_path, "stats")

	assert exit_code == 1
	assert payload["ok"] is False
	assert payload["error"]["code"] == "LOCAL_IO_ERROR"
	assert db_path.exists()


def test_stats_missing_db_still_reports_no_cache(tmp_path: Path) -> None:
	exit_code, payload = _invoke(tmp_path, "stats")

	assert exit_code == 0
	assert payload["ok"] is True
	assert payload["data"]["funnel"]["greeted"] == 0
	assert "缓存尚未建立" in payload["data"]["note"]


def test_inspect_output_path_target_metadata_error_raises(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	target = tmp_path / "report.md"
	target.write_text("old", encoding="utf-8")
	_fail_lstat_for({target}, monkeypatch)

	with pytest.raises(PermissionError):
		inspect_output_path(target)


def test_inspect_output_path_parent_metadata_error_raises(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	parent = tmp_path / "reports"
	parent.mkdir()
	_fail_lstat_for({parent}, monkeypatch)

	with pytest.raises(PermissionError):
		inspect_output_path(parent / "nested" / "report.md")


def test_inspect_output_path_missing_still_ok(tmp_path: Path) -> None:
	target, problem = inspect_output_path(tmp_path / "deep" / "nested" / "report.md")

	assert problem is None
	assert target == tmp_path / "deep" / "nested" / "report.md"


def test_inspect_output_path_file_parent_still_reports_problem(tmp_path: Path) -> None:
	blocker = tmp_path / "blocker"
	blocker.write_text("not a directory", encoding="utf-8")

	_, problem = inspect_output_path(blocker / "report.md")

	assert problem == OutputPathProblem.PARENT_NOT_DIR
