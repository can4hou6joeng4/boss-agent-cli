"""boss clean — 清理过期缓存和临时文件。"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import click

from boss_agent_cli.display import handle_output


@click.command("clean")
@click.option("--dry-run", is_flag=True, default=False, help="仅预览将清理的内容，不实际删除")
@click.option("--all", "clean_all", is_flag=True, default=False, help="清理全部缓存（包括未过期的搜索缓存和打招呼记录）")
@click.option("--days", default=30, help="清理超过指定天数的快照和导出文件")
@click.pass_context
def clean_cmd(ctx: click.Context, dry_run: bool, clean_all: bool, days: int) -> None:
	"""清理过期缓存和临时文件。"""
	data_dir = ctx.obj["data_dir"]
	results = []
	total_freed = 0

	# 1) 搜索缓存
	freed, count = _clean_search_cache(data_dir, dry_run=dry_run, clean_all=clean_all)
	results.append({"target": "搜索缓存", "cleaned": count, "bytes_freed": freed})
	total_freed += freed

	# 2) 索引缓存
	freed, count = _clean_index_cache(data_dir, dry_run=dry_run)
	results.append({"target": "索引缓存", "cleaned": count, "bytes_freed": freed})
	total_freed += freed

	# 3) 聊天快照
	freed, count = _clean_dir(data_dir / "chat-history", days=days, dry_run=dry_run)
	results.append({"target": "聊天快照", "cleaned": count, "bytes_freed": freed})
	total_freed += freed

	# 4) 导出文件
	freed, count = _clean_dir(data_dir / "chat-export", days=days, dry_run=dry_run)
	results.append({"target": "导出文件", "cleaned": count, "bytes_freed": freed})
	total_freed += freed

	# 5) 全量清理额外项
	if clean_all:
		freed, count = _clean_greet_records(data_dir, dry_run=dry_run)
		results.append({"target": "打招呼记录", "cleaned": count, "bytes_freed": freed})
		total_freed += freed

		freed, count = _clean_apply_records(data_dir, dry_run=dry_run)
		results.append({"target": "投递记录", "cleaned": count, "bytes_freed": freed})
		total_freed += freed

	data = {
		"dry_run": dry_run,
		"results": results,
		"total_bytes_freed": total_freed,
		"total_bytes_freed_display": _format_size(total_freed),
	}

	hints: dict[str, list[str]] = {"next_actions": []}
	if dry_run:
		hints["next_actions"].append("boss clean — 执行实际清理")
	else:
		hints["next_actions"].append("boss doctor — 检查清理后环境状态")

	handle_output(ctx, "clean", data, hints=hints)


def _clean_search_cache(data_dir: Path, *, dry_run: bool, clean_all: bool) -> tuple[int, int]:
	"""清理搜索缓存，返回 (释放字节数, 清理条数)。"""
	db_path = data_dir / "cache" / "boss_agent.db"
	if not db_path.exists():
		return 0, 0

	conn = sqlite3.connect(str(db_path))
	try:
		if clean_all:
			row = conn.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(response)), 0) FROM search_cache").fetchone()
		else:
			cutoff = time.time() - 86400
			row = conn.execute(
				"SELECT COUNT(*), COALESCE(SUM(LENGTH(response)), 0) FROM search_cache WHERE created_at < ?",
				(cutoff,),
			).fetchone()

		count, size = row[0], row[1]
		if not dry_run and count > 0:
			if clean_all:
				conn.execute("DELETE FROM search_cache")
			else:
				conn.execute("DELETE FROM search_cache WHERE created_at < ?", (cutoff,))
			conn.commit()
			conn.execute("VACUUM")
		return size, count
	finally:
		conn.close()


def _clean_index_cache(data_dir: Path, *, dry_run: bool) -> tuple[int, int]:
	"""清理索引缓存文件。"""
	path = data_dir / "cache" / "index_cache.json"
	if not path.exists():
		return 0, 0
	size = path.stat().st_size
	if not dry_run:
		path.unlink()
	return size, 1


def _clean_dir(dir_path: Path, *, days: int, dry_run: bool) -> tuple[int, int]:
	"""清理指定目录中超过指定天数的文件。"""
	if not dir_path.exists():
		return 0, 0

	cutoff = time.time() - days * 86400
	total_size = 0
	count = 0

	for f in dir_path.iterdir():
		if f.is_file() and f.stat().st_mtime < cutoff:
			total_size += f.stat().st_size
			count += 1
			if not dry_run:
				f.unlink()

	return total_size, count


def _clean_greet_records(data_dir: Path, *, dry_run: bool) -> tuple[int, int]:
	"""清理打招呼记录。"""
	return _clean_table(data_dir, "greet_records", dry_run=dry_run)


def _clean_apply_records(data_dir: Path, *, dry_run: bool) -> tuple[int, int]:
	"""清理投递记录。"""
	return _clean_table(data_dir, "apply_records", dry_run=dry_run)


def _clean_table(data_dir: Path, table: str, *, dry_run: bool) -> tuple[int, int]:
	"""清理数据库表，返回 (估算字节数, 行数)。"""
	db_path = data_dir / "cache" / "boss_agent.db"
	if not db_path.exists():
		return 0, 0

	conn = sqlite3.connect(str(db_path))
	try:
		row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # noqa: S608
		count = row[0]
		estimated_size = count * 100
		if not dry_run and count > 0:
			conn.execute(f"DELETE FROM {table}")  # noqa: S608
			conn.commit()
		return estimated_size, count
	finally:
		conn.close()


def _format_size(size_bytes: int) -> str:
	"""格式化字节数为可读字符串。"""
	if size_bytes < 1024:
		return f"{size_bytes} B"
	elif size_bytes < 1024 * 1024:
		return f"{size_bytes / 1024:.1f} KB"
	else:
		return f"{size_bytes / (1024 * 1024):.1f} MB"
