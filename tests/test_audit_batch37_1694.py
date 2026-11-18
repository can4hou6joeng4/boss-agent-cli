"""面试线索 CSV 路径状态的 fail-closed 回归测试（Issue #1694）。

`append_interview_lead()` 原来用 `path.exists()` 判断要不要写表头；3.14 会把
权限等元数据错误吞成 False，已有线索历史随即被表头写入截断。修复后只有
``FileNotFoundError`` 才算缺失，其余 OSError / 非普通文件都必须失败。
"""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

import pytest

from boss_agent_cli.automation.storage import AutomationStateError, AutomationStore


def _csv_path(tmp_path: Path) -> Path:
	return tmp_path / "automation" / "interview-leads.csv"


def _ensure_root(tmp_path: Path) -> Path:
	path = _csv_path(tmp_path)
	path.parent.mkdir(parents=True, exist_ok=True)
	return path


def _seed(tmp_path: Path) -> Path:
	store = AutomationStore(tmp_path)
	assert store.append_interview_lead("候选人甲", "周二 14:00", "预约")
	path = _csv_path(tmp_path)
	assert len(path.read_text(encoding="utf-8").splitlines()) == 2
	return path


def _fail_lstat_for(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	real_lstat = Path.lstat

	def lstat_fails(self: Path) -> Any:
		if self == path:
			raise PermissionError("permission denied")
		return real_lstat(self)

	monkeypatch.setattr(Path, "lstat", lstat_fails)


def test_metadata_error_does_not_truncate_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	path = _seed(tmp_path)
	original = path.read_bytes()
	_fail_lstat_for(path, monkeypatch)

	with pytest.raises(AutomationStateError) as caught:
		AutomationStore(tmp_path).append_interview_lead("候选人乙", "周三 10:00", "预约")

	assert caught.value.path == path
	assert path.read_bytes() == original


def test_directory_at_csv_path_is_rejected(tmp_path: Path) -> None:
	path = _csv_path(tmp_path)
	path.mkdir(parents=True)

	with pytest.raises(AutomationStateError):
		AutomationStore(tmp_path).append_interview_lead("候选人甲", "周二 14:00", "预约")
	assert path.is_dir()


def test_dangling_symlink_is_rejected_without_creating_target(tmp_path: Path) -> None:
	path = _ensure_root(tmp_path)
	target = tmp_path / "outside.csv"
	path.symlink_to(target)

	with pytest.raises(AutomationStateError):
		AutomationStore(tmp_path).append_interview_lead("候选人甲", "周二 14:00", "预约")
	assert not target.exists()
	assert path.is_symlink()


def test_fifo_is_rejected_without_blocking_or_reading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	from boss_agent_cli.automation import storage as storage_module

	path = _ensure_root(tmp_path)
	path.write_bytes(b"placeholder")
	original_path_mode = storage_module._path_mode

	monkeypatch.setattr(
		storage_module,
		"_path_mode",
		lambda candidate: stat.S_IFIFO if candidate == path else original_path_mode(candidate),
	)

	def must_not_open(self: Path, *args: Any, **kwargs: Any) -> Any:
		raise AssertionError(f"不能打开非普通文件: {self}")

	monkeypatch.setattr(Path, "open", must_not_open)
	monkeypatch.setattr(Path, "write_text", must_not_open)

	with pytest.raises(AutomationStateError):
		AutomationStore(tmp_path).append_interview_lead("候选人甲", "周二 14:00", "预约")


def test_symlink_to_regular_file_appends_through_link(tmp_path: Path) -> None:
	path = _ensure_root(tmp_path)
	target = tmp_path / "shared.csv"
	target.write_text("candidate_key,interview_time,reason\n", encoding="utf-8")
	path.symlink_to(target)

	assert AutomationStore(tmp_path).append_interview_lead("候选人甲", "周二 14:00", "预约")
	assert target.read_text(encoding="utf-8").splitlines()[1].startswith("候选人甲")
	assert path.is_symlink()


def test_missing_file_creates_header_and_row(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	assert store.append_interview_lead("候选人甲", "周二 14:00", "预约")
	lines = _csv_path(tmp_path).read_text(encoding="utf-8").splitlines()
	assert lines[0] == "candidate_key,interview_time,reason"
	assert lines[1].startswith("候选人甲")


def test_duplicate_returns_false_without_appending(tmp_path: Path) -> None:
	path = _seed(tmp_path)
	store = AutomationStore(tmp_path)
	assert not store.append_interview_lead("候选人甲", "周二 14:00", "预约")
	assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_unreadable_existing_file_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	path = _seed(tmp_path)
	real_open = Path.open

	def open_fails(self: Path, *args: Any, **kwargs: Any) -> Any:
		if self == path and args and args[0] == "r":
			raise PermissionError("permission denied")
		return real_open(self, *args, **kwargs)

	monkeypatch.setattr(Path, "open", open_fails)

	with pytest.raises(AutomationStateError):
		AutomationStore(tmp_path).append_interview_lead("候选人乙", "周三 10:00", "预约")
	assert len(path.read_text(encoding="utf-8").splitlines()) == 2
