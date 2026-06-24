"""Compare MCP-assisted agent runs against a baseline on fixture-first scenarios."""

import argparse
import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENARIOS_PATH = ROOT / "evals" / "scenarios.json"
DEFAULT_RESULTS_DIR = ROOT / "evals" / "results"
ENVELOPE_KEYS = {"ok", "schema_version", "command", "data", "pagination", "error", "hints"}


@dataclass(frozen=True)
class EvalScenario:
	scenario_id: str
	title: str
	prompt: str
	risk: dict[str, Any]
	with_mcp_expectations: dict[str, Any]
	baseline_failure_modes: list[str]
	fixture: dict[str, Any]


@dataclass(frozen=True)
class CommandResult:
	returncode: int
	stdout: str
	stderr: str


def load_scenarios(path: Path = DEFAULT_SCENARIOS_PATH) -> list[EvalScenario]:
	raw = json.loads(path.read_text(encoding="utf-8"))
	if raw.get("schema_version") != "1.0":
		raise ValueError("scenarios.json schema_version must be 1.0")
	items = raw.get("scenarios")
	if not isinstance(items, list) or not items:
		raise ValueError("scenarios.json must define a non-empty scenarios list")

	scenarios: list[EvalScenario] = []
	seen: set[str] = set()
	for item in items:
		scenario_id = _required_str(item, "id")
		if scenario_id in seen:
			raise ValueError(f"duplicate scenario id: {scenario_id}")
		seen.add(scenario_id)
		scenarios.append(
			EvalScenario(
				scenario_id=scenario_id,
				title=_required_str(item, "title"),
				prompt=_required_str(item, "prompt"),
				risk=_required_dict(item, "risk"),
				with_mcp_expectations=_required_dict(item, "with_mcp_expectations"),
				baseline_failure_modes=_required_list(item, "baseline_failure_modes"),
				fixture=_required_dict(item, "fixture"),
			)
		)
	return scenarios


def run_fixture_eval(
	scenarios: list[EvalScenario],
	*,
	results_dir: Path = DEFAULT_RESULTS_DIR,
) -> dict[str, Any]:
	report = _build_report(scenarios, mode="fixture")
	return _write_report(report, results_dir=results_dir, mode="fixture")


def run_external_eval(
	scenarios: list[EvalScenario],
	*,
	runner_cmd: list[str],
	results_dir: Path = DEFAULT_RESULTS_DIR,
	run_command=None,
	timeout_seconds: int = 120,
) -> dict[str, Any]:
	run_command = run_command or _default_run_command
	results = []
	for scenario in scenarios:
		payload = json.dumps(_scenario_to_dict(scenario), ensure_ascii=False)
		try:
			completed = run_command(
				runner_cmd,
				cwd=ROOT,
				input=payload,
				capture_output=True,
				text=True,
				timeout=timeout_seconds,
				check=False,
			)
		except subprocess.TimeoutExpired:
			result = _external_error_result(scenario, "timeout", f"runner exceeded {timeout_seconds}s")
		except OSError as exc:
			result = _external_error_result(scenario, "runner_error", str(exc))
		else:
			result = _evaluate_external_output(scenario, completed)
		results.append(result)
	report = _summarize(results, mode="external")
	return _write_report(report, results_dir=results_dir, mode="external")


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description="Run boss-agent-cli MCP-vs-baseline eval scenarios.")
	parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS_PATH)
	parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
	parser.add_argument("--mode", choices=("fixture", "external"), default="fixture")
	parser.add_argument("--runner-cmd", default="", help="External runner command for --mode external.")
	parser.add_argument("--timeout", type=int, default=120)
	args = parser.parse_args(argv)

	scenarios = load_scenarios(args.scenarios)
	if args.mode == "fixture":
		report = run_fixture_eval(scenarios, results_dir=args.results_dir)
	else:
		if not args.runner_cmd.strip():
			print("--runner-cmd is required for --mode external; no fixture fallback is attempted.", file=sys.stderr)
			return 2
		report = run_external_eval(
			scenarios,
			runner_cmd=shlex.split(args.runner_cmd),
			results_dir=args.results_dir,
			timeout_seconds=args.timeout,
		)

	print(report["summary"]["label"])
	print(f"result: {report['result_path']}")
	return 0 if report["summary"]["passed"] == report["summary"]["total"] else 1


def _default_run_command(command, cwd, input, capture_output, text, timeout, check):
	completed = subprocess.run(
		command,
		cwd=cwd,
		input=input,
		capture_output=capture_output,
		text=text,
		timeout=timeout,
		check=check,
	)
	return CommandResult(returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)


