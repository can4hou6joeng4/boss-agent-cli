"""automation 控制锁冲突的信封契约（Issue #1251）。

控制锁是 automation 自己的并发边界（automation/.control-lock.sqlite3），与缓存库
无关。锁冲突必须报成 `AUTOMATION_BUSY` 并指向真正的锁文件、说明本次操作没有生效；
不能落进 main 的兜底层被报成 `CACHE_BUSY`、把人引去删 cache/boss_agent.db。
"""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest
from click.testing import CliRunner

from boss_agent_cli.automation.storage import AutomationControlBusy, AutomationStore
from boss_agent_cli.main import cli


def _store(tmp_path: Path) -> AutomationStore:
	return AutomationStore(tmp_path)


@contextmanager
def _held_lock(store: AutomationStore) -> Iterator[sqlite3.Connection]:
	"""真实持有控制锁：另一连接拿不到 RESERVED 锁，只能等 busy timeout 后失败。"""
	connection = sqlite3.connect(store.root / ".control-lock.sqlite3", timeout=0.05)
	try:
		connection.execute("BEGIN IMMEDIATE")
		yield connection
	finally:
		connection.rollback()
		connection.close()


def _invoke(tmp_path: Path, *args: str):
	return CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])


def test_agent_stop_reports_automation_busy_not_cache_busy(tmp_path):
	store = _store(tmp_path)
	with _held_lock(store):
		result = _invoke(tmp_path, "agent", "stop")

	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert payload["ok"] is False
	assert payload["command"] == "agent.stop"
	error = payload["error"]
	assert error["code"] == "AUTOMATION_BUSY"
	assert error["recoverable"] is True
	# 指引必须指向真正的锁文件；cache/boss_agent.db 的误报会让人删掉无关的缓存库。
	assert "automation/.control-lock.sqlite3" in error["recovery_action"]
	assert "cache/boss_agent.db" not in error["recovery_action"]
	assert any("agent stats" in action for action in payload["hints"]["next_actions"])
	# 控制更新没有生效：停止开关不能被写成打开。
	assert not store.stop_path.exists()


def test_agent_stop_succeeds_after_lock_released(tmp_path):
	store = _store(tmp_path)
	with _held_lock(store):
		blocked = _invoke(tmp_path, "agent", "stop")
	assert blocked.exit_code == 1

	retried = _invoke(tmp_path, "agent", "stop")
	assert retried.exit_code == 0
	assert json.loads(retried.output)["ok"] is True
	latch = json.loads(store.stop_path.read_text(encoding="utf-8"))
	assert latch["open"] is True


@pytest.mark.parametrize("operation", ["request_stop", "resume", "write_state"])
def test_store_control_operations_raise_control_busy(tmp_path, operation):
	store = _store(tmp_path)
	with _held_lock(store):
		with pytest.raises(AutomationControlBusy):
			if operation == "request_stop":
				store.request_stop("manual")
			elif operation == "resume":
				store.resume()
			else:
				store.write_state({"conversations": {}, "autonomy": {}, "safety": {}})
	assert not store.stop_path.exists()
	assert not store.state_path.exists()


def test_non_lock_operational_error_is_not_translated(tmp_path, monkeypatch):
	store = _store(tmp_path)

	def unavailable(*args, **kwargs):
		raise sqlite3.OperationalError("unable to open database file")

	monkeypatch.setattr("boss_agent_cli.automation.storage.sqlite3.connect", unavailable)
	# 只翻译锁冲突；打不开库之类的错误不能被伪装成「稍后重试即可」的占用。
	with pytest.raises(sqlite3.OperationalError):
		store.request_stop("manual")
