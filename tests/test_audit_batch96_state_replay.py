"""批次 96 审计修复：熔断快照缺 open 字段与 ACTION_UNCONFIRMED 缺 replay_safe（Issue #2397、#2398）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from boss_agent_cli.automation.storage import AutomationStateError, AutomationStore
from boss_agent_cli.wizard.models import StepResult, WorkflowPlan
from boss_agent_cli.wizard.runner import WorkflowRunner, has_blocked_step
from boss_agent_cli.wizard.store import WorkflowStore


def _store(tmp_path: Path, state: object) -> AutomationStore:
	root = tmp_path / "automation"
	root.mkdir(parents=True, exist_ok=True)
	(root / "state.json").write_text(json.dumps(state), encoding="utf-8")
	return AutomationStore(tmp_path)


# ── Issue #2397：熔断快照缺少 open 字段按状态损坏收口 ─────────────────


def test_state_breaker_missing_open_is_state_error(tmp_path: Path) -> None:
	store = _store(
		tmp_path,
		{"autonomy": {"circuit_breaker": {"reason": "platform verification required"}}},
	)

	with pytest.raises(AutomationStateError) as excinfo:
		store.read_state()

	assert "open" in str(excinfo.value)


def test_state_breaker_empty_object_still_means_no_breaker(tmp_path: Path) -> None:
	store = _store(tmp_path, {"autonomy": {"circuit_breaker": {}}})

	assert store.read_state()["autonomy"]["circuit_breaker"] == {}


def test_write_state_rejects_breaker_missing_open(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)

	with pytest.raises(AutomationStateError):
		store.write_state({"autonomy": {"circuit_breaker": {"reason": "x"}}})


# ── Issue #2398：ACTION_UNCONFIRMED 缺少 replay_safe 时仍阻断重放 ─────


class _Ctx:
	def __init__(self, data_dir: Path) -> None:
		self.data_dir = data_dir


def _plan() -> WorkflowPlan:
	return WorkflowPlan(
		role="candidate",
		platform="zhipin",
		goal="job_search",
		inputs={},
		requested_steps=("write_step",),
		mode="headless",
	)


def _seed_unconfirmed(store: WorkflowStore) -> None:
	store.create("r1", _plan())
	store.update_run("r1", status="failed", current_step="write_step")
	store.update_step(
		"r1",
		"write_step",
		status="failed",
		result={},
		# 旧数据 / 外部漂移：ACTION_UNCONFIRMED 但缺少 replay_safe 键。
		error={
			"code": "ACTION_UNCONFIRMED",
			"message": "写请求在途，结果尚未确认",
			"recoverable": False,
			"recovery_action": "先核对，不要自动重试",
		},
	)


def test_has_blocked_step_treats_unconfirmed_without_bool_as_blocked(tmp_path: Path) -> None:
	with WorkflowStore(tmp_path) as store:
		_seed_unconfirmed(store)

		assert has_blocked_step(store.get_checkpoint("r1")) is True


def test_resume_does_not_replay_unconfirmed_without_bool(tmp_path: Path) -> None:
	calls: list[str] = []

	def action(ctx: object, inputs: object, prior: object) -> StepResult:
		calls.append("action")
		return StepResult({"sent": True})

	with WorkflowStore(tmp_path) as store:
		_seed_unconfirmed(store)
		result = WorkflowRunner(store, {"write_step": action}).run(_plan(), _Ctx(tmp_path), run_id="r1")

	assert calls == [], "结果未知的写步骤不得重放"
	assert result["status"] == "failed"
	assert result["steps"][0]["error"]["code"] == "ACTION_UNCONFIRMED"
