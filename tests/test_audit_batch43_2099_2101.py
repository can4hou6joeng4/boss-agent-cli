"""审计批次：salt 并发创建、--limit 约束待执行队列、wizard error 缺 code（Issue #2099–#2101）。"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.models import (
	ActionResult,
	Conversation,
	ConversationRef,
	PendingAction,
	PlatformAction,
)
from boss_agent_cli.automation.runner import run_automation_cycle
from boss_agent_cli.automation.storage import AutomationStore
from boss_agent_cli.cache.store import CacheRecordInvalid
from boss_agent_cli.services import private_files
from boss_agent_cli.services.private_files import SALT_BYTES, load_or_create_private_salt
from boss_agent_cli.wizard.store import WorkflowStore

# ── #2099：锁忙表示另一进程正在创建 salt，不能当成损坏 ──────────────


@contextmanager
def _busy_lock(path: Path) -> Iterator[bool]:
	yield False


def _winner_salt() -> bytes:
	return b"w" * SALT_BYTES


def test_lock_busy_waits_for_creator_and_reuses_salt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""锁忙且 salt 缺失时，在有界窗口内等到创建者落位后复用，而不是直接失败。"""
	auth_dir = tmp_path / "auth"
	auth_dir.mkdir()
	salt_path = auth_dir / "salt"
	monkeypatch.setattr(private_files, "exclusive_file_lock", _busy_lock)
	sleep_calls: list[float] = []

	def fake_sleep(seconds: float) -> None:
		sleep_calls.append(seconds)
		salt_path.write_bytes(_winner_salt())

	monkeypatch.setattr(time, "sleep", fake_sleep)

	assert load_or_create_private_salt(auth_dir) == _winner_salt()
	assert sleep_calls == [private_files.SALT_POLL_SECONDS], "锁忙且 salt 缺失时应等待创建者"


