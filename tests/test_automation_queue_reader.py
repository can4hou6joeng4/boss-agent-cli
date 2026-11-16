"""automation 队列读取：坏 status 与非 UTF-8 字节不能静默变空（Issue #1337）。"""

import json

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


@pytest.mark.parametrize("status", [None, 1, "Pending", "dry-run", True])
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
