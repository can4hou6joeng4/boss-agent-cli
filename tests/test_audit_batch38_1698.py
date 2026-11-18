"""聊天快照路径状态的 fail-closed 回归测试（Issue #1698）。

`_merge_and_write()` 原来用 `os.path.exists()` 判断当天是否已有快照；
3.14 会把权限等元数据错误吞成 False，随后原子写用当前页整份覆盖当天
已合并的分页结果。修复后只有 FileNotFoundError 才算没有历史。
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from boss_agent_cli.services.chat_snapshot import save_snapshot_and_diff


def _items(*entries: tuple[str, str, int]) -> list[dict[str, Any]]:
	return [{"name": name, "uid": uid, "unread": unread} for name, uid, unread in entries]


def _today_path(snapshot_dir: str) -> Path:
	return Path(snapshot_dir) / f"{datetime.date.today().isoformat()}.json"


def _seed(snapshot_dir: Path, items: list[dict[str, Any]]) -> Path:
	snapshot_dir.mkdir(parents=True, exist_ok=True)
	path = _today_path(str(snapshot_dir))
	path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
	return path


def _fail_lstat_for(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	real_lstat = Path.lstat

	def lstat_fails(self: Path) -> Any:
		if self == path:
			raise PermissionError("permission denied")
		return real_lstat(self)

	monkeypatch.setattr(Path, "lstat", lstat_fails)


def test_metadata_error_does_not_overwrite_today_snapshot(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	snapshot_dir = tmp_path / "snapshots"
	path = _seed(snapshot_dir, _items(("甲", "uid-a", 0), ("乙", "uid-b", 1)))
	original = path.read_bytes()
	_fail_lstat_for(path, monkeypatch)

	with pytest.raises(OSError):
		save_snapshot_and_diff(str(snapshot_dir), _items(("丙", "uid-c", 0)), MagicMock())

	assert path.read_bytes() == original


def test_directory_at_snapshot_path_is_rejected(tmp_path: Path) -> None:
	snapshot_dir = tmp_path / "snapshots"
	path = _today_path(str(snapshot_dir))
	path.mkdir(parents=True)

	with pytest.raises(OSError):
		save_snapshot_and_diff(str(snapshot_dir), _items(("甲", "uid-a", 0)), MagicMock())

	assert path.is_dir()


def test_dangling_symlink_is_rejected_without_creating_target(tmp_path: Path) -> None:
	snapshot_dir = tmp_path / "snapshots"
	path = _today_path(str(snapshot_dir))
	snapshot_dir.mkdir(parents=True)
	target = tmp_path / "outside.json"
	path.symlink_to(target)

	with pytest.raises(OSError):
		save_snapshot_and_diff(str(snapshot_dir), _items(("甲", "uid-a", 0)), MagicMock())

	assert not target.exists()
	assert path.is_symlink()


def test_unreadable_today_snapshot_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	snapshot_dir = tmp_path / "snapshots"
	path = _seed(snapshot_dir, _items(("甲", "uid-a", 0)))
	original = path.read_bytes()
	real_read_bytes = Path.read_bytes

	def read_bytes_fails(self: Path) -> bytes:
		if self == path:
			raise PermissionError("permission denied")
		return real_read_bytes(self)

	monkeypatch.setattr(Path, "read_bytes", read_bytes_fails)

	with pytest.raises(PermissionError):
		save_snapshot_and_diff(str(snapshot_dir), _items(("乙", "uid-b", 0)), MagicMock())

	assert real_read_bytes(path) == original


def test_corrupt_today_snapshot_still_falls_back_to_no_history(tmp_path: Path) -> None:
	snapshot_dir = tmp_path / "snapshots"
	snapshot_dir.mkdir()
	path = _today_path(str(snapshot_dir))
	path.write_text("not valid json", encoding="utf-8")

	save_snapshot_and_diff(str(snapshot_dir), _items(("甲", "uid-a", 0)), MagicMock())

	assert json.loads(path.read_text(encoding="utf-8")) == _items(("甲", "uid-a", 0))


def test_paged_export_still_merges_today_contacts(tmp_path: Path) -> None:
	snapshot_dir = tmp_path / "snapshots"

	save_snapshot_and_diff(str(snapshot_dir), _items(("甲", "uid-a", 0)), MagicMock())
	save_snapshot_and_diff(str(snapshot_dir), _items(("乙", "uid-b", 0)), MagicMock())

	saved = json.loads(_today_path(str(snapshot_dir)).read_text(encoding="utf-8"))
	assert {item["uid"] for item in saved} == {"uid-a", "uid-b"}
