"""审计批次 124：argv 里不可编码的 run_id 按输入错误拒绝（Issue #2499）。

孤立代理项作为 sqlite 参数绑定时抛 ``UnicodeEncodeError``，此前被全局兜成
``LOCAL_IO_ERROR`` 并给出磁盘恢复指引。命令入口应报 ``INVALID_PARAM``；
正常 run_id 的「任务不存在」分类保持不变。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.services.text_files import is_utf8_encodable

_BAD_RUN_ID = "\ud800"


def _invoke(tmp_path: Path, *args: str):
	return CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), *args])


def _error(result) -> dict:
	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["ok"] is False
	return payload["error"]


def test_is_utf8_encodable() -> None:
	assert is_utf8_encodable("run-1") is True
	assert is_utf8_encodable("北京") is True
	assert is_utf8_encodable(_BAD_RUN_ID) is False


@pytest.mark.parametrize(
	"command",
	[
		("crawl", "resume"),
		("crawl", "status"),
		("crawl", "stop"),
		("crawl", "results"),
		("crawl", "shortlist"),
	],
	ids=["resume", "status", "stop", "results", "shortlist"],
)
def test_crawl_rejects_unencodable_run_id(tmp_path: Path, command: tuple[str, ...]) -> None:
	error = _error(_invoke(tmp_path, *command, _BAD_RUN_ID))

	assert error["code"] == "INVALID_PARAM"
	assert "run_id" in error["message"]
	assert "UTF-8" in error["message"]
	# 恢复指引不能再指向磁盘 / 权限。
	assert "磁盘" not in error["recovery_action"]


@pytest.mark.parametrize("flag", ["--status", "--resume", "--stop"])
def test_wizard_rejects_unencodable_run_id(tmp_path: Path, flag: str) -> None:
	error = _error(_invoke(tmp_path, "wizard", flag, _BAD_RUN_ID))

	assert error["code"] == "INVALID_PARAM"
	assert "run_id" in error["message"]
	assert "UTF-8" in error["message"]
	assert "磁盘" not in error["recovery_action"]


def test_normal_run_id_keeps_existing_classification(tmp_path: Path) -> None:
	crawl_error = _error(_invoke(tmp_path, "crawl", "status", "run-missing"))
	assert crawl_error["code"] == "CRAWL_RUN_NOT_FOUND"

	wizard_error = _error(_invoke(tmp_path, "wizard", "--status", "run-missing"))
	assert wizard_error["code"] == "WORKFLOW_NOT_FOUND"


def test_blank_run_id_keeps_existing_message(tmp_path: Path) -> None:
	error = _error(_invoke(tmp_path, "crawl", "status", "  "))
	assert error["code"] == "INVALID_PARAM"
	assert "不能为空" in error["message"]
