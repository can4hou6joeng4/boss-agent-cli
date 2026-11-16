"""断点状态漂移与搜索管线错误码的恢复语义。

- 步骤 / run 的 ``status`` 漂移出 ``WorkflowStatus`` 时，读取边界必须按记录
  损坏拒绝，不能失去已完成写步骤的去重（Issue #1382）。
- ``SearchPipelinePlatformError`` 的平台错误码必须保留，不能被兜成
  ``NETWORK_ERROR`` +「重试当前 run_id」；风控码保持终止语义（Issue #1384）。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheRecordInvalid
from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.search_filters import SearchPipelinePlatformError
from boss_agent_cli.wizard.actions import DEFAULT_ACTIONS, ActionContext
from boss_agent_cli.wizard.models import StepResult, WorkflowPlan
from boss_agent_cli.wizard.runner import WorkflowRunner
from boss_agent_cli.wizard.store import WorkflowStore

_ENVELOPE_KEYS = {"ok", "schema_version", "command", "data", "pagination", "error", "hints"}
_RUN_ID = "run-state"


def _seed_write_run(
	data_dir,
	*,
	step_status: str = "completed",
	run_status: str = "completed",
	replay_safe: bool | None = None,
) -> None:
	plan = WorkflowPlan(
		role="recruiter",
		platform="zhipin",
		goal="reply",
		inputs={"friend_id": 42, "message": "离线消息"},
		requested_steps=("recruiter_reply",),
		mode="headless",
	)
	step_error = None
	if replay_safe is not None:
		step_error = {
			"code": "ACTION_UNCONFIRMED" if replay_safe is False else "RATE_LIMITED",
			"message": "离线断点",
			"recoverable": False,
			"recovery_action": "先核对结果，不要自动重试",
			"replay_safe": replay_safe,
		}
	with WorkflowStore(data_dir) as store:
		store.create(_RUN_ID, plan)
		store.update_run(_RUN_ID, status=run_status, current_step="recruiter_reply")
		store.update_step(
			_RUN_ID,
			"recruiter_reply",
			status=step_status,
			result={"sent": True},
			error=step_error,
		)


def _drift_column(data_dir, table: str, column: str, value: Any) -> None:
	with sqlite3.connect(data_dir / "cache" / "boss_agent.db") as conn:
		conn.execute(f"UPDATE {table} SET {column} = ? WHERE run_id = ?", (value, _RUN_ID))


def _snapshot(data_dir):
	with sqlite3.connect(data_dir / "cache" / "boss_agent.db") as conn:
		return (
			conn.execute("SELECT * FROM workflow_runs WHERE run_id = ?", (_RUN_ID,)).fetchone(),
			conn.execute("SELECT * FROM workflow_steps WHERE run_id = ?", (_RUN_ID,)).fetchone(),
		)


def _invoke_resume(data_dir):
	return CliRunner().invoke(cli, ["--json", "--data-dir", str(data_dir), "wizard", "--resume", _RUN_ID])


def _track_replay(monkeypatch):
	replayed = []
	monkeypatch.setitem(
		DEFAULT_ACTIONS,
		"recruiter_reply",
		lambda *args: replayed.append(args) or StepResult({"sent": True}),
	)
	return replayed


def _assert_cache_record_invalid(stdout: str) -> None:
	envelope = json.loads(stdout)
	assert set(envelope) == _ENVELOPE_KEYS
	assert envelope["ok"] is False
	assert envelope["error"]["code"] == "CACHE_RECORD_INVALID"
	assert "status" in envelope["error"]["message"]


@pytest.mark.parametrize("status", ["Completed", "COMPLETED", "3"])
def test_resume_rejects_drifted_completed_step_status_before_dispatch(tmp_path, monkeypatch, status):
	"""已完成步骤的状态漂移后不能失去去重，把不可逆写操作再执行一次。"""
	_seed_write_run(tmp_path)
	_drift_column(tmp_path, "workflow_steps", "status", status)
	before = _snapshot(tmp_path)
	replayed = _track_replay(monkeypatch)

	result = _invoke_resume(tmp_path)

	assert result.exit_code == 1, result.output
	assert replayed == [], result.output
	_assert_cache_record_invalid(result.stdout)
	assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("status", ["Completed", "3"])
def test_resume_rejects_drifted_run_status_before_dispatch(tmp_path, monkeypatch, status):
	_seed_write_run(tmp_path)
	_drift_column(tmp_path, "workflow_runs", "status", status)
	before = _snapshot(tmp_path)
	replayed = _track_replay(monkeypatch)

	result = _invoke_resume(tmp_path)

	assert result.exit_code == 1, result.output
	assert replayed == [], result.output
	_assert_cache_record_invalid(result.stdout)
	assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("status", [None, 3, ["completed"], {"value": "completed"}])
def test_get_checkpoint_rejects_non_string_status(tmp_path, monkeypatch, status):
	"""NULL / 数字 / 容器状态（旧库或手改行）同样在读取边界拒绝。

	``workflow_steps.status`` 有 NOT NULL 约束，正常 SQLite 写不进 NULL；
	这里直接替换读取结果模拟遗留坏行，验证校验本身不依赖写入路径。
	"""
	_seed_write_run(tmp_path)
	with WorkflowStore(tmp_path) as store:
		monkeypatch.setattr(
			store.cache,
			"list_workflow_steps",
			lambda run_id: [
				{
					"step_name": "recruiter_reply",
					"ordinal": 0,
					"status": status,
					"result": {"sent": True},
					"error": None,
				}
			],
		)
		with pytest.raises(CacheRecordInvalid, match="status"):
			store.get_checkpoint(_RUN_ID)


@pytest.mark.parametrize("status", ["Completed", "Running"])
def test_resume_rejects_drifted_inflight_write_instead_of_rerunning(tmp_path, monkeypatch, status):
	"""``replay_safe: false`` 的在途步骤状态漂移后不能重跑。"""
	_seed_write_run(tmp_path, step_status="running", run_status="running", replay_safe=False)
	_drift_column(tmp_path, "workflow_steps", "status", status)
	before = _snapshot(tmp_path)
	replayed = _track_replay(monkeypatch)

	result = _invoke_resume(tmp_path)

	assert result.exit_code == 1, result.output
	assert replayed == [], result.output
	_assert_cache_record_invalid(result.stdout)
	assert _snapshot(tmp_path) == before


def test_list_recent_skips_runs_with_drifted_status(tmp_path):
	_seed_write_run(tmp_path)
	_drift_column(tmp_path, "workflow_steps", "status", "Completed")
	with WorkflowStore(tmp_path) as store:
		assert store.list_recent() == []
		assert store.corrupt_run_ids == [_RUN_ID]


class _StubPlatform:
	def __enter__(self) -> "_StubPlatform":
		return self

	def __exit__(self, exc_type, exc, tb) -> bool:
		return False


def _search_context(data_dir) -> ActionContext:
	return ActionContext(
		data_dir=data_dir,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
		candidate_factory=lambda *args, **kwargs: _StubPlatform(),
	)


def _run_search_failure(data_dir, monkeypatch, code: str) -> dict[str, Any]:
	plan = WorkflowPlan(
		role="candidate",
		platform="zhipin",
		goal="job_search",
		inputs={"query": "Python"},
		requested_steps=("candidate_search",),
		mode="headless",
	)

	def _raise_pipeline_error(*args, **kwargs):
		raise SearchPipelinePlatformError(code, "平台拒绝请求")

	monkeypatch.setattr("boss_agent_cli.wizard.actions.execute_candidate_search", _raise_pipeline_error)
	with WorkflowStore(data_dir) as store:
		return WorkflowRunner(store, DEFAULT_ACTIONS).run(plan, _search_context(data_dir), run_id="search-error")


@pytest.mark.parametrize(
	("code", "expected_recovery"),
	[
		("AUTH_EXPIRED", "login"),
		("TOKEN_REFRESH_FAILED", "login"),
		("RATE_LIMITED", "等待后重试"),
	],
)
def test_candidate_search_pipeline_error_preserves_platform_code(tmp_path, monkeypatch, code, expected_recovery):
	"""登录 / 限流错误码必须保留，恢复指引不能再是「重试当前 run_id」。"""
	run = _run_search_failure(tmp_path, monkeypatch, code)

	assert run["status"] == "failed"
	assert run["error"]["code"] == code
	assert run["error"]["code"] != "NETWORK_ERROR"
	assert run["error"]["recoverable"] is True
	assert expected_recovery in run["error"]["recovery_action"]
	assert "重试当前 run_id" not in run["error"]["recovery_action"]
	if expected_recovery == "login":
		# 登录命令必须指回本次运行的数据目录（Issue #978）。
		assert str(tmp_path.resolve()) in run["error"]["recovery_action"]


@pytest.mark.parametrize("code", ["ACCOUNT_RISK", "ENVIRONMENT_RISK"])
def test_candidate_search_pipeline_risk_code_stays_terminal(tmp_path, monkeypatch, code):
	"""风控码不能被新分支反转成可重试，也不能透传平台响应原文。"""
	run = _run_search_failure(tmp_path, monkeypatch, code)

	assert run["status"] == "failed"
	assert run["error"]["code"] == code
	assert run["error"]["recoverable"] is False
	assert "重试当前 run_id" not in run["error"]["recovery_action"]
	assert "clean --risk-lock" in run["error"]["recovery_action"]
	assert str(tmp_path.resolve()) in run["error"]["recovery_action"]
	assert "平台拒绝请求" not in run["error"]["message"]


def test_candidate_search_unknown_pipeline_code_keeps_network_fallback(tmp_path, monkeypatch):
	run = _run_search_failure(tmp_path, monkeypatch, "SOMETHING_NOT_REGISTERED")

	assert run["status"] == "failed"
	assert run["error"]["code"] == "NETWORK_ERROR"
	assert run["error"]["recoverable"] is True
	assert run["error"]["recovery_action"] == "重试当前 run_id"
