"""批次二十七回归测试：Issue #1662–#1664。

全部测试离线运行；不调用平台 API、浏览器或真实网络。
"""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from boss_agent_cli.display import handle_output
from boss_agent_cli.main import cli
from boss_agent_cli.output import envelope_error, redact_sensitive_text


def test_cjk_adjacent_credential_keys_are_redacted() -> None:
	for text in ("登录token=secret123", "刷新stoken=abcdef", "访问api_key=abc", "登录Bearer abc"):
		assert "[REDACTED]" in redact_sensitive_text(text)
		assert "secret123" not in redact_sensitive_text(text)
		assert "abcdef" not in redact_sensitive_text(text)


def test_all_authorization_schemes_redact_value() -> None:
	for scheme, value in (
		("Basic", "dXNlcjpwYXNz"),
		("Token", "abc123"),
		("Digest", "deadbeef"),
		("Bearer", "eyJ.payload.sig"),
	):
		redacted = redact_sensitive_text(f"Authorization: {scheme} {value}")
		assert redacted == "Authorization: [REDACTED]"


def test_envelope_error_redacts_cjk_adjacent_secret() -> None:
	envelope = json.loads(
		envelope_error("test", code="INVALID_PARAM", message="刷新token=secret123", recoverable=False, recovery_action="停止")
	)
	assert "secret123" not in envelope["error"]["message"]
	assert "[REDACTED]" in envelope["error"]["message"]


def test_tty_operator_actions_are_redacted(monkeypatch) -> None:
	import io

	from rich.console import Console

	stream = io.StringIO()
	console = Console(file=stream, force_terminal=False, width=100)
	monkeypatch.setattr("boss_agent_cli.display.console", console)
	ctx = MagicMock()
	ctx.obj = {"json_output": False}
	with patch.object(sys, "stdout") as stdout:
		stdout.isatty.return_value = True
		handle_output(ctx, "test", {"ok": True}, render=lambda _data: None, hints={"operator_actions": ["token=abc123"]})
	assert "abc123" not in stream.getvalue()
	assert "[REDACTED]" in stream.getvalue()


def test_cli_error_envelope_redacts_cjk_adjacent_secret(tmp_path) -> None:
	result = CliRunner().invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "config", "set", "api_key", "登录token=secret123"],
	)
	# 命令成功与否不重要：只验证出口没有把中文紧邻的凭据明文带进信封。
	assert "secret123" not in result.output
