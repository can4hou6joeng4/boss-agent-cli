"""wizard 存储层与终态边界（Issue #2054–#2058）。"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheRecordInvalid
from boss_agent_cli.main import cli
from boss_agent_cli.wizard.models import StepResult, WizardInput, WorkflowInputError, WorkflowPlan
from boss_agent_cli.wizard.runner import WorkflowActionError, WorkflowRunner
from boss_agent_cli.wizard.store import WorkflowStore


def _plan(*steps: str) -> WorkflowPlan:
	return WorkflowPlan(
		role="candidate",
		platform="zhipin",
		goal="offline",
		inputs={},
		requested_steps=steps,
		mode="headless",
	)


# ── Issue #2054：completed 是终态，resume 不得把它打回 running ───────


def test_resume_completed_run_returns_without_rewriting_state(tmp_path) -> None:
	calls: list[str] = []
	plan = _plan("s1")
	with WorkflowStore(tmp_path) as store:
		store.create("wr-done", plan)
		store.update_step("wr-done", "s1", status="completed", result={"ok": True})
		store.update_run("wr-done", status="completed", current_step="s1", last_result={"done": True})

		result = WorkflowRunner(store, {"s1": lambda *args: calls.append("called")}).run(
			plan, object(), run_id="wr-done"
		)

		assert calls == []
		assert result["status"] == "completed"
		assert result.get("last_result") == {"done": True}
		assert store.get_checkpoint("wr-done")["status"] == "completed"


def test_completed_run_with_unfinished_steps_is_rejected(tmp_path) -> None:
	plan = _plan("s1")
	with WorkflowStore(tmp_path) as store:
		store.create("wr-mixed", plan)
		# 步骤仍是 pending：completed 与步骤状态自相矛盾。
		store.update_run("wr-mixed", status="completed", current_step="s1")

		with pytest.raises(WorkflowActionError) as excinfo:
			WorkflowRunner(store, {"s1": lambda *args: StepResult({"ok": True})}).run(
				plan, object(), run_id="wr-mixed"
			)

	assert excinfo.value.code == "CACHE_RECORD_INVALID"
	assert excinfo.value.replay_safe is True


# ── Issue #2055：ordinal 列与其余列同口径 fail-closed ─────────────────


def test_ordinal_blob_is_rejected_as_corrupt(tmp_path) -> None:
	plan = _plan("s1")
	with WorkflowStore(tmp_path) as store:
		store.create("wr-ord", plan)
		store.cache._conn.execute("UPDATE workflow_steps SET ordinal = x'0102' WHERE run_id = 'wr-ord'")
		store.cache._conn.commit()

		with pytest.raises(CacheRecordInvalid) as excinfo:
			store.get_checkpoint("wr-ord")

	assert "ordinal" in str(excinfo.value)


# ── Issue #2056：坏 run_id 必须出现在 corrupt 名单里 ─────────────────


def test_blob_run_id_is_reported_as_corrupt_not_dropped(tmp_path) -> None:
	plan = _plan("s1")
	with WorkflowStore(tmp_path) as store:
		store.create("wr-blob", plan)
		store.cache._conn.execute("UPDATE workflow_runs SET run_id = x'62726f6b656e'")
		store.cache._conn.commit()

		runs, corrupt = store.cache.list_workflow_runs_with_corrupt(limit=10)

	assert runs == []
	assert len(corrupt) == 1


# ── Issue #2057：stop 与 worker 落 completed 的竞态不得报「未找到」───


def test_stop_race_with_worker_completion_reports_finished(tmp_path, monkeypatch) -> None:
	plan = _plan("s1")
	with WorkflowStore(tmp_path) as store:
		store.create("wr-race", plan)
		store.update_run("wr-race", status="running", current_step="s1")

	original = WorkflowStore.request_stop

	def racing_request_stop(self: WorkflowStore, run_id: str) -> bool:
		# pre-read 与 request_stop 之间 worker 刚落成 completed。
		self.cache._conn.execute(
			"UPDATE workflow_runs SET status = 'completed' WHERE run_id = ?", (run_id,)
		)
		self.cache._conn.commit()
		return original(self, run_id)

	monkeypatch.setattr(WorkflowStore, "request_stop", racing_request_stop)

	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "wizard", "--stop", "wr-race"])

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "WORKFLOW_ALREADY_FINISHED"
	assert "未找到" not in payload["error"]["message"]


# ── Issue #2058：孤立代理字符在输入边界按 INVALID_PARAM 拒绝 ─────────


def test_wizard_input_rejects_lone_surrogates() -> None:
	base = {"role": "candidate", "platform": "zhipin", "goal": "offline"}

	with pytest.raises(WorkflowInputError):
		WizardInput.from_mapping({**base, "inputs": {"q": "\ud800"}})
	with pytest.raises(WorkflowInputError):
		WizardInput.from_mapping({**base, "goal": "\ud800"})
	with pytest.raises(WorkflowInputError):
		WizardInput.from_mapping({**base, "requested_steps": ["s\ud800"]})
	# 合法输入仍然可用。
	assert WizardInput.from_mapping({**base, "inputs": {"q": "ok"}}).inputs == {"q": "ok"}