def test_lock_busy_reports_after_bounded_wait(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""等待窗口耗尽且 salt 始终缺失时才按失败报出。"""
	auth_dir = tmp_path / "auth"
	auth_dir.mkdir()
	monkeypatch.setattr(private_files, "exclusive_file_lock", _busy_lock)
	monkeypatch.setattr(time, "sleep", lambda seconds: None)
	clock = iter([0.0, 0.0, private_files.SALT_WAIT_SECONDS + 1.0])
	monkeypatch.setattr(time, "monotonic", lambda: next(clock, private_files.SALT_WAIT_SECONDS + 1.0))

	with pytest.raises(RuntimeError, match="等待跨进程锁超时"):
		load_or_create_private_salt(auth_dir)


def test_lock_busy_with_existing_salt_does_not_wait(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""salt 已存在时锁忙直接复用，不进入等待循环。"""
	auth_dir = tmp_path / "auth"
	auth_dir.mkdir()
	(auth_dir / "salt").write_bytes(_winner_salt())
	monkeypatch.setattr(private_files, "exclusive_file_lock", _busy_lock)

	def fail_sleep(seconds: float) -> None:
		raise AssertionError("salt 已存在时不应等待创建者")

	monkeypatch.setattr(time, "sleep", fail_sleep)

	assert load_or_create_private_salt(auth_dir) == _winner_salt()


# ── #2100：--limit 同时约束待执行队列的派发 ───────────────────────────


class _Adapter:
	name = "zhipin"

	def __init__(self, refs: list[ConversationRef] | None = None) -> None:
		self.sent: list[str] = []
		self.refs = refs or []
		self.scans = 0

	def ensure_session(self) -> None:
		pass

	def detect_safety_warning(self) -> str | None:
		return None

	def scan_conversations(self, tabs: list[str], max_per_tab: int) -> list[ConversationRef]:
		self.scans += 1
		return list(self.refs)

	def read_conversation(self, ref: ConversationRef) -> Conversation:
		return Conversation(title=f"候选人{ref.id}")

	def execute_action(self, action: Any, message: str, ref: ConversationRef) -> ActionResult:
		self.sent.append(message)
		return ActionResult(status="executed", details={})


def _seed_pending(store: AutomationStore, keys: list[str]) -> None:
	for index, key in enumerate(keys):
		store.append_pending(
			PendingAction(
				id=f"approved-{index}",
				ts="2026-11-20T00:00:00+00:00",
				platform="zhipin",
				candidate_key=key,
				action=PlatformAction.SEND_QUESTIONNAIRE.value,
				status="pending",
				confidence=0.9,
				reason="approved review",
				message=f"您好 {key}，想确认下近期是否看机会？",
			)
		)


def test_limit_caps_pending_dispatch(tmp_path: Path) -> None:
	"""--limit 1 时待执行队列只派发一个会话，其余行保持 pending 留待下一轮。"""
	store = AutomationStore(tmp_path)
	_seed_pending(store, ["候选人A", "候选人B", "候选人C"])
	adapter = _Adapter()

	run_automation_cycle(adapter, store, AutomationConfig(), platform="zhipin", dry_run=False, limit=1)

	assert len(adapter.sent) == 1, f"--limit 1 最多派发一个会话，实际 {len(adapter.sent)}"
	statuses = [item.status for item in store.read_pending()]
	assert statuses.count("executed") == 1
	assert statuses.count("pending") == 2, "未派发的行保持可执行，留待下一轮"


def test_limit_budget_is_shared_between_pending_and_scan(tmp_path: Path) -> None:
	"""队列已用满本轮预算时，扫描路径不再发起请求也不再派发。"""
	store = AutomationStore(tmp_path)
	_seed_pending(store, ["候选人A"])
	adapter = _Adapter([ConversationRef(id="1", tab="新招呼"), ConversationRef(id="2", tab="新招呼")])

	run_automation_cycle(adapter, store, AutomationConfig(), platform="zhipin", dry_run=False, limit=1)

	assert len(adapter.sent) == 1, "队列已用满本轮预算，扫描路径不应再派发"
	assert adapter.scans == 0, "预算用满后不再发起扫描请求"
	assert [item.status for item in store.read_pending()] == ["executed"]


def test_without_limit_pending_dispatches_all(tmp_path: Path) -> None:
	"""不带 --limit 时待执行队列保持原行为：全部派发。"""
	store = AutomationStore(tmp_path)
	_seed_pending(store, ["候选人A", "候选人B"])
	adapter = _Adapter()

	run_automation_cycle(adapter, store, AutomationConfig(), platform="zhipin", dry_run=False)

	assert len(adapter.sent) == 2


# ── #2101：非空 error 必须有非空字符串 code ──────────────────────────


def _workflow_store(tmp_path: Path) -> WorkflowStore:
	store = WorkflowStore(tmp_path)
	store.cache.create_workflow_run(
		"run-1",
		role="candidate",
		platform="zhipin",
		goal="job_search",
		mode="headless",
		params={"inputs": {}, "requested_steps": ["collect"]},
		steps=("collect",),
	)
	return store


def test_missing_error_code_is_rejected(tmp_path: Path) -> None:
	"""缺 code 的失败记录不能放行：出口会回退成 NETWORK_ERROR +「重试」。"""
	store = _workflow_store(tmp_path)
	try:
		store.cache.update_workflow_step(
			"run-1", "collect", status="failed", error={"recoverable": False, "replay_safe": False}
		)

		with pytest.raises(CacheRecordInvalid) as excinfo:
			store.get_checkpoint("run-1")

		assert excinfo.value.column == "error.code"
	finally:
		store.close()


def test_complete_error_contract_still_reads(tmp_path: Path) -> None:
	"""护栏：带完整 code 的失败记录照常读取。"""
	store = _workflow_store(tmp_path)
	try:
		store.cache.update_workflow_step(
			"run-1",
			"collect",
			status="failed",
			error={"code": "LOCAL_STATE_WRITE_FAILED", "recoverable": False, "replay_safe": False},
		)

		checkpoint = store.get_checkpoint("run-1")

		assert checkpoint is not None
		assert checkpoint["steps"][0]["error"]["code"] == "LOCAL_STATE_WRITE_FAILED"
	finally:
		store.close()
