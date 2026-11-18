"""批次二十九回归测试：Issue #1672。

`hr download-resume` 的 `--output` 空值与不支持的扩展名必须在登录与平台
请求之前拒绝；全部测试离线运行，平台与登录态都是 mock。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli

_MODULE = "boss_agent_cli.commands.recruiter.download_resume"


def _invoke(tmp_path: Path, output: str):
	return CliRunner().invoke(
		cli,
		[
			"--data-dir", str(tmp_path), "--json",
			"hr", "download-resume", "123", "--message-id", "456", "--output", output,
		],
	)


def _error(result) -> dict:
	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert payload["ok"] is False
	return payload["error"]


@pytest.mark.parametrize("output", ["", "   "])
def test_blank_output_rejected_before_auth_and_platform(tmp_path: Path, output: str) -> None:
	with (
		patch(f"{_MODULE}.AuthManager") as auth_cls,
		patch(f"{_MODULE}.get_recruiter_platform_instance") as factory,
	):
		result = _invoke(tmp_path, output)

	error = _error(result)
	assert error["code"] == "INVALID_PARAM"
	assert error["recoverable"] is False
	assert "--output" in error["message"]
	auth_cls.assert_not_called()
	factory.assert_not_called()


@pytest.mark.parametrize(
	"output",
	["resume", "resume.txt", "resume.pdf.exe", "resume."],
)
def test_unsupported_suffix_rejected_before_platform_requests(tmp_path: Path, output: str) -> None:
	with (
		patch(f"{_MODULE}.AuthManager") as auth_cls,
		patch(f"{_MODULE}.get_recruiter_platform_instance") as factory,
	):
		result = _invoke(tmp_path, str(tmp_path / output))

	error = _error(result)
	assert error["code"] == "INVALID_PARAM"
	assert error["recoverable"] is False
	assert "扩展名" in error["message"]
	assert ".pdf" in error["message"]
	auth_cls.assert_not_called()
	factory.assert_not_called()


@pytest.mark.parametrize("name", ["resume.pdf", "resume.PDF", "resume.doc", "resume.docx", "resume.png", "resume.jpg", "resume.jpeg"])
def test_supported_suffix_still_reaches_platform(tmp_path: Path, name: str) -> None:
	target = tmp_path / name
	with (
		patch(f"{_MODULE}.AuthManager"),
		patch(f"{_MODULE}.get_recruiter_platform_instance") as factory,
	):
		platform = factory.return_value.__enter__.return_value
		platform.is_success.return_value = True
		platform.unwrap_data.side_effect = lambda response: response["zpData"]
		platform.download_resume_by_friend.return_value = {
			"code": 0,
			"zpData": {"path": str(target), "bytes": 100, "format": "pdf"},
		}
		result = _invoke(tmp_path, str(target))

	assert result.exit_code == 0, result.output
	assert json.loads(result.output)["data"]["downloaded"] is True
	platform.download_resume_by_friend.assert_called_once_with(123, 456, target)
