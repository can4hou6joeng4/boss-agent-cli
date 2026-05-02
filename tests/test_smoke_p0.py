import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMOKE_SCRIPT = ROOT / "scripts" / "smoke_p0.py"


def test_smoke_script_exists():
	assert SMOKE_SCRIPT.exists()


def test_smoke_script_defines_required_step_names():
	spec = importlib.util.spec_from_file_location("smoke_p0", SMOKE_SCRIPT)
	assert spec is not None and spec.loader is not None
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	step_names = [step.name for step in module.DEFAULT_STEPS]
	assert step_names == ["doctor", "status", "search", "detail"]


def test_smoke_script_step_metadata_is_complete():
	spec = importlib.util.spec_from_file_location("smoke_p0", SMOKE_SCRIPT)
	assert spec is not None and spec.loader is not None
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	for step in module.DEFAULT_STEPS:
		assert step.platform
		assert step.purpose
		assert step.preconditions
		assert step.failure_classification


def test_smoke_script_can_build_zhilian_steps():
	spec = importlib.util.spec_from_file_location("smoke_p0", SMOKE_SCRIPT)
	assert spec is not None and spec.loader is not None
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)

	steps = module.build_default_steps("zhilian", query="golang", security_id="demo-security-id")
	assert [step.name for step in steps] == ["doctor", "status", "search", "detail"]
	assert all(step.platform == "zhilian" for step in steps)
	assert steps[0].command == ["boss", "--platform", "zhilian", "doctor"]
	assert steps[1].command == ["boss", "--platform", "zhilian", "status"]


def test_smoke_steps_use_configured_query_and_security_id():
	spec = importlib.util.spec_from_file_location("smoke_p0", SMOKE_SCRIPT)
	assert spec is not None and spec.loader is not None
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)

	steps = module.build_default_steps(
		"zhipin",
		query="python",
		security_id="real-security-id",
	)

	commands = {step.name: step.command for step in steps}
	assert commands["search"] == ["boss", "search", "python"]
	assert commands["detail"] == ["boss", "detail", "real-security-id"]


def test_smoke_steps_use_configured_zhilian_query_and_security_id():
	spec = importlib.util.spec_from_file_location("smoke_p0", SMOKE_SCRIPT)
	assert spec is not None and spec.loader is not None
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)

	steps = module.build_default_steps(
		"zhilian",
		query="java",
		security_id="zhilian-security-id",
	)

	commands = {step.name: step.command for step in steps}
	assert commands["search"] == ["boss", "--platform", "zhilian", "search", "java"]
	assert commands["detail"] == ["boss", "--platform", "zhilian", "detail", "zhilian-security-id"]


def test_smoke_runner_distinguishes_step_failure_types():
	spec = importlib.util.spec_from_file_location("smoke_p0", SMOKE_SCRIPT)
	assert spec is not None and spec.loader is not None
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)

	def fake_run(command, cwd, capture_output, text, timeout, check):
		return module.CommandResult(
			returncode=0,
			stdout='{"ok": true, "schema_version": "1.0", "command": "doctor", "data": {}, "pagination": null, "error": null, "hints": null}',
			stderr="",
		)

	runner = module.SmokeRunner(
		steps=[
			module.SmokeStep(
				name="ok",
				platform="zhipin",
				purpose="pass",
				preconditions=["none"],
				failure_classification="command_error",
				command=["echo", "ok"],
			),
			module.SmokeStep(
				name="missing",
				platform="zhipin",
				purpose="skip",
				preconditions=["env:BOSS_AGENT_FAKE_TOKEN"],
				failure_classification="env_error",
				command=["echo", "skip"],
			),
		],
		run_command=fake_run,
	)

	results = runner.run()
	statuses = {item["name"]: item["status"] for item in results["steps"]}
	assert statuses["ok"] == "pass"
	assert statuses["missing"] == "env_error"


def test_smoke_runner_marks_non_json_stdout_as_contract_error():
	spec = importlib.util.spec_from_file_location("smoke_p0", SMOKE_SCRIPT)
	assert spec is not None and spec.loader is not None
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)

	def fake_run(command, cwd, capture_output, text, timeout, check):
		return module.CommandResult(returncode=0, stdout="not json", stderr="")

	runner = module.SmokeRunner(
		steps=[
			module.SmokeStep(
				name="schema",
				platform="zhipin",
				purpose="contract",
				preconditions=["command:boss"],
				failure_classification="command_error",
				command=["boss", "schema"],
			),
		],
		run_command=fake_run,
	)

	results = runner.run()
	step = results["steps"][0]
	assert step["status"] == "contract_error"
	assert step["ok"] is None
	assert "stdout was not a JSON envelope" in step["detail"]


