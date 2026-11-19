"""wizard 恢复安全：内部错误保留写标记、声明失败终止与 NaN 参数（Issue #1881 #1882 #1883）。"""

from __future__ import annotations

import pytest

from boss_agent_cli.api._base_client import PlatformResponseInvalid
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext
from boss_agent_cli.wizard.models import (
	StepResult,
	WizardInput,
	WorkflowInputError,
	WorkflowPlan,
	WorkflowStatus,
)
from boss_agent_cli.wizard.runner import WorkflowRunner
from boss_agent_cli.wizard.store import WorkflowStore

_RUN_ID = "run-1881"
_CHECK_COMMAND = "boss hr chatmsg 42"


def _plan(*steps: str, inputs: dict | None = None) -> WorkflowPlan:
	return WorkflowPlan(
		role="recruiter",
		platform="zhipin",
		goal="reply",
		inputs=inputs if inputs is not None else {},
		requested_steps=steps,
		mode="headless",
	)


def _context(data_dir) -> ActionContext:
	return ActionContext(data_dir=data_dir, platform="zhipin", role="recruiter", logger=Logger("error"))


def _statuses(run: dict) -> dict[str, str]:
	return {step["step_name"]: step["status"] for step in run["steps"]}


# ── Issue #1881：写请求发出后的内部错误必须保留「结果未知」标记 ──────


def test_internal_error_after_write_dispatch_preserves_marker(tmp_path):
	"""PlatformResponseInvalid 在 INTERNAL_ERROR_TYPES 里：写后发生也不能变成可重放。"""
	calls: list[int] = []

	def write_then_invalid(context, inputs, prior):
		calls.append(1)
		assert context.workflow_control is not None
		context.workflow_control.begin_write(_CHECK_COMMAND)
		raise PlatformResponseInvalid("平台响应不是 JSON 对象")

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, {"recruiter_reply": write_then_invalid})
		run = runner.run(_plan("recruiter_reply"), _context(tmp_path), run_id=_RUN_ID)

		assert run["status"] == "failed"
		assert run["error"]["code"] == "INTERNAL_ERROR"
		assert run["error"]["replay_safe"] is False
		assert run["error"]["recovery_action"] == _CHECK_COMMAND

		resumed = runner.run(_plan("recruiter_reply"), _context(tmp_path), run_id=_RUN_ID)

		# 恢复只返回原失败，不重放可能已生效的写请求。
		assert calls == [1]
		assert resumed["status"] == "failed"
		assert resumed["error"]["code"] == "INTERNAL_ERROR"
		assert resumed["error"]["replay_safe"] is False


def test_internal_error_without_marker_stays_plain(tmp_path):
	"""没有写前标记的 TypeError 保持 INTERNAL_ERROR 的原样语义。"""
	def failing(context, inputs, prior):
		raise TypeError("坏响应结构")

	with WorkflowStore(tmp_path) as store:
		run = WorkflowRunner(store, {"recruiter_reply": failing}).run(
			_plan("recruiter_reply"), _context(tmp_path), run_id=_RUN_ID
		)

	assert run["status"] == "failed"
	assert run["error"]["code"] == "INTERNAL_ERROR"
	assert "replay_safe" not in run["error"]
	assert "TypeError" in run["error"]["message"]


# ── Issue #1882：action 显式返回的失败状态必须终止 run ────────────────


@pytest.mark.parametrize(
	"status, expected_run_status, expected_code",
	[
		(WorkflowStatus.FAILED, "failed", "INTERNAL_ERROR"),
		(WorkflowStatus.PENDING, "failed", "INTERNAL_ERROR"),
		(WorkflowStatus.RUNNING, "failed", "INTERNAL_ERROR"),
		(WorkflowStatus.STOPPED, "stopped", "WORKFLOW_STOPPED"),
	],
)
def test_declared_non_terminal_status_terminates_run(tmp_path, status, expected_run_status, expected_code):
	"""自定义 action 返回 FAILED / STOPPED 等状态时，run 不能继续执行并报 completed。"""
	executed: list[str] = []

	def declared(context, inputs, prior):
		executed.append("first")
		return StepResult({"ok": False}, status=status)

	def second(context, inputs, prior):
		executed.append("second")
		return StepResult({"ok": True})

	with WorkflowStore(tmp_path) as store:
		run = WorkflowRunner(store, {"first": declared, "second": second}).run(
			_plan("first", "second"), _context(tmp_path), run_id=_RUN_ID
		)

	assert executed == ["first"]
	assert run["status"] == expected_run_status
	assert run["error"]["code"] == expected_code
	assert run["current_step"] == "first"
	statuses = _statuses(run)
	assert statuses["first"] == expected_run_status
	assert statuses["second"] == "pending"


