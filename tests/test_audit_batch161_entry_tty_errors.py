"""启动期错误在 TTY 下的渲染（Issue #2786）。"""

import io
import json
import sys

import pytest
from click.testing import CliRunner
from rich.console import Console

from boss_agent_cli import display
from boss_agent_cli.main import cli


def _tty(monkeypatch: pytest.MonkeyPatch, *, width: int = 180) -> io.StringIO:
	stream = io.StringIO()
	monkeypatch.setattr(display, "console", Console(file=stream, width=width, emoji=False))
	monkeypatch.setattr(display, "is_json_mode", display.json_forced)
	return stream


def test_missing_option_value_renders_in_terminal(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
	stream = _tty(monkeypatch)
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "search", "--city"])
	assert result.exit_code == 1
	assert not result.stdout
	out = stream.getvalue()
	for text in ("error", "INVALID_PARAM", "recovery:", "--city"):
		assert text in out


def test_misplaced_global_option_renders_operator_actions(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
	stream = _tty(monkeypatch)
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "search", "--role", "recruiter", "python"])
	assert result.exit_code == 1
	assert not result.stdout
	out = stream.getvalue()
	for text in ("INVALID_PARAM", "recovery:", "全局选项"):
		assert text in out


def test_explicit_json_in_terminal_keeps_envelope(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
	# TTY 路径下显式 --json 仍是硬契约：不能因为 stdout 是终端就改走 stderr 渲染。
	_tty(monkeypatch)
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "search", "--city"])
	assert result.exit_code == 1
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "INVALID_PARAM"


def test_console_script_none_args_respects_explicit_json(
	monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
	# 真实 console script 调用 cli.main(args=None)：--json 只能从 sys.argv 预扫描得到。
	_tty(monkeypatch)
	monkeypatch.setattr(sys, "argv", ["boss", "--data-dir", str(tmp_path), "--json", "search", "--city"])
	with pytest.raises(SystemExit) as exc:
		cli.main(args=None, prog_name="boss")
	assert exc.value.code == 1
	payload = json.loads(capsys.readouterr().out)
	assert payload["error"]["code"] == "INVALID_PARAM"


def test_json_envelope_unchanged(tmp_path) -> None:
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "search", "--city"])
	assert result.exit_code == 1
	payload = json.loads(result.stdout)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert payload["error"]["recoverable"] is False
