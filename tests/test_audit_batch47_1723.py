"""批次四十七审计修复（Issue #1723）：未接统一 `-o` 预检的命令。

- digest：父路径不是目录时先请求平台、后报路径错误；
- digest / stats / resume export：FIFO 等特殊文件被 `os.replace` 替换成普通文件。
"""

import json
import os
from pathlib import Path
import stat

from click.testing import CliRunner
import pytest

from boss_agent_cli.main import cli


def _invoke(tmp_path: Path, *args: str):
	return CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), *args])


def _envelope(result) -> dict:
	return json.loads(result.stdout)


def _make_fifo(path: Path) -> Path:
	os.mkfifo(path)
	return path


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有 POSIX FIFO")
def test_digest_rejects_fifo_output_before_platform(tmp_path, monkeypatch):
	fifo = _make_fifo(tmp_path / "digest.pipe")
	monkeypatch.setattr(
		"boss_agent_cli.commands.digest.AuthManager",
		lambda *args, **kwargs: pytest.fail("FIFO 目标不应走到登录与平台请求"),
	)

	result = _invoke(tmp_path, "digest", "--format", "md", "-o", str(fifo))

	assert result.exit_code == 1, result.stdout
	error = _envelope(result)["error"]
	assert error["code"] == "INVALID_PARAM"
	assert "普通文件" in error["message"]
	assert stat.S_ISFIFO(fifo.lstat().st_mode), "FIFO 不能被原子替换成普通文件"


def test_digest_rejects_parent_not_dir_before_platform(tmp_path, monkeypatch):
	blocker = tmp_path / "blocker"
	blocker.write_text("not a directory", encoding="utf-8")
	monkeypatch.setattr(
		"boss_agent_cli.commands.digest.AuthManager",
		lambda *args, **kwargs: pytest.fail("父路径错误不应走到登录与平台请求"),
	)

	result = _invoke(tmp_path, "digest", "--format", "md", "-o", str(blocker / "digest.md"))

	assert result.exit_code == 1, result.stdout
	error = _envelope(result)["error"]
	assert error["code"] == "LOCAL_IO_ERROR"
	assert "父路径" in error["message"]


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有 POSIX FIFO")
def test_stats_rejects_fifo_output_without_replacing_it(tmp_path):
	fifo = _make_fifo(tmp_path / "stats.pipe")

	result = _invoke(tmp_path, "stats", "--format", "html", "-o", str(fifo))

	assert result.exit_code == 1, result.stdout
	error = _envelope(result)["error"]
	assert error["code"] == "INVALID_PARAM"
	assert stat.S_ISFIFO(fifo.lstat().st_mode), "FIFO 不能被原子替换成普通文件"


def test_stats_rejects_parent_not_dir_output(tmp_path):
	blocker = tmp_path / "blocker"
	blocker.write_text("not a directory", encoding="utf-8")

	result = _invoke(tmp_path, "stats", "--format", "html", "-o", str(blocker / "stats.html"))

	assert result.exit_code == 1, result.stdout
	error = _envelope(result)["error"]
	assert error["code"] == "LOCAL_IO_ERROR"
	assert "父路径" in error["message"]


@pytest.mark.skipif(os.name == "nt", reason="符号链接权限依 Windows 配置而定")
def test_stats_rejects_symlink_output_without_touching_target(tmp_path):
	real = tmp_path / "real.html"
	real.write_text("old", encoding="utf-8")
	link = tmp_path / "link.html"
	link.symlink_to(real)

	result = _invoke(tmp_path, "stats", "--format", "html", "-o", str(link))

	assert result.exit_code == 1, result.stdout
	error = _envelope(result)["error"]
	assert error["code"] == "INVALID_PARAM"
	assert real.read_text(encoding="utf-8") == "old"
	assert link.is_symlink()


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有 POSIX FIFO")
def test_resume_export_rejects_fifo_output(tmp_path):
	fifo = _make_fifo(tmp_path / "resume.pipe")

	result = _invoke(tmp_path, "resume", "export", "demo", "--format", "html", "-o", str(fifo))

	assert result.exit_code == 1, result.stdout
	error = _envelope(result)["error"]
	assert error["code"] == "INVALID_PARAM"
	assert stat.S_ISFIFO(fifo.lstat().st_mode), "FIFO 不能被原子替换成普通文件"
