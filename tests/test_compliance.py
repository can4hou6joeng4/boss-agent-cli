import json

from click.testing import CliRunner

from boss_agent_cli.main import cli


def _invoke(*args: str):
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", *args])
	return result.exit_code, json.loads(result.output)


def test_default_low_risk_mode_blocks_outbound_greet():
	code, parsed = _invoke("greet", "sec_001", "job_001")
	assert code == 1
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "COMPLIANCE_BLOCKED"
	assert parsed["error"]["recoverable"] is False
	assert "平台官网" in parsed["error"]["recovery_action"]


def test_default_low_risk_mode_blocks_platform_data_aggregation():
	code, parsed = _invoke("pipeline")
	assert code == 1
	assert parsed["error"]["code"] == "COMPLIANCE_BLOCKED"
	assert "默认低风险模式" in parsed["error"]["message"]


def test_schema_exposes_current_compliance_mode():
	code, parsed = _invoke("schema")
	assert code == 0
	compliance = parsed["data"]["compliance"]
	assert compliance["default_boundary"] == "low_risk_assistance"
	assert compliance["sensitive_commands_blocked"] is True
	assert "low_risk_mode" not in compliance
	assert "greet" in compliance["blocked_commands"]
	assert "pipeline" in compliance["blocked_commands"]


def test_internal_policy_fixture_keeps_historical_contract_tests_reachable(restricted_surface_args):
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", *restricted_surface_args, "schema"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["compliance"]["sensitive_commands_blocked"] is False
