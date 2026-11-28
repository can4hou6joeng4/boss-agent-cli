"""boss clean — 清理过期缓存和临时文件。"""

from __future__ import annotations

import errno
import os
import shutil
import sqlite3
import stat
import time
from pathlib import Path
from typing import Any

import click

from rich.table import Table

from boss_agent_cli import display
from boss_agent_cli.api.cdp_risk_lock import lock_paths, read_lock
from boss_agent_cli.api.request_budget import budget_directory
from boss_agent_cli.cache.store import CacheDatabaseInvalid, _text_key, is_cache_busy_error, is_cache_local_io_error
from boss_agent_cli.display import (
	boss_command_for_ctx,
	handle_error_output,
	handle_output,
	render_next_steps,
	shell_argument,
)


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
@click.option(
	"--pending-claims",
	"pending_claims",
	default=None,
	metavar="REFERENCE",
	help="按释放失败信封中的 claim_reference 清除该次 greet/apply 预约；不清除其他预约",
)
@click.option(
	"--pending-recruiter-greet",
	"pending_recruiter_greet",
	nargs=2,
	default=None,
	metavar="GEEK_ID JOB_ID",
	help="按招聘者招呼信封中的 geek_id/job_id 清除该次 pending 预约；不清除已发送记录",
)
@click.pass_context
def clean_cmd(
	ctx: click.Context,
	dry_run: bool,
	clean_all: bool,
	privacy: bool,
	days: int,
	risk_lock: bool,
	pending_claims: str | None,
	pending_recruiter_greet: tuple[str, str] | None,
) -> None:
	"""清理过期缓存和临时文件。"""
	if pending_claims is not None and (
		len(pending_claims) != 32 or any(c not in "0123456789abcdef" for c in pending_claims)
	):
		handle_error_output(
			ctx, "clean", code="INVALID_PARAM",
			message="--pending-claims 必须是释放失败信封中的 32 位 claim_reference",
			recovery_action="从 LOCAL_STATE_WRITE_FAILED 信封复制完整 claim_reference；不要猜测其他预约的标识",
		)
		return
	if pending_recruiter_greet is not None:
		geek_id, greet_job_id = pending_recruiter_greet
		if not geek_id.strip() or not greet_job_id.strip():
			handle_error_output(
				ctx, "clean", code="INVALID_PARAM",
				message="--pending-recruiter-greet 需要完整的 geek_id 和 job_id",
				recovery_action="从 LOCAL_STATE_WRITE_FAILED 信封复制这两个标识；不要猜测其他候选人的预约",
			)
			return
		if pending_claims is not None:
			handle_error_output(
				ctx, "clean", code="INVALID_PARAM",
				message="一次只清理一种预约：--pending-claims 与 --pending-recruiter-greet 不能同时使用",
				recovery_action="按信封选择对应的清理选项后重试",
			)
			return
	data_dir = ctx.obj["data_dir"]
	results: list[dict[str, Any]] = []
	total_freed = 0
	# 清理中途失败时把已完成项带回信封（Issue #1830 #1831）；风险锁先于
	# 其他步骤处理，锁的真实状态要单独记下来。
	risk_lock_info: dict[str, Any] | None = None
	current_target = "搜索缓存"

	# 库文件存在但读不动时，先在这里失败：main.py 会把 CacheDatabaseInvalid 转成
	# CACHE_DATABASE_INVALID 信封。否则 --privacy 会先删掉登录态和简历，再在
	# sqlite 里抛 traceback，用户既看不到信封，也不知道哪些文件已经删了。
	# 只对 --privacy 预检：该命令同时是 `clean --risk-lock` 这个恢复入口，锁清理
	# 不读缓存库，不能因为无关的坏库而清不掉锁（Issue #1548）。其余模式由
	# _clean_search_cache 打开库时按同一口径报错。
	db_path = data_dir / "cache" / "boss_agent.db"
	if privacy and _cache_db_present(db_path):
		_open_cache_db(db_path, root=data_dir).close()

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

	try:
		# 0) 本地风险锁：显式 opt-in，文件名及 target 标签沿用历史协议，不连浏览器。
		if risk_lock:
			current_target = "CDP 风控锁"
			directories = dict.fromkeys((Path(data_dir).resolve(), budget_directory(Path(data_dir))))
			# 清理前能读到锁（含空目录 / FIFO 这类损坏锁）才算本次解除过阻断：
			# 空目录与 FIFO 删除成功时 _path_stats 记 0 项，用「删了几项」判断会把
			# 已解除的锁报成仍阻断（Issue #1997）。
			had_lock = any(read_lock(directory) is not None for directory in directories)
			freed, count, skipped = 0, 0, 0
			for directory in directories:
				# 主位置与后备位置都要清理：任一份残留都会让 fail-closed 检查继续阻断
				# 用户已确认恢复的访问（Issue #1270）。
				for path in lock_paths(directory):
					removed_bytes, removed_count, removed_skipped = _clean_lock_path(
						path, dry_run=dry_run, root=directory
					)
					freed += removed_bytes
					count += removed_count
					skipped += removed_skipped
			results.append(_clean_result("CDP 风控锁", freed, count, skipped))
			total_freed += freed
			# 锁是否真的解除要同时满足「清理前确实读到锁」与「清理后读不到锁」：
			# 悬空链接、祖先链接跳过等情形下删除动作不代表 fail-closed 检查
			# 不再阻断；而在没有锁的目录空转仍要报 false，继续充当
			# 「数据目录选错」的探测器（Issue #1829，docs/commands.md）。
			local_lock_removed = (
				not dry_run
				and had_lock
				and all(read_lock(directory) is None for directory in directories)
			)
			risk_lock_info = {
				"local_lock_removed": local_lock_removed,
				"platform_risk_status": "unverified",
			}

		# 1) 搜索缓存
		current_target = "搜索缓存"
		freed, count = _clean_search_cache(data_dir, dry_run=dry_run, clean_all=clean_all)
		results.append(_clean_result("搜索缓存", freed, count))
		total_freed += freed

		# 2) 索引缓存
		current_target = "索引缓存"
		freed, count, skipped = _clean_index_cache(data_dir, dry_run=dry_run)
		results.append(_clean_result("索引缓存", freed, count, skipped))
		total_freed += freed

		# 3) 聊天快照
		current_target = "聊天快照"
		# --privacy --dry-run 时这两个目录由第 5 步整目录统计：预览不删除文件，
		# 这里再按 --days 统计一次会与实际释放量不一致（Issue #2639）。
		freed, count, skipped = 0, 0, 0
		if not (privacy and dry_run):
			freed, count, skipped = _clean_dir(data_dir / "chat-history", days=days, dry_run=dry_run)
		results.append(_clean_result("聊天快照", freed, count, skipped))
		total_freed += freed

		# 4) 导出文件
		current_target = "导出文件"
		freed, count, skipped = 0, 0, 0
		if not (privacy and dry_run):
			freed, count, skipped = _clean_dir(data_dir / "chat-export", days=days, dry_run=dry_run)
		results.append(_clean_result("导出文件", freed, count, skipped))
		total_freed += freed

		# 5) 隐私清理：显式 opt-in，避免误删登录态和本地简历
		if privacy:
			crawl_target = privacy_targets[-1]
			targets = privacy_targets[:-1]
			completed: list[dict[str, Any]] = []
			for index, (target, path) in enumerate(targets):
				current_target = target
				try:
					freed, count, skipped = _clean_path(path, dry_run=dry_run, root=data_dir)
				except OSError as exc:
					_emit_privacy_failure(
						ctx,
						dry_run=dry_run,
						results=list(results),
						completed=completed,
						failed=[{"target": target, "path": str(path), "reason": str(exc)}],
						remaining=[name for name, _ in targets[index + 1:]] + [crawl_target[0]],
						message=f"隐私清理在删除{target}时失败: {exc}",
						risk_lock_info=risk_lock_info,
					)
					return
				entry = _clean_result(target, freed, count, skipped)
				results.append(entry)
				completed.append(entry)
				total_freed += freed
			current_target = crawl_target[0]
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
					risk_lock_info=risk_lock_info,
				)
				return
			entry = _clean_result(crawl_target[0], freed, count, skipped)
			results.append(entry)
			completed.append(entry)
			total_freed += freed

		# 6) 全量清理额外项
		if clean_all:
			current_target = "打招呼记录"
			freed, count = _clean_greet_records(data_dir, dry_run=dry_run)
			results.append(_clean_result("打招呼记录", freed, count))
			total_freed += freed

			current_target = "招聘者打招呼记录"
			freed, count = _clean_recruiter_greet_records(data_dir, dry_run=dry_run)
			results.append(_clean_result("招聘者打招呼记录", freed, count))
			total_freed += freed

			current_target = "投递记录"
			freed, count = _clean_apply_records(data_dir, dry_run=dry_run)
			results.append(_clean_result("投递记录", freed, count))
			total_freed += freed

			current_target = "工作地点发送记录"
			freed, count = _clean_location_records(data_dir, dry_run=dry_run)
			results.append(_clean_result("工作地点发送记录", freed, count))
			total_freed += freed

			current_target = "职位描述缓存"
			freed, count = _clean_job_desc_cache(data_dir, dry_run=dry_run)
			results.append(_clean_result("职位描述缓存", freed, count))
			total_freed += freed

		# 7) 招聘者招呼的定向预约清理：表键是 (geek_id, job_id)，没有 claim_reference。
		# 只删 pending；已发出 / 已拒绝的记录与候选人侧其他动作都保留。
		if pending_recruiter_greet is not None:
			current_target = "招聘者招呼预约"
			freed, count = _clean_pending_recruiter_greet(
				data_dir, pending_recruiter_greet[0], pending_recruiter_greet[1], dry_run=dry_run
			)
			results.append(_clean_result("招聘者招呼预约", freed, count))
			total_freed += freed

		# 8) 仅清除释放失败信封指定的预约。每次预约的随机标识独立，旧指引
		# 不能删除新建预约；其他在途 / 结果未知预约与 sent 行始终保留。
		if pending_claims is not None:
			current_target = "未完成的动作预约"
			freed, count = _clean_pending_action_claims(data_dir, pending_claims, dry_run=dry_run)
			results.append(_clean_result("未完成的动作预约", freed, count))
			total_freed += freed

	except CacheDatabaseInvalid as exc:
		# 缓存库损坏是确定性本地失败；已完成项照样带回（Issue #1831）。
		_emit_clean_partial_failure(
			ctx,
			dry_run=dry_run,
			results=results,
			total_freed=total_freed,
			target=current_target,
			code="CACHE_DATABASE_INVALID",
			exc=exc,
			risk_lock_info=risk_lock_info,
		)
		return
	except sqlite3.DatabaseError as exc:
		# 锁冲突保持 CACHE_BUSY 的可重试语义，交给 main 分流；其余本地数据库
		# 错误按 LOCAL_IO_ERROR 上报部分完成（Issue #1831）。
		if is_cache_busy_error(exc):
			raise
		_emit_clean_partial_failure(
			ctx,
			dry_run=dry_run,
			results=results,
			total_freed=total_freed,
			target=current_target,
			code="LOCAL_IO_ERROR",
			exc=exc,
			risk_lock_info=risk_lock_info,
		)
		return
	except OSError as exc:
		_emit_clean_partial_failure(
			ctx,
			dry_run=dry_run,
			results=results,
			total_freed=total_freed,
			target=current_target,
			code="LOCAL_IO_ERROR",
			exc=exc,
			risk_lock_info=risk_lock_info,
		)
		return

	data = {
		"dry_run": dry_run,
		"results": results,
		"total_bytes_freed": total_freed,
		"total_bytes_freed_display": _format_size(total_freed),
	}
	if risk_lock:
		data["risk_lock"] = risk_lock_info or {
			"local_lock_removed": False,
			"platform_risk_status": "unverified",
		}

	hint_prefix = boss_command_for_ctx(ctx, f"--data-dir {shell_argument(str(Path(data_dir).resolve()))}")
	hints: dict[str, list[str]] = {"next_actions": []}
	operator_actions: list[str] = []
	if risk_lock:
		operator_actions.append(
			"本地清锁不解除服务器风险；请确认官方目标业务页已恢复，再由用户显式恢复原任务，若再次命中风险立即停止"
		)
		if not dry_run and risk_lock_info is not None and not risk_lock_info["local_lock_removed"]:
			operator_actions.append(
				"本地风控锁未能完全清除，后续命令仍会被阻断；检查数据目录下的 cdp_risk_lock.json 与后备位置 cache/cdp_risk_lock.json"
			)
	if dry_run:
		clean_args = ["clean"]
		if clean_all:
			clean_args.append("--all")
		if privacy:
			clean_args.append("--privacy")
		if pending_claims is not None:
			clean_args.extend(("--pending-claims", pending_claims))
		if pending_recruiter_greet is not None:
			clean_args.extend(("--pending-recruiter-greet", *pending_recruiter_greet))
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


