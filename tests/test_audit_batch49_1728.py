"""批次四十九审计修复（Issue #1728）：automation JSONL 队列的特殊文件。

`read_jsonl` 直接 `read_bytes()`：队列路径被换成 FIFO / 设备时会在打开阶段
永久阻塞，`agent run` / `pending list` / `stats` 既不返回信封也不退出。
现在与 `read_state()` / `read_stop()` 同口径，按路径类型 fail-closed。
"""

import json
import os
from pathlib import Path
import stat

from click.testing import CliRunner
import pytest

from boss_agent_cli.automation.storage import AutomationStateError, AutomationStore
from boss_agent_cli.main import cli


def _row(status: str = "pending", row_id: str = "p1") -> dict:
	return {
		"id": row_id,
		"ts": "2026-11-18T00:00:00",
		"platform": "zhipin",
		"candidate_key": "candidate-1",
		"action": "skip",
		"status": status,
		"confidence": 0.99,
		"reason": "",
		"message": "",
	}


def _write_pending(tmp_path: Path) -> AutomationStore:
	store = AutomationStore(tmp_path)
	store.pending_path.write_text(json.dumps(_row(), ensure_ascii=False) + "\n", encoding="utf-8")
	return store


def _make_fifo(path: Path) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	os.mkfifo(path)


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有 POSIX FIFO")
def test_fifo_pending_queue_fails_closed_instead_of_blocking(tmp_path):
	store = AutomationStore(tmp_path)
	_make_fifo(store.pending_path)

	with pytest.raises(AutomationStateError) as exc_info:
		store.read_pending()

	assert str(store.pending_path) in str(exc_info.value)
	assert "普通文件" in str(exc_info.value)
	assert stat.S_ISFIFO(store.pending_path.lstat().st_mode), "FIFO 不能被读取或替换"


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有 POSIX FIFO")
def test_agent_pending_list_reports_fifo_queue_as_state_invalid(tmp_path):
	store = AutomationStore(tmp_path)
	_make_fifo(store.pending_path)

	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "agent", "pending", "list"]
	)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "AUTOMATION_STATE_INVALID"
	assert str(store.pending_path) in payload["error"]["message"]
	assert "普通文件" in payload["error"]["message"]
	assert str(store.pending_path) in payload["error"]["recovery_action"]


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有 POSIX FIFO")
def test_agent_stats_reports_fifo_action_log_as_state_invalid(tmp_path):
	store = AutomationStore(tmp_path)
	_make_fifo(store.root / "action-log.jsonl")

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "agent", "stats"])

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "AUTOMATION_STATE_INVALID"
	assert "普通文件" in payload["error"]["message"]


def test_dangling_symlink_queue_fails_closed(tmp_path):
	store = AutomationStore(tmp_path)
	try:
		store.pending_path.symlink_to(tmp_path / "missing-target.jsonl")
	except (OSError, NotImplementedError):
		pytest.skip("当前文件系统不支持符号链接")

	with pytest.raises(AutomationStateError) as exc_info:
		store.read_pending()

	assert "悬空" in str(exc_info.value)


def test_symlink_to_regular_queue_still_reads(tmp_path):
	"""指向普通文件的符号链接保持沿链接读取的既有行为。"""
	store = _write_pending(tmp_path)
	target = store.pending_path
	real = tmp_path / "real-pending.jsonl"
	target.rename(real)
	try:
		target.symlink_to(real)
	except (OSError, NotImplementedError):
		pytest.skip("当前文件系统不支持符号链接")

	assert [item.id for item in store.read_pending()] == ["p1"]
