"""CLI contract invariants for Agent-facing output."""

from __future__ import annotations

import json
from typing import Any

import pytest
from click.testing import CliRunner, Result

from boss_agent_cli.main import cli


ENVELOPE_KEYS = {
	"ok",
	"schema_version",
	"command",
	"data",
	"pagination",
	"error",
	"hints",
}

ERROR_KEYS = {"code", "message", "recoverable", "recovery_action"}


def _parse_single_envelope(result: Result) -> dict[str, Any]:
	output = result.output.strip()
	assert output
	assert "\n" not in output
	payload = json.loads(output)
	assert set(payload) == ENVELOPE_KEYS
	assert payload["schema_version"] == "1.0"
	return payload


@pytest.mark.parametrize(
	"args, command",
	[
		(["schema"], "schema"),
		(["--json", "schema"], "schema"),
		(["cities"], "cities"),
	],
)
def test_success_commands_emit_single_json_envelope(args: list[str], command: str) -> None:
	result = CliRunner().invoke(cli, args)

	assert result.exit_code == 0
	payload = _parse_single_envelope(result)
	assert payload["ok"] is True
	assert payload["command"] == command
	assert payload["error"] is None
	assert result.stderr == ""


@pytest.mark.parametrize(
	"args, command, code",
	[
		(["--platform", "nonexistent", "schema"], "boss", "INVALID_PARAM"),
		(["schema", "--format", "xml"], "schema", "INVALID_PARAM"),
		(["search"], "search", "INVALID_PARAM"),
	],
)
def test_failure_commands_emit_single_json_error_envelope(
	args: list[str],
	command: str,
	code: str,
) -> None:
	result = CliRunner().invoke(cli, args)

	assert result.exit_code == 1
	payload = _parse_single_envelope(result)
	assert payload["ok"] is False
	assert payload["command"] == command
	assert payload["data"] is None
	assert payload["pagination"] is None
	assert set(payload["error"]) == ERROR_KEYS
	assert payload["error"]["code"] == code
	assert isinstance(payload["error"]["message"], str)
	assert isinstance(payload["error"]["recoverable"], bool)
	assert "recovery_action" in payload["error"]
	assert result.stderr == ""


def _invoke_schema(tmp_path, *extra_args: str) -> dict[str, Any]:
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), *extra_args, "--json", "schema"])
	assert result.exit_code == 0, result.output
	return json.loads(result.output.strip())


def test_browser_mode_flag_maps_to_internal_cdp_required(tmp_path: Any) -> None:
	"""CLI 拼写 cdp-required 必须映射为内部值 cdp_required。"""
	payload = _invoke_schema(tmp_path, "--browser-mode", "cdp-required")
	assert payload["data"]["current_browser_mode"] == "cdp-required"


def test_browser_mode_defaults_to_auto(tmp_path: Any) -> None:
	payload = _invoke_schema(tmp_path)
	assert payload["data"]["current_browser_mode"] == "auto"


def test_browser_mode_config_spelling_compatibility(tmp_path: Any) -> None:
	"""config 里允许 cdp_required（下划线）与 cdp-required 两种拼写。"""
	import json as _json
	(tmp_path / "config.json").write_text(_json.dumps({"browser_mode": "cdp_required"}), encoding="utf-8")
	payload = _invoke_schema(tmp_path)
	assert payload["data"]["current_browser_mode"] == "cdp-required"


def test_browser_mode_rejects_unknown_value(tmp_path: Any) -> None:
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--browser-mode", "bridge", "--json", "schema"])
	assert result.exit_code != 0