def _cache_db_present(db_path: Path) -> bool:
	"""缓存库是否存在；只有 ``FileNotFoundError`` 才算不存在。

	3.14 的 ``Path.exists()`` 会把权限等 ``OSError`` 吞成 ``False``。隐私预检
	若据此跳过，后续会先删掉登录态与简历，再把数据库当成不存在静默返回 0
	（Issue #1690）。路径状态读不出来时上抛本地 IO 错误，禁止继续删除。
	"""
	try:
		db_path.lstat()
	except FileNotFoundError:
		return False
	except OSError as exc:
		if exc.filename is None:
			exc.filename = str(db_path)
		raise
	except ValueError as exc:
		error = OSError(f"无法读取缓存库路径状态：{exc}")
		error.filename = str(db_path)
		raise error from exc
	return True


def _open_cache_db(db_path: Path, *, root: Path) -> sqlite3.Connection:
	"""打开缓存库，并把内容损坏的 sqlite3.DatabaseError 归一成 CacheDatabaseInvalid。

	clean 直接操作 SQLite，不经过 CacheStore 的启动校验；库文件损坏时若把
	DatabaseError 抛给顶层，只会得到 traceback 而不是信封。锁冲突与本地 IO
	错误不是内容损坏，原样上抛交给 main 分流成 CACHE_BUSY / LOCAL_IO_ERROR。

	打开前确认是普通文件，且数据目录以下没有祖先链接：符号链接（含悬空链接）
	会让 SQLite 在链接目标处创建或改写数据目录之外的文件，clean 的 DELETE /
	VACUUM 不能写穿链接（Issue #2946）。root 本身仍允许是用户选择的目录链接。
	"""
	link = _first_symlink_component(root, db_path)
	if link is not None:
		raise OSError(errno.EINVAL, "缓存库路径包含符号链接，拒绝打开", str(link))
	try:
		mode = db_path.lstat().st_mode
	except FileNotFoundError:
		# 预检与打开之间被并发删除：交给 SQLite 按新库处理。
		pass
	except OSError as exc:
		if exc.filename is None:
			exc.filename = str(db_path)
		raise
	except ValueError as exc:
		error = OSError(f"无法读取缓存库路径状态：{exc}")
		error.filename = str(db_path)
		raise error from exc
	else:
		if stat.S_ISLNK(mode):
			raise OSError(errno.EINVAL, "缓存库是符号链接，拒绝打开", str(db_path))
		if not stat.S_ISREG(mode):
			raise OSError(errno.EINVAL, "缓存库不是普通文件", str(db_path))
	try:
		conn = sqlite3.connect(str(db_path))
		conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
	except sqlite3.DatabaseError as exc:
		# 锁冲突与只读 / 磁盘错误不是库内容损坏：等锁释放或修好磁盘即可，
		# 指引用户删库会丢掉候选池、预设与采集断点（Issue #1547）。原样上抛，
		# 由 main 的 sqlite 分支映射成可重试的 CACHE_BUSY / LOCAL_IO_ERROR。
		if is_cache_busy_error(exc) or is_cache_local_io_error(exc):
			raise
		raise CacheDatabaseInvalid(db_path, str(exc)) from exc
	return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
	"""旧版本数据目录可能缺少后加的表，按「没有可清理项」处理而不是报错。"""
	row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
	return row is not None


