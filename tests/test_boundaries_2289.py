"""Issue #2289：`~未知用户` 不能按字面量落盘，也不能被说成「文件不存在」。

`os.path.expanduser` 在系统里查不到 `~某用户` 时原样返回字面量：`-o` 会随后在
当前目录建出一个名叫 `~某用户` 的目录并把产物写进去，`resume import` 会把写错的
家目录报成文件缺失。`Path.expanduser` 在同一情形抛 `RuntimeError`，与
`--data-dir` / `request_budget_dir` 的口径一致。
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.commands._output_path import validate_output_path_or_emit
from boss_agent_cli.main import cli
from boss_agent_cli.services.output_path import (
	OutputPathExpansionError,
	OutputPathProblem,
	inspect_output_path,
	prepare_output_path,
)

UNKNOWN = "~no-such-user-boss-agent-cli-2289"


def _unknown_user_raises() -> bool:
	"""平台不支持 `~user` 展开时（例如部分 Windows 运行环境）跳过这些用例。"""
	try:
		Path(f"{UNKNOWN}/probe").expanduser()
	except RuntimeError:
		return True
	return False


pytestmark = pytest.mark.skipif(
	not _unknown_user_raises(), reason="当前平台无法用 RuntimeError 判定未知用户"
)


def test_inspect_flags_unknown_user_without_touching_disk(tmp_path, monkeypatch):
	monkeypatch.chdir(tmp_path)

	target, problem = inspect_output_path(f"{UNKNOWN}/report.html")

	assert problem is OutputPathProblem.UNEXPANDABLE_USER
	assert str(target) == f"{UNKNOWN}/report.html"
	assert list(tmp_path.iterdir()) == []


def test_prepare_refuses_unknown_user_before_creating_directories(tmp_path, monkeypatch):
	monkeypatch.chdir(tmp_path)

	with pytest.raises(OutputPathExpansionError) as exc_info:
		prepare_output_path(f"{UNKNOWN}/report.html")

	assert "绝对路径" in str(exc_info.value)
	assert list(tmp_path.iterdir()) == []


def test_validate_emits_invalid_param_and_creates_nothing(tmp_path, monkeypatch):
	monkeypatch.chdir(tmp_path)
	payloads = []

	class _Ctx:
		def exit(self, code=0):
			raise SystemExit(code)

	with patch("boss_agent_cli.display.handle_error_output", side_effect=lambda ctx, *a, **k: payloads.append(k)):
		result = validate_output_path_or_emit(_Ctx(), "stats", f"{UNKNOWN}/report.html")

	assert result is None
	assert payloads[0]["code"] == "INVALID_PARAM"
	assert "查不到的用户" in payloads[0]["message"]
	assert list(tmp_path.iterdir()) == []


def test_stats_rejects_unknown_user_output(tmp_path, monkeypatch):
	monkeypatch.chdir(tmp_path)
	result = CliRunner().invoke(
		cli,
		["--data-dir", str(tmp_path / "data"), "stats", "--format", "html", "-o", f"{UNKNOWN}/report.html"],
	)
	envelope = json.loads(result.output)
	assert result.exit_code == 1
	assert envelope["ok"] is False
	assert envelope["error"]["code"] == "INVALID_PARAM"
	assert not Path(UNKNOWN).exists()


def test_resume_import_reports_expansion_failure_not_missing_file(tmp_path):
	result = CliRunner().invoke(
		cli,
		["--data-dir", str(tmp_path / "data"), "resume", "import", f"{UNKNOWN}/cv.json"],
	)
	envelope = json.loads(result.output)

	assert result.exit_code == 1
	assert envelope["error"]["code"] == "INVALID_PARAM"
	assert "家目录写法无法展开" in envelope["error"]["message"]
	assert "不存在" not in envelope["error"]["message"]


def test_current_user_home_still_expands(tmp_path, monkeypatch):
	monkeypatch.setenv("HOME", str(tmp_path))

	target, problem = inspect_output_path("~/report.html")

	assert problem is None
	assert target == tmp_path / "report.html"


def test_plain_relative_paths_are_untouched(tmp_path, monkeypatch):
	monkeypatch.chdir(tmp_path)

	target, problem = inspect_output_path("report.html")

	assert problem is None
	assert target == Path("report.html")


def _wizard_context(tmp_path):
	class _Context:
		data_dir = tmp_path / "data"
		# 真实上下文才会用到这些，路径校验分支不会
		data_dir_config = None

	return _Context()


def test_wizard_export_maps_unknown_user_to_invalid_param(tmp_path):
	from boss_agent_cli.wizard.actions import WorkflowActionError, _export_output_path

	with pytest.raises(WorkflowActionError) as exc_info:
		_export_output_path(_wizard_context(tmp_path), {"output": f"{UNKNOWN}/jobs.json"}, "json")

	assert exc_info.value.code == "INVALID_PARAM"
	assert "查不到的用户" in str(exc_info.value)


def test_wizard_export_maps_unwritable_parent_to_local_io_error(tmp_path):
	from boss_agent_cli.wizard.actions import WorkflowActionError, _export_output_path

	# NOT_WRITABLE 原来不在契约表里：查表 KeyError 会被兜成 INTERNAL_ERROR。
	with patch(
		"boss_agent_cli.wizard.actions.inspect_output_path",
		return_value=(tmp_path / "jobs.json", OutputPathProblem.NOT_WRITABLE),
	):
		with pytest.raises(WorkflowActionError) as exc_info:
			_export_output_path(_wizard_context(tmp_path), {"output": str(tmp_path / "jobs.json")}, "json")

	assert exc_info.value.code == "LOCAL_IO_ERROR"
	assert "不可写" in str(exc_info.value)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 家目录语义")
def test_known_user_still_works():
	import getpass

	target, problem = inspect_output_path(f"~{getpass.getuser()}/report.html")

	assert problem is None
	assert not str(target).startswith("~")
	assert os.path.isabs(str(target))
