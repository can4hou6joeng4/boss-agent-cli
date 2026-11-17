"""配置损坏独立错误码 ``CONFIG_INVALID``（Issue #1525）。

``INVALID_PARAM`` 只表示调用方参数错误（不可重试）；手改坏的本地配置在修复
配置后可重新执行，但不进入同一次运行的自动重试循环。
"""

from pathlib import Path

from boss_agent_cli.schema.error_codes import ERROR_CODES
from boss_agent_cli.wizard.actions import _classify_action_error
from boss_agent_cli.wizard.models import StepResult, WorkflowPlan
from boss_agent_cli.wizard.runner import WorkflowActionError, WorkflowRunner
from boss_agent_cli.wizard.store import WorkflowStore


def _plan(*steps: str) -> WorkflowPlan:
	return WorkflowPlan(
		role="candidate",
		platform="zhipin",
		goal="job_search",
		inputs={"query": "Python"},
		requested_steps=steps,
		mode="headless",
	)


def test_config_invalid_is_registered_as_recoverable() -> None:
	assert ERROR_CODES["INVALID_PARAM"]["recoverable"] is False
	assert ERROR_CODES["CONFIG_INVALID"]["recoverable"] is True
	assert ERROR_CODES["CONFIG_INVALID"]["recovery_action"]


def test_runner_does_not_auto_retry_config_invalid_action(tmp_path: Path) -> None:
	attempts = 0

	def broken(context, inputs, prior):
		nonlocal attempts
		attempts += 1
		raise WorkflowActionError(
			"CONFIG_INVALID",
			"本地 AI 配置损坏",
			recoverable=True,
			recovery_action="boss ai config",
			auto_retry=False,
		)

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, {"broken": broken})
		run = runner.run(_plan("broken"), object(), run_id="run-config", max_retries=2)

	assert attempts == 1
	assert run["status"] == "failed"
	assert run["error"]["code"] == "CONFIG_INVALID"
	assert run["error"]["recoverable"] is True


def test_runner_still_retries_recoverable_network_errors(tmp_path: Path) -> None:
	"""auto_retry 默认值不能改变原有的网络错误重试行为。"""
	attempts = 0

	def flaky(context, inputs, prior):
		nonlocal attempts
		attempts += 1
		if attempts == 1:
			raise WorkflowActionError("RATE_LIMITED", "retry", recoverable=True)
		return StepResult({"attempts": attempts})

	with WorkflowStore(tmp_path) as store:
		run = WorkflowRunner(store, {"flaky": flaky}).run(_plan("flaky"), object(), run_id="run-retry", max_retries=1)

	assert run["status"] == "completed"
	assert attempts == 2


def test_classify_action_error_marks_invalid_param_non_retryable() -> None:
	code, recoverable, recovery = _classify_action_error("INVALID_PARAM", "参数错误")
	assert code == "INVALID_PARAM"
	assert recoverable is False
	assert "换一条" in recovery


def test_classify_action_error_uses_config_contract() -> None:
	code, recoverable, recovery = _classify_action_error("CONFIG_INVALID", "配置损坏")
	assert code == "CONFIG_INVALID"
	assert recoverable is True
	assert recovery == ERROR_CODES["CONFIG_INVALID"]["recovery_action"]