def _clean_search_cache(data_dir: Path, *, dry_run: bool, clean_all: bool) -> tuple[int, int]:
	"""清理搜索缓存，返回 (释放字节数, 清理条数)。"""
	db_path = data_dir / "cache" / "boss_agent.db"
	if not _cache_db_present(db_path):
		return 0, 0

	conn = _open_cache_db(db_path, root=data_dir)
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
			# 但锁冲突 / 只读 / 磁盘错误不是内容损坏，原样上抛交给 main 分流，
			# 不能引导用户删掉完好的本地库（Issue #1547）。
			if is_cache_busy_error(exc) or is_cache_local_io_error(exc):
				raise
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


def _lstat_mode(path: Path) -> int | None:
	"""返回路径自身的 lstat 类型位；只有 ``FileNotFoundError`` 算缺失。

	clean 的每个分支都在决定「删除还是跳过」：3.14 的 ``exists()`` /
	``is_file()`` / ``is_symlink()`` 会把权限等 ``OSError`` 吞成 ``False``，
	路径状态读不出来时曾被当成「没有可清理内容」，命令以成功结束
	（Issue #1712）。其它错误带文件名上抛，由 main 分流成 ``LOCAL_IO_ERROR``。
	"""
	try:
		return path.lstat().st_mode
	except FileNotFoundError:
		return None
	except ValueError as exc:
		error = OSError(f"无法读取路径状态：{exc}")
		error.filename = str(path)
		raise error from exc
	except OSError as exc:
		if exc.filename is None:
			exc.filename = str(path)
		raise


