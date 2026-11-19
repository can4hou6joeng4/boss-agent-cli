"""automation 状态、队列与锁的漂移边界（Issue #1928–#1933）。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from boss_agent_cli.automation.config import automation_config_from_dict
from boss_agent_cli.automation.models import AutomationMode
from boss_agent_cli.automation.storage import AutomationStateError, AutomationStore


def _store(tmp_path: Path) -> AutomationStore:
	return AutomationStore(tmp_path)


def test_stats_ignores_unhashable_status(tmp_path) -> None:
	store = _store(tmp_path)
	store.root.joinpath("action-log.jsonl").write_text(
		json.dumps({"ts": "2026-11-19T00:00:00+00:00", "status": [], "reason": "manual-edit"}) + "\n",
		encoding="utf-8",
	)

	stats = store.stats()

	assert stats["events"] == 1
	assert stats["recent_errors"] == []


def test_write_state_reports_deep_nesting_as_state_error(tmp_path) -> None:
	store = _store(tmp_path)
	deep: dict[str, object] = {}
	cursor = deep
	for _ in range(1500):
		child: dict[str, object] = {}
		cursor["a"] = child
		cursor = child

	with pytest.raises(AutomationStateError) as caught:
		store.write_state(deep)

	assert "无法序列化" in str(caught.value)
	assert not list(store.root.glob(".state.json.*"))


@pytest.mark.parametrize("lock_name", ["execution_lock", "_control_lock"])
def test_lock_local_io_error_stays_a_local_io_error(tmp_path, monkeypatch, lock_name) -> None:
	store = _store(tmp_path)

	def local_io_failure(*args, **kwargs):
		raise sqlite3.OperationalError("unable to open database file")

	monkeypatch.setattr("boss_agent_cli.automation.storage.sqlite3.connect", local_io_failure)

	with pytest.raises(sqlite3.OperationalError):
		with getattr(store, lock_name)():
			pass


def test_review_status_drift_fails_closed(tmp_path) -> None:
	store = _store(tmp_path)
	store.root.joinpath("human-review-queue.jsonl").write_text(
		json.dumps(
			{
				"id": "r1",
				"ts": "t",
				"platform": "p",
				"candidate_key": "c",
				"action": "send_follow_up",
				"status": {"x": 1},
				"confidence": 0.9,
				"reason": "r",
				"message": "m",
			}
		)
		+ "\n",
		encoding="utf-8",
	)

	with pytest.raises(AutomationStateError) as caught:
		store.read_reviews()

	assert "status" in str(caught.value)


@pytest.mark.parametrize("raw", [[], "", 0, False])
def test_automation_config_rejects_falsy_non_dict(raw) -> None:
	with pytest.raises(ValueError):
		automation_config_from_dict(raw)


def test_automation_config_none_uses_defaults() -> None:
	assert automation_config_from_dict(None).mode == AutomationMode.AUTONOMOUS


def test_interview_lead_write_errors_carry_path(tmp_path) -> None:
	store = _store(tmp_path)
	assert store.append_interview_lead("a", "t", "r") is True
	path = store.root / "interview-leads.csv"
	path.chmod(0o444)
	try:
		with pytest.raises(AutomationStateError) as caught:
			store.append_interview_lead("b", "t", "r")
	finally:
		path.chmod(0o644)

	assert "interview-leads.csv" in str(caught.value)


def test_interview_lead_rejects_lone_surrogate(tmp_path) -> None:
	store = _store(tmp_path)

	with pytest.raises(AutomationStateError) as caught:
		store.append_interview_lead("abc\ud800", "t", "r")

	assert "interview-leads.csv" in str(caught.value)
