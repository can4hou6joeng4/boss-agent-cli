"""`ai @file` / `resume import` 外部输入读取的 fail-closed 回归测试（Issue #1714）。

两处此前用 ``exists()`` / ``is_dir()`` 预检后直接 ``read_text()``：FIFO 会
永久阻塞，3.14 下元数据错误又被当成「文件不存在」。现在只有缺失才算缺失，
非普通文件一律拒绝，不打开读取。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from boss_agent_cli.commands import ai_cmd
from boss_agent_cli.main import cli
from boss_agent_cli.resume.store import ResumeImportError, ResumeStore


def _invoke(data_dir: Path, *args: str) -> tuple[int, dict[str, Any]]:
	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(data_dir), *args])
	return result.exit_code, json.loads(result.stdout)


def _fail_lstat_for(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	real_lstat = Path.lstat

	def lstat_fails(self: Path) -> Any:
		if self == path:
			raise PermissionError("permission denied")
		return real_lstat(self)

	monkeypatch.setattr(Path, "lstat", lstat_fails)


def _make_fifo(path: Path) -> None:
	if os.name == "nt":
		pytest.skip("Windows 不提供 os.mkfifo")
	path.parent.mkdir(parents=True, exist_ok=True)
	os.mkfifo(path)


@pytest.fixture
def fake_ai(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setattr(ai_cmd, "_require_ai_service", lambda ctx: object())


def test_ai_at_file_fifo_is_rejected_without_reading(
	tmp_path: Path, fake_ai: None
) -> None:
	fifo = tmp_path / "prompt.fifo"
	_make_fifo(fifo)

	exit_code, payload = _invoke(tmp_path, "ai", "analyze-jd", f"@{fifo}", "--resume", "demo")

	assert exit_code == 1
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "不是普通文件" in payload["error"]["message"]


def test_ai_at_file_metadata_error_is_local_io(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_ai: None
) -> None:
	path = tmp_path / "jd.txt"
	path.write_text("职位描述", encoding="utf-8")
	_fail_lstat_for(path, monkeypatch)

	exit_code, payload = _invoke(tmp_path, "ai", "analyze-jd", f"@{path}", "--resume", "demo")

	assert exit_code == 1
	assert payload["ok"] is False
	assert payload["error"]["code"] == "LOCAL_IO_ERROR"


def test_ai_at_missing_file_still_reports_not_found(
	tmp_path: Path, fake_ai: None
) -> None:
	missing = tmp_path / "missing.txt"

	exit_code, payload = _invoke(tmp_path, "ai", "analyze-jd", f"@{missing}", "--resume", "demo")

	assert exit_code == 1
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "不存在" in payload["error"]["message"]


def test_ai_at_directory_still_reports_directory(tmp_path: Path, fake_ai: None) -> None:
	exit_code, payload = _invoke(tmp_path, "ai", "analyze-jd", f"@{tmp_path}", "--resume", "demo")

	assert exit_code == 1
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "是目录" in payload["error"]["message"]


def test_ai_at_dangling_symlink_is_rejected(tmp_path: Path, fake_ai: None) -> None:
	link = tmp_path / "dangling.txt"
	link.symlink_to(tmp_path / "outside.txt")

	exit_code, payload = _invoke(tmp_path, "ai", "analyze-jd", f"@{link}", "--resume", "demo")

	assert exit_code == 1
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "不是普通文件" in payload["error"]["message"]


def test_resume_import_fifo_is_rejected_without_reading(tmp_path: Path) -> None:
	fifo = tmp_path / "resume.fifo"
	_make_fifo(fifo)

	exit_code, payload = _invoke(tmp_path, "resume", "import", str(fifo))

	assert exit_code == 1
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "不是普通文件" in payload["error"]["message"]


def test_resume_import_metadata_error_is_local_io(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	path = tmp_path / "resume.json"
	path.write_text('{"name": "demo"}', encoding="utf-8")
	_fail_lstat_for(path, monkeypatch)

	exit_code, payload = _invoke(tmp_path, "resume", "import", str(path))

	assert exit_code == 1
	assert payload["ok"] is False
	assert payload["error"]["code"] == "LOCAL_IO_ERROR"


def test_resume_store_import_file_rejects_non_regular_path(tmp_path: Path) -> None:
	store = ResumeStore(tmp_path / "resumes")
	fifo = tmp_path / "resume.fifo"
	if os.name == "nt":
		pytest.skip("Windows 不提供 os.mkfifo")
	os.mkfifo(fifo)

	with pytest.raises(ResumeImportError):
		store.import_file(fifo)


def test_resume_store_import_file_keeps_missing_as_file_not_found(tmp_path: Path) -> None:
	store = ResumeStore(tmp_path / "resumes")

	with pytest.raises(FileNotFoundError):
		store.import_file(tmp_path / "missing.json")