def _stat_mode(path: Path) -> int | None:
	"""跟随符号链接取目标类型位；悬空链接返回 None，其余错误上抛。"""
	try:
		return path.stat().st_mode
	except FileNotFoundError:
		return None
	except ValueError as exc:
		error = OSError(f"无法读取路径状态：{exc}")
		error.filename = str(path)
		raise error from exc
	except OSError as exc:
		if exc.filename is None:
			exc.filename = str(path)
		raise


def _first_symlink_component(root: Path, path: Path) -> Path | None:
	"""返回 ``root`` 之下（含 ``path`` 本身）最靠前的符号链接。

	clean 只应删除数据目录内的真实文件：目标本身或任意一级祖先目录是符号
	链接时，删除都会落到目录外。``root`` 自身允许是链接（用户显式选择的
	``--data-dir``），因此从它的子项开始检查。路径状态读不出来时上抛，
	不能把「无法确认不是链接」当成「不是链接」（Issue #1712）。
	"""
	try:
		relative = path.relative_to(root)
	except ValueError:
		return None
	current = root
	for part in relative.parts:
		current = current / part
		mode = _lstat_mode(current)
		if mode is None:
			return None
		if stat.S_ISLNK(mode):
			return current
	return None


def _is_writable(path: Path) -> bool:
	"""预检用的可写判断；独立成函数便于测试替换 ``os.access``。"""
	return os.access(path, os.W_OK | os.X_OK)


