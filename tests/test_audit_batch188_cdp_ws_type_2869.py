"""Issue #2869: CDP WebSocket 端点类型/形状校验。"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.auth.browser import probe_cdp
from boss_agent_cli.main import cli


def _json_response(payload: Any) -> MagicMock:
	response = MagicMock()
	response.json.return_value = payload
	return response


@pytest.mark.parametrize("payload", [
	{"webSocketDebuggerUrl": {"url": "ws://example.invalid/devtools/browser/x"}},
	{"webSocketDebuggerUrl": True},
	{"webSocketDebuggerUrl": "http://example.invalid/v1"},
	{"webSocketDebuggerUrl": "devtools/browser/x"},
	{"webSocketDebuggerUrl": "wss://"},
])
def test_probe_cdp_rejects_invalid_ws_url(payload: dict[str, Any]) -> None:
	with patch("httpx.get", return_value=_json_response(payload)):
		assert probe_cdp("http://example.invalid:9222") is None


def test_probe_cdp_accepts_ws_endpoint() -> None:
	url = "wss://example.invalid/devtools/browser/x"
	with patch("httpx.get", return_value=_json_response({"webSocketDebuggerUrl": url})):
		assert probe_cdp("http://example.invalid:9222") == url


def test_doctor_fails_closed_on_non_string_cdp_result(tmp_path: Path, monkeypatch) -> None:
	monkeypatch.setenv("HOME", str(tmp_path))
	monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
	with (
		patch("boss_agent_cli.commands.doctor.AuthManager") as auth,
		patch("boss_agent_cli.commands.doctor.probe_cdp", return_value={"url": "ws://example.invalid/x"}),
		patch("boss_agent_cli.commands.doctor.httpx.get", side_effect=RuntimeError("offline")),
		patch("boss_agent_cli.commands.doctor.extract_cookies", return_value=None),
		patch("boss_agent_cli.commands.doctor.patchright_chromium_revision", return_value="1248"),
	):
		auth.return_value.check_status.return_value = None
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "doctor"])

	assert result.exit_code == 0
	parsed = json.loads(result.output)
	checks = {item["name"]: item for item in parsed["data"]["checks"]}
	assert checks["cdp"]["status"] == "warn"
	assert isinstance(checks["cdp"]["detail"], str)
	assert checks["browser_channel"]["status"] == "warn"
