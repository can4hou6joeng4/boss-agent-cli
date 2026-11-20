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
		(README_PATH, ["Agent 会话", "不引入遥测", "不得回退", "历史协议兼容"]),
		(
			README_PATH.with_name("README.en.md"),
			["agent session", "No telemetry", "must not fall back", "historical protocol compatibility"],
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


# ── 外部 runner 输出契约（Issue #2258） ─────────────────────────────


def _fixture_payload(scenario_input: str) -> tuple[str, dict]:
	"""按 stdin 里的场景 id 取出仓库夹具，作为内存 runner 的合规输出。"""
	scenario_id = json.loads(scenario_input)["id"]
	raw = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
	fixture = next(item["fixture"] for item in raw["scenarios"] if item["id"] == scenario_id)
	return scenario_id, {"with_mcp": fixture["with_mcp"], "baseline": fixture["baseline"]}


def _run_external(module, tmp_path, respond):
	"""respond(scenario_id, payload) 返回 runner 的 stdout 文本，或直接抛异常。"""

	def fake_run(command, cwd, input, capture_output, text, timeout, check):
		scenario_id, payload = _fixture_payload(input)
		return module.CommandResult(returncode=0, stdout=respond(scenario_id, payload), stderr="")

	return module.run_external_eval(
		module.load_scenarios(SCENARIOS_PATH),
		runner_cmd=["in-memory-runner"],
		results_dir=tmp_path,
		run_command=fake_run,
	)


def test_external_runner_with_comparable_data_is_scored(tmp_path):
	module = load_eval_module()

	report = _run_external(module, tmp_path, lambda _id, payload: json.dumps(payload, ensure_ascii=False))

	assert report["summary"]["label"] == "4/4 passed"
	assert all(item["baseline"]["status"] == "fail" for item in report["scenarios"])


def _drop(side, key=None):
	def mutate(payload):
		if key is None:
			del payload[side]
		else:
			del payload[side][key]
		return json.dumps(payload)

	return mutate


def _set(side, key, value):
	def mutate(payload):
		if key is None:
			payload[side] = value
		else:
			payload[side][key] = value
		return json.dumps(payload)

	return mutate


_CONTRACT_MUTATIONS = {
	"missing_baseline": (_drop("baseline"), "missing baseline run"),
	"missing_with_mcp": (_drop("with_mcp"), "missing with_mcp run"),
	"baseline_empty_object": (_set("baseline", None, {}), "baseline.tool_calls is required"),
	"baseline_null": (_set("baseline", None, None), "baseline must be an object"),
	"with_mcp_null": (_set("with_mcp", None, None), "with_mcp must be an object"),
	"with_mcp_list": (_set("with_mcp", None, []), "with_mcp must be an object"),
	"tool_calls_null": (_set("with_mcp", "tool_calls", None), "with_mcp.tool_calls must be an array"),
	"tool_calls_missing": (_drop("with_mcp", "tool_calls"), "with_mcp.tool_calls is required"),
	"envelopes_missing": (_drop("baseline", "envelopes"), "baseline.envelopes is required"),
	"envelopes_object": (_set("baseline", "envelopes", {}), "baseline.envelopes must be an array"),
	"envelopes_scalar_item": (_set("with_mcp", "envelopes", [1]), "with_mcp.envelopes[0] must be an object"),
	"tool_call_scalar_item": (
		_set("with_mcp", "tool_calls", ["boss_search"]),
		"with_mcp.tool_calls[0] must be an object",
	),
	"tool_call_without_name": (
		_set("baseline", "tool_calls", [{"arguments": {}}]),
		"baseline.tool_calls[0].name must be a non-empty string",
	),
	"tool_call_arguments_list": (
		_set("baseline", "tool_calls", [{"name": "boss_search", "arguments": ["golang"]}]),
		"baseline.tool_calls[0].arguments must be an object",
	),
	"blocked_codes_string": (
		_set("with_mcp", "blocked_codes", "ACCOUNT_RISK"),
		"with_mcp.blocked_codes must be an array",
	),
	"recovery_actions_object": (
		_set("baseline", "recovery_actions", {}),
		"baseline.recovery_actions must be an array",
	),
	"top_level_array": (lambda payload: "[]", "runner stdout must be a JSON object"),
	"empty_stdout": (lambda payload: "", "runner stdout was not JSON"),
	"oversized_integer": (lambda payload: '{"with_mcp": ' + "9" * 5000 + "}", "runner stdout was not JSON"),
}


@pytest.mark.parametrize("name", sorted(_CONTRACT_MUTATIONS))
def test_external_contract_violation_fails_only_that_scenario(tmp_path, name):
	module = load_eval_module()
	mutate, expected_detail = _CONTRACT_MUTATIONS[name]

	def respond(scenario_id, payload):
		return mutate(payload) if scenario_id == "welfare_search" else json.dumps(payload, ensure_ascii=False)

	report = _run_external(module, tmp_path, respond)

	by_id = {item["id"]: item for item in report["scenarios"]}
	broken = by_id.pop("welfare_search")
	assert broken["passed"] is False
	assert broken["with_mcp"]["status"] == "contract_error"
	assert any(expected_detail in failure for failure in broken["with_mcp"]["failures"])
	assert broken["baseline"]["status"] == "not_evaluated"
	# 一个场景的契约错误不影响其余场景的评分与报告。
	assert len(by_id) == 3
	assert all(item["passed"] for item in by_id.values())
	assert report["summary"]["label"] == "3/4 passed"
	assert len(list(tmp_path.glob("*-external.json"))) == 1


def test_external_runner_undecodable_output_is_a_contract_error(tmp_path):
	module = load_eval_module()

	def respond(scenario_id, payload):
		if scenario_id == "detail_shortlist":
			raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
		return json.dumps(payload, ensure_ascii=False)

	report = _run_external(module, tmp_path, respond)

	by_id = {item["id"]: item for item in report["scenarios"]}
	assert by_id["detail_shortlist"]["with_mcp"]["status"] == "contract_error"
	assert by_id["detail_shortlist"]["with_mcp"]["failures"] == ["runner output was not valid UTF-8"]
	assert report["summary"]["label"] == "3/4 passed"


def test_external_explicit_empty_baseline_is_still_comparable(tmp_path):
	"""显式的空数组表示「确实没有调用、没有信封」，仍进入评分。"""
	module = load_eval_module()

	def respond(_scenario_id, payload):
		payload["baseline"] = {"tool_calls": [], "envelopes": []}
		return json.dumps(payload, ensure_ascii=False)

	report = _run_external(module, tmp_path, respond)

	assert report["summary"]["label"] == "4/4 passed"
	assert all(item["baseline"]["status"] == "fail" for item in report["scenarios"])


def test_external_tool_call_may_omit_or_null_arguments(tmp_path):
	module = load_eval_module()

	def respond(_scenario_id, payload):
		payload["baseline"]["tool_calls"] = [{"name": "browser_scrape"}, {"name": "browser_scrape", "arguments": None}]
		return json.dumps(payload, ensure_ascii=False)

	report = _run_external(module, tmp_path, respond)

	assert report["summary"]["label"] == "4/4 passed"


@pytest.mark.parametrize("mutation", ["missing_baseline", "tool_calls_null", "envelopes_scalar_item"])
def test_load_scenarios_rejects_incomparable_fixture(tmp_path, mutation):
	module = load_eval_module()
	raw = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
	fixture = raw["scenarios"][0]["fixture"]
	if mutation == "missing_baseline":
		del fixture["baseline"]
	elif mutation == "tool_calls_null":
		fixture["with_mcp"]["tool_calls"] = None
	else:
		fixture["baseline"]["envelopes"] = [1]
	path = tmp_path / "scenarios.json"
	path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

	with pytest.raises(ValueError, match="fixture is not comparable"):
		module.load_scenarios(path)


def test_default_runner_exchanges_utf8_regardless_of_locale(monkeypatch):
	"""场景 JSON 含中文且 ensure_ascii=False；不能跟随系统区域编码（如 GBK）。"""
	module = load_eval_module()
	seen = {}

	class Completed:
		returncode = 0
		stdout = "{}"
		stderr = ""

	def fake_subprocess_run(command, **kwargs):
		seen.update(kwargs)
		return Completed()

	monkeypatch.setattr(module.subprocess, "run", fake_subprocess_run)

	result = module._default_run_command(
		["runner"], cwd=ROOT, input="{}", capture_output=True, text=True, timeout=5, check=False
	)

	assert seen["encoding"] == "utf-8"
	assert seen["text"] is True
	assert result == module.CommandResult(returncode=0, stdout="{}", stderr="")
