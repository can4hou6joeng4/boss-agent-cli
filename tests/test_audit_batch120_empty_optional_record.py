"""审计批次 120：可空 JSON 列的读取只认 SQL NULL（Issue #2484）。

`_decode_optional_record` 原来用 `not raw` 判空，空串 / 空 BLOB / 空数组 / 0
都被解码成 `None`。`workflow_steps.error` 是在途写请求的唯一证据：漂移成空值时
`_persisted_write_marker` 读不到标记，恢复会把结果未知的写操作重放。

契约：
1. 只有 SQL NULL 表示「没有值」，其它漂移值走 `_decode_record` 报
   `CacheRecordInvalid`；
2. 写请求已发出后遇到空值标记，按 `ACTION_UNCONFIRMED` fail-closed，不重放；
3. 停止 / 展示路径既有的 `CacheRecordInvalid` 处理保持不变。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from boss_agent_cli.cache.store import CacheRecordInvalid
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext
from boss_agent_cli.wizard.models import WorkflowPlan
from boss_agent_cli.wizard.runner import WorkflowRunner
from boss_agent_cli.wizard.store import WorkflowStore

_CHECK_COMMAND = "boss hr chatmsg 42"
_RUN_ID = "run-empty-marker"
_DRIFT_IDS = ("empty-text", "empty-blob", "empty-array", "integer")
_DRIFT_VALUES = ("", b"", "[]", 0)


def _plan(*steps: str) -> WorkflowPlan:
	return WorkflowPlan(
		role="recruiter",
		platform="zhipin",
		goal="offline",
		inputs={},
		requested_steps=steps,
		mode="headless",
	)


def _context(data_dir: Path) -> ActionContext:
	return ActionContext(data_dir=data_dir, platform="zhipin", role="recruiter", logger=Logger("error"))


def _corrupt_step_error(data_dir: Path, raw: object) -> None:
	with sqlite3.connect(data_dir / "cache" / "boss_agent.db") as conn:
		conn.execute(
			"UPDATE workflow_steps SET error = ? WHERE run_id = ? AND step_name = ?",
			(raw, _RUN_ID, "recruiter_reply"),
		)


@pytest.mark.parametrize("stored_error", _DRIFT_VALUES, ids=_DRIFT_IDS)
def test_empty_write_marker_is_fail_closed_and_not_replayed(tmp_path, stored_error):
	"""写请求发出后标记漂移成空值：按结果未知结算，恢复不重放（Issue #2484）。"""
	calls: list[int] = []

	def write_then_corrupt(context, inputs, prior):
		calls.append(1)
		assert context.workflow_control is not None
		context.workflow_control.begin_write(_CHECK_COMMAND)
		_corrupt_step_error(tmp_path, stored_error)
		raise RuntimeError("boom after dispatch")

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, {"recruiter_reply": write_then_corrupt})
		run = runner.run(_plan("recruiter_reply"), _context(tmp_path), run_id=_RUN_ID)
		again = runner.run(_plan("recruiter_reply"), _context(tmp_path), run_id=_RUN_ID)

	assert calls == [1], "结果未知的写操作不能被重放"
	# 原始错误码保留（Issue #1604），但写标记是权威：不可重放、不可自动重试。
	assert run["status"] == "failed"
	assert run["error"]["code"] == "NETWORK_ERROR"
	assert run["error"]["replay_safe"] is False
	assert run["error"]["recoverable"] is False
	assert "写请求在途" in run["error"]["message"]
	assert again["error"]["replay_safe"] is False


@pytest.mark.parametrize("stored_error", _DRIFT_VALUES, ids=_DRIFT_IDS)
def test_empty_optional_column_rejected_at_read_boundary(tmp_path, stored_error):
	"""空值不是「没有值」：读取边界按记录损坏拒绝，不能静默解成 None。"""
	with WorkflowStore(tmp_path) as store:
		store.create(_RUN_ID, _plan("recruiter_reply"))
		store.update_step(_RUN_ID, "recruiter_reply", status="running")
		_corrupt_step_error(tmp_path, stored_error)

		with pytest.raises(CacheRecordInvalid) as exc_info:
			store.get_checkpoint(_RUN_ID)

	assert exc_info.value.kind == "workflow_step"
	assert exc_info.value.column == "error"


def test_sql_null_still_decodes_to_absent(tmp_path):
	"""既有的 NULL 语义不变：没有写标记的 running 步骤照常返回 error=None。"""
	with WorkflowStore(tmp_path) as store:
		store.create(_RUN_ID, _plan("recruiter_reply"))
		store.update_step(_RUN_ID, "recruiter_reply", status="running")
		run = store.get_checkpoint(_RUN_ID)

	assert run is not None
	[step] = run["steps"]
	assert step["error"] is None
	assert step["result"] is None


def test_empty_workflow_run_error_is_reported_as_corrupt(tmp_path):
	"""同一解码边界也服务 workflow_runs.error：空串按损坏上报，不伪装成「无错误」。"""
	with WorkflowStore(tmp_path) as store:
		store.create(_RUN_ID, _plan("recruiter_reply"))
		with sqlite3.connect(tmp_path / "cache" / "boss_agent.db") as conn:
			conn.execute("UPDATE workflow_runs SET error = '' WHERE run_id = ?", (_RUN_ID,))

		with pytest.raises(CacheRecordInvalid) as exc_info:
			store.get_checkpoint(_RUN_ID)

	assert exc_info.value.kind == "workflow_run"
	assert exc_info.value.column == "error"
