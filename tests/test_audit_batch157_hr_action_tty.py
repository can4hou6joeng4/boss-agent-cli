"""招聘者写操作命令的终端确认渲染（Issue #2774 第二批）。"""

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner, Result
from rich.console import Console

from boss_agent_cli import display
from boss_agent_cli.main import cli


def _tty(monkeypatch: pytest.MonkeyPatch, *, width: int = 110) -> io.StringIO:
	stream = io.StringIO()
	monkeypatch.setattr(display, "console", Console(file=stream, width=width, emoji=False))
	monkeypatch.setattr(display, "is_json_mode", display.json_forced)
	return stream


def _invoke(data_dir: Path, *args: str) -> Result:
	return CliRunner().invoke(cli, ["--role", "recruiter", "--data-dir", str(data_dir), *args])


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: response.get("zpData") if "zpData" in response else response.get("data")
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	return instance


@patch("boss_agent_cli.commands.recruiter.reply.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.reply.AuthManager")
def test_hr_reply_renders_acknowledgement(
	mock_auth_cls, mock_platform_cls, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	stream = _tty(monkeypatch)
	platform = _ctx_mock(mock_platform_cls)
	platform.send_message_by_friend.return_value = {"code": 0, "zpData": {"friendId": 123}}
	result = _invoke(tmp_path, "hr", "reply", "123", "您好，方便约面吗")
	assert result.exit_code == 0, result.output or repr(result.exception)
	assert not result.stdout
	out = stream.getvalue()
	assert "hr reply" in out
	assert "friend_id: 123" in out
	assert "sent: True" in out
	assert "hr chat" in out
	# 正文不进信封，也不进终端。
	assert "方便约面" not in out
	assert "方便约面" not in json.loads(_invoke(tmp_path, "--json", "hr", "reply", "123", "您好，方便约面吗").stdout)["data"].values()


@patch("boss_agent_cli.commands.recruiter.request_resume.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.request_resume.AuthManager")
def test_hr_request_resume_renders_acknowledgement(
	mock_auth_cls, mock_platform_cls, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	stream = _tty(monkeypatch)
	platform = _ctx_mock(mock_platform_cls)
	platform.exchange_request_by_friend.return_value = {"code": 0, "zpData": {}}
	result = _invoke(tmp_path, "hr", "request-resume", "123")
	assert result.exit_code == 0, result.output or repr(result.exception)
	assert not result.stdout
	out = stream.getvalue()
	assert "hr request-resume" in out
	assert "requested: True" in out
	assert "hr resume" in out


def test_hr_accept_resume_dry_run_renders_preview(
	monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	stream = _tty(monkeypatch)
	result = _invoke(tmp_path, "hr", "accept-resume", "5", "--message-id", "9", "--dry-run")
	assert result.exit_code == 0, result.output or repr(result.exception)
	assert not result.stdout
	out = stream.getvalue()
	assert "hr accept-resume" in out
	assert "dry_run: True" in out
	assert "明确批准" in out


def test_hr_location_dry_run_renders_preview(
	monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	stream = _tty(monkeypatch)
	result = _invoke(tmp_path, "hr", "location", "5", "--dry-run")
	assert result.exit_code == 0, result.output or repr(result.exception)
	assert not result.stdout
	out = stream.getvalue()
	assert "hr location" in out
	assert "dry_run: True" in out
	assert "securityId" in out


def test_hr_greet_dry_run_renders_preview(
	monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	stream = _tty(monkeypatch)
	result = _invoke(
		tmp_path, "hr", "greet",
		"--geek-id", "geek", "--job-id", "job", "--expect-id", "expect",
		"--lid", "lid", "--security-id", "security", "--message", "您好", "--dry-run",
	)
	assert result.exit_code == 0, result.output or repr(result.exception)
	assert not result.stdout
	out = stream.getvalue()
	assert "hr greet" in out
	assert "dry_run: True" in out
	assert "核对候选人和话术" in out


@patch("boss_agent_cli.commands.recruiter.download_resume.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.download_resume.AuthManager")
def test_hr_download_resume_renders_saved_path(
	mock_auth_cls, mock_platform_cls, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	stream = _tty(monkeypatch, width=160)
	platform = _ctx_mock(mock_platform_cls)
	platform.download_resume_by_friend.return_value = {"code": 0, "zpData": {"file_name": "demo.pdf"}}
	target = tmp_path / "demo.pdf"
	result = _invoke(
		tmp_path, "hr", "download-resume", "123", "--message-id", "456", "--output", str(target),
	)
	assert result.exit_code == 0, result.output or repr(result.exception)
	assert not result.stdout
	out = stream.getvalue()
	assert "hr download-resume" in out
	assert "downloaded: True" in out
	assert str(target) in out
	assert "hr chatmsg" in out
