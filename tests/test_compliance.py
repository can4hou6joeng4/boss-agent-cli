import ast
import json
from pathlib import Path

from click.testing import CliRunner

from boss_agent_cli.compliance import (
	capability_policy,
	low_risk_blocked_commands,
	require_capability_mode,
	require_compliance_allowed,
	restricted_commands,
)
from boss_agent_cli.config import DEFAULTS, load_config
from boss_agent_cli.main import cli


def _invoke(*args: str, data_dir: Path | None = None):
	runner = CliRunner()
	global_args = ["--data-dir", str(data_dir)] if data_dir is not None else []
	result = runner.invoke(cli, ["--json", *global_args, *args])
	return result.exit_code, json.loads(result.output)


def test_default_mode_enters_outbound_greet_business_layer(tmp_path):
	code, parsed = _invoke("greet", "sec_001", "job_001", data_dir=tmp_path)
	assert code == 1
	assert parsed["ok"] is False
	assert parsed["error"]["code"] != "COMPLIANCE_BLOCKED"


def test_default_mode_enters_platform_data_aggregation_business_layer(tmp_path):
	code, parsed = _invoke("pipeline", data_dir=tmp_path)
	assert code in {0, 1}
	assert parsed["ok"] is True or parsed["error"]["code"] != "COMPLIANCE_BLOCKED"


def test_default_mode_enters_recruiter_candidate_screening_business_layer(tmp_path):
	for args, command in [
		(("hr", "candidates", "python"), "recruiter-candidates"),
		(("hr", "resume", "geek_001", "--job-id", "job_001", "--security-id", "sec_001"), "recruiter-resume"),
		(("hr", "request-resume", "12345"), "recruiter-request-resume"),
	]:
		code, parsed = _invoke(*args, data_dir=tmp_path)
		assert code == 1
		assert parsed["ok"] is False
		assert parsed["command"] == command
		assert parsed["error"]["code"] != "COMPLIANCE_BLOCKED"


def test_raw_chatmsg_enters_business_layer_in_default_mode(tmp_path):
	code, parsed = _invoke("chatmsg", "sec_001", "--raw", data_dir=tmp_path)
	assert code == 1
	assert parsed["ok"] is False
	assert parsed["error"]["code"] != "COMPLIANCE_BLOCKED"


def test_schema_exposes_current_compliance_mode():
	code, parsed = _invoke("schema")
	assert code == 0
	compliance = parsed["data"]["compliance"]
	assert compliance["default_boundary"] == "open_capabilities"
	assert compliance["operating_mode"] == "assisted"
	assert compliance["available_modes"] == ["assisted", "research"]
	assert compliance["sensitive_commands_blocked"] is False
	assert "low_risk_mode" not in compliance
	assert compliance["blocked_commands"] == []
	assert compliance["capabilities"]["greet"]["risk_class"] == "platform_write"
	assert compliance["capabilities"]["greet"]["allowed_modes"] == ["assisted", "research"]
	assert compliance["capabilities"]["greet"]["requires_explicit_consent"] is False


def test_research_mode_allows_registered_sensitive_commands(restricted_surface_args):
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", *restricted_surface_args, "schema"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	compliance = parsed["data"]["compliance"]
	assert compliance["operating_mode"] == "research"
	assert compliance["sensitive_commands_blocked"] is False
	assert compliance["blocked_commands"] == []
	assert restricted_commands("research") == set()
	assert restricted_commands("assisted") == set()


def test_legacy_low_risk_false_migrates_to_research(tmp_path):
	config_path = tmp_path / "config.json"
	config_path.write_text('{"low_risk_mode": false}', encoding="utf-8")
	assert load_config(config_path)["operating_mode"] == "research"
	assert capability_policy("greet") is not None


def test_operating_mode_uses_current_default_without_user_override(monkeypatch):
	monkeypatch.setitem(DEFAULTS, "operating_mode", "research")
	assert load_config(None)["operating_mode"] == "research"


def test_invalid_persisted_operating_mode_falls_back_to_assisted(tmp_path):
	config_path = tmp_path / "config.json"
	config_path.write_text('{"operating_mode": "invalid"}', encoding="utf-8")
	assert load_config(config_path)["operating_mode"] == "assisted"


def test_internal_policy_fixture_keeps_historical_contract_tests_reachable(restricted_surface_args):
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", *restricted_surface_args, "schema"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["compliance"]["sensitive_commands_blocked"] is False


def test_compatibility_guards_are_noops_in_both_modes():
	ctx = type("Context", (), {"obj": {"config": {"operating_mode": "assisted"}}})()
	assert require_compliance_allowed(ctx, "greet") is True
	require_capability_mode("assisted", "crawl")
	require_capability_mode("research", "crawl")


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


def test_compliance_registry_matches_all_guarded_capability_metadata():
	"""Every compatibility guard remains backed by schema-visible risk metadata."""
	guarded = _guarded_command_names_from_source()
	assert guarded
	assert low_risk_blocked_commands() == set()
	assert all(capability_policy(command) is not None for command in guarded)


def test_schema_reports_no_blocked_commands_for_guarded_capabilities():
	code, parsed = _invoke("schema")
	assert code == 0
	assert parsed["data"]["compliance"]["blocked_commands"] == []
