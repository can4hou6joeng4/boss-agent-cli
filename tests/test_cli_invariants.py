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


def _invoke_schema(tmp_path: Any, *extra_args: str) -> dict[str, Any]:
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), *extra_args, "--json", "schema"])
	assert result.exit_code == 0, result.output
	return json.loads(result.output.strip())


def test_browser_source_flag_is_exposed_in_schema(tmp_path: Any) -> None:
	"""--browser-source 的字面量必须原样进 current_browser_source，不做映射。"""
	payload = _invoke_schema(tmp_path, "--browser-source", "stored-cookie")
	assert payload["data"]["current_browser_source"] == "stored-cookie"


def test_browser_source_defaults_to_auto(tmp_path: Any) -> None:
	payload = _invoke_schema(tmp_path)
	assert payload["data"]["current_browser_source"] == "auto"


def test_browser_source_reads_config_value(tmp_path: Any) -> None:
	"""config.json 里的 browser_source 必须被读取（无需 CLI 显式传）。"""
	(tmp_path / "config.json").write_text(json.dumps({"browser_source": "existing-browser"}), encoding="utf-8")
	payload = _invoke_schema(tmp_path)
	assert payload["data"]["current_browser_source"] == "existing-browser"


def test_browser_source_rejects_unknown_config_value(tmp_path: Any) -> None:
	"""config.json 里的非法 browser_source 走 main.py 的白名单分支（不是 click.Choice），必须以 INVALID_PARAM 信封拒绝。"""
	(tmp_path / "config.json").write_text(json.dumps({"browser_source": "cdp"}), encoding="utf-8")
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "schema"])
	assert result.exit_code != 0
	payload = json.loads(result.output.strip())
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "cdp" in payload["error"]["message"]


def test_browser_source_config_value_is_normalized(tmp_path: Any) -> None:
	"""config.json 里带大小写/空白的合法值按 resolve_policy 同样的规则归一化后接受。"""
	(tmp_path / "config.json").write_text(json.dumps({"browser_source": " Stored-Cookie "}), encoding="utf-8")
	payload = _invoke_schema(tmp_path)
	assert payload["data"]["current_browser_source"] == "stored-cookie"


def test_browser_source_rejects_unknown_value(tmp_path: Any) -> None:
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--browser-source", "bridge", "--json", "schema"])
	assert result.exit_code != 0
