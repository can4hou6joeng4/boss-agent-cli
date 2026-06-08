import ast
import json
from pathlib import Path

from click.testing import CliRunner

from boss_agent_cli.compliance import low_risk_blocked_commands
from boss_agent_cli.main import cli


def _invoke(*args: str):
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", *args])
	return result.exit_code, json.loads(result.output)


def _assert_compliance_block_hints(parsed: dict) -> None:
	hints = parsed["hints"]
	assert hints["policy"] == "low_risk_assistance"
	assert hints["blocked"] is True
	assert hints["manual_action_required"] is True
	assert hints["allowed_alternatives"] == ["search", "detail", "show", "shortlist"]
	assert hints["next_actions"]


def test_default_low_risk_mode_blocks_outbound_greet():
	code, parsed = _invoke("greet", "sec_001", "job_001")
	assert code == 1
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "COMPLIANCE_BLOCKED"
	assert parsed["error"]["recoverable"] is False
	assert "平台官网" in parsed["error"]["recovery_action"]
	_assert_compliance_block_hints(parsed)


def test_default_low_risk_mode_blocks_platform_data_aggregation():
	code, parsed = _invoke("pipeline")
	assert code == 1
	assert parsed["error"]["code"] == "COMPLIANCE_BLOCKED"
	assert "默认低风险模式" in parsed["error"]["message"]
	_assert_compliance_block_hints(parsed)


def test_default_low_risk_mode_blocks_recruiter_candidate_screening():
	for args, command in [
		(("hr", "candidates", "python"), "recruiter-candidates"),
		(("hr", "resume", "geek_001", "--job-id", "job_001", "--security-id", "sec_001"), "recruiter-resume"),
		(("hr", "request-resume", "12345"), "recruiter-request-resume"),
	]:
		code, parsed = _invoke(*args)
		assert code == 1
		assert parsed["ok"] is False
		assert parsed["command"] == command
		assert parsed["error"]["code"] == "COMPLIANCE_BLOCKED"
		assert "候选" in parsed["error"]["message"] or "简历" in parsed["error"]["message"]
		assert parsed["error"]["recoverable"] is False
		_assert_compliance_block_hints(parsed)


def test_raw_chatmsg_does_not_bypass_low_risk_compliance():
	code, parsed = _invoke("chatmsg", "sec_001", "--raw")
	assert code == 1
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "COMPLIANCE_BLOCKED"


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


def _guarded_command_names_from_source() -> set[str]:
	"""Return command ids passed to require_compliance_allowed(ctx, ...)."""
	commands_dir = Path(__file__).resolve().parents[1] / "src" / "boss_agent_cli" / "commands"
	guarded: set[str] = set()
	for path in commands_dir.rglob("*.py"):
		tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
		for node in ast.walk(tree):
			if not isinstance(node, ast.Call):
				continue
			func = node.func
			if not isinstance(func, ast.Name) or func.id != "require_compliance_allowed":
				continue
			if len(node.args) < 2 or not isinstance(node.args[1], ast.Constant):
				continue
			command = node.args[1].value
			if isinstance(command, str):
				guarded.add(command)
	return guarded


def test_compliance_registry_matches_all_guarded_sensitive_commands():
	"""Every command using the compliance guard must be listed in schema-visible policy data."""
	guarded = _guarded_command_names_from_source()
	blocked = low_risk_blocked_commands()
	assert guarded
	assert guarded == blocked


def test_schema_blocked_commands_match_guarded_sensitive_commands():
	code, parsed = _invoke("schema")
	assert code == 0
	assert set(parsed["data"]["compliance"]["blocked_commands"]) == _guarded_command_names_from_source()
