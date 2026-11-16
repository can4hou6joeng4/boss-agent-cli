"""automation 执行锁的回归测试（Issue #1253）。

待执行队列按整文件回写：两个 executor 同时读取同一份快照时，后写者会用旧行
覆盖先写者的在途标记，同一条动作可能被派发两次。现在整个周期持有跨进程执行锁
(`automation/.run-lock.sqlite3`)，第二个 executor 在发出任何请求前就得到
`AUTOMATION_RUN_BUSY`；显式 `--resume` 也在锁内执行，不会抢先清除熔断。
"""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest
from click.testing import CliRunner

from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.models import ActionResult, PendingAction, PlatformAction, ReviewItem
from boss_agent_cli.automation.runner import run_automation_cycle
from boss_agent_cli.automation.storage import AutomationControlBusy, AutomationRunBusy, AutomationStateError, AutomationStore
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
def _held_run_lock(store: AutomationStore) -> Iterator[None]:
	"""真实持有执行锁：另一个 executor 拿不到 RESERVED 锁。"""
	connection = sqlite3.connect(store.root / ".run-lock.sqlite3", timeout=0.05)
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


def test_cycle_refuses_to_send_while_another_executor_holds_the_lock(tmp_path):
	store = AutomationStore(tmp_path)
	_pending(store)
	adapter = _Adapter()
	with _held_run_lock(store):
		with pytest.raises(AutomationRunBusy):
			run_automation_cycle(adapter, store, AutomationConfig(), platform="zhipin", dry_run=False)
	# 冲突发生在任何请求发出之前：没有发送，队列也保持原样。
	assert adapter.sent == []
	assert [item.status for item in store.read_pending()] == ["pending"]


def test_cycle_runs_after_lock_released(tmp_path):
	store = AutomationStore(tmp_path)
	_pending(store)
	adapter = _Adapter()
	with _held_run_lock(store):
		with pytest.raises(AutomationRunBusy):
			run_automation_cycle(adapter, store, AutomationConfig(), platform="zhipin", dry_run=False)

	report = run_automation_cycle(adapter, store, AutomationConfig(), platform="zhipin", dry_run=False)
	assert report.status == "OK"
	assert len(adapter.sent) == 1
	assert [item.status for item in store.read_pending()] == ["executed"]


def test_resume_does_not_clear_breaker_when_another_executor_runs(tmp_path):
	store = AutomationStore(tmp_path)
	store.request_stop("manual-stop")
	adapter = _Adapter()
	with _held_run_lock(store):
		with pytest.raises(AutomationRunBusy):
			run_automation_cycle(
				adapter,
				store,
				AutomationConfig(),
				platform="zhipin",
				dry_run=True,
				resume=True,
			)
	# 拿不到执行锁时，显式恢复不能先行关闭熔断。
	assert store.read_stop()["open"] is True

	run_automation_cycle(adapter, store, AutomationConfig(), platform="zhipin", dry_run=True, resume=True)
	assert store.read_stop()["open"] is False


def test_agent_run_reports_run_busy_envelope(tmp_path):
	store = AutomationStore(tmp_path)
	with _held_run_lock(store):
		result = _invoke(tmp_path, "--role", "recruiter", "agent", "run", "--dry-run", "--limit", "1")

	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert payload["ok"] is False
	assert payload["command"] == "agent.run"
	error = payload["error"]
	assert error["code"] == "AUTOMATION_RUN_BUSY"
	assert error["recoverable"] is True
	assert "没有发出任何请求" in error["message"]
	assert any("agent stats" in action for action in payload["hints"]["next_actions"])


def test_review_approve_reports_run_busy_without_queueing(tmp_path):
	store = AutomationStore(tmp_path)
	store.append_review(ReviewItem(
		id="review-1",
		ts="2026-11-16T00:00:00+00:00",
		platform="zhipin",
		candidate_key="候选人A",
		action=PlatformAction.SEND_QUESTIONNAIRE.value,
		status="review",
		confidence=0.5,
		reason="needs review",
		message="您好",
	))
	with _held_run_lock(store):
		result = _invoke(tmp_path, "agent", "review", "approve", "review-1")

	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "AUTOMATION_RUN_BUSY"
	# 批准没有生效：复核项仍是 review，待执行队列没有新增行。
	assert [item.status for item in store.read_reviews()] == ["review"]
	assert store.read_pending() == []


def test_non_lock_operational_error_is_reported_as_state_error(tmp_path, monkeypatch):
	store = AutomationStore(tmp_path)

	def unavailable(*args, **kwargs):
		raise sqlite3.OperationalError("unable to open database file")

	monkeypatch.setattr("boss_agent_cli.automation.storage.sqlite3.connect", unavailable)
	# 打不开数据库不是锁冲突（不能误报成 busy），也不该让命令丢掉信封：
	# 按确定性的状态损坏报错（Issue #1304）。
	with pytest.raises(AutomationStateError) as excinfo:
		with store.execution_lock():
			pass
	assert not isinstance(excinfo.value, AutomationRunBusy)
	assert not isinstance(excinfo.value, AutomationControlBusy)
