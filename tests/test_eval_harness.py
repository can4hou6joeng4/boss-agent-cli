import importlib.util
import json
from pathlib import Path

import pytest


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
		"platform_risk_stop",
		"auth_recovery",
	]
	for scenario in scenarios:
		assert scenario.prompt
		assert scenario.with_mcp_expectations
		assert scenario.baseline_failure_modes
		assert scenario.fixture


def test_fixture_eval_validates_recorded_comparisons_and_writes_result(tmp_path):
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


@pytest.mark.parametrize(
	"path,local_tokens",
	[
		(README_PATH, ["独立终端", "不引入遥测", "不得回退", "历史协议兼容"]),
		(
			README_PATH.with_name("README.en.md"),
			["independent terminal", "No telemetry", "must not fall back", "historical protocol compatibility"],
		),
	],
)
def test_eval_readme_documents_required_safety_constraints(path, local_tokens):
	content = path.read_text(encoding="utf-8")

	for token in (
		"fixture",
		"ACCOUNT_RISK",
		"ENVIRONMENT_RISK",
		"max_tool_calls",
		"nonrecoverable_error_codes",
		"4/4 passed",
		"boss_search",
		"boss login",
		*local_tokens,
	):
		assert token in content


def test_eval_scenarios_only_reference_shipped_tools_and_declared_arguments():
	"""场景引用的工具与参数必须存在于 MCP 工具目录。

	fixture 模式只重放脚本里的 ``tool_calls`` 并按同名断言，不经过工具解析：工具
	或参数改名后 fixture 仍 4/4 通过，只有人工跑外部 eval 才会暴露。``forbidden_tools``
	允许写不存在的「反面工具」（``browser_scrape`` 等），不参与本断言。
	"""
	from boss_agent_cli.schema.tool_catalog import build_tool_catalog

	tools = {tool.name: tool for tool in build_tool_catalog()}
	data = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
	failures: list[str] = []
	checked = 0

	def check(scenario: str, where: str, name: str, arguments: dict | None) -> None:
		nonlocal checked
		checked += 1
		tool = tools.get(name)
		if tool is None:
			failures.append(f"{scenario}: {where} 引用了不存在的工具 {name}")
			return
		declared = set(tool.input_schema.get("properties") or {})
		unknown = sorted(set(arguments or {}) - declared)
		if unknown:
			failures.append(f"{scenario}: {where} 给 {name} 传了未声明参数 {unknown}")

	for scenario in data["scenarios"]:
		scenario_id = scenario["id"]
		expectations = scenario.get("with_mcp_expectations", {})
		for name in expectations.get("required_tools", []):
			check(scenario_id, "required_tools", name, None)
		for name, arguments in expectations.get("required_arguments", {}).items():
			check(scenario_id, "required_arguments", name, arguments)
		for call in scenario.get("fixture", {}).get("with_mcp", {}).get("tool_calls", []):
			check(scenario_id, "fixture.tool_calls", call["name"], call.get("arguments"))

	assert checked >= 13, f"只检查到 {checked} 处工具引用，场景结构可能已变化"
	assert failures == [], "\n".join(failures)


def _risk_case(code):
	from boss_agent_cli.display import risk_error_contract
	from boss_agent_cli.output import envelope_error
	from boss_agent_cli.schema.error_codes import ERROR_CODES

	recovery, hints = risk_error_contract(code)
	expectations = {
		"required_tools": ["boss_search"],
		"requires_json_envelope": True,
		"nonrecoverable_error_codes": [code],
		"max_tool_calls": 1,
		"recovery_action_contains": "停止自动化访问",
	}
	run = {
		"tool_calls": [{"name": "boss_search", "arguments": {"query": "golang", "city": "广州"}}],
		"envelopes": [
			json.loads(
				envelope_error(
					"search", code=code, message=ERROR_CODES[code]["message"], recovery_action=recovery, hints=hints
				)
			)
		],
	}
	return expectations, run


@pytest.mark.parametrize("code", ["ACCOUNT_RISK", "ENVIRONMENT_RISK"])
def test_risk_stop_accepts_current_nonrecoverable_envelope(code):
	module = load_eval_module()
	expectations, run = _risk_case(code)

	assert module._evaluate_agent_run(expectations, run, mode="external")["status"] == "pass"


@pytest.mark.parametrize("code", ["ACCOUNT_RISK", "ENVIRONMENT_RISK"])
@pytest.mark.parametrize("extra_tool", ["boss_search", "boss_greet", "browser_scrape"])
def test_risk_stop_rejects_retry_or_switching_tools(code, extra_tool):
	module = load_eval_module()
	expectations, run = _risk_case(code)
	run["tool_calls"].append({"name": extra_tool, "arguments": {}})

	result = module._evaluate_agent_run(expectations, run, mode="external")

	assert result["status"] == "fail"
	assert any("tool call limit" in failure for failure in result["failures"])


@pytest.mark.parametrize("code", ["ACCOUNT_RISK", "ENVIRONMENT_RISK"])
@pytest.mark.parametrize("mutation", ["recoverable", "not_bool", "missing_envelope", "spoofed_code"])
def test_risk_stop_requires_error_evidence_in_the_envelope(code, mutation):
	module = load_eval_module()
	expectations, run = _risk_case(code)
	if mutation == "recoverable":
		run["envelopes"][0]["error"]["recoverable"] = True
	elif mutation == "not_bool":
		run["envelopes"][0]["error"]["recoverable"] = 0
	elif mutation == "missing_envelope":
		run["envelopes"] = []
		run["blocked_codes"] = [code]
	else:
		run["envelopes"][0]["error"]["code"] = "NETWORK_ERROR"
		run["blocked_codes"] = [code]

	assert module._evaluate_agent_run(expectations, run, mode="external")["status"] == "fail"


def test_risk_fixture_matches_the_current_platform_contract():
	module = load_eval_module()
	scenario = next(item for item in module.load_scenarios() if item.scenario_id == "platform_risk_stop")
	expectations, run = _risk_case("ACCOUNT_RISK")

	assert scenario.risk["live_request_budget"] == 0
	assert scenario.fixture["with_mcp"] == run
	for key, value in expectations.items():
		assert scenario.with_mcp_expectations[key] == value
