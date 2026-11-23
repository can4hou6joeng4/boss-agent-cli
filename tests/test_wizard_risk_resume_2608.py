"""风控断点不给 Agent 递 --resume；写前标记漂移不得重放写步骤（Issue #2608 / #2609）。"""

from __future__ import annotations

import json
from typing import Any

from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext
from boss_agent_cli.wizard.models import StepResult, WorkflowPlan, WorkflowStatus
from boss_agent_cli.wizard.runner import WorkflowRunner
from boss_agent_cli.wizard.store import WorkflowStore

_RUN = "wrn_fixture"
_UNKNOWN = {
	"code": "ACTION_UNCONFIRMED",
	"message": "写请求在途，结果尚未确认",
	"recoverable": False,
	"recovery_action": "核对平台状态",
	"replay_safe": False,
}
_RISK = {
	"code": "ACCOUNT_RISK",
	"message": "账号风控",
	"recoverable": False,
	"recovery_action": "停止并人工处理",
}


def _plan() -> WorkflowPlan:
	return WorkflowPlan(
		role="recruiter",
		platform="zhipin",
		goal="offline fixture",
		inputs={},
		requested_steps=["fake_read", "fake_write"],
		mode="headless",
	)


def _context(data_dir: Any) -> ActionContext:
	return ActionContext(data_dir=data_dir, platform="zhipin", role="recruiter", logger=Logger("error"))


def _seed(
	data_dir: Any,
	*,
	step_status: str,
	step_error: dict[str, Any] | None,
	run_status: str,
	run_error: dict[str, Any] | None,
) -> None:
	with WorkflowStore(data_dir) as store:
		store.create(_RUN, _plan())
		conn = store.cache._conn
		conn.execute(
			"UPDATE workflow_steps SET status = 'completed', result = ? WHERE run_id = ? AND step_name = 'fake_read'",
			(json.dumps({}), _RUN),
		)
		conn.execute(
			"UPDATE workflow_steps SET status = ?, error = ? WHERE run_id = ? AND step_name = 'fake_write'",
			(step_status, json.dumps(step_error) if step_error is not None else None, _RUN),
		)
		conn.execute(
			"UPDATE workflow_runs SET status = ?, current_step = 'fake_write', error = ? WHERE run_id = ?",
			(run_status, json.dumps(run_error) if run_error is not None else None, _RUN),
		)
		conn.commit()


def _resume(data_dir: Any) -> tuple[list[int], dict[str, Any]]:
	calls: list[int] = []

	def fake_write(context, inputs, prior):
		calls.append(1)
		return StepResult(status=WorkflowStatus.COMPLETED, data={})

	with WorkflowStore(data_dir) as store:
		runner = WorkflowRunner(store, {"fake_write": fake_write})
		run = runner.run(_plan(), _context(data_dir), run_id=_RUN)
	return calls, run


def _status_envelope(data_dir: Any) -> dict[str, Any]:
	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(data_dir), "wizard", "--status", _RUN])
	assert result.exit_code == 0, result.output
	return json.loads(result.output)


def test_risk_run_error_drops_resume_hint(tmp_path):
	_seed(tmp_path, step_status="failed", step_error=None, run_status="failed", run_error=_RISK)

	payload = _status_envelope(tmp_path)
	actions = payload["hints"]["next_actions"]
	assert all("--resume" not in action for action in actions)
	assert any("doctor" in action for action in actions)
	assert payload["hints"]["operator_actions"]


def test_risk_step_error_drops_resume_hint(tmp_path):
	_seed(tmp_path, step_status="failed", step_error=_RISK, run_status="failed", run_error=None)

	payload = _status_envelope(tmp_path)
	assert all("--resume" not in action for action in payload["hints"]["next_actions"])
	assert payload["hints"]["operator_actions"]


def test_operator_can_still_resume_risk_checkpoint_explicitly(tmp_path):
	# 契约要求操作者处理完官方页面、清除本地风险锁后显式恢复原任务（Issue #419）。
	_seed(tmp_path, step_status="failed", step_error=_RISK, run_status="failed", run_error=_RISK)

	calls, run = _resume(tmp_path)
	assert calls == [1]
	assert run["status"] == "completed"


def test_waiting_input_with_write_marker_blocks_resume(tmp_path):
	_seed(tmp_path, step_status="waiting_input", step_error=_UNKNOWN, run_status="waiting_input", run_error=None)

	calls, run = _resume(tmp_path)
	assert calls == []
	assert run["status"] == "failed"


def test_run_level_marker_blocks_resume_when_step_error_is_empty(tmp_path):
	for step_error in (None, {}):
		dir_path = tmp_path / ("null" if step_error is None else "empty")
		dir_path.mkdir()
		_seed(dir_path, step_status="failed", step_error=step_error, run_status="failed", run_error=_UNKNOWN)

		calls, run = _resume(dir_path)
		assert calls == []
		assert run["status"] == "failed"


def test_running_step_with_drifted_marker_code_blocks_resume(tmp_path):
	drifted = {**_UNKNOWN, "code": "action_unconfirmed"}
	drifted.pop("replay_safe")
	_seed(tmp_path, step_status="running", step_error=drifted, run_status="running", run_error=None)

	calls, run = _resume(tmp_path)
	assert calls == []
	assert run["status"] == "failed"
