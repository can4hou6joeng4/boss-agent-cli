"""Issue #2929：基线里的空 uid 不能按轮换 security_id 参与跨天 diff。"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

from boss_agent_cli.services.chat_snapshot import save_snapshot_and_diff


class _Logger:
	def warning(self, message: str) -> None:
		pass


def _write_previous(snapshot_dir: str, items: list[dict]) -> None:
	os.makedirs(snapshot_dir, exist_ok=True)
	day = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
	Path(snapshot_dir, f"{day}.json").write_text(json.dumps(items), encoding="utf-8")
	Path(snapshot_dir, f"{day}.meta").write_text(json.dumps({"complete": True}), encoding="utf-8")


def _today_snapshot(snapshot_dir: str) -> list[dict]:
	day = datetime.date.today().isoformat()
	return json.loads(Path(snapshot_dir, f"{day}.json").read_text(encoding="utf-8"))


def test_empty_uid_baseline_row_is_not_reported_as_removed(tmp_path: Path) -> None:
	snapshot_dir = str(tmp_path / "snapshots")
	_write_previous(snapshot_dir, [
		{"uid": "u1", "security_id": "s-day2", "unread": 0, "name": "演示用户"},
		{"uid": "", "security_id": "s-rot-2", "unread": 0, "name": "演示用户"},
	])

	result = save_snapshot_and_diff(
		snapshot_dir,
		[{"uid": "u1", "security_id": "s-day3", "unread": 1, "name": "演示用户"}],
		_Logger(),
		complete=True,
	)

	assert result["removed"] == []
	assert result["added"] == []
	assert [item["uid"] for item in result["new_unread"]] == ["u1"]
	assert all(item["uid"] for item in _today_snapshot(snapshot_dir))


def test_empty_uid_row_is_kept_for_paging_but_not_used_as_diff_identity(tmp_path: Path) -> None:
	snapshot_dir = str(tmp_path / "snapshots")

	result = save_snapshot_and_diff(
		snapshot_dir,
		[
			{"uid": "u1", "security_id": "s-a", "unread": 0, "name": "演示用户"},
			{"uid": "", "security_id": "s-rot", "unread": 2, "name": "演示用户"},
		],
		_Logger(),
		complete=False,
	)

	assert result["complete"] is False
	rows = _today_snapshot(snapshot_dir)
	assert {row["security_id"] for row in rows} == {"s-a", "s-rot"}

	# 把这份不完整观察当作昨天的基线；今天完整导出只有稳定 uid 那条。
	day = datetime.date.today().isoformat()
	previous = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
	Path(snapshot_dir, f"{previous}.json").write_text(
		Path(snapshot_dir, f"{day}.json").read_text(encoding="utf-8"), encoding="utf-8"
	)
	Path(snapshot_dir, f"{previous}.meta").write_text(json.dumps({"complete": False}), encoding="utf-8")
	Path(snapshot_dir, f"{day}.json").unlink()
	Path(snapshot_dir, f"{day}.meta").unlink()

	next_day = save_snapshot_and_diff(
		snapshot_dir,
		[{"uid": "u1", "security_id": "s-new", "unread": 2, "name": "演示用户"}],
		_Logger(),
		complete=True,
	)

	assert next_day["removed"] == []


def test_legacy_record_without_uid_key_still_diffed_by_security_id(tmp_path: Path) -> None:
	snapshot_dir = str(tmp_path / "snapshots")
	_write_previous(snapshot_dir, [{"security_id": "legacy-token", "unread": 0, "name": "历史用户"}])

	result = save_snapshot_and_diff(
		snapshot_dir,
		[{"security_id": "legacy-token", "unread": 1, "name": "历史用户"}],
		_Logger(),
		complete=True,
	)

	assert result["added"] == []
	assert result["removed"] == []
	assert len(result["new_unread"]) == 1
