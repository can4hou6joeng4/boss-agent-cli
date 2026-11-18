"""批次五十七审计修复（Issue #1746）：automation 整文件写入拒绝符号链接。

读取侧允许沿普通文件链接读取（#1728），但整文件写入用临时文件 +
``os.replace`` 会静默把链接替换成普通文件；恢复链接后旧 pending / review
行复活，可能被当成待执行动作重放。追加写入同样不能写穿链接目标。
"""

import json
import os
from pathlib import Path
import stat

import pytest

from boss_agent_cli.automation.models import PendingAction
from boss_agent_cli.automation.storage import AutomationStateError, AutomationStore


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


def _link(path: Path, target: Path) -> None:
	try:
		path.symlink_to(target)
	except (OSError, NotImplementedError):
		pytest.skip("当前文件系统不支持符号链接")


def test_write_pending_refuses_symlink_without_replacing_it(tmp_path):
	store = AutomationStore(tmp_path)
	target = tmp_path / "outside-pending.jsonl"
	target.write_text(json.dumps({"id": "old"}) + "\n", encoding="utf-8")
	_link(store.pending_path, target)

	with pytest.raises(AutomationStateError) as exc_info:
		store.write_pending([_pending()])

	assert "符号链接" in str(exc_info.value)
	assert store.pending_path.is_symlink()
	assert target.read_text(encoding="utf-8") == '{"id": "old"}\n'


def test_write_pending_refuses_dangling_symlink_without_creating_target(tmp_path):
	store = AutomationStore(tmp_path)
	target = tmp_path / "missing-target.jsonl"
	_link(store.pending_path, target)

	with pytest.raises(AutomationStateError) as exc_info:
		store.write_pending([_pending()])

	assert "符号链接" in str(exc_info.value)
	assert not target.exists()


def test_write_state_refuses_symlink_and_keeps_link_and_target(tmp_path):
	store = AutomationStore(tmp_path)
	target = tmp_path / "outside-state.json"
	target.write_text('{"old": true}', encoding="utf-8")
	_link(store.state_path, target)

	with pytest.raises(AutomationStateError) as exc_info:
		store.write_state({"autonomy": {}})

	assert "符号链接" in str(exc_info.value)
	assert store.state_path.is_symlink()
	assert target.read_text(encoding="utf-8") == '{"old": true}'


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有 POSIX FIFO")
def test_write_pending_does_not_replace_fifo(tmp_path):
	store = AutomationStore(tmp_path)
	os.mkfifo(store.pending_path)

	with pytest.raises(AutomationStateError) as exc_info:
		store.write_pending([_pending()])

	assert "普通文件" in str(exc_info.value)
	assert stat.S_ISFIFO(store.pending_path.lstat().st_mode)


def test_missing_regular_files_are_still_created(tmp_path):
	store = AutomationStore(tmp_path)

	store.write_pending([_pending()])
	store.write_state({"autonomy": {}})
	store.append_pending(_pending())

	assert len(store.read_pending()) == 2
	assert store.read_state()["autonomy"] == {}


def test_resume_refuses_symlinked_state_without_replacing_it(tmp_path):
	"""resume 直接原子写 state.json，不能绕过 write_state 的链接保护（Issue #1751）。"""
	store = AutomationStore(tmp_path)
	store._set_stop("人工停止")
	target = tmp_path / "outside-state.json"
	target.write_text(
		'{"autonomy": {"circuit_breaker": {"open": true, "reason": "旧熔断"}}}',
		encoding="utf-8",
	)
	_link(store.state_path, target)

	with pytest.raises(AutomationStateError) as exc_info:
		store.resume()

	assert "符号链接" in str(exc_info.value)
	assert store.state_path.is_symlink()
	assert "旧熔断" in target.read_text(encoding="utf-8")
	assert store.read_stop().get("open") is True, "失败不能解除熔断"
