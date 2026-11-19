"""wizard 恢复路径的审计回归（Issue #2033–#2035）。"""

from __future__ import annotations

import json
import sqlite3

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.wizard.models import StepResult, WorkflowPlan
from boss_agent_cli.wizard.runner import WorkflowActionError, WorkflowRunner
from boss_agent_cli.wizard.store import WorkflowStore


def _plan(*steps: str, inputs: dict | None = None) -> WorkflowPlan:
	return WorkflowPlan(
		role="candidate",
		platform="zhipin",
		goal="offline",
		inputs=inputs or {},
		requested_steps=steps,
		mode="headless",
	)


def _db_path(tmp_path):
	return tmp_path / "cache" / "boss_agent.db"


def test_plan_match_normalizes_null_inputs_and_omitted_steps() -> None:
	"""读取边界接受 `inputs: null` 与省略 requested_steps；比较必须同口径归一
	（Issue #2033）。"""
	plan = _plan()
	for params in ({"inputs": None}, {"inputs": {}, "requested_steps": None}):
		existing = {
			"role": "candidate",
			"platform": "zhipin",
			"goal": "offline",
			"mode": "headless",
			"params": params,
		}
		WorkflowRunner._assert_plan_matches(existing, plan)

	with pytest.raises(WorkflowActionError) as excinfo:
		WorkflowRunner._assert_plan_matches(
			{
				"role": "candidate",
				"platform": "zhipin",
				"goal": "offline",
				"mode": "headless",
				"params": {"inputs": {"city": "北京"}},
			},
			plan,
		)
	assert excinfo.value.code == "WORKFLOW_PLAN_MISMATCH"


def test_runner_refuses_to_dispatch_when_step_record_missing(tmp_path) -> None:
	"""步骤行缺失时动作不得派发：无记录的动作既无法记账，也无从判断重放
	（Issue #2034）。"""
	calls: list[str] = []

	def action(context, inputs, prior):
		calls.append("called")
		return StepResult({"ok": True})

	plan = _plan("s1")
	with WorkflowStore(tmp_path) as store:
		store.create("wr-missing-step", plan)
		conn = sqlite3.connect(_db_path(tmp_path))
		try:
			conn.execute("DELETE FROM workflow_steps WHERE run_id = 'wr-missing-step'")
			conn.commit()
		finally:
			conn.close()
		with pytest.raises(WorkflowActionError) as excinfo:
			WorkflowRunner(store, {"s1": action}).run(plan, object(), run_id="wr-missing-step")

	assert calls == []
	assert excinfo.value.code == "LOCAL_STATE_WRITE_FAILED"
	assert (excinfo.value.details or {}).get("original_code") == "CACHE_RECORD_INVALID"


def test_stop_on_completed_corrupt_run_reports_finished_not_missing(tmp_path) -> None:
	"""记录存在但读不出、SQL 判定为 completed 时不能报「未找到」引导核对 ID
	（Issue #2035）。"""
	plan = _plan("s1")
	with WorkflowStore(tmp_path) as store:
		store.create("wr-done-corrupt", plan)
		store._cache._conn.execute(
			"UPDATE workflow_runs SET status = 'completed', params = ? WHERE run_id = ?",
			(json.dumps({"inputs": [1], "requested_steps": ["s1"]}), "wr-done-corrupt"),
		)
		store._cache._conn.commit()

	result = CliRunner().invoke(
		cli, ["--json", "--data-dir", str(tmp_path), "wizard", "--stop", "wr-done-corrupt"]
	)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "WORKFLOW_ALREADY_FINISHED"
	assert "未找到" not in payload["error"]["message"]
	assert "核对 ID" not in (payload["error"]["recovery_action"] or "")
