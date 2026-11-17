"""Issue #1501–#1504：命令层输出路径与本地错误语义。

覆盖：
- chat / export 的显式空 ``-o ""`` 返回 ``INVALID_PARAM``，不写默认文件、
  不向 stdout 输出裸 HTML（Issue #1501）；
- ``chat -o`` 没配 ``--export`` 时不再静默忽略（Issue #1502）；
- ``hr download-resume`` 的本地写盘 OSError 按 ``LOCAL_IO_ERROR`` 上报（Issue #1503）；
- 联系人缺少 securityId 时按不可重试错误上报并给出刷新命令（Issue #1504）。
"""

import errno
import json
import shlex
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.display import error_contract_for_code
from boss_agent_cli.main import cli


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.name = "zhipin"
	instance.unwrap_data.side_effect = lambda response: (
		response.get("zpData") if "zpData" in response else response.get("data")
	)
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	return instance


def _run(data_dir: Path, *args: str):
	return CliRunner().invoke(cli, ["--data-dir", str(data_dir), "--json", *args])


def _error_payload(result) -> dict:
	assert result.exit_code == 1, result.output
	return json.loads(result.output)


# ── Issue #1501：显式空 -o ────────────────────────────────────────────


@pytest.mark.parametrize("empty", ["", "   "])
@patch("boss_agent_cli.commands.chat.get_platform_instance")
@patch("boss_agent_cli.commands.chat.AuthManager")
def test_chat_empty_output_is_invalid_param_without_default_file(mock_auth_cls, mock_platform_cls, empty, tmp_path):
	"""``chat --export md -o ""`` 不能静默写默认导出文件。"""
	result = _run(tmp_path, "chat", "--export", "md", "-o", empty)

	payload = _error_payload(result)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "-o" in payload["error"]["message"]
	assert "不能为空" in payload["error"]["message"]
	assert "默认" in payload["error"]["message"]
	mock_platform_cls.assert_not_called()
	# 默认导出目录与快照目录都不应出现：错误发生在任何平台请求与本地写入之前。
	assert not (tmp_path / "chat-export").exists()
	assert not (tmp_path / "chat-history").exists()


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_html_empty_output_is_invalid_param_without_raw_html(
	mock_auth_cls, mock_platform_cls, tmp_path, monkeypatch,
):
	"""``export --format html -o ""`` 不能向 stdout 直出 HTML。"""
	cwd = tmp_path / "cwd"
	cwd.mkdir()
	monkeypatch.chdir(cwd)

	result = _run(tmp_path, "export", "python", "--format", "html", "-o", "")

	payload = _error_payload(result)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "-o" in payload["error"]["message"]
	assert "不能为空" in payload["error"]["message"]
	# stdout 只有一个 JSON 信封，不能混入裸 HTML 文档。
	assert "<html" not in result.output.lower()
	assert "<!doctype" not in result.output.lower()
	mock_platform_cls.assert_not_called()
	assert list(cwd.iterdir()) == []


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_csv_empty_output_is_invalid_param_without_writing(
	mock_auth_cls, mock_platform_cls, tmp_path, monkeypatch,
):
	"""``export --format csv -o ""`` 不能静默返回「没写文件」的成功信封。"""
	cwd = tmp_path / "cwd"
	cwd.mkdir()
	monkeypatch.chdir(cwd)

	result = _run(tmp_path, "export", "python", "--format", "csv", "-o", "")

	payload = _error_payload(result)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "-o" in payload["error"]["message"]
	assert "不能为空" in payload["error"]["message"]
	mock_platform_cls.assert_not_called()
	assert list(cwd.iterdir()) == []


# ── Issue #1502：chat -o 没配 --export ───────────────────────────────


@patch("boss_agent_cli.commands.chat.get_platform_instance")
@patch("boss_agent_cli.commands.chat.AuthManager")
def test_chat_output_without_export_is_invalid_param(mock_auth_cls, mock_platform_cls, tmp_path):
	target = tmp_path / "x.md"
	result = _run(tmp_path, "chat", "-o", str(target))

	payload = _error_payload(result)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "--export" in payload["error"]["message"]
	assert payload["hints"]["next_actions"]
	mock_platform_cls.assert_not_called()
	assert not target.exists()


# ── Issue #1503：download-resume 本地写盘错误 ────────────────────────


@patch("boss_agent_cli.commands.recruiter.download_resume.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.download_resume.AuthManager")
def test_download_resume_disk_full_maps_to_local_io_error(mock_auth_cls, mock_platform_cls, tmp_path):
	"""ENOSPC 是本地写入失败，不能报成可修正参数的 INVALID_PARAM。"""
	platform = _ctx_mock(mock_platform_cls)
	platform.download_resume_by_friend.side_effect = OSError(
		errno.ENOSPC, "No space left on device"
	)

	result = _run(
		tmp_path, "hr", "download-resume", "123",
		"--message-id", "456", "--output", str(tmp_path / "resume.pdf"),
	)

	payload = _error_payload(result)
	error = payload["error"]
	recoverable, recovery_action = error_contract_for_code("LOCAL_IO_ERROR")
	assert error["code"] == "LOCAL_IO_ERROR"
	assert error["recoverable"] is recoverable is False
	assert error["recovery_action"] == recovery_action
	assert "磁盘空间" in error["message"]
	assert "磁盘空间" in error["recovery_action"]
	assert "权限" in error["recovery_action"]


@patch("boss_agent_cli.commands.recruiter.download_resume.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.download_resume.AuthManager")
def test_download_resume_existing_file_stays_invalid_param(mock_auth_cls, mock_platform_cls, tmp_path):
	"""不覆盖已有文件仍是参数问题，不能被归进本地 IO 错误。"""
	platform = _ctx_mock(mock_platform_cls)
	platform.download_resume_by_friend.side_effect = FileExistsError(
		errno.EEXIST, "File exists"
	)

	result = _run(
		tmp_path, "hr", "download-resume", "123",
		"--message-id", "456", "--output", str(tmp_path / "resume.pdf"),
	)

	payload = _error_payload(result)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert payload["error"]["recoverable"] is False


# ── Issue #1504：联系人缺少 securityId ──────────────────────────────


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_contact_missing_security_id_is_not_retryable_with_refresh_hint(
	mock_auth_cls, mock_platform_cls, tmp_path,
):
	"""数据缺失是确定性结果：不可重试，并给出同一数据目录的刷新命令。"""
	platform = _ctx_mock(mock_platform_cls)
	item = {"uid": 10001, "name": "联系人甲"}
	platform.friend_list.return_value = {"zpData": {"result": [item], "friendList": [item]}}

	result = _run(tmp_path, "chatmsg", "10001")

	payload = _error_payload(result)
	error = payload["error"]
	assert error["code"] == "NETWORK_ERROR"
	assert error["recoverable"] is False
	next_actions = payload["hints"]["next_actions"]
	assert len(next_actions) == 1
	assert shlex.split(next_actions[0]) == [
		"boss", "--data-dir", str(tmp_path.resolve()), "chat",
	]
	assert next_actions[0] in error["recovery_action"]
	assert "security_id" in error["recovery_action"]
	platform.chat_history.assert_not_called()
