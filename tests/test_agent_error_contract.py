"""Agent-facing CLI error envelope contract tests."""

import json
from typing import Any

import pytest
from click.testing import CliRunner, Result

from boss_agent_cli.main import cli


_ERROR_KEYS = {"code", "message", "recoverable", "recovery_action"}


def _assert_single_stdout_json_error(result: Result, *, command: str, code: str = "INVALID_PARAM") -> dict[str, Any]:
	"""Agent-facing errors must stay as a single machine-consumable stdout envelope."""
	assert result.exit_code == 1
	assert result.stderr == ""
	assert result.output.strip()
	assert "\n" not in result.output.strip()
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["schema_version"] == "1.0"
	assert parsed["command"] == command
	assert parsed["data"] is None
	assert parsed["pagination"] is None
	assert set(parsed["error"]) == _ERROR_KEYS
	assert parsed["error"]["code"] == code
	assert isinstance(parsed["error"]["message"], str)
	assert isinstance(parsed["error"]["recoverable"], bool)
	return parsed


@pytest.mark.parametrize(
	("args", "command", "message_fragment"),
	[
		(("--platform", "unknown", "status"), "boss", "--platform"),
		(("--delay", "fast", "status"), "boss", "--delay"),
		(("--role", "admin", "status"), "boss", "--role"),
		(("detail",), "detail", "SECURITY_ID"),
		(("config", "get"), "get", "KEY"),
		(("shortlist", "add"), "add", "SECURITY_ID"),
	],
)
def test_agent_facing_usage_errors_emit_stdout_json_envelope(tmp_path, args, command, message_fragment):
	"""Global parameter and high-frequency usage errors keep the JSON error contract."""
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), *args])

	parsed = _assert_single_stdout_json_error(result, command=command)
	assert message_fragment in parsed["error"]["message"]
