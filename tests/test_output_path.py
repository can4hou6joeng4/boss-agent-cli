"""`-o` 输出路径语义：展开 `~` 并创建父目录。

MCP 通过子进程调用 CLI（`mcp_server._run_boss`），参数不经 shell，`~` 不会被
shell 展开。命令里不处理就会在当前目录建出一个字面量 `~` 目录。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from boss_agent_cli.services.output_path import OutputPathProblem, inspect_output_path, prepare_output_path


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


# ── inspect_output_path：无副作用的前置检查 ────────────────────────────


@pytest.mark.parametrize(
	"make, expected",
	[
		(lambda p: p, OutputPathProblem.IS_DIRECTORY),
		# 已有文件本身不是目录：目标是那个文件时无问题，是它的子路径才是父路径问题。
		(lambda p: p / "blocker" / "x.md", OutputPathProblem.PARENT_NOT_DIR),
		(lambda p: p / "blocker" / "nested" / "x.md", OutputPathProblem.PARENT_NOT_DIR),
	],
)
def test_inspect_output_path_reports_problems(make, expected, tmp_path):
	"""只回答问题、不建目录：`-o` 的问题必须在平台请求之前暴露。"""
	blocker = tmp_path / "blocker"
	blocker.write_text("not a directory", encoding="utf-8")

	_, problem = inspect_output_path(make(tmp_path))

	assert problem == expected


@pytest.mark.parametrize("suffix", ["report.md", "nested/report.md"])
@pytest.mark.parametrize("destination_kind", ["file", "directory", "missing"])
def test_inspect_output_path_checks_symlink_ancestors(tmp_path, suffix, destination_kind):
	"""目录链接可复用，文件链接或悬空链接不能充当任意一级父目录。"""
	destination = tmp_path / "destination"
	if destination_kind == "file":
		destination.write_text("not a directory", encoding="utf-8")
	elif destination_kind == "directory":
		destination.mkdir()
	link = tmp_path / "link"
	try:
		link.symlink_to(destination, target_is_directory=destination_kind == "directory")
	except (OSError, NotImplementedError):
		pytest.skip("当前环境不支持创建符号链接")

	target, problem = inspect_output_path(link / suffix)

	assert target == link / suffix
	assert problem == (None if destination_kind == "directory" else OutputPathProblem.PARENT_NOT_DIR)
	assert not (destination / "nested").exists()


def test_inspect_output_path_accepts_existing_file_target(tmp_path):
	"""覆盖已有普通文件是允许的（调用方自行决定是否覆盖）。"""
	existing = tmp_path / "report.md"
	existing.write_text("old", encoding="utf-8")

	_, problem = inspect_output_path(existing)

	assert problem is None


def test_inspect_output_path_does_not_create_directories(tmp_path):
	"""(tmp_path)/deep/nested/x.md 的父目录在检查后仍不存在。"""
	target_path = tmp_path / "deep" / "nested" / "x.md"

	target, problem = inspect_output_path(target_path)

	assert problem is None
	assert target == target_path
	assert not (tmp_path / "deep").exists()


def test_inspect_output_path_accepts_writable_target(tmp_path):
	target, problem = inspect_output_path(tmp_path / "report.md")

	assert problem is None
	assert target == tmp_path / "report.md"
