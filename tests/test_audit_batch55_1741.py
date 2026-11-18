"""批次五十五审计修复（Issue #1741）：automation JSONL 追加按路径类型判定。

`_append_jsonl` 直接 `open("a")`：队列被换成 FIFO 时会在打开阶段永久阻塞，
悬空链接会在目标处新建文件、把动作日志写到数据目录之外。读取侧（#1728）
已 fail-closed，现在写入侧与它同口径。
"""

import json
import os
from pathlib import Path
import stat

from click.testing import CliRunner
import pytest

from boss_agent_cli.automation.models import AutomationEvent, EventStatus, PendingAction, PlatformAction
from boss_agent_cli.automation.storage import AutomationStateError, AutomationStore
from boss_agent_cli.main import cli


def _make_fifo(path: Path) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	os.mkfifo(path)


def _event() -> AutomationEvent:
	return AutomationEvent(
		ts="2026-11-19T00:00:00",
		platform="zhipin",
		role="candidate",
		candidate_key="candidate-1",
		action=PlatformAction.SKIP,
		status=EventStatus.SKIPPED,
		confidence=0.99,
		reason="",
	)


def _pending() -> PendingAction:
	return PendingAction(
		id="p1",
		ts="2026-11-19T00:00:00",
		platform="zhipin",
		candidate_key="candidate-1",
		action="skip",
		status="pending",
		confidence=0.99,
		reason="",
	)


def _review_row() -> dict:
	return {
		"id": "r1",
		"ts": "2026-11-19T00:00:00",
		"platform": "zhipin",
		"candidate_key": "candidate-1",
		"action": "skip",
		"status": "review",
		"confidence": 0.99,
		"reason": "",
		"message": "",
	}


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有 POSIX FIFO")
def test_fifo_action_log_append_fails_closed_instead_of_blocking(tmp_path):
	store = AutomationStore(tmp_path)
	log_path = store.root / "action-log.jsonl"
	_make_fifo(log_path)

	with pytest.raises(AutomationStateError) as exc_info:
		store.append_event(_event())

	assert str(log_path) in str(exc_info.value)
	assert "无法写入" in str(exc_info.value)
	assert stat.S_ISFIFO(log_path.lstat().st_mode), "FIFO 不能被替换"


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有 POSIX FIFO")
def test_fifo_pending_queue_append_fails_closed(tmp_path):
	store = AutomationStore(tmp_path)
	_make_fifo(store.pending_path)

	with pytest.raises(AutomationStateError) as exc_info:
		store.append_pending(_pending())

	assert "普通文件" in str(exc_info.value)
	assert stat.S_ISFIFO(store.pending_path.lstat().st_mode)


def test_dangling_symlink_append_does_not_create_target(tmp_path):
	store = AutomationStore(tmp_path)
	target = tmp_path / "outside-action-log.jsonl"
	try:
		(store.root / "action-log.jsonl").symlink_to(target)
	except (OSError, NotImplementedError):
		pytest.skip("当前文件系统不支持符号链接")

	with pytest.raises(AutomationStateError) as exc_info:
		store.append_event(_event())

	assert "悬空" in str(exc_info.value)
	assert not target.exists(), "追加不能新建链接目标"


def test_symlink_to_regular_queue_still_appends_through_link(tmp_path):
	store = AutomationStore(tmp_path)
	real = tmp_path / "real-action-log.jsonl"
	real.write_text("", encoding="utf-8")
	try:
		(store.root / "action-log.jsonl").symlink_to(real)
	except (OSError, NotImplementedError):
		pytest.skip("当前文件系统不支持符号链接")

	store.append_event(_event())

	lines = real.read_text(encoding="utf-8").splitlines()
	assert len(lines) == 1
	assert json.loads(lines[0])["candidate_key"] == "candidate-1"


def test_missing_queue_file_is_created_by_append(tmp_path):
	store = AutomationStore(tmp_path)

	store.append_event(_event())

	assert store.read_jsonl("action-log.jsonl")[0]["candidate_key"] == "candidate-1"


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有 POSIX FIFO")
def test_agent_review_reject_reports_fifo_action_log_as_state_invalid(tmp_path):
	store = AutomationStore(tmp_path)
	store.reviews_path.write_text(json.dumps(_review_row(), ensure_ascii=False) + "\n", encoding="utf-8")
	_make_fifo(store.root / "action-log.jsonl")

	result = CliRunner().invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "agent", "review", "reject", "r1", "--reason", "duplicate"],
	)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "AUTOMATION_STATE_INVALID"
	assert "无法写入" in payload["error"]["message"]