def test_declared_failure_after_write_dispatch_is_not_replayable(tmp_path):
	"""声明失败时若写前标记还在，按结果未知处理（fail-closed）。"""
	def write_then_fail(context, inputs, prior):
		assert context.workflow_control is not None
		context.workflow_control.begin_write(_CHECK_COMMAND)
		return StepResult({"ok": False}, status=WorkflowStatus.FAILED)

	with WorkflowStore(tmp_path) as store:
		run = WorkflowRunner(store, {"recruiter_reply": write_then_fail}).run(
			_plan("recruiter_reply"), _context(tmp_path), run_id=_RUN_ID
		)

	assert run["status"] == "failed"
	assert run["error"]["code"] == "INTERNAL_ERROR"
	assert run["error"]["replay_safe"] is False


def test_declared_completed_status_still_runs_next_step(tmp_path):
	"""COMPLETED 的既有语义不变：后续步骤照常执行并完成。"""
	executed: list[str] = []

	def first(context, inputs, prior):
		executed.append("first")
		return StepResult({"ok": True})

	def second(context, inputs, prior):
		executed.append("second")
		return StepResult({"ok": True})

	with WorkflowStore(tmp_path) as store:
		run = WorkflowRunner(store, {"first": first, "second": second}).run(
			_plan("first", "second"), _context(tmp_path), run_id=_RUN_ID
		)

	assert executed == ["first", "second"]
	assert run["status"] == "completed"
	assert run["error"] is None


# ── Issue #1883：NaN / Infinity 在入口拒绝，旧断点仍可恢复 ────────────


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_from_mapping_rejects_non_finite_inputs(bad: float) -> None:
	with pytest.raises(WorkflowInputError):
		WizardInput.from_mapping(
			{"role": "recruiter", "platform": "zhipin", "goal": "reply", "inputs": {"n": bad}}
		)


def test_from_mapping_rejects_nested_non_finite_inputs() -> None:
	with pytest.raises(WorkflowInputError) as excinfo:
		WizardInput.from_mapping(
			{
				"role": "recruiter",
				"platform": "zhipin",
				"goal": "reply",
				"inputs": {"filters": [1, {"x": float("nan")}]},
			}
		)

	assert "inputs.filters[1].x" in str(excinfo.value)


def test_from_mapping_keeps_finite_floats() -> None:
	wizard_input = WizardInput.from_mapping(
		{"role": "recruiter", "platform": "zhipin", "goal": "reply", "inputs": {"n": 1.5}}
	)

	assert wizard_input.inputs == {"n": 1.5}


def test_plans_equal_treats_nan_as_same_value() -> None:
	assert WorkflowRunner._plans_equal(
		{"inputs": {"n": float("nan"), "query": "python"}},
		{"inputs": {"n": float("nan"), "query": "python"}},
	)
	assert not WorkflowRunner._plans_equal(
		{"inputs": {"n": float("nan"), "query": "python"}},
		{"inputs": {"n": float("nan"), "query": "golang"}},
	)


def test_plans_equal_compares_json_style_sequences() -> None:
	assert WorkflowRunner._plans_equal({"inputs": {"ids": [1, 2]}}, {"inputs": {"ids": (1, 2)}})
	assert not WorkflowRunner._plans_equal({"inputs": {"ids": [1, 2]}}, {"inputs": {"ids": [1, 3]}})


def test_legacy_nan_checkpoint_still_resumes(tmp_path):
	"""旧版本落库的 NaN 参数不会再撞上永久 WORKFLOW_PLAN_MISMATCH。"""
	plan = _plan("read_only", inputs={"query": "python", "n": float("nan")})

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, {"read_only": lambda *args: StepResult({"ok": True})})
		first = runner.run(plan, _context(tmp_path), run_id="run-nan")

		assert first["status"] == "completed"

		resumed = runner.run(plan, _context(tmp_path), run_id="run-nan")

		assert resumed["status"] == "completed"
		assert resumed["error"] is None