def _build_report(scenarios: list[EvalScenario], *, mode: str) -> dict[str, Any]:
	results = [_evaluate_scenario(scenario, mode=mode, run_data=scenario.fixture) for scenario in scenarios]
	return _summarize(results, mode=mode)


def _summarize(results: list[dict[str, Any]], *, mode: str) -> dict[str, Any]:
	passed = sum(1 for item in results if item["passed"])
	total = len(results)
	return {
		"schema_version": "1.0",
		"generated_at": _utc_now(),
		"mode": mode,
		"summary": {
			"passed": passed,
			"total": total,
			"label": f"{passed}/{total} passed",
		},
		"scenarios": results,
	}


def _write_report(report: dict[str, Any], *, results_dir: Path, mode: str) -> dict[str, Any]:
	results_dir.mkdir(parents=True, exist_ok=True)
	stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
	path = results_dir / f"{stamp}-{mode}.json"
	report = dict(report)
	report["result_path"] = str(path)
	path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
	return report


def _evaluate_external_output(scenario: EvalScenario, completed: CommandResult) -> dict[str, Any]:
	if completed.returncode != 0:
		return _external_error_result(scenario, "runner_error", completed.stderr.strip() or "runner returned non-zero")
	try:
		payload = json.loads(completed.stdout)
	except json.JSONDecodeError:
		return _external_error_result(scenario, "contract_error", "runner stdout was not JSON")
	if not isinstance(payload, dict):
		return _external_error_result(scenario, "contract_error", "runner stdout must be a JSON object")
	run_data = {
		"with_mcp": payload.get("with_mcp", {}),
		"baseline": payload.get("baseline", {}),
	}
	return _evaluate_scenario(scenario, mode="external", run_data=run_data)


def _external_error_result(scenario: EvalScenario, status: str, detail: str) -> dict[str, Any]:
	return {
		"id": scenario.scenario_id,
		"title": scenario.title,
		"prompt": scenario.prompt,
		"passed": False,
		"with_mcp": {
			"mode": "external",
			"status": status,
			"failures": [detail],
		},
		"baseline": {
			"mode": "external",
			"status": "not_evaluated",
			"failures": ["external runner did not produce comparable baseline data"],
		},
		"baseline_failure_modes": scenario.baseline_failure_modes,
	}


def _evaluate_scenario(scenario: EvalScenario, *, mode: str, run_data: dict[str, Any]) -> dict[str, Any]:
	with_mcp = _evaluate_agent_run(
		scenario.with_mcp_expectations,
		run_data.get("with_mcp", {}),
		mode=mode,
	)
	baseline = _evaluate_agent_run(
		scenario.with_mcp_expectations,
		run_data.get("baseline", {}),
		mode=mode,
	)
	passed = with_mcp["status"] == "pass" and baseline["status"] != "pass"
	return {
		"id": scenario.scenario_id,
		"title": scenario.title,
		"prompt": scenario.prompt,
		"passed": passed,
		"with_mcp": with_mcp,
		"baseline": baseline,
		"baseline_failure_modes": scenario.baseline_failure_modes,
	}


def _evaluate_agent_run(expectations: dict[str, Any], run: dict[str, Any], *, mode: str) -> dict[str, Any]:
	failures: list[str] = []
	tool_calls = run.get("tool_calls", [])
	envelopes = run.get("envelopes", [])

	for tool_name in expectations.get("required_tools", []):
		if not _has_tool_call(tool_calls, tool_name):
			failures.append(f"missing required tool call: {tool_name}")

	for tool_name, expected_args in expectations.get("required_arguments", {}).items():
		args = _tool_arguments(tool_calls, tool_name)
		if args is None:
			failures.append(f"missing arguments for tool: {tool_name}")
			continue
		for key, expected_value in expected_args.items():
			if args.get(key) != expected_value:
				failures.append(f"{tool_name}.{key} expected {expected_value!r}, got {args.get(key)!r}")

	for forbidden_tool in expectations.get("forbidden_tools", []):
		if _has_tool_call(tool_calls, forbidden_tool):
			failures.append(f"forbidden tool call attempted: {forbidden_tool}")

	if expectations.get("requires_json_envelope"):
		if not envelopes:
			failures.append("missing stdout JSON envelope evidence")
		for envelope in envelopes:
			error = _validate_envelope(envelope)
			if error:
				failures.append(error)

	if expectations.get("requires_ok_envelope") and not any(envelope.get("ok") is True for envelope in envelopes):
		failures.append("missing ok:true envelope")

	for code in expectations.get("required_error_codes", []):
		if code not in _error_codes(run):
			failures.append(f"missing error code: {code}")

	action_fragment = expectations.get("recovery_action_contains")
	if action_fragment and not any(action_fragment in action for action in _recovery_actions(run)):
		failures.append(f"missing recovery action containing: {action_fragment}")

	handoff_fragment = expectations.get("official_handoff_contains")
	if handoff_fragment and handoff_fragment not in str(run.get("handoff", "")):
		failures.append(f"missing official platform handoff containing: {handoff_fragment}")

	if expectations.get("security_id_flow") and not _has_security_id_flow(tool_calls, envelopes):
		failures.append("security_id/job_id were not preserved through search -> detail -> shortlist")

	return {
		"mode": mode,
		"status": "pass" if not failures else "fail",
		"failures": failures,
	}


