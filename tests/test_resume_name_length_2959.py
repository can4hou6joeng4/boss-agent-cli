"""简历名称超过文件系统文件名上限时按参数错误拒绝（Issue #2959）。

拼成 ``<name>.json`` 后超过 NAME_MAX 的名称不可能有对应文件。旧实现直接访问
路径，ENAMETOOLONG 被 show 报成「简历损坏」、被 delete / init 报成「本地磁盘
错误」，恢复指引与真实原因无关。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli

# 300 字节 ASCII：加上 .json 后超过 POSIX / Windows 的文件名上限。
_OVERLONG_NAME = "a" * 300
# 250 字节 + ".json" = 255，落在常见 NAME_MAX 之内。
_MAX_LENGTH_NAME = "a" * 250
_OVERLONG_NAME_INPUT = {"name": _OVERLONG_NAME}


def _invoke(data_dir: Path, *args: str):
	return CliRunner().invoke(cli, ["--data-dir", str(data_dir), "--json", *args])


@pytest.mark.parametrize(
	"argv",
	[
		pytest.param(["resume", "show", _OVERLONG_NAME], id="show"),
		pytest.param(["resume", "delete", _OVERLONG_NAME], id="delete"),
		pytest.param(["resume", "init", "--name", _OVERLONG_NAME], id="init"),
		pytest.param(
			["resume", "edit", _OVERLONG_NAME, "--field", "title", "--value", "新标题"],
			id="edit",
		),
		pytest.param(["resume", "clone", _MAX_LENGTH_NAME, _OVERLONG_NAME], id="clone-target"),
		pytest.param(["resume", "clone", _OVERLONG_NAME, _MAX_LENGTH_NAME], id="clone-source"),
	],
)
def test_overlong_resume_names_are_invalid_params(tmp_path: Path, argv: list[str]) -> None:
	result = _invoke(tmp_path, *argv)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "过长" in payload["error"]["message"]
	assert "boss" in payload["error"]["recovery_action"]
	assert list((tmp_path / "resumes").glob("*.json")) == []


def test_max_length_resume_name_still_works(tmp_path: Path) -> None:
	init = _invoke(tmp_path, "resume", "init", "--name", _MAX_LENGTH_NAME)
	assert init.exit_code == 0, init.output
	assert len(list((tmp_path / "resumes").glob("*.json"))) == 1

	show = _invoke(tmp_path, "resume", "show", _MAX_LENGTH_NAME)
	assert show.exit_code == 0, show.output

	delete = _invoke(tmp_path, "resume", "delete", _MAX_LENGTH_NAME)
	assert delete.exit_code == 0, delete.output
	assert list((tmp_path / "resumes").glob("*.json")) == []


def test_import_rejects_overlong_name(tmp_path: Path) -> None:
	source = tmp_path / "import.json"
	source.write_text(json.dumps(_OVERLONG_NAME_INPUT), encoding="utf-8")

	result = _invoke(tmp_path, "resume", "import", str(source))

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "过长" in payload["error"]["message"]
	assert list((tmp_path / "resumes").glob("*.json")) == []


def test_other_name_validation_reasons_are_unchanged() -> None:
	"""长度检查不能顶掉既有的路径 / 设备名拒绝原因。"""
	from boss_agent_cli.resume.store import _unsafe_resume_name_reason

	assert "路径分隔符" in (_unsafe_resume_name_reason("a/b") or "")
	assert "保留设备名" in (_unsafe_resume_name_reason("CON") or "")
	assert _unsafe_resume_name_reason(_MAX_LENGTH_NAME) is None
	assert "过长" in (_unsafe_resume_name_reason(_OVERLONG_NAME) or "")
