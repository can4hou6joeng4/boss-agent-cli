"""wizard 恢复时的重放阻断与断点错误契约（Issue #1956–#1960）。"""

from __future__ import annotations

import json

import pytest

from boss_agent_cli.cache.store import CacheRecordInvalid
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext
from boss_agent_cli.wizard.models import StepResult, WorkflowPlan, WorkflowStatus
from boss_agent_cli.wizard.runner import (
	WorkflowActionError,
	WorkflowControl,
	WorkflowRunner,
	has_blocked_step,
)
from boss_agent_cli.wizard.store import WorkflowStore

_RUN_ID = "run-1956"
_CHECK_COMMAND = "boss hr chatmsg 42"
_UNKNOWN = {
	"code": "WORKFLOW_STOPPED",
	"message": "结果未知",
	"recoverable": False,
	"recovery_action": _CHECK_COMMAND,
	"replay_safe": False,
}


def _plan(*steps: str) -> WorkflowPlan:
	return WorkflowPlan(
		role="recruiter",
		platform="zhipin",
		goal="reply",
		inputs={},
		requested_steps=steps,
		mode="headless",
	)


def _context(data_dir) -> ActionContext:
	return ActionContext(data_dir=data_dir, platform="zhipin", role="recruiter", logger=Logger("error"))


def _set_step(store: WorkflowStore, **columns: str | None) -> None:
	assignments = ", ".join(f"{name} = ?" for name in columns)
	conn = store.cache._conn
	conn.execute(
		f"UPDATE workflow_steps SET {assignments} WHERE run_id = ?", (*columns.values(), _RUN_ID)
	)
	conn.commit()


def _set_run_status(store: WorkflowStore, status: str) -> None:
	conn = store.cache._conn
	conn.execute("UPDATE workflow_runs SET status = ?, error = NULL WHERE run_id = ?", (status, _RUN_ID))
	conn.commit()


# ── Issue #1956：写后的停止 / 超时信号保留结果未知标记 ───────────────


@pytest.mark.parametrize("code", ["WORKFLOW_TIMEOUT", "WORKFLOW_STOPPED"])
def test_control_signal_after_write_keeps_unknown_marker(tmp_path, code: str) -> None:
	calls: list[int] = []

	def write_then_signal(context, inputs, prior):
		calls.append(1)
		context.workflow_control.begin_write(_CHECK_COMMAND)
		raise WorkflowActionError(code, "控制检查中止", recoverable=True)

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, {"recruiter_reply": write_then_signal})
		run = runner.run(_plan("recruiter_reply"), _context(tmp_path), run_id=_RUN_ID)

		assert run["error"]["code"] == code
		assert run["error"]["replay_safe"] is False
		assert run["error"]["recovery_action"] == _CHECK_COMMAND

		runner.run(_plan("recruiter_reply"), _context(tmp_path), run_id=_RUN_ID)

		assert calls == [1]


def test_control_signal_without_write_stays_replayable(tmp_path) -> None:
	def signal_only(context, inputs, prior):
		raise WorkflowActionError("WORKFLOW_TIMEOUT", "超时", recoverable=True)

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, {"recruiter_reply": signal_only})
		run = runner.run(_plan("recruiter_reply"), _context(tmp_path), run_id=_RUN_ID)

	assert run["error"]["code"] == "WORKFLOW_TIMEOUT"
	assert run["error"].get("replay_safe") is not False


# ── Issue #1957：stopped 步骤带 replay_safe=false 同样阻断 ───────────


def test_stopped_step_with_unknown_result_blocks_resume(tmp_path) -> None:
	calls: list[int] = []

	def write(context, inputs, prior):
		calls.append(1)
		return StepResult(status=WorkflowStatus.COMPLETED, data={"sent": True})

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, {"recruiter_reply": write})
		runner.run(_plan("recruiter_reply"), _context(tmp_path), run_id=_RUN_ID)
		assert calls == [1]
		# 模拟：步骤已落 stopped + 结果未知，执行器在写 run 状态前消失。
		_set_step(store, status="stopped", result=None, error=json.dumps(_UNKNOWN))
		_set_run_status(store, "running")

		resumed = runner.run(_plan("recruiter_reply"), _context(tmp_path), run_id=_RUN_ID)

		assert calls == [1]
		assert resumed["status"] != "completed"
		assert resumed["error"]["replay_safe"] is False


def test_has_blocked_step_counts_stopped_steps() -> None:
	run = {"steps": [{"status": "stopped", "error": {"replay_safe": False}}]}

	assert has_blocked_step(run) is True
	assert has_blocked_step({"steps": [{"status": "stopped", "error": {"replay_safe": True}}]}) is False


# ── Issue #1958：绑定内部任务不清掉写前标记 ─────────────────────────


def test_bind_inner_run_keeps_write_marker(tmp_path) -> None:
	def write_then_bind(context, inputs, prior):
		control: WorkflowControl = context.workflow_control
		control.begin_write(_CHECK_COMMAND)
		control.bind_inner_run("crl_x")
		raise SystemExit(1)

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, {"recruiter_reply": write_then_bind})
		with pytest.raises(SystemExit):
			runner.run(_plan("recruiter_reply"), _context(tmp_path), run_id=_RUN_ID)

		step = store.cache.list_workflow_steps(_RUN_ID)[0]

	assert step["result"] == {"inner_run_id": "crl_x"}
	assert step["error"]["replay_safe"] is False


# ── Issue #1959：已完成步骤 result 为空也不重跑 ─────────────────────


@pytest.mark.parametrize("result", [None, "{}"])
def test_completed_step_with_empty_result_is_not_rerun(tmp_path, result) -> None:
	calls: list[str] = []

	def write(context, inputs, prior):
		calls.append("write")
		return StepResult(status=WorkflowStatus.COMPLETED, data={"sent": True})

	def fail(context, inputs, prior):
		calls.append("next")
		raise WorkflowActionError("NETWORK_ERROR", "暂时失败", recoverable=True)

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, {"recruiter_reply": write, "recruiter_followup": fail})
		plan = _plan("recruiter_reply", "recruiter_followup")
		runner.run(plan, _context(tmp_path), run_id=_RUN_ID)
		conn = store.cache._conn
		conn.execute(
			"UPDATE workflow_steps SET result = ? WHERE run_id = ? AND step_name = ?",
			(result, _RUN_ID, "recruiter_reply"),
		)
		conn.commit()

		runner.run(plan, _context(tmp_path), run_id=_RUN_ID)

	assert calls.count("write") == 1


# ── Issue #1960：error.code / message 类型漂移按记录损坏拒绝 ─────────


@pytest.mark.parametrize(
	"drift",
	[{"code": {"a": 1}}, {"code": 123}, {"code": None}, {"code": ""}, {"message": ["m"]}],
)
def test_error_code_and_message_drift_is_rejected(tmp_path, drift) -> None:
	def fail(context, inputs, prior):
		raise WorkflowActionError("NETWORK_ERROR", "暂时失败", recoverable=True)

	with WorkflowStore(tmp_path) as store:
		WorkflowRunner(store, {"recruiter_reply": fail}).run(
			_plan("recruiter_reply"), _context(tmp_path), run_id=_RUN_ID
		)
		_set_step(store, error=json.dumps({**_UNKNOWN, **drift}))

		with pytest.raises(CacheRecordInvalid):
			store.get_checkpoint(_RUN_ID)
