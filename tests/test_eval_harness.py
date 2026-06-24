import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVAL_SCRIPT = ROOT / "evals" / "run_eval.py"
SCENARIOS_PATH = ROOT / "evals" / "scenarios.json"
README_PATH = ROOT / "evals" / "README.md"


def load_eval_module():
	spec = importlib.util.spec_from_file_location("eval_run_eval", EVAL_SCRIPT)
	assert spec is not None and spec.loader is not None
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def test_eval_scenarios_define_required_four_cases():
	module = load_eval_module()

	scenarios = module.load_scenarios(SCENARIOS_PATH)

	assert [scenario.scenario_id for scenario in scenarios] == [
		"welfare_search",
		"detail_shortlist",
		"compliance_boundary",
		"auth_recovery",
	]
	for scenario in scenarios:
		assert scenario.prompt
		assert scenario.with_mcp_expectations
		assert scenario.baseline_failure_modes
		assert scenario.fixture


def test_fixture_eval_proves_with_mcp_beats_baseline_and_writes_result(tmp_path):
	module = load_eval_module()
	scenarios = module.load_scenarios(SCENARIOS_PATH)

	report = module.run_fixture_eval(scenarios, results_dir=tmp_path)

	assert report["summary"]["passed"] == 4
	assert report["summary"]["total"] == 4
	assert report["summary"]["label"] == "4/4 passed"
	assert all(item["passed"] for item in report["scenarios"])
	assert all(item["with_mcp"]["status"] == "pass" for item in report["scenarios"])
	assert all(item["baseline"]["status"] != "pass" for item in report["scenarios"])
	result_files = sorted(tmp_path.glob("*.json"))
	assert len(result_files) == 1
	persisted = json.loads(result_files[0].read_text(encoding="utf-8"))
	assert persisted["summary"]["label"] == "4/4 passed"


def test_external_runner_failure_does_not_fallback_to_fixture(tmp_path):
	module = load_eval_module()
	scenarios = module.load_scenarios(SCENARIOS_PATH)

	def fake_run(command, cwd, input, capture_output, text, timeout, check):
		return module.CommandResult(returncode=1, stdout="", stderr="runner failed")

	report = module.run_external_eval(
		scenarios,
		runner_cmd=["fake-agent-runner"],
		results_dir=tmp_path,
		run_command=fake_run,
	)

	assert report["summary"]["passed"] == 0
	assert report["summary"]["total"] == 4
	assert report["summary"]["label"] == "0/4 passed"
	assert all(not item["passed"] for item in report["scenarios"])
	assert all(item["with_mcp"]["mode"] == "external" for item in report["scenarios"])
	assert all(item["with_mcp"]["status"] == "runner_error" for item in report["scenarios"])


def test_eval_readme_documents_required_safety_constraints():
	content = README_PATH.read_text(encoding="utf-8")

	for token in (
		"独立终端",
		"fixture",
		"ACCOUNT_RISK",
		"不引入遥测",
		"不得 fallback",
		"4/4 passed",
		"boss_search",
		"COMPLIANCE_BLOCKED",
		"boss login",
	):
		assert token in content
