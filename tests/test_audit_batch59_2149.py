"""审计批次五十九：空 requested_steps 的 resume 不得零步写 completed（Issue #2149）。

读取边界会把历史断点缺失的 ``requested_steps`` 归一成空元组（#2033）。旧实现
零步执行后无条件落 ``completed``，步骤行仍是 ``pending``：下一次恢复撞上
#2118 的「completed 与未完成步骤自相矛盾」检查，该 run 永久不可恢复。
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext
from boss_agent_cli.wizard.models import StepResult, WorkflowPlan
from boss_agent_cli.wizard.runner import WorkflowActionError, WorkflowRunner
from boss_agent_cli.wizard.store import WorkflowStore

_RUN_ID = "run-2149"


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


def _step_calls(**overrides):
	"""记录调用次数的离线动作；默认成功。"""
	calls: list[str] = []

	def action(context, inputs, prior):
		calls.append("call")
		return StepResult({"done": True}, **overrides)

	return action, calls


def _rewrite_params(data_dir, run_id: str, params: dict) -> None:
	with sqlite3.connect(data_dir / "cache" / "boss_agent.db") as conn:
		conn.execute(
			"UPDATE workflow_runs SET status = 'running', params = ? WHERE run_id = ?",
			(json.dumps(params), run_id),
		)


def test_empty_plan_is_rejected_before_writing_completed(tmp_path, monkeypatch) -> None:
	"""CLI 恢复空计划时应报告断点损坏，并提示重新创建任务。"""
	action_a, calls_a = _step_calls()
	action_b, calls_b = _step_calls()
	monkeypatch.setattr("boss_agent_cli.commands.wizard.DEFAULT_ACTIONS", {"a": action_a, "b": action_b})

	with WorkflowStore(tmp_path) as store:
		store.create(_RUN_ID, _plan("a", "b"))
		_rewrite_params(tmp_path, _RUN_ID, {"inputs": {}, "requested_steps": None})

	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "wizard", "--resume", _RUN_ID])

	assert result.exit_code == 1, result.stdout
	error = json.loads(result.stdout)["error"]
	assert error["code"] == "CACHE_RECORD_INVALID"
	assert error["recoverable"] is True
	assert "wizard --input-json" in error["recovery_action"]
	assert calls_a == [] and calls_b == []
	with WorkflowStore(tmp_path) as store:
		checkpoint = store.get_checkpoint(_RUN_ID)
		assert checkpoint["status"] == "running"
		assert [step["status"] for step in checkpoint["steps"]] == ["pending", "pending"]


def test_new_run_with_empty_plan_is_rejected(tmp_path) -> None:
	"""直接 API 传入空计划同样拒绝，避免创建永远读不出的 run 行。"""
	action, calls = _step_calls()

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, {"a": action})

		with pytest.raises(WorkflowActionError) as excinfo:
			runner.run(_plan(), _context(tmp_path), run_id=_RUN_ID)

		assert excinfo.value.code == "CACHE_RECORD_INVALID"
		assert calls == []
		assert store.get(_RUN_ID) is None


def test_nonempty_plan_still_completes(tmp_path) -> None:
	"""回归：正常非空计划照常执行并落 completed。"""
	action, calls = _step_calls()

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, {"a": action})
		run = runner.run(_plan("a"), _context(tmp_path), run_id=_RUN_ID)

		assert calls == ["call"]
		assert run["status"] == "completed"
		assert [step["status"] for step in store.get_checkpoint(_RUN_ID)["steps"]] == ["completed"]
