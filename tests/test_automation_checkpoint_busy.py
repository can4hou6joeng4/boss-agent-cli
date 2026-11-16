"""周期内控制锁冲突的语义（Issue #1267）。

动作派发成功、队列已写回 executed 之后，checkpoint 撞上控制锁时不能报
「本次操作没有生效」：平台动作可能已经发出。runner 把周期内的冲突转换成
`AutomationControlBusyDispatched`，命令层按 `LOCAL_STATE_WRITE_FAILED` 上报
（禁止自动重发，要求先核对官方页面）；`--dry-run` 与 `--resume` 入口的冲突
没有产生平台动作，保持 `AUTOMATION_BUSY`。
"""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest
from click.testing import CliRunner

from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.models import ActionResult, PendingAction, PlatformAction
from boss_agent_cli.automation.runner import run_automation_cycle
from boss_agent_cli.automation.storage import (
	AutomationControlBusy,
	AutomationControlBusyDispatched,
	AutomationStore,
)
from boss_agent_cli.main import cli


class _Adapter:
	name = "zhipin"

	def __init__(self) -> None:
		self.sent: list[str] = []

	def ensure_session(self) -> None:
		pass

	def detect_safety_warning(self):
		return None

	def scan_conversations(self, tabs, max_per_tab):
		return []

	def execute_action(self, action, message, ref):
		self.sent.append(message)
		return ActionResult(status="executed", details={})


@contextmanager
def _held_control_lock(store: AutomationStore) -> Iterator[None]:
	"""真实持有控制锁：周期内的 checkpoint / write_state 都会撞锁。"""
	connection = sqlite3.connect(store.root / ".control-lock.sqlite3", timeout=0.05)
	try:
		connection.execute("BEGIN IMMEDIATE")
		yield
	finally:
		connection.rollback()
		connection.close()


def _pending(store: AutomationStore) -> None:
	store.append_pending(PendingAction(
		id="approved-1",
		ts="2026-11-16T00:00:00+00:00",
		platform="zhipin",
		candidate_key="候选人A",
		action=PlatformAction.SEND_QUESTIONNAIRE.value,
		status="pending",
		confidence=0.9,
		reason="approved review",
		message="您好，想确认下近期是否看机会？",
	))


def _invoke(tmp_path: Path, *args: str):
	return CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])


def test_cycle_marks_control_conflict_as_dispatched_after_action(tmp_path):
	store = AutomationStore(tmp_path)
	_pending(store)
	adapter = _Adapter()
	with _held_control_lock(store):
		with pytest.raises(AutomationControlBusyDispatched) as excinfo:
			run_automation_cycle(adapter, store, AutomationConfig(), platform="zhipin", dry_run=False)

	# 仍是 AutomationControlBusy，调用方按基类捕获也不会漏。
	assert isinstance(excinfo.value, AutomationControlBusy)
	# 动作确实已经发出，队列在 checkpoint 之前已写回 executed。
	assert len(adapter.sent) == 1
	assert [item.status for item in store.read_pending()] == ["executed"]


def test_agent_run_reports_state_write_failure_after_dispatch(tmp_path, monkeypatch):
	store = AutomationStore(tmp_path)
	_pending(store)
	adapter = _Adapter()
	monkeypatch.setattr("boss_agent_cli.commands.agent.build_automation_adapter", lambda *a, **k: adapter)
	with _held_control_lock(store):
		result = _invoke(tmp_path, "--role", "recruiter", "agent", "run", "--limit", "1")

	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert payload["ok"] is False
	assert payload["command"] == "agent.run"
	error = payload["error"]
	assert error["code"] == "LOCAL_STATE_WRITE_FAILED"
	assert error["recoverable"] is False
	assert "可能已发出" in error["message"]
	assert "核对" in error["recovery_action"]
	assert any("agent stats" in action for action in payload["hints"]["next_actions"])
	# 队列里的动作已确认执行，重新运行不会重发；但熔断状态没落盘。
	assert len(adapter.sent) == 1
	assert [item.status for item in store.read_pending()] == ["executed"]


def test_dry_run_control_conflict_still_reports_not_effective(tmp_path, monkeypatch):
	store = AutomationStore(tmp_path)
	_pending(store)
	adapter = _Adapter()
	monkeypatch.setattr("boss_agent_cli.commands.agent.build_automation_adapter", lambda *a, **k: adapter)
	with _held_control_lock(store):
		result = _invoke(tmp_path, "--role", "recruiter", "agent", "run", "--dry-run", "--limit", "1")

	assert result.exit_code == 1
	payload = json.loads(result.output)
	# 预览没有发出任何请求：撞锁时「本次操作没有生效」是准确的，不该升级成
	# 需要人工核对的本地写失败。
	assert payload["error"]["code"] == "AUTOMATION_BUSY"
	assert payload["error"]["recoverable"] is True
	assert "没有生效" in payload["error"]["recovery_action"]
	assert adapter.sent == []
	assert [item.status for item in store.read_pending()] == ["pending"]


def test_resume_control_conflict_is_not_reported_as_dispatched(tmp_path):
	store = AutomationStore(tmp_path)
	store.request_stop("manual-stop")
	adapter = _Adapter()
	with _held_control_lock(store):
		with pytest.raises(AutomationControlBusy) as excinfo:
			run_automation_cycle(
				adapter,
				store,
				AutomationConfig(),
				platform="zhipin",
				dry_run=False,
				resume=True,
			)

	# 恢复入口在 try 之外：撞锁时既没有解除熔断也没有派发动作。
	assert not isinstance(excinfo.value, AutomationControlBusyDispatched)
	assert adapter.sent == []
	assert store.read_stop()["open"] is True
