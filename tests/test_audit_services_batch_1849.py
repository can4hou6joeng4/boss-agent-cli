"""output_path / file_lock / chat 快照完整性（Issue #1849 #1850 #1852）。"""

import datetime
import errno
import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from boss_agent_cli.services import chat_snapshot
from boss_agent_cli.services.chat_export import render_export
from boss_agent_cli.services.file_lock import try_lock_exclusive
from boss_agent_cli.services.output_path import (
	prepare_output_path,
	refuse_symlink_target,
)


def _friend(uid: str) -> dict[str, object]:
	return {"uid": uid, "security_id": f"sid-{uid}", "name": f"联系人{uid}", "unread": 0, "msg_status": "已读"}


def _snapshot_path(directory: Path, days_ago: int) -> Path:
	day = datetime.date.today() - datetime.timedelta(days=days_ago)
	return directory / f"{day.isoformat()}.json"


def _meta_path(snapshot: Path) -> Path:
	return Path(str(snapshot)[: -len(".json")] + ".meta")


# ── Issue #1849：prepare_output_path 拒绝符号链接与非普通文件 ─────────


def test_prepare_output_path_refuses_symlink_target(tmp_path: Path) -> None:
	real = tmp_path / "real.md"
	real.write_text("old", encoding="utf-8")
	link = tmp_path / "link.md"
	link.symlink_to(real)

	with pytest.raises(OSError) as exc_info:
		prepare_output_path(link)

	assert exc_info.value.errno == errno.EINVAL
	assert "拒绝写入符号链接目标" in str(exc_info.value)
	assert real.read_text(encoding="utf-8") == "old"


def test_prepare_output_path_refuses_dangling_symlink(tmp_path: Path) -> None:
	link = tmp_path / "dangling.md"
	link.symlink_to(tmp_path / "missing.md")

	with pytest.raises(OSError) as exc_info:
		prepare_output_path(link)

	assert exc_info.value.errno == errno.EINVAL


def test_prepare_output_path_refuses_directory(tmp_path: Path) -> None:
	target = tmp_path / "out"
	target.mkdir()

	with pytest.raises(IsADirectoryError):
		prepare_output_path(target)


def test_prepare_output_path_allows_missing_and_regular(tmp_path: Path) -> None:
	regular = tmp_path / "existing.md"
	regular.write_text("ok", encoding="utf-8")

	assert prepare_output_path(tmp_path / "nested" / "new.md") == tmp_path / "nested" / "new.md"
	assert (tmp_path / "nested").is_dir()
	assert prepare_output_path(regular) == regular


def test_refuse_symlink_target_uses_lstat(tmp_path: Path) -> None:
	real = tmp_path / "real.md"
	real.write_text("ok", encoding="utf-8")
	link = tmp_path / "link.md"
	link.symlink_to(real)

	refuse_symlink_target(tmp_path / "missing.md")
	refuse_symlink_target(real)
	with pytest.raises(OSError):
		refuse_symlink_target(link)


# ── Issue #1850：file_lock 只把真正的锁冲突当 busy ───────────────────


posix_only = pytest.mark.skipif(os.name == "nt", reason="flock 只在 POSIX 上使用")


def _flock_raising(error: BaseException):
	def raiser(*args: object, **kwargs: object) -> None:
		raise error

	return raiser


@posix_only
@pytest.mark.parametrize("err", [errno.EAGAIN, errno.EWOULDBLOCK, errno.EDEADLK])
def test_try_lock_treats_contention_errnos_as_busy(tmp_path: Path, monkeypatch, err: int) -> None:
	import fcntl

	monkeypatch.setattr(fcntl, "flock", _flock_raising(OSError(err, "busy")))
	with (tmp_path / "lease").open("a+b") as handle:
		assert try_lock_exclusive(handle) is False


@posix_only
@pytest.mark.parametrize("err", [errno.ENAMETOOLONG, errno.EINVAL, errno.ENOSPC])
def test_try_lock_reraises_non_contention_errnos(tmp_path: Path, monkeypatch, err: int) -> None:
	import fcntl

	monkeypatch.setattr(fcntl, "flock", _flock_raising(OSError(err, "not a lock conflict")))
	with (tmp_path / "lease").open("a+b") as handle:
		with pytest.raises(OSError) as exc_info:
			try_lock_exclusive(handle)
	assert exc_info.value.errno == err


# ── Issue #1852：上一份快照不完整时不能宣称新增 ───────────────────────


def test_incomplete_baseline_does_not_report_added(tmp_path: Path) -> None:
	prev = _snapshot_path(tmp_path, 1)
	prev.write_text(json.dumps([_friend("A")]), encoding="utf-8")
	_meta_path(prev).write_text(json.dumps({"complete": False}), encoding="utf-8")

	result = chat_snapshot.save_snapshot_and_diff(str(tmp_path), [_friend("A"), _friend("B")], MagicMock(), complete=True)

	assert result["baseline_complete"] is False
	assert result["added"] == []


def test_legacy_snapshot_without_marker_keeps_previous_behavior(tmp_path: Path) -> None:
	prev = _snapshot_path(tmp_path, 1)
	prev.write_text(json.dumps([_friend("A")]), encoding="utf-8")

	result = chat_snapshot.save_snapshot_and_diff(str(tmp_path), [_friend("A"), _friend("B")], MagicMock(), complete=True)

	assert result["baseline_complete"] is True
	assert [item["uid"] for item in result["added"]] == ["B"]


def test_corrupt_marker_is_treated_as_incomplete(tmp_path: Path) -> None:
	prev = _snapshot_path(tmp_path, 1)
	prev.write_text(json.dumps([_friend("A")]), encoding="utf-8")
	_meta_path(prev).write_text("{not json", encoding="utf-8")

	result = chat_snapshot.save_snapshot_and_diff(str(tmp_path), [_friend("A"), _friend("B")], MagicMock(), complete=True)

	assert result["baseline_complete"] is False
	assert result["added"] == []


def test_marker_tracks_completeness_of_todays_snapshot(tmp_path: Path) -> None:
	_snapshot_path(tmp_path, 1).write_text(json.dumps([_friend("A"), _friend("B")]), encoding="utf-8")
	today = _snapshot_path(tmp_path, 0)

	chat_snapshot.save_snapshot_and_diff(str(tmp_path), [_friend("A")], MagicMock(), complete=False)
	assert json.loads(_meta_path(today).read_text(encoding="utf-8")) == {"complete": False}

	chat_snapshot.save_snapshot_and_diff(str(tmp_path), [_friend("A")], MagicMock(), complete=True)
	assert json.loads(_meta_path(today).read_text(encoding="utf-8")) == {"complete": True}


def test_diff_summary_mentions_incomplete_baseline() -> None:
	text = render_export(
		[_friend("A")],
		"md",
		None,
		None,
		{
			"is_first": False,
			"prev_date": "2026-11-18",
			"complete": True,
			"baseline_complete": False,
			"added": [],
			"removed": [],
			"new_unread": [],
		},
	)

	assert "上次采集不完整，未判断新增" in text