def _privacy_preflight(data_dir: Path, targets: list[tuple[str, Path]]) -> list[dict[str, str]]:
	"""只读预检隐私清理目标，返回不可处理项。

	不创建、不删除任何路径。符号链接（目标本身或祖先）按「跳过并报告」处理，
	属于可安全完成的目标；其余已存在目标要求父目录可写，整棵真实目录树都要
	可写、可进入且可枚举（``shutil.rmtree`` 需要先删掉子项），不跟随嵌套链接。
	"""
	problems: list[dict[str, str]] = []
	for target, path in targets:
		try:
			link = _first_symlink_component(data_dir, path)
		except OSError as exc:
			problems.append({"target": target, "path": str(path), "reason": f"无法读取路径状态: {exc}"})
			continue
		if link is not None:
			continue
		try:
			mode = _lstat_mode(path)
		except OSError as exc:
			problems.append({"target": target, "path": str(path), "reason": f"无法读取路径: {exc}"})
			continue
		if mode is None:
			continue
		if not _is_writable(path.parent):
			problems.append({"target": target, "path": str(path), "reason": "父目录不可写"})
			continue
		if not stat.S_ISDIR(mode):
			continue
		directories = [path]
		while directories:
			directory = directories.pop()
			if not _is_writable(directory):
				problems.append({"target": target, "path": str(directory), "reason": "目录不可写或不可进入"})
				continue
			try:
				items = list(directory.iterdir())
			except OSError as exc:
				problems.append({"target": target, "path": str(directory), "reason": f"无法枚举目录: {exc}"})
				continue
			for item in items:
				try:
					item_mode = _lstat_mode(item)
				except OSError as exc:
					problems.append({"target": target, "path": str(item), "reason": f"无法读取路径状态: {exc}"})
					continue
				# 只按 lstat 类型递归真实目录，目录链接和悬空链接都不进入。
				if item_mode is not None and stat.S_ISDIR(item_mode):
					directories.append(item)
	db_path = data_dir / "cache" / "boss_agent.db"
	# 文件只需要写权限；X_OK 对普通文件恒为假，不能复用目录的 W_OK|X_OK 判断。
	try:
		db_present = _cache_db_present(db_path)
	except OSError as exc:
		problems.append(
			{"target": "crawl 任务与导出", "path": str(db_path), "reason": f"无法读取路径: {exc}"}
		)
	else:
		if db_present and not os.access(db_path, os.W_OK):
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
	risk_lock_info: dict[str, Any] | None = None,
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
	operator_actions = [
		"隐私清理在完成前失败：按 failed 中的路径确认权限或改用可写的数据目录后重新执行 clean --privacy"
	]
	# 清锁先于隐私清理执行：后续失败也要交代锁的真实状态（Issue #1830 口径，#2639）。
	if risk_lock_info is not None and risk_lock_info.get("local_lock_removed"):
		data["risk_lock"] = dict(risk_lock_info)
		message += "；本地风控锁此前已清除，该部分不会回滚"
		operator_actions.append("本地风控锁已清除，不需要因为本次失败重新执行 clean --risk-lock")
	handle_error_output(
		ctx,
		"clean",
		code="LOCAL_IO_ERROR",
		message=message,
		data=data,
		hints={"operator_actions": operator_actions},
	)