def test_smoke_runner_marks_ok_false_envelope_as_command_error():
	spec = importlib.util.spec_from_file_location("smoke_p0", SMOKE_SCRIPT)
	assert spec is not None and spec.loader is not None
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)

	def fake_run(command, cwd, capture_output, text, timeout, check):
		return module.CommandResult(
			returncode=1,
			stdout='{"ok": false, "schema_version": "1.0", "command": "status", "data": null, "pagination": null, "error": {"code": "AUTH_REQUIRED", "message": "未登录", "recoverable": true, "recovery_action": "boss login"}, "hints": null}',
			stderr="",
		)

	runner = module.SmokeRunner(
		steps=[
			module.SmokeStep(
				name="status",
				platform="zhipin",
				purpose="auth",
				preconditions=["command:boss"],
				failure_classification="env_error",
				command=["boss", "status"],
			),
		],
		run_command=fake_run,
	)

	results = runner.run()
	step = results["steps"][0]
	assert step["status"] == "env_error"
	assert step["ok"] is False
	assert step["error_code"] == "AUTH_REQUIRED"
	assert step["recovery_action"] == "boss login"


def test_smoke_runner_redacts_detail_security_id_from_reported_command():
	spec = importlib.util.spec_from_file_location("smoke_p0", SMOKE_SCRIPT)
	assert spec is not None and spec.loader is not None
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)

	def fake_run(command, cwd, capture_output, text, timeout, check):
		return module.CommandResult(
			returncode=0,
			stdout='{"ok": true, "schema_version": "1.0", "command": "detail", "data": {}, "pagination": null, "error": null, "hints": null}',
			stderr="",
		)

	runner = module.SmokeRunner(
		steps=[
			module.SmokeStep(
				name="detail",
				platform="zhipin",
				purpose="privacy",
				preconditions=["command:boss"],
				failure_classification="command_error",
				command=["boss", "detail", "real-security-id"],
			),
		],
		run_command=fake_run,
	)

	step = runner.run()["steps"][0]
	assert step["status"] == "pass"
	assert step["command"] == ["boss", "detail", "<redacted>"]


def test_smoke_runner_marks_exit_code_ok_mismatch_as_contract_error():
	spec = importlib.util.spec_from_file_location("smoke_p0", SMOKE_SCRIPT)
	assert spec is not None and spec.loader is not None
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)

	def fake_run(command, cwd, capture_output, text, timeout, check):
		return module.CommandResult(
			returncode=1,
			stdout='{"ok": true, "schema_version": "1.0", "command": "status", "data": {}, "pagination": null, "error": null, "hints": null}',
			stderr="",
		)

	runner = module.SmokeRunner(
		steps=[
			module.SmokeStep(
				name="status",
				platform="zhipin",
				purpose="contract",
				preconditions=["command:boss"],
				failure_classification="env_error",
				command=["boss", "status"],
			),
		],
		run_command=fake_run,
	)

	step = runner.run()["steps"][0]
	assert step["status"] == "contract_error"
	assert step["ok"] is True
	assert step["returncode"] == 1
	assert step["detail"] == "stdout envelope ok did not match process exit code"


def test_smoke_runner_marks_ok_false_exit_zero_as_contract_error():
	spec = importlib.util.spec_from_file_location("smoke_p0", SMOKE_SCRIPT)
	assert spec is not None and spec.loader is not None
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)

	def fake_run(command, cwd, capture_output, text, timeout, check):
		return module.CommandResult(
			returncode=0,
			stdout='{"ok": false, "schema_version": "1.0", "command": "status", "data": null, "pagination": null, "error": {"code": "AUTH_REQUIRED", "message": "未登录", "recoverable": true, "recovery_action": "boss login"}, "hints": null}',
			stderr="",
		)

	runner = module.SmokeRunner(
		steps=[
			module.SmokeStep(
				name="status",
				platform="zhipin",
				purpose="contract",
				preconditions=["command:boss"],
				failure_classification="env_error",
				command=["boss", "status"],
			),
		],
		run_command=fake_run,
	)

	step = runner.run()["steps"][0]
	assert step["status"] == "contract_error"
	assert step["ok"] is False
	assert step["returncode"] == 0
	assert step["detail"] == "stdout envelope ok did not match process exit code"


