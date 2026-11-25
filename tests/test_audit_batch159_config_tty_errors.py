"""config 错误路径在 TTY 下的渲染（Issue #2781）。"""

import io
import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console

from boss_agent_cli import display
from boss_agent_cli.main import cli


def _tty(monkeypatch: pytest.MonkeyPatch, *, width: int = 200) -> io.StringIO:
	stream = io.StringIO()
	monkeypatch.setattr(display, "console", Console(file=stream, width=width, emoji=False))
	monkeypatch.setattr(display, "is_json_mode", display.json_forced)
	return stream


def _invoke(data_dir: Path, *args: str):
	return CliRunner().invoke(cli, ["--data-dir", str(data_dir), *args])


def test_config_unknown_key_renders_error_in_terminal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
	stream = _tty(monkeypatch)
	result = _invoke(tmp_path, "config", "get", "bad_key")
	assert result.exit_code == 1
	assert not result.stdout
	out = stream.getvalue()
	for text in ("error", "INVALID_PARAM", "未知配置项", "recovery:", "可用项"):
		assert text in out


def test_config_invalid_value_renders_recovery_in_terminal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
	stream = _tty(monkeypatch)
	result = _invoke(tmp_path, "config", "set", "log_level", "bogus")
	assert result.exit_code == 1
	assert not result.stdout
	out = stream.getvalue()
	for text in ("error", "INVALID_PARAM", "log_level 必须是以下值之一", "recovery:", "config list"):
		assert text in out


def test_config_write_failure_renders_error_in_terminal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
	stream = _tty(monkeypatch)

	def fail_replace(source, destination):
		raise OSError("disk full")

	monkeypatch.setattr(os, "replace", fail_replace)
	result = _invoke(tmp_path, "config", "set", "log_level", "debug")
	assert result.exit_code == 1
	assert not result.stdout
	out = stream.getvalue()
	for text in ("error", "LOCAL_IO_ERROR", "写入", "config.json", "不要自动重试"):
		assert text in out


def test_config_errors_keep_json_envelope(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
	monkeypatch.setattr(display, "is_json_mode", display.json_forced)
	unknown = json.loads(_invoke(tmp_path, "--json", "config", "get", "bad_key").stdout)
	assert unknown["error"]["code"] == "INVALID_PARAM"
	assert "可用项" in unknown["error"]["message"]
	assert unknown["hints"]["next_actions"][0].endswith("config list")

	def fail_replace(source, destination):
		raise OSError("disk full")

	monkeypatch.setattr(os, "replace", fail_replace)
	write_failure = json.loads(_invoke(tmp_path, "--json", "config", "set", "log_level", "debug").stdout)
	assert write_failure["error"]["code"] == "LOCAL_IO_ERROR"
	assert write_failure["error"]["recoverable"] is False
	assert "不要自动重试" in write_failure["error"]["recovery_action"]
