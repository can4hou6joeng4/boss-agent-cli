"""wizard 审计回归：中断保留未确认写标记、空步骤断点拒绝恢复。

- Ctrl-C 发生在写请求发出之后时，``begin_write`` 已落盘的
  ``ACTION_UNCONFIRMED``（``replay_safe=false``）不能被 ``WORKFLOW_STOPPED``
  覆盖，否则 ``--resume`` 会重放一个结果未知的写操作（Issue #1478）。
- ``params.requested_steps == []`` 的断点恢复后不会执行任何步骤，却会被标成
  ``completed``；读取边界必须按记录损坏拒绝（Issue #1483）。
"""

from __future__ import annotations

import json
import shlex
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheRecordInvalid
from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext
from boss_agent_cli.wizard.models import WorkflowPlan
from boss_agent_cli.wizard.runner import WorkflowRunner
from boss_agent_cli.wizard.store import WorkflowStore

_ENVELOPE_KEYS = {"ok", "schema_version", "command", "data", "pagination", "error", "hints"}
_CHECK_COMMAND = "boss hr chatmsg 42"
_RUN_ID = "run-interrupt"


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


def _corrupt_step_error(data_dir: Path, raw: str) -> None:
	with sqlite3.connect(data_dir / "cache" / "boss_agent.db") as conn:
		conn.execute(
			"UPDATE workflow_steps SET error = ? WHERE run_id = ? AND step_name = ?",
			(raw, _RUN_ID, "recruiter_reply"),
		)


# ── Issue #1478：Ctrl-C 不得覆盖未确认写标记 ─────────────────────────


def test_ctrl_c_after_write_dispatch_preserves_unconfirmed_marker(tmp_path):
	"""写请求发出后按 Ctrl-C：步骤落成 failed 并保留核对命令，恢复不重放。"""
	calls = []

	def write_then_interrupt(context, inputs, prior):
		calls.append(dict(inputs))
		assert context.workflow_control is not None
		context.workflow_control.begin_write(_CHECK_COMMAND)
		raise KeyboardInterrupt

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, {"recruiter_reply": write_then_interrupt})
		run = runner.run(_plan("recruiter_reply"), _context(tmp_path), run_id=_RUN_ID)

		assert run["status"] == "failed"
		assert run["current_step"] == "recruiter_reply"
		assert run["error"]["code"] == "ACTION_UNCONFIRMED"
		assert run["error"]["replay_safe"] is False
		assert run["error"]["recoverable"] is False
		assert _CHECK_COMMAND in run["error"]["recovery_action"]
		assert "中断" in run["error"]["message"]
		[step] = run["steps"]
		assert step["status"] == "failed"
		assert step["error"]["code"] == "ACTION_UNCONFIRMED"
		assert step["error"]["replay_safe"] is False
		assert _CHECK_COMMAND in step["error"]["recovery_action"]

		# 恢复同一 run_id：未确认的写操作不得重放，原失败状态原样返回。
		again = runner.run(_plan("recruiter_reply"), _context(tmp_path), run_id=_RUN_ID)

	assert calls == [{}]
	assert again["status"] == "failed"
	assert again["error"]["code"] == "ACTION_UNCONFIRMED"
	assert again["error"]["replay_safe"] is False


def test_ctrl_c_without_write_marker_still_reports_workflow_stopped(tmp_path):
	"""没有写请求在途的记录：保持原有中断语义（stopped），不误报结果未知。"""
	calls = []

	def read_then_interrupt(context, inputs, prior):
		calls.append(1)
		raise KeyboardInterrupt

	with WorkflowStore(tmp_path) as store:
		run = WorkflowRunner(store, {"recruiter_chat": read_then_interrupt}).run(
			_plan("recruiter_chat"), _context(tmp_path), run_id="run-read"
		)

	assert calls == [1]
	assert run["status"] == "stopped"
	assert run["error"]["code"] == "WORKFLOW_STOPPED"
	[step] = run["steps"]
	assert step["status"] == "stopped"
	assert step["error"]["code"] == "WORKFLOW_STOPPED"


@pytest.mark.parametrize(
	("stored_error", "keeps_check_command"),
	[
		# error 不是 JSON object：读取边界会拒绝整行，必须按结果未知兜底。
		(json.dumps(["not-an-object"]), False),
		# replay_safe 类型漂移成字符串：不能按「可重放」放行。
		(
			json.dumps(
				{
					"code": "ACTION_UNCONFIRMED",
					"message": "写请求在途，结果尚未确认",
					"recoverable": False,
					"recovery_action": _CHECK_COMMAND,
					"replay_safe": "false",
				}
			),
			True,
		),
	],
	ids=["non-object", "replay-safe-string"],
)
def test_ctrl_c_with_malformed_write_marker_is_fail_closed(tmp_path, stored_error, keeps_check_command):
	"""标记损坏或类型漂移时按「结果未知」处理，不能因读不出来就放行重放。"""
	def write_corrupt_then_interrupt(context, inputs, prior):
		assert context.workflow_control is not None
		context.workflow_control.begin_write(_CHECK_COMMAND)
		_corrupt_step_error(tmp_path, stored_error)
		raise KeyboardInterrupt

	with WorkflowStore(tmp_path) as store:
		run = WorkflowRunner(store, {"recruiter_reply": write_corrupt_then_interrupt}).run(
			_plan("recruiter_reply"), _context(tmp_path), run_id=_RUN_ID
		)

	assert run["status"] == "failed"
	assert run["error"]["code"] == "ACTION_UNCONFIRMED"
	assert run["error"]["replay_safe"] is False
	assert run["error"]["recoverable"] is False
	if keeps_check_command:
		assert _CHECK_COMMAND in run["error"]["recovery_action"]
	assert "中断" in run["error"]["message"]
	[step] = run["steps"]
	assert step["status"] == "failed"
	assert step["error"]["code"] == "ACTION_UNCONFIRMED"
	assert step["error"]["replay_safe"] is False


