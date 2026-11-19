"""审计批次：wizard 写前标记原子落盘、completed 缺行拒绝、last_result 保留（Issue #2117–#2119）。"""

from __future__ import annotations

import pytest

from boss_agent_cli.cache.store import CacheBusyError
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext
from boss_agent_cli.wizard.models import StepResult, WorkflowPlan, WorkflowStatus
from boss_agent_cli.wizard.runner import WorkflowActionError, WorkflowRunner
from boss_agent_cli.wizard.store import WorkflowStore

_RUN_ID = "run-2117"
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


# ── #2117：声明失败时写前标记与步骤结果一次落盘 ─────────────────────


def test_resume_does_not_replay_when_update_run_fails_after_declared_failure(
	tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
	calls: list[int] = []

	def write_then_fail(context, inputs, prior):
		calls.append(1)
		context.workflow_control.begin_write(_CHECK_COMMAND)
		return StepResult({"sent": False}, status=WorkflowStatus.FAILED)

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, {"recruiter_reply": write_then_fail})
		real_update_run = store.update_run
		failed_once = {"done": False}

		def flaky_update_run(run_id, **kwargs):
			# action 返回后的那次 run 结算携带 last_result；让它失败以暴露窗口。
			if not failed_once["done"] and kwargs.get("status") == "running" and "last_result" in kwargs:
				failed_once["done"] = True
				raise CacheBusyError("database is locked")
			return real_update_run(run_id, **kwargs)

		monkeypatch.setattr(store, "update_run", flaky_update_run)

		with pytest.raises(WorkflowActionError) as excinfo:
			runner.run(_plan("recruiter_reply"), _context(tmp_path), run_id=_RUN_ID)
		assert excinfo.value.code == "LOCAL_STATE_WRITE_FAILED"
		assert excinfo.value.replay_safe is False

		# 写前标记必须已经在步骤记录里，而不是等结算补写。
		step = store.cache.list_workflow_steps(_RUN_ID)[0]
		assert step["error"]["replay_safe"] is False

		monkeypatch.setattr(store, "update_run", real_update_run)
		resumed = runner.run(_plan("recruiter_reply"), _context(tmp_path), run_id=_RUN_ID)

		assert calls == [1], "--resume 不得重放结果未知的写请求"
		assert resumed["status"] == "failed"


def test_declared_failure_without_write_marker_keeps_step_error_clean(tmp_path) -> None:
	# 护栏：没有 begin_write 的声明失败仍落成普通失败，步骤不带伪造标记。
	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(
			store,
			{"recruiter_reply": lambda context, inputs, prior: StepResult({}, status=WorkflowStatus.FAILED)},
		)
		run = runner.run(_plan("recruiter_reply"), _context(tmp_path), run_id=_RUN_ID)

		step = store.cache.list_workflow_steps(_RUN_ID)[0]
		assert step["status"] == "failed"
		assert step["error"]["code"] == "INTERNAL_ERROR"
		assert step["error"].get("replay_safe") is not False
		assert run["status"] == "failed"


# ── #2118：completed 终态必须对照 requested_steps ────────────────────


def test_completed_run_with_missing_step_row_is_rejected(tmp_path) -> None:
	with WorkflowStore(tmp_path) as store:
		plan = _plan("a", "b")
		store.create(_RUN_ID, plan)
		store.update_step(_RUN_ID, "a", status="completed", result={})
		store.update_step(_RUN_ID, "b", status="completed", result={})
		store.update_run(_RUN_ID, status="completed", current_step=None)
		store.cache._conn.execute(
			"DELETE FROM workflow_steps WHERE run_id = ? AND step_name = 'b'", (_RUN_ID,)
		)
		store.cache._conn.commit()
		runner = WorkflowRunner(store, {})

		with pytest.raises(WorkflowActionError) as excinfo:
			runner.run(plan, _context(tmp_path), run_id=_RUN_ID)

		assert excinfo.value.code == "CACHE_RECORD_INVALID"


def test_completed_run_with_all_steps_still_returns_checkpoint(tmp_path) -> None:
	with WorkflowStore(tmp_path) as store:
		plan = _plan("a", "b")
		store.create(_RUN_ID, plan)
		store.update_step(_RUN_ID, "a", status="completed", result={})
		store.update_step(_RUN_ID, "b", status="completed", result={})
		store.update_run(_RUN_ID, status="completed", current_step=None)
		runner = WorkflowRunner(store, {})

		run = runner.run(plan, _context(tmp_path), run_id=_RUN_ID)

		assert run["status"] == "completed"


# ── #2119：中断写结算保留 run.last_result ───────────────────────────


def test_settle_interrupted_writes_keeps_last_result(tmp_path) -> None:
	with WorkflowStore(tmp_path) as store:
		plan = _plan("a")
		store.create(_RUN_ID, plan)
		store.update_run(
			_RUN_ID,
			status="running",
			current_step="a",
			last_result={"data": {"jobs": [1, 2]}},
		)
		store.update_step(_RUN_ID, "a", status="running", result={"inner_run_id": "crawl-1"})
		store.set_step_error(
			_RUN_ID,
			"a",
			{
				"code": "ACTION_UNCONFIRMED",
				"message": "写请求在途，结果尚未确认",
				"recoverable": False,
				"recovery_action": _CHECK_COMMAND,
				"replay_safe": False,
			},
		)
		runner = WorkflowRunner(store, {})

		run = runner.run(plan, _context(tmp_path), run_id=_RUN_ID)

		assert run["status"] == "failed"
		assert run["last_result"] == {"data": {"jobs": [1, 2]}}
