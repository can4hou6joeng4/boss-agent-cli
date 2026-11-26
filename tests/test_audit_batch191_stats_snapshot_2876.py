"""stats 的全部计数必须来自同一 SQLite 读取快照（Issue #2876）。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from boss_agent_cli.commands import stats


_NOW = 2_000_000_001.0


def test_all_counts_share_one_read_snapshot_during_concurrent_write(tmp_path: Path, monkeypatch) -> None:
	"""累计查询完成后插入当前记录，窗口查询不能看到这条新提交。"""
	db_path = tmp_path / "cache" / "boss_agent.db"
	db_path.parent.mkdir(parents=True)
	real_connect = sqlite3.connect
	with real_connect(db_path) as setup:
		setup.execute("PRAGMA journal_mode=WAL")
		for table, column in (
			("greet_records", "greeted_at"),
			("apply_records", "applied_at"),
			("shortlist_records", "created_at"),
			("watch_hits", "first_seen_at"),
		):
			setup.execute(f"CREATE TABLE {table} ({column} REAL)")

	inserted: list[bool] = []

	def connect(path: str | Path):
		connection = real_connect(path)

		def trace(sql: str) -> None:
			if sql == "SELECT COUNT(*) FROM apply_records" and not inserted:
				with real_connect(db_path) as writer:
					writer.execute("INSERT INTO greet_records VALUES (?)", (_NOW,))
				inserted.append(True)

		connection.set_trace_callback(trace)
		return connection

	monkeypatch.setattr(stats.sqlite3, "connect", connect)
	monkeypatch.setattr(stats.time, "time", lambda: _NOW)

	data = stats._collect_stats(db_path, 30)

	assert inserted
	assert data["funnel"]["greeted"] == 0
	assert data["window"]["greeted"] == 0
