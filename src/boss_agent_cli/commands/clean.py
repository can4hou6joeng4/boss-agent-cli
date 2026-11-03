"""boss clean — 清理过期缓存和临时文件。"""

from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

import click

from rich.table import Table

from boss_agent_cli import display
from boss_agent_cli.api.cdp_risk_lock import lock_path
from boss_agent_cli.api.request_budget import budget_directory
from boss_agent_cli.cache.store import CacheDatabaseInvalid
from boss_agent_cli.display import handle_output, render_next_steps


@click.command("clean")
@click.option("--dry-run", is_flag=True, default=False, help="仅预览将清理的内容，不实际删除")
@click.option(
	"--all", "clean_all", is_flag=True, default=False, help="清理全部缓存（包括未过期的搜索缓存和打招呼记录）"
)
@click.option("--privacy", is_flag=True, default=False, help="清理本地敏感数据（登录会话、简历、聊天快照和导出文件）")
# 负数会让 cutoff 落到未来（time.time() - days * 86400），目录里所有文件都算「过期」，
# 一次 `clean --days -1` 就把聊天快照和导出全部删掉。上限 100 年则挡住整数溢出：
# 天数要转成 float，10**400 这类无界整数会抛 OverflowError 而不是给出信封。
@click.option("--days", default=30, type=click.IntRange(0, 36500), help="清理超过指定天数的快照和导出文件")
@click.option(
	"--risk-lock",
	"risk_lock",
	is_flag=True,
	default=False,
	help="用户确认官方目标业务页恢复后显式清除 BOSS 本地风控锁；不解除服务器风险",
)
@click.pass_context
def clean_cmd(ctx: click.Context, dry_run: bool, clean_all: bool, privacy: bool, days: int, risk_lock: bool) -> None:
	"""清理过期缓存和临时文件。"""
	data_dir = ctx.obj["data_dir"]
	results = []
	total_freed = 0

	# 库文件存在但读不动时，先在这里失败：main.py 会把 CacheDatabaseInvalid 转成
	# CACHE_DATABASE_INVALID 信封。否则 --privacy 会先删掉登录态和简历，再在
	# sqlite 里抛 traceback，用户既看不到信封，也不知道哪些文件已经删了。
	db_path = data_dir / "cache" / "boss_agent.db"
	if db_path.exists():
		_open_cache_db(db_path).close()

	# 0) 本地风险锁：显式 opt-in，文件名及 target 标签沿用历史协议，不连浏览器。
	if risk_lock:
		directories = dict.fromkeys((Path(data_dir).resolve(), budget_directory(Path(data_dir))))
		freed, count = 0, 0
		for directory in directories:
			removed_bytes, removed_count = _clean_path(lock_path(directory), dry_run=dry_run)
			freed += removed_bytes
			count += removed_count
		results.append({"target": "CDP 风控锁", "cleaned": count, "bytes_freed": freed})
		total_freed += freed

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

	# 5) 隐私清理：显式 opt-in，避免误删登录态和本地简历
	if privacy:
		for target, path in (
			("登录会话", data_dir / "auth" / "session.enc"),
			("本地简历", data_dir / "resumes"),
			("聊天快照", data_dir / "chat-history"),
			("导出文件", data_dir / "chat-export"),
		):
			freed, count = _clean_path(path, dry_run=dry_run)
			results.append({"target": target, "cleaned": count, "bytes_freed": freed})
			total_freed += freed
		freed, count = _clean_crawl_data(data_dir, dry_run=dry_run)
		results.append({"target": "crawl 任务与导出", "cleaned": count, "bytes_freed": freed})
		total_freed += freed

	# 6) 全量清理额外项
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
	if risk_lock:
		data["risk_lock"] = {
			"local_lock_removed": bool(results[0]["cleaned"] and not dry_run),
			"platform_risk_status": "unverified",
		}

	hints: dict[str, list[str]] = {"next_actions": []}
	operator_actions: list[str] = []
	if risk_lock:
		operator_actions.append(
			"本地清锁不解除服务器风险；请确认官方目标业务页已恢复，再由用户显式恢复原任务，若再次命中风险立即停止"
		)
	if dry_run and risk_lock:
		operator_actions.append("预演未解除锁；确认恢复后由用户执行 boss clean --risk-lock")
	elif dry_run:
		hints["next_actions"].append("boss clean")
	elif privacy:
		hints["next_actions"].append("boss login")
		hints["next_actions"].append("boss doctor")
		operator_actions.append("隐私清理会清除本地登录态，需要重新登录后再继续")
	else:
		hints["next_actions"].append("boss doctor")
	if operator_actions:
		hints["operator_actions"] = operator_actions

	handle_output(
		ctx,
		"clean",
		data,
		render=lambda d: _render_clean(d, hints["next_actions"]),
		hints=hints,
	)