def test_cli_resume_after_ctrl_c_reports_unconfirmed_without_resume_hint(tmp_path):
	"""CLI 出口：恢复被中断的写操作返回 ACTION_UNCONFIRMED，状态提示不含 --resume。"""
	def write_then_interrupt(context, inputs, prior):
		assert context.workflow_control is not None
		context.workflow_control.begin_write(_CHECK_COMMAND)
		raise KeyboardInterrupt

	with WorkflowStore(tmp_path) as store:
		WorkflowRunner(store, {"recruiter_reply": write_then_interrupt}).run(
			_plan("recruiter_reply"), _context(tmp_path), run_id=_RUN_ID
		)

	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "wizard", "--resume", _RUN_ID]
	)
	assert result.exit_code == 1, result.output
	envelope = json.loads(result.stdout)
	assert set(envelope) == _ENVELOPE_KEYS
	assert envelope["ok"] is False
	assert envelope["error"]["code"] == "ACTION_UNCONFIRMED"
	assert envelope["error"]["recoverable"] is False
	assert _CHECK_COMMAND in envelope["error"]["recovery_action"]
	run = envelope["error"]["details"]["run"]
	assert run["status"] == "failed"
	assert run["steps"][0]["error"]["replay_safe"] is False

	status = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "wizard", "--status", _RUN_ID]
	)
	assert status.exit_code == 0, status.output
	actions = [shlex.split(action) for action in json.loads(status.stdout)["hints"]["next_actions"]]
	assert actions and all("--resume" not in action for action in actions)


# ── Issue #1483：空 requested_steps 断点不得恢复成 completed ──────────

_EMPTY_STEPS_RUN_ID = "run-empty-steps"


def _seed_empty_steps_run(data_dir: Path) -> None:
	plan = WorkflowPlan(
		role="candidate",
		platform="zhipin",
		goal="shortlist",
		inputs={},
		requested_steps=(),
		mode="headless",
	)
	with WorkflowStore(data_dir) as store:
		store.create(_EMPTY_STEPS_RUN_ID, plan)


def test_empty_requested_steps_record_is_rejected_at_read_boundary(tmp_path):
	_seed_empty_steps_run(tmp_path)

	with WorkflowStore(tmp_path) as store:
		with pytest.raises(CacheRecordInvalid) as exc_info:
			store.get_checkpoint(_EMPTY_STEPS_RUN_ID)
		assert store.list_recent() == []
		assert store.corrupt_run_ids == [_EMPTY_STEPS_RUN_ID]

	invalid = exc_info.value
	assert invalid.kind == "workflow_run"
	assert invalid.column == "params.requested_steps"
	assert "不能是空数组" in str(invalid)


def test_cli_resume_of_empty_requested_steps_is_rejected_without_running(tmp_path):
	_seed_empty_steps_run(tmp_path)

	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "wizard", "--resume", _EMPTY_STEPS_RUN_ID]
	)
	assert result.exit_code == 1, result.output
	envelope = json.loads(result.stdout)
	assert set(envelope) == _ENVELOPE_KEYS
	assert envelope["ok"] is False
	assert envelope["error"]["code"] == "CACHE_RECORD_INVALID"
	assert envelope["error"]["recoverable"] is True
	assert "不能是空数组" in envelope["error"]["message"]
	# 本地记录损坏：只能重新创建任务，不能照旧 resume。
	assert "重新创建任务" in envelope["error"]["recovery_action"]
	assert "--resume" not in envelope["error"]["recovery_action"]

	# 断点没有被改写：run 仍是 pending，步骤也没有被标记 completed。
	with sqlite3.connect(tmp_path / "cache" / "boss_agent.db") as conn:
		status, params = conn.execute(
			"SELECT status, params FROM workflow_runs WHERE run_id = ?", (_EMPTY_STEPS_RUN_ID,)
		).fetchone()
		step_statuses = [
			row[0]
			for row in conn.execute(
				"SELECT status FROM workflow_steps WHERE run_id = ?", (_EMPTY_STEPS_RUN_ID,)
			)
		]
	assert status == "pending"
	assert json.loads(params)["requested_steps"] == []
	assert step_statuses == []
