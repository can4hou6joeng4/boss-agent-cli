"""boss stats — 投递转化漏斗统计。

只读聚合本地缓存数据，给出打招呼 → 投递 → 候选池 → 监控新增的全景视图。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import click

from boss_agent_cli.display import handle_output


def _safe_count(conn: sqlite3.Connection, table: str) -> int:
	try:
		return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
	except sqlite3.OperationalError:
		return 0


def _count_since(conn: sqlite3.Connection, table: str, column: str, since: float) -> int:
	try:
		return conn.execute(
			f"SELECT COUNT(*) FROM {table} WHERE {column} >= ?", (since,)
		).fetchone()[0]
	except sqlite3.OperationalError:
		return 0


def _ratio(numer: int, denom: int) -> float:
	if denom <= 0:
		return 0.0
	return round(numer / denom, 4)


@click.command("stats")
@click.option("--days", default=30, type=int, help="统计窗口天数（默认 30 天）")
@click.pass_context
def stats_cmd(ctx, days):
	"""投递转化漏斗统计（只读聚合）"""
	data_dir: Path = ctx.obj["data_dir"]
	db_path = data_dir / "cache" / "boss_agent.db"

	if not db_path.exists():
		handle_output(
			ctx, "stats",
			{
				"funnel": {"greeted": 0, "applied": 0, "shortlist": 0, "watch_hits": 0},
				"conversion": {"apply_rate": 0.0, "shortlist_rate": 0.0},
				"window_days": days,
				"note": "缓存尚未建立，先跑一次 search/greet/apply 再查看",
			},
		)
		return

	since = time.time() - days * 86400
	conn = sqlite3.connect(str(db_path))
	try:
		greeted_total = _safe_count(conn, "greet_records")
		applied_total = _safe_count(conn, "apply_records")
		shortlist_total = _safe_count(conn, "shortlist_records")

		greeted_window = _count_since(conn, "greet_records", "greeted_at", since)
		applied_window = _count_since(conn, "apply_records", "applied_at", since)
		shortlist_window = _count_since(conn, "shortlist_records", "created_at", since)
		watch_window = _count_since(conn, "watch_hits", "first_seen_at", since)
	finally:
		conn.close()

	data = {
		"window_days": days,
		"funnel": {
			"greeted": greeted_total,
			"applied": applied_total,
			"shortlist": shortlist_total,
		},
		"window": {
			"greeted": greeted_window,
			"applied": applied_window,
			"shortlist": shortlist_window,
			"watch_hits": watch_window,
		},
		"conversion": {
			"apply_rate": _ratio(applied_total, greeted_total),
			"shortlist_rate": _ratio(shortlist_total, greeted_total),
			"apply_rate_window": _ratio(applied_window, greeted_window),
		},
	}

	hints: list[str] = []
	if greeted_total == 0:
		hints.append("boss search <query> 搜索职位")
	elif applied_total == 0 and greeted_total > 0:
		hints.append("boss apply <security_id> <job_id> 发起投递")
	if data["conversion"]["apply_rate"] < 0.1 and greeted_total >= 20:
		hints.append("打招呼转投递率偏低，考虑调整目标岗位或优化简历（boss ai optimize）")
	hints.append("boss pipeline 查看候选进度")
	hints.append("boss follow-up 查看需要跟进的联系人")

	handle_output(ctx, "stats", data, hints={"next_actions": hints})
