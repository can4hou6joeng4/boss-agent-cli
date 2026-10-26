"""`--exit-codes sysexits` 契约：默认 0/1 不变，opt-in 后按错误类别映射。"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from boss_agent_cli import output
from boss_agent_cli.main import cli
from boss_agent_cli.schema.error_codes import ERROR_CODES


@pytest.fixture(autouse=True)
def _reset_exit_codes_mode():
	output.set_exit_codes_mode("envelope")
	yield
	output.set_exit_codes_mode("envelope")


def test_mapping_only_uses_registered_codes_and_sysexits_values():
	assert set(output.SYSEXITS_EXIT_CODES) <= set(ERROR_CODES)
	assert set(output.SYSEXITS_EXIT_CODES.values()) <= {69, 75, 77, 78}


@pytest.mark.parametrize(
	"code, expected",
	[
		("INVALID_PARAM", 78),
		("AUTH_REQUIRED", 77),
		("AUTH_EXPIRED", 77),
		("LOGIN_EXPIRED", 77),
		("TOKEN_REFRESH_FAILED", 77),
		("LOGIN_CREDENTIAL_EXTRACTION_FAILED", 77),
		("RATE_LIMITED", 75),
		("REQUEST_BUDGET_EXHAUSTED", 75),
		("LOGIN_TIMEOUT", 75),
		("BROWSER_NAV_TIMEOUT", 75),
		("WORKFLOW_TIMEOUT", 75),
		("ACCOUNT_RISK", 69),
		("ENVIRONMENT_RISK", 69),
		("ENVIRONMENT_RISK_LOCKED", 69),
		("LOGIN_RISK_CONTROL", 69),
		("NOT_SUPPORTED", 69),
		("PLATFORM_NOT_SUPPORTED", 69),
		("JOB_NOT_FOUND", 1),
		("UNKNOWN_CODE", 1),
		("", 1),
	],
)
def test_sysexits_mapping(code: str, expected: int) -> None:
	output.set_exit_codes_mode("sysexits")
	assert output.exit_code_for_error(code) == expected


def test_envelope_mode_always_exits_1() -> None:
	assert output.exit_code_for_error("AUTH_REQUIRED") == 1
	assert output.exit_code_for_error("INVALID_PARAM") == 1


def test_set_exit_codes_mode_normalizes_and_falls_back() -> None:
	output.set_exit_codes_mode(" SYSEXITS ")
	assert output.exit_code_for_error("AUTH_REQUIRED") == 77
	output.set_exit_codes_mode("nope")
	assert output.exit_code_for_error("AUTH_REQUIRED") == 1


def test_default_cli_failure_keeps_exit_1(tmp_path) -> None:
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "search"])
	assert result.exit_code == 1
	payload = json.loads(result.output.strip())
	assert payload["error"]["code"] == "INVALID_PARAM"


def test_sysexits_maps_usage_error_to_78(tmp_path) -> None:
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--exit-codes", "sysexits", "search"])
	assert result.exit_code == 78
	payload = json.loads(result.output.strip())
	assert payload["error"]["code"] == "INVALID_PARAM"


def test_sysexits_prescan_covers_group_level_parse_errors(tmp_path) -> None:
	"""组级解析错误发生在回调之前，预扫描仍应让显式 sysexits 生效。"""
	result = CliRunner().invoke(
		cli,
		["--data-dir", str(tmp_path), "--exit-codes", "sysexits", "--definitely-not-an-option", "schema"],
	)
	assert result.exit_code == 78


def test_invalid_exit_codes_value_is_usage_error(tmp_path) -> None:
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--exit-codes", "nope", "schema"])
	assert result.exit_code == 1
	payload = json.loads(result.output.strip())
	assert payload["error"]["code"] == "INVALID_PARAM"