def _emit_clean_partial_failure(
	ctx: click.Context,
	*,
	dry_run: bool,
	results: list[dict[str, Any]],
	total_freed: int,
	target: str,
	code: str,
	exc: BaseException,
	risk_lock_info: dict[str, Any] | None,
) -> None:
	"""clean 中途失败时输出错误信封，并带回已完成项与失败项。

	各清理项彼此独立：已经删掉的内容不能让调用方从 ``data=null`` 里猜
	（Issue #1497 #1831）。清锁先于其他步骤执行，后续失败还必须交代锁的
	真实状态，不能让恢复入口的效果被 ``ok=false`` 吞掉（Issue #1830）。
	"""
	data: dict[str, Any] = {
		"dry_run": dry_run,
		"results": list(results),
		"total_bytes_freed": total_freed,
		"total_bytes_freed_display": _format_size(total_freed),
		"failed": [{"target": target, "reason": str(exc)}],
	}
	message = f"清理在{target}时失败: {exc}"
	operator_actions = [
		"clean 的清理项彼此独立：按 data.results 确认已清理项，修正 failed 中的路径后重新执行 clean"
	]
	if risk_lock_info is not None and risk_lock_info.get("local_lock_removed"):
		data["risk_lock"] = dict(risk_lock_info)
		message += "；本地风控锁此前已清除，该部分不会回滚"
		operator_actions.append("本地风控锁已清除，不需要因为本次失败重新执行 clean --risk-lock")
	handle_error_output(
		ctx,
		"clean",
		code=code,
		message=message,
		data=data,
		hints={"operator_actions": operator_actions},
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
	mode = _lstat_mode(dir_path)
	if mode is None:
		return 0, 0, 0
	if stat.S_ISLNK(mode):
		return 0, 0, 1
	if not stat.S_ISDIR(mode):
		raise OSError(errno.ENOTDIR, "清理目标不是目录", str(dir_path))

	cutoff = time.time() - days * 86400
	total_size = 0
	count = 0
	skipped = 0

	for f in dir_path.iterdir():
		entry_mode = _lstat_mode(f)
		if entry_mode is None:
			# 枚举后被并发删除：没有可清理内容，也不算失败。
			continue
		if stat.S_ISLNK(entry_mode):
			target_mode = _stat_mode(f)
			if target_mode is None or not stat.S_ISREG(target_mode):
				# 指向目录的链接、悬空链接：不跟随，也不假装清理过。
				skipped += 1
				continue
		elif not stat.S_ISREG(entry_mode):
			# 子目录、FIFO / 设备：不是可清理的快照文件。
			continue
		try:
			info = f.stat()
		except FileNotFoundError:
			continue
		if info.st_mtime < cutoff:
			size = info.st_size
			if stat.S_ISLNK(entry_mode):
				# 只删除链接本身：目标大小不是本次释放的字节数（Issue #2000）。
				try:
					size = f.lstat().st_size
				except FileNotFoundError:
					size = 0
			total_size += size
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
	mode = _lstat_mode(path)
	if mode is None:
		return 0, 0, 0
	if stat.S_ISLNK(mode):
		target_mode = _stat_mode(path)
		if target_mode is not None and stat.S_ISDIR(target_mode):
			# 目录链接：不跟随、不 rmtree，链接目标里的文件必须保留。
			return 0, 0, 1
		if target_mode is not None:
			# 文件链接：only unlink the link itself; the target is untouched.
			if not dry_run:
				path.unlink()
			return 0, 1, 0
		# 悬空链接维持既有语义：没有可清理内容，保持原样。
		return 0, 0, 0
	size, count = _path_stats(path)
	if not dry_run:
		if stat.S_ISDIR(mode):
			shutil.rmtree(path)
		else:
			path.unlink()
	return size, count, 0



def _clean_lock_path(path: Path, *, dry_run: bool, root: Path) -> tuple[int, int, int]:
	"""清理单个风险锁路径；符号链接一律只删除链接本身。

	锁读取把悬空链接、目录等非常规路径按损坏锁 fail-closed 处理
	（Issue #1686）；通用 ``_clean_path`` 对悬空链接「保持原样」，会让
	``clean --risk-lock`` 以成功结束而锁仍生效（Issue #1829）。链接绝不
	跟随：祖先目录是链接时与 ``_clean_path`` 同口径跳过并报告。
	"""
	link = _first_symlink_component(root, path)
	if link is not None and link != path:
		return 0, 0, 1
	mode = _lstat_mode(path)
	if mode is not None and stat.S_ISLNK(mode):
		# 目标存在且是普通文件时沿用 _path_stats 的字节数口径（lstat，只删链接
		# 本身）；悬空链接按 0 字节记一条，链接本身才是阻断来源。
		size = 0
		target_mode = _stat_mode(path)
		if target_mode is not None and stat.S_ISREG(target_mode):
			try:
				size = path.lstat().st_size
			except FileNotFoundError:
				size = 0
		if not dry_run:
			path.unlink()
		return size, 1, 0
	return _clean_path(path, dry_run=dry_run, root=root)


def _path_stats(path: Path) -> tuple[int, int]:
	"""统计文件/目录字节数和文件数量；路径元数据错误上抛（Issue #1712）。"""
	mode = _lstat_mode(path)
	if mode is None:
		return 0, 0
	if stat.S_ISLNK(mode):
		target_mode = _stat_mode(path)
		if target_mode is None or not stat.S_ISREG(target_mode):
			return 0, 0
		try:
			# 删除只影响链接本身：字节数取 lstat，不能把目标大小记成已释放（Issue #2000）。
			return path.lstat().st_size, 1
		except FileNotFoundError:
			return 0, 0
	if stat.S_ISREG(mode):
		return path.stat().st_size, 1
	if not stat.S_ISDIR(mode):
		return 0, 0
	size = 0
	count = 0
	directories = [path]
	while directories:
		directory = directories.pop()
		try:
			# rglob 会静默跳过不可读的子目录，使清理预览虚报为零（Issue #2572）。
			items = list(directory.iterdir())
		except FileNotFoundError:
			continue
		for item in items:
			try:
				entry = item.lstat()
			except FileNotFoundError:
				continue
			if stat.S_ISDIR(entry.st_mode):
				directories.append(item)
				continue
			if stat.S_ISLNK(entry.st_mode):
				target_mode = _stat_mode(item)
				if target_mode is None or not stat.S_ISREG(target_mode):
					continue
			elif not stat.S_ISREG(entry.st_mode):
				continue
			# 同样只统计嵌套文件链接本身，不把目标大小记成已释放。
			size += entry.st_size
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


def _clean_pending_action_claims(data_dir: Path, reference: str, *, dry_run: bool) -> tuple[int, int]:
	"""仅清除指定版本的 pending 预约，其他账号、动作与后续新预约均保留。

	恢复标识只在已证明未发送且释放失败时输出；历史预约不补发标识。
	"""
	db_path = data_dir / "cache" / "boss_agent.db"
	if not _cache_db_present(db_path):
		return 0, 0
	conn = _open_cache_db(db_path, root=data_dir)
	try:
		if not _table_exists(conn, "candidate_action_claims"):
			return 0, 0
		columns = {row[1] for row in conn.execute("PRAGMA table_info(candidate_action_claims)")}
		if "recovery_id" not in columns:
			return 0, 0
		where = "status = 'pending' AND action IN ('greet', 'apply') AND recovery_id = ?"
		if dry_run:
			row = conn.execute(f"SELECT COUNT(*) FROM candidate_action_claims WHERE {where}", (reference,)).fetchone()
			count = int(row[0])
		else:
			cursor = conn.execute(f"DELETE FROM candidate_action_claims WHERE {where}", (reference,))
			count = cursor.rowcount
			conn.commit()
		return count * 100, count
	finally:
		conn.close()


def _clean_pending_recruiter_greet(
	data_dir: Path, geek_id: str, job_id: str, *, dry_run: bool
) -> tuple[int, int]:
	"""只清除指定候选人与职位的 pending 招呼预约（Issue #3110）。

	招聘者招呼记录用 (geek_id, job_id) 作业务键，没有候选人与职位共用的随机
	claim_reference。这里按同一对键删除，且只碰 pending：已发出 / 已拒绝的
	去重记录仍然阻止重复招呼。
	"""
	db_path = data_dir / "cache" / "boss_agent.db"
	if not _cache_db_present(db_path):
		return 0, 0
	conn = _open_cache_db(db_path, root=data_dir)
	try:
		if not _table_exists(conn, "recruiter_greet_records"):
			return 0, 0
		where = "geek_id = ? AND job_id = ? AND status = 'pending'"
		params = (_text_key(geek_id), _text_key(job_id))
		if dry_run:
			row = conn.execute(
				f"SELECT COUNT(*) FROM recruiter_greet_records WHERE {where}", params
			).fetchone()
			count = int(row[0])
		else:
			cursor = conn.execute(f"DELETE FROM recruiter_greet_records WHERE {where}", params)
			count = cursor.rowcount
			conn.commit()
		return count * 100, count
	finally:
		conn.close()


def _clean_job_desc_cache(data_dir: Path, *, dry_run: bool) -> tuple[int, int]:
	"""清理职位描述缓存。

	``get_job_desc()`` 只在命中过期条目时懒删除，冷门职位写入后没有任何
	出口；``--all`` 是唯一能整体回收这部分空间的路径。
	"""
	return _clean_table(data_dir, "job_desc_cache", dry_run=dry_run)


def _clean_crawl_data(data_dir: Path, *, dry_run: bool) -> tuple[int, int, int]:
	# 先删数据库行、再删产物文件（Issue #2241）：completed 快速路径在导出前后
	# 重读 crawl_runs，行已消失即放弃写出 / 删除刚写的文件。反过来的顺序
	# （先删文件再删行）会让并发的 resume 在两次删除之间重建隐私产物。
	db_path = data_dir / "cache" / "boss_agent.db"
	rows_removed = 0
	if _cache_db_present(db_path):
		conn = _open_cache_db(db_path, root=data_dir)
		try:
			if _table_exists(conn, "crawl_runs"):
				row = conn.execute("SELECT COUNT(*) FROM crawl_runs").fetchone()
				rows_removed = int(row[0])
				if not dry_run:
					for table in ("crawl_jobs", "crawl_runs", "crawl_budget"):
						if _table_exists(conn, table):
							conn.execute(f"DELETE FROM {table}")  # noqa: S608 - 表名来自固定白名单
					conn.commit()
		finally:
			conn.close()
	files_size, files_count, skipped = _clean_path(data_dir / "crawl", dry_run=dry_run, root=data_dir)
	return files_size + rows_removed * 200, files_count + rows_removed, skipped


def _clean_table(data_dir: Path, table: str, *, dry_run: bool) -> tuple[int, int]:
	"""清理数据库表，返回 (估算字节数, 行数)。"""
	db_path = data_dir / "cache" / "boss_agent.db"
	if not _cache_db_present(db_path):
		return 0, 0

	conn = _open_cache_db(db_path, root=data_dir)
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
