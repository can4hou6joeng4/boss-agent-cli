"""批次五十审计修复（Issue #1730）：上一份聊天快照为 FIFO 时不再阻塞。

`load_snapshot()` 直接 `open()`：快照目录里出现按日期命名的 FIFO 时，
`chat --export` 在写完当天快照后永久等待。当天快照读取已在 #1698 走
`read_bytes_if_regular()`，这条路径现在同口径。
"""

import datetime
import json
import os
import stat
from unittest.mock import MagicMock

import pytest

from boss_agent_cli.services.chat_snapshot import load_snapshot, save_snapshot_and_diff


def _yesterday() -> str:
	return (datetime.date.today() - datetime.timedelta(days=1)).isoformat()


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有 POSIX FIFO")
def test_fifo_previous_snapshot_does_not_block_export_diff(tmp_path):
	snapshot_dir = tmp_path / "snapshots"
	snapshot_dir.mkdir()
	fifo = snapshot_dir / f"{_yesterday()}.json"
	os.mkfifo(fifo)

	result = save_snapshot_and_diff(
		str(snapshot_dir),
		[{"name": "甲", "uid": "uid-a", "unread": 0}],
		MagicMock(),
	)

	# 上一天快照读不出来只能让 diff 退化成「首份快照」，不能挂死导出。
	assert result["is_first"] is True
	assert stat.S_ISFIFO(fifo.lstat().st_mode), "FIFO 不能被替换或删除"


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有 POSIX FIFO")
def test_load_snapshot_fifo_warns_and_returns_none(tmp_path):
	fifo = tmp_path / "2026-01-01.json"
	os.mkfifo(fifo)
	logger = MagicMock()

	assert load_snapshot(str(fifo), logger) is None
	logger.warning.assert_called_once()


def test_load_snapshot_dangling_symlink_returns_none(tmp_path):
	link = tmp_path / "2026-01-01.json"
	try:
		link.symlink_to(tmp_path / "missing.json")
	except (OSError, NotImplementedError):
		pytest.skip("当前文件系统不支持符号链接")
	logger = MagicMock()

	assert load_snapshot(str(link), logger) is None
	logger.warning.assert_called_once()


def test_load_snapshot_symlink_to_regular_file_still_reads(tmp_path):
	real = tmp_path / "real.json"
	real.write_text(json.dumps([{"uid": "uid-a"}]), encoding="utf-8")
	link = tmp_path / "2026-01-01.json"
	try:
		link.symlink_to(real)
	except (OSError, NotImplementedError):
		pytest.skip("当前文件系统不支持符号链接")

	assert load_snapshot(str(link), MagicMock()) == [{"uid": "uid-a"}]


def test_load_snapshot_missing_file_is_unavailable(tmp_path):
	logger = MagicMock()

	assert load_snapshot(str(tmp_path / "missing.json"), logger) is None
	logger.warning.assert_called_once()
