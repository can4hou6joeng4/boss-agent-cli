"""`-o` 输出路径语义：展开 `~` 并创建父目录。

MCP 通过子进程调用 CLI（`mcp_server._run_boss`），参数不经 shell，`~` 不会被
shell 展开。命令里不处理就会在当前目录建出一个字面量 `~` 目录。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from boss_agent_cli.services.output_path import prepare_output_path


def test_expands_tilde(monkeypatch, tmp_path):
	monkeypatch.setenv("HOME", str(tmp_path))
	monkeypatch.setenv("USERPROFILE", str(tmp_path))

	target = prepare_output_path("~/out/report.md")

	assert target == tmp_path / "out" / "report.md"
	assert target.parent.is_dir()


def test_creates_nested_parents(tmp_path):
	target = prepare_output_path(tmp_path / "a" / "b" / "c" / "report.html")

	assert target.parent.is_dir()
	assert target == tmp_path / "a" / "b" / "c" / "report.html"


def test_bare_filename_keeps_relative_path(tmp_path, monkeypatch):
	monkeypatch.chdir(tmp_path)

	target = prepare_output_path("report.json")

	assert not target.is_absolute()
	assert target.parent == Path(".")


def test_existing_parent_is_reused(tmp_path):
	parent = tmp_path / "exists"
	parent.mkdir()

	assert prepare_output_path(parent / "report.md") == parent / "report.md"


def test_parent_that_is_a_file_raises_oserror(tmp_path):
	blocker = tmp_path / "blocker"
	blocker.write_text("not a directory", encoding="utf-8")

	with pytest.raises(OSError):
		prepare_output_path(blocker / "report.md")
