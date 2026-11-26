"""超长 / 空简历名在 ai、agent 与向导入口按参数错误拒绝（Issue #2969）。

#2959 只覆盖了 ``resume`` 命令组；其余入口仍直接访问路径，``ENAMETOOLONG``
被兜成 ``RESUME_FILE_INVALID``、消息带出本机绝对路径，指引里的 ``resume
delete <超长名>`` 自己也会被拒。三个入口必须与 ``resume`` 共享同一判定。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.commands import ai_cmd
from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.runner import WorkflowActionError

# 300 字节 ASCII：加上 .json 后超过 POSIX / Windows 的文件名上限。
_OVERLONG_NAME = "a" * 300
# 250 字节 + ".json" = 255，仍在常见 NAME_MAX 之内。
_MAX_LENGTH_NAME = "a" * 250


def _invoke(data_dir: Path, *args: str):
	return CliRunner().invoke(cli, ["--data-dir", str(data_dir), "--json", *args])


def _assert_overlong_rejected(payload: dict, data_dir: Path) -> None:
	error = payload["error"]
	assert error["code"] == "INVALID_PARAM"
	assert "过长" in error["message"]
	# ENAMETOOLONG 的系统错误文本会带出本机绝对路径，参数错误不能复制它。
	assert str(data_dir) not in error["message"]
	assert "resume delete" not in " ".join(payload["hints"].get("next_actions") or [])


def test_ai_polish_rejects_overlong_resume_name(tmp_path: Path, monkeypatch) -> None:
	monkeypatch.setattr(ai_cmd, "_require_ai_service", lambda ctx: object())

	result = _invoke(tmp_path, "ai", "polish", _OVERLONG_NAME)

	assert result.exit_code == 1, result.output
	_assert_overlong_rejected(json.loads(result.stdout), tmp_path)


def test_agent_crawl_rejects_overlong_resume_name(tmp_path: Path) -> None:
	result = _invoke(tmp_path, "agent", "crawl", "--run-id", "nope", "--resume", _OVERLONG_NAME)

	assert result.exit_code == 1, result.output
	_assert_overlong_rejected(json.loads(result.stdout), tmp_path)


def test_wizard_ai_assist_rejects_overlong_resume_name(tmp_path: Path) -> None:
	context = ActionContext(data_dir=tmp_path, platform="zhipin", role="candidate", logger=Logger())

	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["ai_assist"](context, {"resume": _OVERLONG_NAME, "prompt": "优化"}, {})

	assert exc_info.value.code == "INVALID_PARAM"
	assert "过长" in str(exc_info.value)
	assert str(tmp_path) not in str(exc_info.value)
	assert "resume delete" not in exc_info.value.recovery_action


def test_ai_blank_resume_name_stays_invalid_param(tmp_path: Path, monkeypatch) -> None:
	monkeypatch.setattr(ai_cmd, "_require_ai_service", lambda ctx: object())

	result = _invoke(tmp_path, "ai", "polish", "   ")

	assert result.exit_code == 1, result.output
	error = json.loads(result.stdout)["error"]
	assert error["code"] == "INVALID_PARAM"
	assert "不能为空" in error["message"]


def test_max_length_name_is_not_rejected_as_invalid_param(tmp_path: Path, monkeypatch) -> None:
	# 250 字符 + ".json" 仍在 NAME_MAX 内：长度校验放行，随后按「不存在」继续。
	monkeypatch.setattr(ai_cmd, "_require_ai_service", lambda ctx: object())

	result = _invoke(tmp_path, "ai", "polish", _MAX_LENGTH_NAME)

	assert result.exit_code == 1, result.output
	assert json.loads(result.stdout)["error"]["code"] == "RESUME_NOT_FOUND"
