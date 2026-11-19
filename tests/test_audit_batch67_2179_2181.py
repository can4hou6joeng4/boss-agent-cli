"""聊天快照的覆盖缺失、负未读数与跨日读取顺序（Issue #2179 / #2180 / #2181）。

- 当前页有条目因缺少身份被丢弃时，不推断「已消失」，落盘标记降级；
- 旧快照混有无法识别的条目时，不作为判断「新增」的完整基线；
- 历史负未读数恢复为 0 不算新增未读；
- 持同一把锁读标记与快照本体，避免跨日并发读到不同版本。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from boss_agent_cli.output import Logger
from boss_agent_cli.services import chat_snapshot as snapshot
from boss_agent_cli.services.file_lock import exclusive_file_lock, run_lock_path


def _write_previous(directory: Path, payload: list[object], *, complete: bool | None = None) -> Path:
	path = directory / f"{date.today() - timedelta(days=1)}.json"
	path.write_text(json.dumps(payload), encoding="utf-8")
	if complete is not None:
		Path(snapshot._meta_path(str(path))).write_text(
			json.dumps({"complete": complete}), encoding="utf-8"
		)
	return path


def _today_meta(directory: Path) -> dict[str, object]:
	path = directory / f"{date.today()}.meta"
	return json.loads(path.read_text(encoding="utf-8"))


# ── Issue #2179：丢弃条目后不能声称完整 ─────────────────────────────


def test_current_dropped_entry_suppresses_removed_and_marks_incomplete(tmp_path: Path) -> None:
	a, b = {"uid": "100"}, {"uid": "200"}
	bad_a = {"uid": True, "security_id": None}
	_write_previous(tmp_path, [a, b])

	diff = snapshot.save_snapshot_and_diff(str(tmp_path), [bad_a, b], Logger(), complete=True)

	# 坏条目只是读不出来，不能证明该联系人消失。
	assert diff["removed"] == []
	assert diff["complete"] is False
	assert _today_meta(tmp_path) == {"complete": False}
	# 坏条目本身不会写进快照。
	assert json.loads((tmp_path / f"{date.today()}.json").read_text()) == [b]


def test_baseline_with_dropped_entry_suppresses_added(tmp_path: Path) -> None:
	a, b = {"uid": "100"}, {"uid": "200"}
	bad_a = {"uid": True, "security_id": None}
	_write_previous(tmp_path, [bad_a, b])

	diff = snapshot.save_snapshot_and_diff(str(tmp_path), [a, b], Logger(), complete=True)

	# 旧快照里的坏条目可能正是补齐身份后的 a：不能宣称 a 是新增。
	assert diff["added"] == []
	assert diff["baseline_complete"] is False


def test_all_usable_complete_snapshot_keeps_marker(tmp_path: Path) -> None:
	a = {"uid": "100"}
	_write_previous(tmp_path, [])

	diff = snapshot.save_snapshot_and_diff(str(tmp_path), [a], Logger(), complete=True)

	assert diff["complete"] is True
	assert _today_meta(tmp_path) == {"complete": True}


# ── Issue #2180：负未读数 ──────────────────────────────────────────


def test_negative_historical_unread_is_not_new_unread(tmp_path: Path) -> None:
	_write_previous(tmp_path, [{"uid": "100", "unread": -1}])

	diff = snapshot.save_snapshot_and_diff(
		str(tmp_path), [{"uid": "100", "unread": 0}], Logger(), complete=True
	)

	assert diff["new_unread"] == []


def test_negative_current_unread_does_not_compare_as_increase(tmp_path: Path) -> None:
	_write_previous(tmp_path, [{"uid": "100", "unread": 2}])

	diff = snapshot.save_snapshot_and_diff(
		str(tmp_path), [{"uid": "100", "unread": -1}], Logger(), complete=True
	)

	assert diff["new_unread"] == []


# ── Issue #2181：在同一把日锁下读快照与标记 ─────────────────────────


def test_previous_snapshot_and_marker_hold_the_writer_lock(tmp_path: Path) -> None:
	a, b = {"uid": "100"}, {"uid": "200"}
	previous = _write_previous(tmp_path, [a], complete=False)
	lock = run_lock_path(tmp_path, f"chat-snapshot-{previous.name}")
	read_snapshot = snapshot._load_snapshot_detailed
	read_marker = snapshot._read_complete_marker

	def check_snapshot_lock(path: str, logger: Logger):
		with exclusive_file_lock(lock) as acquired:
			assert not acquired
		return read_snapshot(path, logger)

	def check_marker_lock(path: str) -> bool:
		with exclusive_file_lock(lock) as acquired:
			assert not acquired
		return read_marker(path)

	with (
		patch.object(snapshot, "_load_snapshot_detailed", side_effect=check_snapshot_lock),
		patch.object(snapshot, "_read_complete_marker", side_effect=check_marker_lock),
	):
		diff = snapshot.save_snapshot_and_diff(str(tmp_path), [a, b], Logger(), complete=True)

	assert diff["baseline_complete"] is False
	assert diff["added"] == []


def test_partial_write_drops_stale_complete_marker_from_mixed_same_day_snapshot(tmp_path: Path) -> None:
	today = date.today()
	(tmp_path / f"{today}.json").write_text(json.dumps([{"uid": "100"}, None]), encoding="utf-8")
	(tmp_path / f"{today}.meta").write_text(json.dumps({"complete": True}), encoding="utf-8")

	snapshot.save_snapshot_and_diff(str(tmp_path), [{"uid": "200"}], Logger(), complete=False)

	assert _today_meta(tmp_path) == {"complete": False}


def test_load_snapshot_public_wrapper_still_returns_items(tmp_path: Path) -> None:
	a = {"uid": "100"}
	path = tmp_path / "snapshot.json"
	path.write_text(json.dumps([a]), encoding="utf-8")

	assert snapshot.load_snapshot(str(path), Logger()) == [a]