def test_smoke_runner_marks_missing_error_fields_as_contract_error():
	spec = importlib.util.spec_from_file_location("smoke_p0", SMOKE_SCRIPT)
	assert spec is not None and spec.loader is not None
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)

	def fake_run(command, cwd, capture_output, text, timeout, check):
		return module.CommandResult(
			returncode=1,
			stdout='{"ok": false, "schema_version": "1.0", "command": "status", "data": null, "pagination": null, "error": {"message": "未登录"}, "hints": null}',
			stderr="",
		)

	runner = module.SmokeRunner(
		steps=[
			module.SmokeStep(
				name="status",
				platform="zhipin",
				purpose="contract",
				preconditions=["command:boss"],
				failure_classification="env_error",
				command=["boss", "status"],
			),
		],
		run_command=fake_run,
	)

	step = runner.run()["steps"][0]
	assert step["status"] == "contract_error"
	assert step["ok"] is False
	assert step["detail"] == "stdout envelope error did not match the error shape"


def test_smoke_runner_marks_timeout():
	spec = importlib.util.spec_from_file_location("smoke_p0", SMOKE_SCRIPT)
	assert spec is not None and spec.loader is not None
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)

	def fake_timeout(command, cwd, capture_output, text, timeout, check):
		raise module.subprocess.TimeoutExpired(command, timeout)

	runner = module.SmokeRunner(
		steps=[
			module.SmokeStep(
				name="search",
				platform="zhipin",
				purpose="timeout",
				preconditions=["command:boss"],
				failure_classification="command_error",
				command=["boss", "search", "golang"],
			),
		],
		run_command=fake_timeout,
		timeout_seconds=7,
	)

	results = runner.run()
	step = results["steps"][0]
	assert step["status"] == "timeout"
	assert step["detail"] == "command exceeded 7s"


def test_smoke_runner_dry_run_does_not_execute_commands():
	spec = importlib.util.spec_from_file_location("smoke_p0", SMOKE_SCRIPT)
	assert spec is not None and spec.loader is not None
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)

	calls = []

	def fake_run(command, cwd, capture_output, text, timeout, check):
		calls.append(command)
		return module.CommandResult(returncode=0, stdout="{}", stderr="")

	runner = module.SmokeRunner(
		steps=[
			module.SmokeStep(
				name="doctor",
				platform="zhipin",
				purpose="dry",
				preconditions=["command:boss"],
				failure_classification="env_error",
				command=["boss", "doctor"],
			),
		],
		run_command=fake_run,
		dry_run=True,
	)

	results = runner.run()
	assert calls == []
	assert results["steps"][0]["status"] == "dry_run"
	assert results["steps"][0]["detail"] == "command not executed"


def test_smoke_runner_dry_run_bypasses_live_env_preconditions(monkeypatch):
	spec = importlib.util.spec_from_file_location("smoke_p0", SMOKE_SCRIPT)
	assert spec is not None and spec.loader is not None
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)

	monkeypatch.delenv("BOSS_SMOKE_QUERY", raising=False)
	calls = []

	def fake_run(command, cwd, capture_output, text, timeout, check):
		calls.append(command)
		return module.CommandResult(returncode=0, stdout="{}", stderr="")

	runner = module.SmokeRunner(
		steps=[
			module.SmokeStep(
				name="search",
				platform="zhipin",
				purpose="dry",
				preconditions=["command:boss", "env:BOSS_SMOKE_QUERY"],
				failure_classification="command_error",
				command=["boss", "search", "golang"],
			),
		],
		run_command=fake_run,
		dry_run=True,
	)

	results = runner.run()
	assert calls == []
	assert results["steps"][0]["status"] == "dry_run"
	assert results["steps"][0]["detail"] == "command not executed"


def test_smoke_docs_describe_env_controls_and_failure_classes():
	content = (ROOT / "docs" / "smoke-testing.md").read_text(encoding="utf-8")

	assert "BOSS_SMOKE_PLATFORM" in content
	assert "BOSS_SMOKE_QUERY" in content
	assert "BOSS_SMOKE_SECURITY_ID" in content
	assert "BOSS_SMOKE_TIMEOUT" in content
	assert "BOSS_SMOKE_DRY_RUN" in content
	assert "contract_error" in content
	assert "timeout" in content
	assert "dry_run" in content
	assert "不提交真实 Cookie" in content
