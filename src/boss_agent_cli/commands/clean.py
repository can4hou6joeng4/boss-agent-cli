"""boss clean — 清理过期缓存和临时文件。"""

from __future__ import annotations

import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

import click

from rich.table import Table

from boss_agent_cli import display
from boss_agent_cli.api.cdp_risk_lock import lock_paths
from boss_agent_cli.api.request_budget import budget_directory
from boss_agent_cli.cache.store import CacheDatabaseInvalid
from boss_agent_cli.display import boss_command_for_ctx, handle_error_output, handle_output, render_next_steps, shell_argument


@click.command("clean")
@click.option("--dry-run", is_flag=True, default=False, help="仅预览将清理的内容，不实际删除")
@click.option(
	"--all", "clean_all", is_flag=True, default=False,
	help="清理全部缓存（包括未过期的搜索缓存、打招呼/投递记录、招聘者打招呼记录、职位描述缓存和工作地点发送记录）",
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

	# 隐私清理的只读预检必须发生在任何删除之前：否则登录态先被删掉，随后
	# 在某个目录链接 / 不可写目录上失败，用户既看不到已完成项，也无法判断
	# 隐私数据是否真的清理干净（Issue #1497）。符号链接目标按「跳过并报告」
	# 处理，属于可安全完成；真正不可处理的路径直接出错误信封。
	privacy_targets: list[tuple[str, Path]] = []
	if privacy:
		privacy_targets = [
			("登录会话", data_dir / "auth" / "session.enc"),
			("本地简历", data_dir / "resumes"),
			("聊天快照", data_dir / "chat-history"),
			("导出文件", data_dir / "chat-export"),
			("crawl 任务与导出", data_dir / "crawl"),
		]
		if not dry_run:
			problems = _privacy_preflight(data_dir, privacy_targets)
			if problems:
				failed_targets = {item["target"] for item in problems}
				_emit_privacy_failure(
					ctx,
					dry_run=dry_run,
					results=[],
					completed=[],
					failed=problems,
					remaining=[target for target, _ in privacy_targets if target not in failed_targets],
					message=f"隐私清理预检失败：{problems[0]['target']}（{problems[0]['reason']}）",
				)
				return

	# 0) 本地风险锁：显式 opt-in，文件名及 target 标签沿用历史协议，不连浏览器。
	if risk_lock:
		directories = dict.fromkeys((Path(data_dir).resolve(), budget_directory(Path(data_dir))))
		freed, count, skipped = 0, 0, 0
		for directory in directories:
			# 主位置与后备位置都要清理：任一份残留都会让 fail-closed 检查继续阻断
			# 用户已确认恢复的访问（Issue #1270）。
			for path in lock_paths(directory):
				removed_bytes, removed_count, removed_skipped = _clean_path(
					path, dry_run=dry_run, root=directory
				)
				freed += removed_bytes
				count += removed_count
				skipped += removed_skipped
		results.append(_clean_result("CDP 风控锁", freed, count, skipped))
		total_freed += freed

	# 1) 搜索缓存
	freed, count = _clean_search_cache(data_dir, dry_run=dry_run, clean_all=clean_all)
	results.append(_clean_result("搜索缓存", freed, count))
	total_freed += freed

	# 2) 索引缓存
	freed, count, skipped = _clean_index_cache(data_dir, dry_run=dry_run)
	results.append(_clean_result("索引缓存", freed, count, skipped))
	total_freed += freed

	# 3) 聊天快照
	freed, count, skipped = _clean_dir(data_dir / "chat-history", days=days, dry_run=dry_run)
	results.append(_clean_result("聊天快照", freed, count, skipped))
	total_freed += freed

	# 4) 导出文件
	freed, count, skipped = _clean_dir(data_dir / "chat-export", days=days, dry_run=dry_run)
	results.append(_clean_result("导出文件", freed, count, skipped))
	total_freed += freed

	# 5) 隐私清理：显式 opt-in，避免误删登录态和本地简历
	if privacy:
		crawl_target = privacy_targets[-1]
		targets = privacy_targets[:-1]
		completed: list[dict[str, Any]] = []
		for index, (target, path) in enumerate(targets):
			try:
				freed, count, skipped = _clean_path(path, dry_run=dry_run, root=data_dir)
			except OSError as exc:
				_emit_privacy_failure(
					ctx,
					dry_run=dry_run,
					results=list(results),
					completed=completed,
					failed=[{"target": target, "path": str(path), "reason": str(exc)}],
					remaining=[name for name, _ in targets[index + 1:]],
					message=f"隐私清理在删除{target}时失败: {exc}",
				)
				return
			entry = _clean_result(target, freed, count, skipped)
			results.append(entry)
			completed.append(entry)
			total_freed += freed
		try:
			freed, count, skipped = _clean_crawl_data(data_dir, dry_run=dry_run)
		except (OSError, sqlite3.DatabaseError, CacheDatabaseInvalid) as exc:
			_emit_privacy_failure(
				ctx,
				dry_run=dry_run,
				results=list(results),
				completed=completed,
				failed=[{"target": crawl_target[0], "path": str(crawl_target[1]), "reason": str(exc)}],
				remaining=[],
				message=f"隐私清理在删除{crawl_target[0]}时失败: {exc}",
			)
			return
		entry = _clean_result(crawl_target[0], freed, count, skipped)
		results.append(entry)
		completed.append(entry)
		total_freed += freed

	# 6) 全量清理额外项
	if clean_all:
		freed, count = _clean_greet_records(data_dir, dry_run=dry_run)
		results.append(_clean_result("打招呼记录", freed, count))
		total_freed += freed

		freed, count = _clean_recruiter_greet_records(data_dir, dry_run=dry_run)
		results.append(_clean_result("招聘者打招呼记录", freed, count))
		total_freed += freed

		freed, count = _clean_apply_records(data_dir, dry_run=dry_run)
		results.append(_clean_result("投递记录", freed, count))
		total_freed += freed

		freed, count = _clean_location_records(data_dir, dry_run=dry_run)
		results.append(_clean_result("工作地点发送记录", freed, count))
		total_freed += freed

		freed, count = _clean_job_desc_cache(data_dir, dry_run=dry_run)
		results.append(_clean_result("职位描述缓存", freed, count))
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

	hint_prefix = boss_command_for_ctx(ctx, f"--data-dir {shell_argument(str(data_dir))}")
	hints: dict[str, list[str]] = {"next_actions": []}
	operator_actions: list[str] = []
	if risk_lock:
		operator_actions.append(
			"本地清锁不解除服务器风险；请确认官方目标业务页已恢复，再由用户显式恢复原任务，若再次命中风险立即停止"
		)
	if dry_run:
		clean_args = ["clean"]
		if clean_all:
			clean_args.append("--all")
		if privacy:
			clean_args.append("--privacy")
		clean_args.extend(("--days", str(days)))
		if risk_lock:
			clean_args.append("--risk-lock")
		clean_action = f"{hint_prefix} {' '.join(clean_args)}"
		if risk_lock:
			operator_actions.append(f"预演未解除锁；确认恢复后由用户执行 {clean_action}")
		else:
			hints["next_actions"].append(clean_action)
	elif privacy:
		hints["next_actions"].append(f"{hint_prefix} login")
		hints["next_actions"].append(f"{hint_prefix} doctor")
		operator_actions.append("隐私清理会清除本地登录态，需要重新登录后再继续")
	else:
		hints["next_actions"].append(f"{hint_prefix} doctor")
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
	for row in data.get("results", []):
		if row.get("skipped_symlinks"):
			display.console.print(
				f"  跳过符号链接 [bold]{row['skipped_symlinks']}[/bold] 个（{row.get('target', '-')}）：不删除链接或链接目标"
			)
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
		except sqlite3.DatabaseError as exc:
			# 打开时正常、清理时才发现列漂移（旧库、迁移中断、同名表）：
			# 与打开失败同口径报 CACHE_DATABASE_INVALID，不能让 clean 自己
			# 变成 traceback——它正是缓存异常时的恢复入口（Issue #1139）。
			raise CacheDatabaseInvalid(db_path, str(exc)) from exc
	finally:
		conn.close()


def _clean_result(target: str, freed: int, count: int, skipped: int = 0) -> dict[str, Any]:
	"""单条清理结果；符号链接跳过时在结果里说明原因。"""
	entry: dict[str, Any] = {
		"target": target,
		"cleaned": count,
		"bytes_freed": freed,
		"skipped_symlinks": skipped,
	}
	if skipped:
		entry["warning"] = f"{target} 含符号链接，已跳过：不删除链接，也不删除链接目标"
	return entry


def _first_symlink_component(root: Path, path: Path) -> Path | None:
	"""返回 ``root`` 之下（含 ``path`` 本身）最靠前的符号链接。

	clean 只应删除数据目录内的真实文件：目标本身或任意一级祖先目录是符号
	链接时，删除都会落到目录外。``root`` 自身允许是链接（用户显式选择的
	``--data-dir``），因此从它的子项开始检查。
	"""
	try:
		relative = path.relative_to(root)
	except ValueError:
		return None
	current = root
	for part in relative.parts:
		current = current / part
		if current.is_symlink():
			return current
	return None


def _is_writable(path: Path) -> bool:
	"""预检用的可写判断；独立成函数便于测试替换 ``os.access``。"""
	return os.access(path, os.W_OK | os.X_OK)


def _privacy_preflight(data_dir: Path, targets: list[tuple[str, Path]]) -> list[dict[str, str]]:
	"""只读预检隐私清理目标，返回不可处理项。

	不创建、不删除任何路径。符号链接（目标本身或祖先）按「跳过并报告」处理，
	属于可安全完成的目标；其余已存在目标要求父目录可写，目录自身还要可写
	（``shutil.rmtree`` 需要先删掉子项）。
	"""
	problems: list[dict[str, str]] = []
	for target, path in targets:
		if _first_symlink_component(data_dir, path) is not None:
			continue
		try:
			path.lstat()
		except FileNotFoundError:
			continue
		except OSError as exc:
			problems.append({"target": target, "path": str(path), "reason": f"无法读取路径: {exc}"})
			continue
		if not _is_writable(path.parent):
			problems.append({"target": target, "path": str(path), "reason": "父目录不可写"})
			continue
		if path.is_dir() and not _is_writable(path):
			problems.append({"target": target, "path": str(path), "reason": "目录不可写"})
	db_path = data_dir / "cache" / "boss_agent.db"
	# 文件只需要写权限；X_OK 对普通文件恒为假，不能复用目录的 W_OK|X_OK 判断。
	if db_path.exists() and not os.access(db_path, os.W_OK):
		problems.append(
			{"target": "crawl 任务与导出", "path": str(db_path), "reason": "缓存库不可写"}
		)
	return problems


def _emit_privacy_failure(
	ctx: click.Context,
	*,
	dry_run: bool,
	results: list[dict[str, Any]],
	completed: list[dict[str, Any]],
	failed: list[dict[str, str]],
	remaining: list[str],
	message: str,
) -> None:
	"""隐私清理失败时输出错误信封，并带回已完成 / 失败 / 未处理项。

	错误信封通常 ``data=null``；批量删除可能已经删掉一部分，必须让用户和
	Agent 看到实际进度，不能只剩一句 ``LOCAL_IO_ERROR``（Issue #1497）。
	"""
	freed_bytes = sum(int(item.get("bytes_freed", 0)) for item in results)
	data: dict[str, Any] = {
		"dry_run": dry_run,
		"results": results,
		"total_bytes_freed": freed_bytes,
		"total_bytes_freed_display": _format_size(freed_bytes),
		"completed": completed,
		"failed": failed,
		"remaining": remaining,
	}
	handle_error_output(
		ctx,
		"clean",
		code="LOCAL_IO_ERROR",
		message=message,
		data=data,
		hints={
			"operator_actions": [
				"隐私清理在完成前失败：按 failed 中的路径确认权限或改用可写的数据目录后重新执行 clean --privacy"
			]
		},
	)


def _clean_index_cache(data_dir: Path, *, dry_run: bool) -> tuple[int, int, int]:
	"""清理索引缓存文件；符号链接走统一的不跟随路径。"""
	return _clean_path(data_dir / "cache" / "index_cache.json", dry_run=dry_run, root=data_dir)


def _clean_dir(dir_path: Path, *, days: int, dry_run: bool) -> tuple[int, int, int]:
	"""清理指定目录中超过指定天数的文件。

	返回 ``(释放字节数, 清理条数, 跳过的符号链接数)``。整个目录是符号链接时
	直接跳过：``iterdir()`` 会跟随链接，把链接目标里的文件按 mtime 删掉
	（Issue #1497）。目录内的文件符号链接仍按既有语义只 ``unlink`` 链接本身，
	不会删除链接目标。
	"""
	if dir_path.is_symlink():
		return 0, 0, 1
	if not dir_path.exists():
		return 0, 0, 0

	cutoff = time.time() - days * 86400
	total_size = 0
	count = 0
	skipped = 0

	for f in dir_path.iterdir():
		if f.is_symlink() and not f.is_file():
			# 指向目录的链接、悬空链接：不跟随，也不假装清理过。
			skipped += 1
			continue
		if f.is_file() and f.stat().st_mtime < cutoff:
			total_size += f.stat().st_size
			count += 1
			if not dry_run:
				f.unlink()

	return total_size, count, skipped


def _clean_path(path: Path, *, dry_run: bool, root: Path | None = None) -> tuple[int, int, int]:
	"""清理单个文件或目录，返回 ``(释放字节数, 清理条数, 跳过的符号链接数)``。

	祖先目录是符号链接时一律跳过：继续删除会落到数据目录之外（Issue #1497）。
	路径本身是目录链接时也跳过并报告，``shutil.rmtree`` 对它只会抛 ``OSError``；
	文件链接沿用既有语义，只 ``unlink`` 链接本身，绝不写穿或删除链接目标。
	"""
	if root is not None:
		link = _first_symlink_component(root, path)
		if link is not None and link != path:
			return 0, 0, 1
	if path.is_symlink():
		if path.is_dir():
			# 目录链接：不跟随、不 rmtree，链接目标里的文件必须保留。
			return 0, 0, 1
		if path.exists():
			# 文件链接：only unlink the link itself; the target is untouched.
			if not dry_run:
				path.unlink()
			return 0, 1, 0
		# 悬空链接维持既有语义：没有可清理内容，保持原样。
		return 0, 0, 0
	if not path.exists():
		return 0, 0, 0
	size, count = _path_stats(path)
	if not dry_run:
		if path.is_dir():
			shutil.rmtree(path)
		else:
			path.unlink()
	return size, count, 0


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


def _clean_recruiter_greet_records(data_dir: Path, *, dry_run: bool) -> tuple[int, int]:
	"""清理招聘者打招呼记录；与求职者侧 greet_records 对称。"""
	return _clean_table(data_dir, "recruiter_greet_records", dry_run=dry_run)


def _clean_apply_records(data_dir: Path, *, dry_run: bool) -> tuple[int, int]:
	"""清理投递记录。"""
	return _clean_table(data_dir, "apply_records", dry_run=dry_run)


def _clean_location_records(data_dir: Path, *, dry_run: bool) -> tuple[int, int]:
	"""清理招聘者工作地点发送记录。"""
	return _clean_table(data_dir, "recruiter_location_records", dry_run=dry_run)


def _clean_job_desc_cache(data_dir: Path, *, dry_run: bool) -> tuple[int, int]:
	"""清理职位描述缓存。

	``get_job_desc()`` 只在命中过期条目时懒删除，冷门职位写入后没有任何
	出口；``--all`` 是唯一能整体回收这部分空间的路径。
	"""
	return _clean_table(data_dir, "job_desc_cache", dry_run=dry_run)


def _clean_crawl_data(data_dir: Path, *, dry_run: bool) -> tuple[int, int, int]:
	files_size, files_count, skipped = _clean_path(data_dir / "crawl", dry_run=dry_run, root=data_dir)
	db_path = data_dir / "cache" / "boss_agent.db"
	if not db_path.exists():
		return files_size, files_count, skipped
	conn = _open_cache_db(db_path)
	try:
		if not _table_exists(conn, "crawl_runs"):
			return files_size, files_count, skipped
		row = conn.execute("SELECT COUNT(*) FROM crawl_runs").fetchone()
		count = int(row[0])
		if not dry_run:
			for table in ("crawl_jobs", "crawl_runs", "crawl_budget"):
				if _table_exists(conn, table):
					conn.execute(f"DELETE FROM {table}")  # noqa: S608 - 表名来自固定白名单
			conn.commit()
		return files_size + count * 200, files_count + count, skipped
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