def _render_clean(data: dict[str, Any], next_steps: list[str]) -> None:
	"""清理结果表格（TTY 路径）。dry-run 时明确标注未真正删除。"""
	title = "clean（预演，未实际删除）" if data.get("dry_run") else "clean"
	table = Table(title=title)
	table.add_column("目标", style="bold cyan")
	table.add_column("条数", justify="right", style="green")
	table.add_column("释放", justify="right", style="yellow")
	for row in data.get("results", []):
		table.add_row(
			str(row.get("target", "-")),
			str(row.get("cleaned", 0)),
			_human_bytes(row.get("bytes_freed", 0)),
		)
	display.console.print(table)
	display.console.print(f"  合计释放 [bold]{data.get('total_bytes_freed_display', '0 B')}[/bold]")
	render_next_steps(next_steps)


def _human_bytes(value: int) -> str:
	"""字节数转人类可读；与信封里的 total_bytes_freed_display 口径一致。"""
	size = float(value)
	for unit in ("B", "KB", "MB", "GB"):
		if size < 1024 or unit == "GB":
			return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
		size /= 1024
	return f"{size:.1f} GB"


def _open_cache_db(db_path: Path) -> sqlite3.Connection:
	"""打开缓存库，并把 sqlite3.DatabaseError 归一成 CacheDatabaseInvalid。

	clean 直接操作 SQLite，不经过 CacheStore 的启动校验；库文件损坏时若把
	DatabaseError 抛给顶层，只会得到 traceback 而不是信封。
	"""
	try:
		conn = sqlite3.connect(str(db_path))
		conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
	except sqlite3.DatabaseError as exc:
		raise CacheDatabaseInvalid(db_path, str(exc)) from exc
	return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
	"""旧版本数据目录可能缺少后加的表，按「没有可清理项」处理而不是报错。"""
	row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
	return row is not None


def _clean_search_cache(data_dir: Path, *, dry_run: bool, clean_all: bool) -> tuple[int, int]:
	"""清理搜索缓存，返回 (释放字节数, 清理条数)。"""
	db_path = data_dir / "cache" / "boss_agent.db"
	if not db_path.exists():
		return 0, 0

	conn = _open_cache_db(db_path)
	try:
		if not _table_exists(conn, "search_cache"):
			return 0, 0
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


def _clean_path(path: Path, *, dry_run: bool) -> tuple[int, int]:
	"""清理单个文件或目录，返回 (释放字节数, 清理条数)。"""
	if not path.exists():
		return 0, 0
	size, count = _path_stats(path)
	if not dry_run:
		if path.is_dir():
			shutil.rmtree(path)
		else:
			path.unlink()
	return size, count


def _path_stats(path: Path) -> tuple[int, int]:
	"""统计文件/目录字节数和文件数量。"""
	if path.is_file():
		return path.stat().st_size, 1
	if not path.is_dir():
		return 0, 0
	size = 0
	count = 0
	for item in path.rglob("*"):
		if item.is_file():
			size += item.stat().st_size
			count += 1
	return size, count


def _clean_greet_records(data_dir: Path, *, dry_run: bool) -> tuple[int, int]:
	"""清理打招呼记录。"""
	return _clean_table(data_dir, "greet_records", dry_run=dry_run)


def _clean_apply_records(data_dir: Path, *, dry_run: bool) -> tuple[int, int]:
	"""清理投递记录。"""
	return _clean_table(data_dir, "apply_records", dry_run=dry_run)


def _clean_crawl_data(data_dir: Path, *, dry_run: bool) -> tuple[int, int]:
	files_size, files_count = _clean_path(data_dir / "crawl", dry_run=dry_run)
	db_path = data_dir / "cache" / "boss_agent.db"
	if not db_path.exists():
		return files_size, files_count
	conn = _open_cache_db(db_path)
	try:
		if not _table_exists(conn, "crawl_runs"):
			return files_size, files_count
		row = conn.execute("SELECT COUNT(*) FROM crawl_runs").fetchone()
		count = int(row[0])
		if not dry_run:
			for table in ("crawl_jobs", "crawl_runs", "crawl_budget"):
				if _table_exists(conn, table):
					conn.execute(f"DELETE FROM {table}")  # noqa: S608 - 表名来自固定白名单
			conn.commit()
		return files_size + count * 200, files_count + count
	finally:
		conn.close()


def _clean_table(data_dir: Path, table: str, *, dry_run: bool) -> tuple[int, int]:
	"""清理数据库表，返回 (估算字节数, 行数)。"""
	db_path = data_dir / "cache" / "boss_agent.db"
	if not db_path.exists():
		return 0, 0

	conn = _open_cache_db(db_path)
	try:
		if not _table_exists(conn, table):
			return 0, 0
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
