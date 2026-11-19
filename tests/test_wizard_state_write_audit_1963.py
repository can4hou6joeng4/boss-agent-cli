"""wizard 断点落盘失败与记录缺失的边界（Issue #1963–#1965），以及时间戳列漂移。"""

from __future__ import annotations

import pytest

from boss_agent_cli.api.client import PlatformRiskError
from boss_agent_cli.cache.store import CacheRecordInvalid
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext
from boss_agent_cli.wizard.models import StepResult, WorkflowPlan, WorkflowStatus
from boss_agent_cli.wizard.runner import WorkflowActionError, WorkflowRunner
from boss_agent_cli.wizard.store import WorkflowStore

_RUN_ID = "run-1963"


class _AccountRisk(PlatformRiskError):
	code = "ACCOUNT_RISK"

	@property
	def recoverable(self) -> bool:
		return False

	@property
	def recovery_action(self) -> str:
		return "先在官方页面核对账号状态；不要重试"


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


# ── Issue #1963：断点落盘失败不得掩盖风控 / 预算停止码 ───────────────


def _hold_write_lock(store: WorkflowStore):
	"""另开连接持有写锁，模拟其它 boss 命令正在写缓存库。"""
	lock_path = store.cache._conn.execute("PRAGMA database_list").fetchone()[2]
	import sqlite3

	lock = sqlite3.connect(lock_path)
	lock.execute("BEGIN IMMEDIATE")
	store.cache._conn.execute("PRAGMA busy_timeout = 200")
	return lock


def test_risk_error_survives_checkpoint_write_failure(tmp_path) -> None:
	holder: list[object] = []

	def risky(context, inputs, prior):
		holder.append(_hold_write_lock(store_ref[0]))
		raise _AccountRisk("账号存在风险")

	store_ref: list[WorkflowStore] = []
	with WorkflowStore(tmp_path) as store:
		store_ref.append(store)
		runner = WorkflowRunner(store, {"recruiter_reply": risky})
		try:
			with pytest.raises(WorkflowActionError) as caught:
				runner.run(_plan("recruiter_reply"), _context(tmp_path), run_id=_RUN_ID)
		finally:
			for lock in holder:
				lock.rollback()  # type: ignore[attr-defined]
				lock.close()  # type: ignore[attr-defined]

	assert caught.value.code == "ACCOUNT_RISK"
	assert caught.value.replay_safe is False
	assert caught.value.recoverable is False
	assert "断点写入失败" in str(caught.value)


def test_other_failure_reports_local_state_write_failed(tmp_path) -> None:
	holder: list[object] = []

	def plain(context, inputs, prior):
		holder.append(_hold_write_lock(store_ref[0]))
		raise WorkflowActionError("NETWORK_ERROR", "暂时失败", recoverable=True)

	store_ref: list[WorkflowStore] = []
	with WorkflowStore(tmp_path) as store:
		store_ref.append(store)
		runner = WorkflowRunner(store, {"recruiter_reply": plain})
		try:
			with pytest.raises(WorkflowActionError) as caught:
				runner.run(_plan("recruiter_reply"), _context(tmp_path), run_id=_RUN_ID)
		finally:
			for lock in holder:
				lock.rollback()  # type: ignore[attr-defined]
				lock.close()  # type: ignore[attr-defined]

	assert caught.value.code == "LOCAL_STATE_WRITE_FAILED"
	assert caught.value.replay_safe is False
	assert caught.value.details["original_code"] == "NETWORK_ERROR"


def test_completed_run_state_write_failure_is_not_silent(tmp_path) -> None:
	holder: list[object] = []

	def ok(context, inputs, prior):
		holder.append(_hold_write_lock(store_ref[0]))
		return StepResult(status=WorkflowStatus.COMPLETED, data={"done": True})

	store_ref: list[WorkflowStore] = []
	with WorkflowStore(tmp_path) as store:
		store_ref.append(store)
		runner = WorkflowRunner(store, {"recruiter_reply": ok})
		try:
			with pytest.raises(WorkflowActionError) as caught:
				runner.run(_plan("recruiter_reply"), _context(tmp_path), run_id=_RUN_ID)
		finally:
			for lock in holder:
				lock.rollback()  # type: ignore[attr-defined]
				lock.close()  # type: ignore[attr-defined]

	assert caught.value.code == "LOCAL_STATE_WRITE_FAILED"
	assert caught.value.replay_safe is False


# ── Issue #1965：步骤记录缺失按记录损坏上报 ─────────────────────────


def test_missing_step_row_maps_to_cache_record_invalid(tmp_path) -> None:
	def write(context, inputs, prior):
		context.workflow_control.begin_write("boss hr chatmsg 42")

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, {"recruiter_reply": write})
		store.create(_RUN_ID, _plan("recruiter_reply"))
		conn = store.cache._conn
		conn.execute("DELETE FROM workflow_steps WHERE run_id = ?", (_RUN_ID,))
		conn.commit()

		with pytest.raises(WorkflowActionError) as caught:
			runner.run(_plan("recruiter_reply"), _context(tmp_path), run_id=_RUN_ID)

	assert caught.value.code == "CACHE_RECORD_INVALID"
	assert caught.value.replay_safe is False


# ── Issue #1964：时间戳列漂移成 BLOB / 文本按记录损坏拒绝 ────────────


def _drift_workflow_runs(store: WorkflowStore, column: str, value: object) -> None:
	conn = store.cache._conn
	conn.execute(f"UPDATE workflow_runs SET {column} = ? WHERE run_id = ?", (value, _RUN_ID))
	conn.commit()


@pytest.mark.parametrize("column", ["created_at", "updated_at"])
@pytest.mark.parametrize("value", [b"\xff\xfe", "yesterday"])
def test_workflow_run_timestamp_drift_is_rejected(tmp_path, column: str, value: object) -> None:
	with WorkflowStore(tmp_path) as store:
		store.create(_RUN_ID, _plan("recruiter_reply"))
		_drift_workflow_runs(store, column, value)

		with pytest.raises(CacheRecordInvalid) as caught:
			store.get_checkpoint(_RUN_ID)

	assert column in str(caught.value)


@pytest.mark.parametrize("column", ["started_at", "finished_at"])
def test_workflow_step_timestamp_drift_is_rejected(tmp_path, column: str) -> None:
	with WorkflowStore(tmp_path) as store:
		store.create(_RUN_ID, _plan("recruiter_reply"))
		conn = store.cache._conn
		conn.execute(
			f"UPDATE workflow_steps SET {column} = ? WHERE run_id = ?", (b"\x00\x01", _RUN_ID)
		)
		conn.commit()

		with pytest.raises(CacheRecordInvalid):
			store.get_checkpoint(_RUN_ID)


def test_saved_search_timestamp_drift_is_reported_as_corrupt(tmp_path) -> None:
	with WorkflowStore(tmp_path) as store:
		store.cache.save_saved_search("预设", {"query": "python"})
		conn = store.cache._conn
		conn.execute("UPDATE saved_searches SET created_at = ?", (b"\xff",))
		conn.commit()

		items, corrupt = store.cache.list_saved_searches_with_corrupt()

	assert items == []
	assert corrupt == ["预设"]
