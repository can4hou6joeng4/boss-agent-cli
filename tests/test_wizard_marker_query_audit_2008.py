"""wizard 控制信号保留写前标记与 query 类型校验（Issue #2008–#2009）。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from boss_agent_cli.output import Logger
from boss_agent_cli.wizard import runner as wizard_runner
from boss_agent_cli.wizard.actions import ActionContext, _candidate_search, execute_candidate_search
from boss_agent_cli.wizard.models import StepResult, WorkflowInputError, WorkflowPlan, WorkflowStatus
from boss_agent_cli.wizard.runner import WorkflowRunner
from boss_agent_cli.wizard.store import WorkflowStore

_RUN_ID = "run-2008"
_CHECK_COMMAND = "boss hr chatmsg 42"


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


# ── #2008：action 声明失败后到达的控制信号保留写前标记 ───────────────


def test_timeout_after_declared_failure_keeps_write_marker(
	tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
	# 用受控时钟代替 sleep：机器繁忙时 0.05 秒可能在动作开始前就到点，
	# 那样测的是「动作前的超时」，而不是写标记后的超时（Issue #2008）。
	clock = [1000.0]
	monkeypatch.setattr(wizard_runner, "time", SimpleNamespace(monotonic=lambda: clock[0]))
	calls: list[int] = []

	def write_then_fail(context, inputs, prior):
		calls.append(1)
		control = context.workflow_control
		control.begin_write(_CHECK_COMMAND)
		# 声明写标记后才越过 deadline，确保 runner 在动作返回后轮询到超时。
		clock[0] += 1.0
		return StepResult({"sent": False}, status=WorkflowStatus.FAILED)

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, {"recruiter_reply": write_then_fail})
		run = runner.run(
			_plan("recruiter_reply"), _context(tmp_path), run_id=_RUN_ID, timeout_seconds=0.05
		)

		assert run["status"] == "failed"
		assert run["error"]["code"] == "WORKFLOW_TIMEOUT"
		assert run["error"]["replay_safe"] is False
		assert run["error"]["recovery_action"] == _CHECK_COMMAND
		step = store.cache.list_workflow_steps(_RUN_ID)[0]
		assert step["error"]["replay_safe"] is False, "update_step 之后步骤里必须仍有在途证据"

		resumed = runner.run(_plan("recruiter_reply"), _context(tmp_path), run_id=_RUN_ID)

		assert calls == [1], "--resume 不得重放结果未知的写请求"
		assert resumed["status"] != "completed"


def test_stop_after_declared_failure_keeps_write_marker(tmp_path) -> None:
	calls: list[int] = []

	def write_then_fail_and_stop(context, inputs, prior):
		calls.append(1)
		control = context.workflow_control
		control.begin_write(_CHECK_COMMAND)
		control.store.update_run(
			control.run_id,
			status=WorkflowStatus.STOPPED.value,
			current_step=control.step_name,
		)
		return StepResult({"sent": False}, status=WorkflowStatus.FAILED)

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, {"recruiter_reply": write_then_fail_and_stop})
		run = runner.run(_plan("recruiter_reply"), _context(tmp_path), run_id=_RUN_ID)

		assert run["status"] == "stopped"
		assert run["error"]["code"] == "WORKFLOW_STOPPED"
		assert run["error"]["replay_safe"] is False
		assert run["error"]["recovery_action"] == _CHECK_COMMAND
		step = store.cache.list_workflow_steps(_RUN_ID)[0]
		assert step["status"] == "failed"
		assert step["error"]["replay_safe"] is False

		resumed = runner.run(_plan("recruiter_reply"), _context(tmp_path), run_id=_RUN_ID)

		assert calls == [1], "停止后 --resume 同样不得重放结果未知的写请求"
		assert resumed["status"] != "completed"


def test_declared_failure_without_control_keeps_previous_behavior(tmp_path) -> None:
	# 护栏：没有控制信号时仍走 _finish_declared_status，写标记不应改变失败码。
	def write_then_fail(context, inputs, prior):
		context.workflow_control.begin_write(_CHECK_COMMAND)
		return StepResult({"sent": False}, status=WorkflowStatus.FAILED)

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, {"recruiter_reply": write_then_fail})
		run = runner.run(_plan("recruiter_reply"), _context(tmp_path), run_id=_RUN_ID)

		assert run["status"] == "failed"
		assert run["error"]["code"] == "INTERNAL_ERROR"
		assert run["error"]["replay_safe"] is False


# ── #2009：job_search 的 query 类型校验 ─────────────────────────────


@pytest.mark.parametrize("query", [False, 0, {"k": 1}, ["kw"]])
def test_execute_candidate_search_rejects_non_string_query(query) -> None:
	with pytest.raises(WorkflowInputError) as exc_info:
		execute_candidate_search(MagicMock(), MagicMock(), Logger("error"), {"query": query})

	assert "query 必须是字符串" in str(exc_info.value)


def test_execute_candidate_search_treats_missing_and_null_query_as_empty() -> None:
	seen: list[str] = []

	def fake_pipeline(platform, cache, logger, *, criteria, **kwargs):
		seen.append(criteria.query)
		return SimpleNamespace(items=[])

	execute_candidate_search(
		MagicMock(), MagicMock(), Logger("error"), {"query": None}, pipeline=fake_pipeline
	)
	execute_candidate_search(MagicMock(), MagicMock(), Logger("error"), {}, pipeline=fake_pipeline)

	assert seen == ["", ""]


def test_job_search_action_reports_invalid_param_for_non_string_query(tmp_path) -> None:
	plan = WorkflowPlan(
		role="candidate",
		platform="zhipin",
		goal="job_search",
		inputs={"query": False},
		requested_steps=("candidate_search",),
		mode="headless",
	)
	context = ActionContext(data_dir=tmp_path, platform="zhipin", role="candidate", logger=Logger("error"))

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, {"candidate_search": _candidate_search})
		run = runner.run(plan, context, run_id="run-2009")

	assert run["status"] == "failed"
	assert run["error"]["code"] == "INVALID_PARAM"
	assert "query 必须是字符串" in run["error"]["message"]
