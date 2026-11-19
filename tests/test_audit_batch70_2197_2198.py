"""导出路径可写性预检与清洗函数非字符串兜底（Issue #2197 / #2198）。
"""

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.services.chat_utils import escape_md_cell, sanitize_csv_cell
from boss_agent_cli.services.output_path import OutputPathProblem, inspect_output_path


def _readonly_dir_or_skip(path: Path) -> Path:
	if os.name == "nt":
		pytest.skip("Windows 的目录权限模型不同")
	if hasattr(os, "geteuid") and os.geteuid() == 0:
		pytest.skip("root 不受目录写权限位限制")
	path.mkdir()
	os.chmod(path, 0o555)
	return path


# ── Issue #2197：不可写祖先目录在平台请求之前暴露 ─────────────────────


def test_inspect_output_path_reports_unwritable_ancestor(tmp_path):
	readonly = _readonly_dir_or_skip(tmp_path / "ro")
	try:
		_, problem = inspect_output_path(readonly / "new" / "out.md")
	finally:
		os.chmod(readonly, 0o755)

	assert problem is OutputPathProblem.NOT_WRITABLE


def test_inspect_output_path_accepts_writable_directory(tmp_path):
	_, problem = inspect_output_path(tmp_path / "new" / "out.md")

	assert problem is None


def test_chat_export_preflight_emits_local_io_without_platform_request(tmp_path, monkeypatch):
	"""只读父目录要在任何平台请求之前落 LOCAL_IO_ERROR。"""
	readonly = _readonly_dir_or_skip(tmp_path / "ro")

	def explode(*args, **kwargs):
		raise AssertionError("预检失败不应构造平台客户端")

	monkeypatch.setattr("boss_agent_cli.commands.chat.get_platform_instance", explode)
	try:
		result = CliRunner().invoke(
			cli,
			["--json", "--data-dir", str(tmp_path), "chat", "--export", "md", "-o", str(readonly / "new" / "out.md")],
		)
	finally:
		os.chmod(readonly, 0o755)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "LOCAL_IO_ERROR"
	assert "不可写" in payload["error"]["message"]


# ── Issue #2198：非字符串兜底走同一套清洗链 ────────────────────────────


def test_sanitize_csv_cell_prefixes_drifted_negative_integer():
	assert sanitize_csv_cell(-1) == "'-1"


def test_sanitize_csv_cell_huge_integer_degrades_to_empty():
	assert sanitize_csv_cell(10**4301) == ""


def test_escape_md_cell_huge_integer_degrades_to_empty():
	assert escape_md_cell(10**4301) == ""


def test_escape_md_cell_escapes_container_text():
	escaped = escape_md_cell(["a|b"])

	assert "\\|" in escaped


def test_sanitize_csv_cell_keeps_existing_contract():
	assert sanitize_csv_cell("-") == "-"
	assert sanitize_csv_cell("=cmd") == "'=cmd"
	assert sanitize_csv_cell("a\tb") == "a b"