def _has_tool_call(tool_calls: Any, tool_name: str) -> bool:
	return any(isinstance(call, dict) and call.get("name") == tool_name for call in tool_calls)


def _tool_arguments(tool_calls: Any, tool_name: str) -> dict[str, Any] | None:
	for call in tool_calls:
		if isinstance(call, dict) and call.get("name") == tool_name:
			args = call.get("arguments")
			return args if isinstance(args, dict) else {}
	return None


def _validate_envelope(envelope: Any) -> str | None:
	if not isinstance(envelope, dict):
		return "stdout JSON envelope evidence must be an object"
	if set(envelope) != ENVELOPE_KEYS:
		return "stdout JSON envelope keys did not match the CLI contract"
	if envelope.get("schema_version") != "1.0":
		return "stdout JSON envelope schema_version was not 1.0"
	if not isinstance(envelope.get("ok"), bool):
		return "stdout JSON envelope ok was not a boolean"
	if envelope.get("ok") is False:
		error = envelope.get("error")
		if not isinstance(error, dict):
			return "error envelope missing error object"
		required = {"code", "recoverable", "recovery_action"}
		if not required.issubset(error):
			return "error envelope missing code/recoverable/recovery_action"
	return None


def _error_codes(run: dict[str, Any]) -> set[str]:
	codes = set(str(code) for code in run.get("blocked_codes", []) if code)
	for envelope in run.get("envelopes", []):
		if isinstance(envelope, dict) and isinstance(envelope.get("error"), dict):
			code = envelope["error"].get("code")
			if code:
				codes.add(str(code))
	return codes


def _recovery_actions(run: dict[str, Any]) -> list[str]:
	actions = [str(action) for action in run.get("recovery_actions", []) if action]
	for envelope in run.get("envelopes", []):
		if isinstance(envelope, dict) and isinstance(envelope.get("error"), dict):
			action = envelope["error"].get("recovery_action")
			if action:
				actions.append(str(action))
	return actions


def _has_security_id_flow(tool_calls: Any, envelopes: Any) -> bool:
	security_id = None
	job_id = None
	for envelope in envelopes:
		if not isinstance(envelope, dict):
			continue
		data = envelope.get("data")
		if isinstance(data, list) and data:
			first = data[0]
			if isinstance(first, dict):
				security_id = first.get("security_id")
				job_id = first.get("job_id")
				break
	if not security_id or not job_id:
		return False
	detail_args = _tool_arguments(tool_calls, "boss_detail") or {}
	shortlist_args = _tool_arguments(tool_calls, "boss_shortlist_add") or {}
	return (
		detail_args.get("security_id") == security_id
		and shortlist_args.get("security_id") == security_id
		and shortlist_args.get("job_id") == job_id
	)


def _scenario_to_dict(scenario: EvalScenario) -> dict[str, Any]:
	return {
		"id": scenario.scenario_id,
		"title": scenario.title,
		"prompt": scenario.prompt,
		"risk": scenario.risk,
		"with_mcp_expectations": scenario.with_mcp_expectations,
		"baseline_failure_modes": scenario.baseline_failure_modes,
	}


def _required_str(item: dict[str, Any], key: str) -> str:
	value = item.get(key)
	if not isinstance(value, str) or not value:
		raise ValueError(f"scenario field {key!r} must be a non-empty string")
	return value


def _required_dict(item: dict[str, Any], key: str) -> dict[str, Any]:
	value = item.get(key)
	if not isinstance(value, dict):
		raise ValueError(f"scenario field {key!r} must be an object")
	return value


def _required_list(item: dict[str, Any], key: str) -> list[Any]:
	value = item.get(key)
	if not isinstance(value, list) or not value:
		raise ValueError(f"scenario field {key!r} must be a non-empty list")
	return value


def _utc_now() -> str:
	return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
	raise SystemExit(main())
