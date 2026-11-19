"""wizard 断点矛盾、在途标记、输入深度与计划比较（Issue #2183–#2188）。

- 已完成结果挂在 pending 步骤上：拒绝恢复，不重放；
- 非终态结果带写前标记中断：与 running 同口径结算，阻断重放；
- 请求步骤漂移成子集：第一次恢复就拒绝，不把漏跑的 run 写成 completed；
- 深嵌套 --input-json：返回 INVALID_PARAM 信封，不再裸 RecursionError；
- 失败 run 的顶层 error 为空对象：出口回退到断点步骤的错误；
- 计划比较区分布尔与整数，同时保留 NaN 兼容。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing, suppress
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheRecordInvalid
from boss_agent_cli.main import cli
from boss_agent_cli.wizard.models import StepResult, WorkflowPlan, WorkflowStatus
from boss_agent_cli.wizard.runner import WorkflowActionError, WorkflowRunner
from boss_agent_cli.wizard.store import WorkflowStore


def _plan(*steps: str, inputs: dict[str, object] | None = None) -> WorkflowPlan:
	return WorkflowPlan(
		"candidate", "zhipin", "job_search", {} if inputs is None else inputs, steps, "headless"
	)


def _invoke(root, *args: str, actions=None):
	with patch.dict("boss_agent_cli.commands.wizard.DEFAULT_ACTIONS", actions or {}, clear=True):
		return CliRunner().invoke(cli, ["--json", "--data-dir", str(root), "wizard", *args])


def _sql(root, statement: str, args: tuple[object, ...] = ()) -> None:
	with closing(sqlite3.connect(root / "cache" / "boss_agent.db")) as db, db:
		db.execute(statement, args)


class _Interrupted(Exception):
	pass


def _interrupt_after_step(event, *_):
	if event == "step_finished":
		raise _Interrupted


# ── Issue #2183：pending 步骤不能带 completed 结果 ──────────────────


def test_completed_result_on_pending_step_is_rejected(tmp_path) -> None:
	calls: list[str] = []

	def greet(*_):
		calls.append("greet")
		return StepResult({"sent": True})

	with WorkflowStore(tmp_path) as store, suppress(_Interrupted):
		WorkflowRunner(store, {"candidate_greet": greet}).run(
			_plan("candidate_greet"), object(), run_id="r", on_event=_interrupt_after_step
		)
	_sql(tmp_path, "UPDATE workflow_steps SET status='pending' WHERE run_id='r'")

	payload = json.loads(_invoke(tmp_path, "--resume", "r", actions={"candidate_greet": greet}).stdout)

	assert payload["error"]["code"] == "CACHE_RECORD_INVALID"
	assert calls == ["greet"]


def test_get_checkpoint_rejects_completed_result_on_pending_step(tmp_path) -> None:
	with WorkflowStore(tmp_path) as store:
		store.create("r", _plan("candidate_greet"))
		store.update_step("r", "candidate_greet", status="pending", result=StepResult({}).to_dict())

		with pytest.raises(CacheRecordInvalid, match="pending"):
			store.get_checkpoint("r")


# ── Issue #2184：pending + 写前标记阻断重放 ─────────────────────────


def test_pending_step_with_write_marker_blocks_replay(tmp_path) -> None:
	calls: list[str] = []

	def write(ctx, *_):
		ctx.control.begin_write("offline check")
		calls.append("write")
		return StepResult({}, status=WorkflowStatus.PENDING)

	context = SimpleNamespace(with_workflow_control=lambda control: SimpleNamespace(control=control))
	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, {"write": write})
		with suppress(_Interrupted):
			runner.run(_plan("write"), context, run_id="r", on_event=_interrupt_after_step)
		second = runner.run(_plan("write"), context, run_id="r")

	assert calls == ["write"]
	assert second["status"] == "failed"
	assert second["steps"][0]["status"] == "failed"
	assert second["steps"][0]["error"]["replay_safe"] is False


# ── Issue #2185：请求步骤与步骤行必须一致 ──────────────────────────


def test_requested_steps_drift_is_rejected_before_completing(tmp_path) -> None:
	with WorkflowStore(tmp_path) as store:
		store.create("r", _plan("auth_status", "candidate_search"))
		store.update_step("r", "auth_status", status="completed", result=StepResult({}).to_dict())
	_sql(
		tmp_path,
		"UPDATE workflow_runs SET params=? WHERE run_id='r'",
		(json.dumps({"inputs": {}, "requested_steps": ["auth_status"]}),),
	)

	payload = json.loads(_invoke(tmp_path, "--resume", "r").stdout)

	assert payload["error"]["code"] == "CACHE_RECORD_INVALID"
	with WorkflowStore(tmp_path) as store:
		run = store.get_checkpoint("r")
	assert run["status"] != "completed"
	assert run["steps"][1]["status"] == "pending"


# ── Issue #2186：深嵌套 input-json ─────────────────────────────────


def test_deep_input_json_returns_invalid_param_envelope(tmp_path) -> None:
	raw = (
		'{"role":"candidate","platform":"zhipin","goal":"job_search","inputs":{"x":'
		+ "[" * 1500 + "0" + "]" * 1500 + "}}"
	)

	result = _invoke(tmp_path, "--input-json", raw)
	payload = json.loads(result.stdout)

	assert result.exit_code == 1
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "嵌套超过" in payload["error"]["message"]


# ── Issue #2187：失败 run 的空 error 回退到步骤错误 ────────────────


def test_empty_run_error_falls_back_to_step_error(tmp_path) -> None:
	def uncertain(*_):
		raise WorkflowActionError(
			"ACTION_UNCONFIRMED", "offline", recovery_action="check first", replay_safe=False
		)

	with WorkflowStore(tmp_path) as store:
		WorkflowRunner(store, {"write": uncertain}).run(_plan("write"), object(), run_id="r")
	_sql(tmp_path, "UPDATE workflow_runs SET error='{}' WHERE run_id='r'")

	payload = json.loads(_invoke(tmp_path, "--resume", "r").stdout)

	assert payload["error"]["code"] == "ACTION_UNCONFIRMED"
	assert payload["error"]["recovery_action"] == "check first"


# ── Issue #2188：计划比较区分布尔与整数 ────────────────────────────


def test_plan_comparison_distinguishes_bool_and_int(tmp_path) -> None:
	def second(_, inputs, __):
		if inputs["flag"] is False:
			raise WorkflowActionError("NETWORK_ERROR", "offline retry", recoverable=True)
		return StepResult({"flag": inputs["flag"]})

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, {"first": lambda *_: StepResult({}), "second": second})
		first = runner.run(_plan("first", "second", inputs={"flag": False}), object(), run_id="r")
		assert first["status"] == "failed"
		with pytest.raises(WorkflowActionError) as raised:
			runner.run(_plan("first", "second", inputs={"flag": 0}), object(), run_id="r")

	assert raised.value.code == "WORKFLOW_PLAN_MISMATCH"


def test_plans_equal_keeps_nan_compatibility() -> None:
	assert WorkflowRunner._plans_equal({"x": float("nan")}, {"x": float("nan")}) is True
