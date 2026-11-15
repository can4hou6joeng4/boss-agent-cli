"""恢复前拒绝损坏的重放标记，避免把结果未知的写步骤再次执行。"""

from __future__ import annotations

import json
import sqlite3

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.wizard.actions import DEFAULT_ACTIONS
from boss_agent_cli.wizard.models import StepResult, WorkflowPlan
from boss_agent_cli.wizard.store import WorkflowStore

_ENVELOPE_KEYS = {"ok", "schema_version", "command", "data", "pagination", "error", "hints"}
_MISSING = object()
_INVALID_MARKERS = [None, 0, 1, "false", [], {}]


def _seed(data_dir, *, status="failed", marker=False, table="workflow_steps"):
	error = {
		"code": "ACTION_UNCONFIRMED" if marker is False else "RATE_LIMITED",
		"message": "离线断点",
		"recoverable": False,
		"recovery_action": "先核对结果，不要自动重试",
	}
	valid = {**error, "replay_safe": False}
	if marker is not _MISSING:
		error["replay_safe"] = marker
	if isinstance(marker, bool) or marker is _MISSING:
		valid = error
	plan = WorkflowPlan(
		role="recruiter", platform="zhipin", goal="reply",
		inputs={"friend_id": 42, "message": "离线消息"},
		requested_steps=("recruiter_reply",), mode="headless",
	)
	with WorkflowStore(data_dir) as store:
		store.create("run-marker", plan)
		store.update_run(
			"run-marker", status=status, current_step="recruiter_reply",
			error=error if table == "workflow_runs" else valid,
		)
		store.update_step(
			"run-marker", "recruiter_reply", status=status,
			error=error if table == "workflow_steps" else valid,
		)


def _snapshot(data_dir):
	with sqlite3.connect(data_dir / "cache" / "boss_agent.db") as conn:
		return (
			conn.execute("SELECT * FROM workflow_runs WHERE run_id = 'run-marker'").fetchone(),
			conn.execute("SELECT * FROM workflow_steps WHERE run_id = 'run-marker'").fetchone(),
		)


def _invoke(data_dir, flag="--resume"):
	return CliRunner().invoke(cli, ["--json", "--data-dir", str(data_dir), "wizard", flag, "run-marker"])


def _track_replay(monkeypatch):
	replayed = []
	monkeypatch.setitem(
		DEFAULT_ACTIONS, "recruiter_reply", lambda *args: replayed.append(args) or StepResult({"sent": True}),
	)
	return replayed


@pytest.mark.parametrize("status", ["running", "failed"])
@pytest.mark.parametrize("marker", _INVALID_MARKERS)
def test_resume_rejects_invalid_step_replay_marker_before_dispatch(tmp_path, monkeypatch, status, marker):
	_seed(tmp_path, status=status)
	with sqlite3.connect(tmp_path / "cache" / "boss_agent.db") as conn:
		error = json.loads(conn.execute("SELECT error FROM workflow_steps").fetchone()[0])
		error["replay_safe"] = marker
		conn.execute("UPDATE workflow_steps SET error = ?", (json.dumps(error),))
	before = _snapshot(tmp_path)
	replayed = _track_replay(monkeypatch)

	result = _invoke(tmp_path)

	assert replayed == [], result.stdout
	assert result.exit_code == 1, result.output
	envelope = json.loads(result.stdout)
	assert set(envelope) == _ENVELOPE_KEYS
	assert envelope["ok"] is False
	assert envelope["error"]["code"] == "CACHE_RECORD_INVALID"
	assert "error.replay_safe" in envelope["error"]["message"]
	assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("marker", _INVALID_MARKERS)
def test_resume_rejects_invalid_run_replay_marker_without_rewriting(tmp_path, monkeypatch, marker):
	_seed(tmp_path, marker=marker, table="workflow_runs")
	before = _snapshot(tmp_path)
	replayed = _track_replay(monkeypatch)

	result = _invoke(tmp_path)

	assert replayed == []
	assert result.exit_code == 1, result.output
	envelope = json.loads(result.stdout)
	assert envelope["error"]["code"] == "CACHE_RECORD_INVALID"
	assert "error.replay_safe" in envelope["error"]["message"]
	assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("table", ["workflow_runs", "workflow_steps"])
def test_status_and_recent_list_recognize_invalid_replay_marker(tmp_path, table):
	_seed(tmp_path, marker="false", table=table)
	before = _snapshot(tmp_path)

	result = _invoke(tmp_path, "--status")

	assert result.exit_code == 1, result.output
	assert json.loads(result.stdout)["error"]["code"] == "CACHE_RECORD_INVALID"
	with WorkflowStore(tmp_path) as store:
		assert store.list_recent() == []
		assert store.corrupt_run_ids == ["run-marker"]
	assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("status", ["running", "failed"])
@pytest.mark.parametrize("marker", [False, True, _MISSING], ids=["false", "true", "missing"])
def test_boolean_and_legacy_missing_replay_markers_keep_existing_behavior(tmp_path, monkeypatch, status, marker):
	_seed(tmp_path, status=status, marker=marker)
	replayed = _track_replay(monkeypatch)

	result = _invoke(tmp_path)

	envelope = json.loads(result.stdout)
	if marker is False:
		assert result.exit_code == 1, result.output
		assert envelope["error"]["code"] == "ACTION_UNCONFIRMED"
		assert replayed == []
	else:
		assert result.exit_code == 0, result.output
		assert envelope["data"]["status"] == "completed"
		assert len(replayed) == 1


@pytest.mark.parametrize("table", ["workflow_runs", "workflow_steps"])
def test_stop_still_persists_before_reading_an_invalid_replay_marker(tmp_path, table):
	_seed(tmp_path, status="running", marker=0, table=table)

	result = _invoke(tmp_path, "--stop")

	assert result.exit_code == 0, result.output
	assert json.loads(result.stdout)["data"]["status"] == "stopped"
	assert _snapshot(tmp_path)[0][5] == "stopped"
