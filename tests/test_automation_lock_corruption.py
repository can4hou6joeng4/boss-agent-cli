"""自动化锁文件损坏与零派发周期控制锁冲突的语义（Issue #1304 / #1307）。

`.run-lock.sqlite3` / `.control-lock.sqlite3` 被垃圾字节或同名目录占位时，
`sqlite3.DatabaseError` 原先会原样上抛：命令丢掉整个 JSON 信封，`agent run`
还会被兜成可重试的 `NETWORK_ERROR`。零派发周期里控制锁冲突只意味着本地状态
写回没成功，不能报成「平台动作可能已生效」。
"""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest
from click.testing import CliRunner

from boss_agent_cli.automation.config import AutomationConfig
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
		return None


@contextmanager
def _held_control_lock(store: AutomationStore) -> Iterator[None]:
	connection = sqlite3.connect(store.root / ".control-lock.sqlite3", timeout=0.05)
	try:
		connection.execute("BEGIN IMMEDIATE")
		yield
	finally:
		connection.rollback()
		connection.close()


def _invoke(tmp_path: Path, *args: str):
	return CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "--role", "recruiter", *args])


def _envelope(result):
	payload = json.loads(result.output)
	assert payload["ok"] is False
	return payload


# ── 锁文件损坏：确定性本地错误，保留信封 ──────────────────────────


def test_corrupt_control_lock_reports_state_invalid(tmp_path):
	(tmp_path / "automation").mkdir()
	(tmp_path / "automation" / ".control-lock.sqlite3").write_bytes(b"not a database")

	result = _invoke(tmp_path, "agent", "stop")

	assert result.exit_code == 1
	payload = _envelope(result)
	assert payload["error"]["code"] == "AUTOMATION_STATE_INVALID"
	assert "无法解析" in payload["error"]["message"]


def test_control_lock_path_is_directory_reports_state_invalid(tmp_path):
	(tmp_path / "automation").mkdir()
	(tmp_path / "automation" / ".control-lock.sqlite3").mkdir()

	result = _invoke(tmp_path, "agent", "stop")

	assert result.exit_code == 1
	assert _envelope(result)["error"]["code"] == "AUTOMATION_STATE_INVALID"


def test_corrupt_run_lock_reports_state_invalid(tmp_path):
	(tmp_path / "automation").mkdir()
	(tmp_path / "automation" / ".run-lock.sqlite3").write_bytes(b"not a database")

	result = _invoke(tmp_path, "agent", "review", "approve", "missing")

	assert result.exit_code == 1
	assert _envelope(result)["error"]["code"] == "AUTOMATION_STATE_INVALID"


def test_agent_run_with_corrupt_lock_is_not_network_error(tmp_path, monkeypatch):
	(tmp_path / "automation").mkdir()
	(tmp_path / "automation" / ".run-lock.sqlite3").write_bytes(b"not a database")
	adapter = _Adapter()
	monkeypatch.setattr("boss_agent_cli.commands.agent.build_automation_adapter", lambda *a, **k: adapter)

	result = _invoke(tmp_path, "agent", "run", "--limit", "1")

	assert result.exit_code == 1
	payload = _envelope(result)
	assert payload["error"]["code"] == "AUTOMATION_STATE_INVALID"
	assert payload["error"]["recoverable"] is not False
	assert adapter.sent == []


# ── 零派发周期：控制锁冲突仍是「没有生效」 ────────────────────────


def test_zero_dispatch_control_conflict_is_not_reported_as_dispatched(tmp_path):
	store = AutomationStore(tmp_path)
	adapter = _Adapter()

	with _held_control_lock(store):
		with pytest.raises(AutomationControlBusy) as excinfo:
			run_automation_cycle(adapter, store, AutomationConfig(), platform="zhipin", dry_run=False)

	assert not isinstance(excinfo.value, AutomationControlBusyDispatched)
	assert adapter.sent == []
