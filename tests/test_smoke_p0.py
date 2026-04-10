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
		assert step.purpose
		assert step.preconditions
		assert step.failure_classification


def test_smoke_runner_distinguishes_step_failure_types():
	spec = importlib.util.spec_from_file_location("smoke_p0", SMOKE_SCRIPT)
	assert spec is not None and spec.loader is not None
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)

	runner = module.SmokeRunner(
		steps=[
			module.SmokeStep(
				name="ok",
				purpose="pass",
				preconditions=["none"],
				failure_classification="command_error",
				command=["echo", "ok"],
			),
			module.SmokeStep(
				name="missing",
				purpose="skip",
				preconditions=["env:BOSS_AGENT_FAKE_TOKEN"],
				failure_classification="env_error",
				command=["echo", "skip"],
			),
		]
	)

	results = runner.run()
	statuses = {item["name"]: item["status"] for item in results["steps"]}
	assert statuses["ok"] == "pass"
	assert statuses["missing"] == "env_error"
