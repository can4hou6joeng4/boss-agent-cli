"""automation 队列读取：坏 status 与非 UTF-8 字节不能静默变空（Issue #1337）。"""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.automation.storage import AutomationStateError, AutomationStore
from boss_agent_cli.main import cli


def _row(status, row_id: str = "p1") -> dict:
	return {
		"id": row_id,
		"ts": "2026-11-17T00:00:00",
		"platform": "zhipin",
		"candidate_key": "candidate-1",
		"action": "skip",
		"status": status,
		"confidence": 0.99,
		"reason": "",
		"message": "",
	}


def _write_pending(tmp_path, rows) -> AutomationStore:
	store = AutomationStore(tmp_path)
	store.pending_path.write_text(
		"".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
		encoding="utf-8",
	)
	return store


@pytest.mark.parametrize("status", [None, 1, "Pending", "dry-run", True, ["pending"], {"name": "pending"}])
def test_pending_row_with_unknown_status_fails_closed(tmp_path, status):
	store = _write_pending(tmp_path, [_row(status)])

	with pytest.raises(AutomationStateError) as exc_info:
		store.read_pending()

	assert "status" in str(exc_info.value)
	assert repr(status) in str(exc_info.value)


@pytest.mark.parametrize("status", ["pending", "executing", "executed", "verification-required"])
def test_pending_row_accepts_known_statuses(tmp_path, status):
	store = _write_pending(tmp_path, [_row(status)])

	assert [item.status for item in store.read_pending()] == [status]


def test_invalid_utf8_line_does_not_hide_valid_rows(tmp_path):
	store = AutomationStore(tmp_path)
	valid = json.dumps(_row("pending"), ensure_ascii=False).encode("utf-8")
	store.pending_path.write_bytes(valid + b"\n" + "手工编辑".encode("gbk") + b"\n")

	assert [item.id for item in store.read_pending()] == ["p1"]


def test_file_with_only_invalid_utf8_lines_reads_as_empty(tmp_path):
	store = AutomationStore(tmp_path)
	store.pending_path.write_bytes("手工编辑".encode("gbk") + b"\n")

	assert store.read_pending() == []


def test_agent_pending_list_reports_bad_status_envelope(tmp_path):
	_write_pending(tmp_path, [_row("Pending")])

	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "agent", "pending", "list"]
	)

	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "AUTOMATION_STATE_INVALID"
	assert "status" in payload["error"]["message"]


@pytest.mark.parametrize("status", [["pending"], {"name": "pending"}])
def test_agent_pending_list_unhashable_status_is_state_invalid(tmp_path, status):
	"""数组 / 对象 status 不能让 `in frozenset` 抛 TypeError 兜成 INTERNAL_ERROR（Issue #1410）。"""
	_write_pending(tmp_path, [_row(status)])

	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "agent", "pending", "list"]
	)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "AUTOMATION_STATE_INVALID"
	assert "status" in payload["error"]["message"]


def test_missing_queue_file_reads_as_empty(tmp_path):
	"""只有 FileNotFoundError 才是空队列；新数据目录不报错（Issue #1406）。"""
	store = AutomationStore(tmp_path)

	assert store.read_pending() == []
	assert store.read_reviews() == []
	assert store.read_jsonl("action-log.jsonl") == []


def test_unreadable_queue_file_fails_closed(tmp_path, monkeypatch):
	"""权限不足不能读成空队列，否则已批准动作会被静默跳过（Issue #1406）。"""
	store = _write_pending(tmp_path, [_row("pending")])

	def deny_read(self: Path) -> bytes:
		raise PermissionError(13, "Permission denied", str(self))

	monkeypatch.setattr(Path, "read_bytes", deny_read)

	with pytest.raises(AutomationStateError) as exc_info:
		store.read_pending()

	assert str(store.pending_path) in str(exc_info.value)
	assert "无法读取" in str(exc_info.value)


def test_agent_pending_list_reports_unreadable_queue_as_state_invalid(tmp_path):
	"""队列路径被目录占用（读不出来）时指向该文件，而不是返回 ok=true items=[]。"""
	store = AutomationStore(tmp_path)
	store.pending_path.mkdir()

	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "agent", "pending", "list"]
	)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "AUTOMATION_STATE_INVALID"
	assert str(store.pending_path) in payload["error"]["message"]
	assert "无法读取" in payload["error"]["message"]
	assert str(store.pending_path) in payload["error"]["recovery_action"]
	assert "可读" in payload["error"]["recovery_action"]


def test_agent_run_does_not_skip_unreadable_pending_queue(tmp_path):
	"""`agent run --dry-run` 不能在队列读不出来时照常执行并跳过已批准动作（Issue #1406）。"""
	store = AutomationStore(tmp_path)
	store.pending_path.mkdir()

	result = CliRunner().invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "--role", "recruiter", "agent", "run", "--dry-run", "--limit", "1"],
	)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "AUTOMATION_STATE_INVALID"
	assert str(store.pending_path) in payload["error"]["message"]
	assert not (store.root / "action-log.jsonl").exists(), "读失败不能留下「已处理」事件"
