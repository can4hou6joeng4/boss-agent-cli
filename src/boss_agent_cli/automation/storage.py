"""File-backed automation state, queues, and event logs."""

from __future__ import annotations

import csv
import io
import itertools
import json
import math
import os
import sqlite3
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from boss_agent_cli.automation.models import AutomationEvent, PendingAction, PlatformAction, ReviewItem
from boss_agent_cli.cache.store import database_path_problem, is_cache_busy_error, is_cache_local_io_error
from boss_agent_cli.services.chat_utils import sanitize_csv_cell


class AutomationStateError(ValueError):
	"""自动化状态文件（`state.json` 或队列 JSONL）读不出来；调用方应输出信封并停止自动化。"""

	def __init__(self, path: Path, detail: str) -> None:
		super().__init__(f"{path} 无法解析（{detail}）")
		self.path = path
		self.detail = detail


class AutomationControlBusy(RuntimeError):
	"""automation 控制锁被其他进程占用：本次控制更新没有生效，可稍后重试。"""


class AutomationControlBusyDispatched(AutomationControlBusy):
	"""周期内控制锁冲突：平台动作可能已经发出，不能沿用「本次操作没有生效」（Issue #1267）。

	执行周期里业务状态落盘与队列推进是两次独立的本地写入：动作派发成功、
	业务状态尚未落盘时 checkpoint 撞上控制锁，平台侧可能已经生效。调用方
	必须按「结果可能已提交」的语义上报（禁止自动重发），而不是让操作者直接重试。
	"""


class AutomationRunBusy(RuntimeError):
	"""另一个 agent run 正在同一数据目录执行：本次调用没有发出任何请求。"""


_AUTOMATION_DIR = "automation"
_STOP_FILE = "stop.json"


def _path_mode(path: Path) -> int | None:
	"""仅对缺失路径返回 None，悬空链接保留链接自身的类型。"""
	# 3.14 的 Path.exists() / is_file() 会吞掉权限等 OSError（Issue #1680）。
	try:
		mode = path.lstat().st_mode
	except FileNotFoundError:
		return None
	if stat.S_ISLNK(mode):
		try:
			return path.stat().st_mode
		except FileNotFoundError:
			pass
	return mode


def _refuse_symlink_write(path: Path, label: str) -> None:
	"""写入前拒绝符号链接与特殊文件。

	读取侧允许沿「指向普通文件的符号链接」读取（Issue #1728），但写入侧不能
	沿用同一判定：``open("a")`` 会写穿链接目标（数据落到数据目录之外），
	临时文件 + ``os.replace`` 则会静默把链接替换成普通文件、让目标保留旧行；
	恢复链接后旧的 pending / review 行会复活并可能被重放（Issue #1746）。
	与 ``write_private_bytes`` 的符号链接拒绝（Issue #1355）同口径。
	"""
	try:
		mode = path.lstat().st_mode
	except FileNotFoundError:
		return
	except (OSError, ValueError) as exc:
		raise AutomationStateError(path, f"{label} 无法读取路径状态（{exc}）") from exc
	if stat.S_ISLNK(mode):
		raise AutomationStateError(
			path,
			f"{label} 无法写入：路径是符号链接；请改用普通文件路径（先备份再移除链接），"
			"自动化不会写穿链接或替换链接本身",
		)
	if not stat.S_ISREG(mode):
		raise AutomationStateError(
			path,
			f"{label} 无法写入：路径不是普通文件（目录 / FIFO / 设备）；"
			"请先检查该路径后重试",
		)


_INTERVIEW_LEAD_HEADER = "candidate_key,interview_time,reason\n"


def _write_text_atomic(path: Path, text: str) -> None:
	"""原子写小型文本文件：临时文件 + fsync + ``os.replace``。

	表头用截断式 ``write_text`` 写到一半崩溃会留下 0 字节或半截表头，
	之后每次追加都会把它当成数据行跳过 / 粘连（Issue #2331）。
	"""
	fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
	try:
		with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
			handle.write(text)
			handle.flush()
			os.fsync(handle.fileno())
		os.replace(temporary, path)
	finally:
		Path(temporary).unlink(missing_ok=True)


def _unparsed_jsonl_lines(payload: bytes) -> list[bytes]:
	"""返回读取路径会跳过的原始行：坏编码 / 坏 JSON / 非 object（Issue #2172）。

	判定必须与 ``AutomationStore.read_jsonl`` 的容错口径保持一致：这里多留一行
	无害，少留一行就是整文件回写时的静默数据丢失。
	"""
	unparsed: list[bytes] = []
	for raw_line in payload.splitlines():
		try:
			line = raw_line.decode("utf-8")
		except UnicodeDecodeError:
			unparsed.append(raw_line)
			continue
		if not line.strip():
			continue
		try:
			parsed = json.loads(line)
		except (ValueError, RecursionError):
			unparsed.append(raw_line)
			continue
		if not isinstance(parsed, dict):
			unparsed.append(raw_line)
	return unparsed


class AutomationStore:
	"""Small JSONL-backed store for the recruiter automation engine."""

	def __init__(self, data_dir: Path) -> None:
		self.root = data_dir / _AUTOMATION_DIR
		self.root.mkdir(parents=True, exist_ok=True)
		(self.root / "archive").mkdir(exist_ok=True)

	@property
	def state_path(self) -> Path:
		return self.root / "state.json"

	@property
	def stop_path(self) -> Path:
		return self.root / _STOP_FILE

	def read_stop(self) -> dict[str, Any]:
		"""读取独立停止标记，业务快照不能解除它。"""
		return self.read_stop_for(self.root.parent)

	@staticmethod
	def read_stop_for(data_dir: Path) -> dict[str, Any]:
		"""只读停止标记，不创建目录；读取失败时保持熔断。"""
		path = data_dir / _AUTOMATION_DIR / _STOP_FILE
		try:
			mode = _path_mode(path)
			if mode is None:
				return {}
			if stat.S_ISLNK(mode):
				return {"open": True, "reason": "stop.json 是悬空符号链接，已按熔断处理", "corrupt": True}
			# FIFO / 设备不能直接 read_text，否则会阻塞（Issue #1675）。
			if not stat.S_ISREG(mode):
				return {"open": True, "reason": "stop.json 不是普通文件，已按熔断处理", "corrupt": True}
			loaded = json.loads(path.read_text(encoding="utf-8"))
		# ValueError 覆盖 JSONDecodeError / UnicodeDecodeError 与 3.11+ 的超长整数
		# 上限；RecursionError 覆盖深嵌套。熔断状态读不出来必须 fail-closed：
		# 抛出去会让调用方按「未知状态」继续，只有 corrupt 标记能通知人工核对。
		except (ValueError, OSError, RecursionError) as exc:
			return {"open": True, "reason": f"stop.json 无法读取或解析，已按熔断处理：{exc}", "corrupt": True}
		if not isinstance(loaded, dict):
			return {"open": True, "reason": "stop.json 顶层不是 JSON object，已按熔断处理", "corrupt": True}
		if "open" not in loaded:
			# 缺少开关字段同样意味着「停止状态未知」：老读者会把它当没停止，
			# resume() 也看不到可解除的闩锁（Issue #1598）。与类型漂移、解析
			# 失败一样 fail-closed，不给自动化放行任何写操作。
			return {
				"open": True,
				"reason": "stop.json 缺少 open 字段，已按熔断处理",
				"corrupt": True,
			}
		if not isinstance(loaded["open"], bool):
			# 熔断开关只认布尔：0 / null / "false" 这类真值漂移若被当成「已关闭」，
			# 一次写坏就能放行自动化写操作；而 "true" 又会让两个读取器（SafetyGuard
			# 的真值与 location 的 `is not True`）得到相反结论（Issue #1242）。
			# 与顶层非 object 一致，按损坏处理并停下等人工核对。
			return {
				"open": True,
				"reason": "stop.json 的 open 字段不是布尔值，已按熔断处理",
				"corrupt": True,
			}
		return cast("dict[str, Any]", loaded)

	def request_stop(self, reason: str) -> None:
		with self._control_lock():
			self._set_stop(reason)

	def _set_stop(self, reason: str) -> None:
		path = self.stop_path
		try:
			mode = _path_mode(path)
		except (OSError, ValueError) as exc:
			raise AutomationStateError(path, f"无法读取 stop.json 的路径状态（{exc}）") from exc
		# 目录 / 指向目录的符号链接无法通过 os.replace 写入，先报状态损坏（Issue #1684）。
		# 悬空链接本身可以被原子替换，仍允许写入。
		if mode is not None and not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
			raise AutomationStateError(path, "stop.json 不是普通文件，请先检查该路径后重试")
		self._write_json_atomic(path, {
			"open": True,
			"reason": reason,
			"opened_at": datetime.now(timezone.utc).isoformat(),
		})

	def resume(self) -> bool:
		"""Explicit operator recovery; retain every confirmed business checkpoint."""
		with self._control_lock():
			path = self.stop_path
			try:
				mode = _path_mode(path)
			except (OSError, ValueError) as exc:
				raise AutomationStateError(path, f"无法读取 stop.json 的路径状态（{exc}）") from exc
			# 先校验，避免同名目录导致第二次原子写失败（Issue #1678）。
			# 其他现存非普通文件也需人工处理，悬空链接仍允许原子替换。
			if mode is not None and not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
				raise AutomationStateError(path, "stop.json 不是普通文件，请先检查该路径后重试")
			state = self.read_state()
			if not state.get("autonomy", {}).get("circuit_breaker", {}).get("open"):
				return False
			# resume 直接走原子写、不经过 write_state：不在这里拒链接会静默替换
			# state.json 链接，目标保留旧熔断 / checkpoint，恢复链接后旧状态复活
			# （Issue #1751）。stop.json 的悬空链接仍允许原子替换。
			_refuse_symlink_write(self.state_path, "state.json")
			closed = {
				"open": False,
				"reason": "explicit-resume-after-verification",
				"resumed_at": datetime.now(timezone.utc).isoformat(),
			}
			state.setdefault("autonomy", {})["circuit_breaker"] = closed
			# State first: an interruption between writes leaves the old latch open.
			# Keeping a closed marker also prevents stale open snapshots relocking it.
			self._write_json_atomic(self.state_path, state)
			self._write_json_atomic(self.stop_path, closed)
			return True

	@contextmanager
	def execution_lock(self) -> Iterator[None]:
		"""Hold a cross-process execution lock for one automation cycle.

		待执行队列是整文件回写的：两个 executor 同时读到同一份快照时，后写者会
		用旧行覆盖先写者的在途标记，同一条动作可能被派发两次（Issue #1253）。
		执行锁串行化整个周期；它独立于 `.control-lock.sqlite3`，因此 `agent stop`
		仍能在运行期间立即写入熔断。
		"""
		connection: sqlite3.Connection | None = None
		try:
			lock_path = self.root / ".run-lock.sqlite3"
			try:
				problem = database_path_problem(lock_path)
			except (OSError, ValueError) as exc:
				raise AutomationStateError(lock_path, f"自动化执行锁无法读取路径状态（{exc}）") from exc
			# SQLite 会跟随符号链接把库写进目标文件（Issue #1743，与 #1739 同口径）。
			if problem is not None:
				raise AutomationStateError(lock_path, f"自动化执行锁无法打开（{problem}）")
			try:
				connection = sqlite3.connect(lock_path, timeout=0.5)
				connection.execute("BEGIN IMMEDIATE")
			except sqlite3.DatabaseError as exc:
				if isinstance(exc, sqlite3.OperationalError) and is_cache_busy_error(exc):
					raise AutomationRunBusy(
						"另一个 boss agent run 正在执行，本次没有发出任何请求"
					) from exc
				# 目录不可写 / 磁盘满等本地 IO 错误不是锁文件损坏：原样上抛交给
				# main 的 LOCAL_IO_ERROR 分支（Issue #1930）。
				if is_cache_local_io_error(exc):
					raise
				# 锁文件损坏或路径被同名目录占用是确定性本地状态问题：原样上抛
				# 会让命令丢掉整个信封，或在 agent run 里被兜成可重试的
				# NETWORK_ERROR（Issue #1304）。
				raise AutomationStateError(
					lock_path,
					f"自动化执行锁无法打开（{exc}）",
				) from exc
			yield
			connection.commit()
		finally:
			if connection is not None:
				connection.close()

	@contextmanager
	def _control_lock(self) -> Iterator[None]:
		"""Serialize tiny control updates across processes with a bounded wait."""
		connection: sqlite3.Connection | None = None
		try:
			lock_path = self.root / ".control-lock.sqlite3"
			try:
				problem = database_path_problem(lock_path)
			except (OSError, ValueError) as exc:
				raise AutomationStateError(lock_path, f"自动化控制锁无法读取路径状态（{exc}）") from exc
			# 同执行锁：符号链接会让 SQLite 把控制锁库写进链接目标（Issue #1743）。
			if problem is not None:
				raise AutomationStateError(lock_path, f"自动化控制锁无法打开（{problem}）")
			try:
				connection = sqlite3.connect(lock_path, timeout=1.0)
				connection.execute("BEGIN IMMEDIATE")
			except sqlite3.DatabaseError as exc:
				# 锁冲突是 automation 自己的并发边界，不是缓存库问题：直接上抛会被
				# main 的兜底层报成 CACHE_BUSY 并指向 cache/boss_agent.db，而实际的
				# 锁文件是 automation/.control-lock.sqlite3（Issue #1251）。
				if isinstance(exc, sqlite3.OperationalError) and is_cache_busy_error(exc):
					raise AutomationControlBusy(
						"automation 控制锁正被其他进程占用，本次操作没有生效"
					) from exc
				# 与执行锁同口径：本地 IO 错误交给 main 的 LOCAL_IO_ERROR 分支（Issue #1930）。
				if is_cache_local_io_error(exc):
					raise
				# 锁文件损坏 / 路径被同名目录占用：确定性本地状态问题（Issue #1304）。
				raise AutomationStateError(
					lock_path,
					f"自动化控制锁无法打开（{exc}）",
				) from exc
			yield
			connection.commit()
		finally:
			if connection is not None:
				connection.close()

	def read_state(self) -> dict[str, Any]:
		path = self.state_path
		try:
			mode = _path_mode(path)
		except (OSError, ValueError) as exc:
			raise AutomationStateError(path, f"无法读取 state.json 的路径状态（{exc}）") from exc
		if mode is None:
			state: dict[str, Any] = {"conversations": {}, "autonomy": {}, "safety": {}}
		else:
			# 悬空链接 / 目录等路径给不出可信状态，不能静默当成空状态（Issue #1682）。
			if stat.S_ISLNK(mode):
				raise AutomationStateError(path, "state.json 是悬空符号链接，无法读取状态")
			if not stat.S_ISREG(mode):
				raise AutomationStateError(path, "state.json 不是普通文件")
			try:
				loaded = json.loads(path.read_text(encoding="utf-8"))
			except (ValueError, OSError, RecursionError) as exc:
				raise AutomationStateError(path, str(exc)) from exc
			if not isinstance(loaded, dict):
				raise AutomationStateError(self.state_path, "顶层必须是 JSON object")
			state = cast("dict[str, Any]", loaded)
			# 后续读取按 object 逐层取值：嵌套容器类型漂移在这里报成状态损坏，
			# 而不是让 AttributeError 被兜成 INTERNAL_ERROR（Issue #1130）。
			for key in ("conversations", "autonomy", "safety"):
				if key in state and not isinstance(state[key], dict):
					raise AutomationStateError(self.state_path, f"{key} 必须是 JSON object")
			breaker = state.get("autonomy", {}).get("circuit_breaker", {})
			if not isinstance(breaker, dict):
				raise AutomationStateError(self.state_path, "autonomy.circuit_breaker 必须是 JSON object")
			if breaker and "open" not in breaker:
				# 熔断快照缺少开关字段 = 停止状态未知：SafetyGuard 的 ``.get("open")``
				# 会把它当「已关闭」继续自动化写操作；与 stop.json 的缺字段口径一致
				# fail-closed（Issue #1598、#2397）。空对象表示没有熔断。
				raise AutomationStateError(
					self.state_path,
					"autonomy.circuit_breaker 缺少 open 字段；停止状态未知，"
					"请先核对平台实际状态，再修正该字段或移除 circuit_breaker",
				)
			if "open" in breaker and not isinstance(breaker["open"], bool):
				# 真值语义会让 0 / null 关闭熔断、"false" 打开熔断（Issue #1242）；
				# 与 consecutive_errors 一样按状态损坏处理。
				raise AutomationStateError(self.state_path, "autonomy.circuit_breaker.open 必须是布尔值")
			for candidate_key, prior in state.get("conversations", {}).items():
				if not isinstance(prior, dict):
					raise AutomationStateError(self.state_path, f"conversations[{candidate_key!r}] 必须是 JSON object")
			errors = state.get("safety", {}).get("consecutive_errors", 0)
			if isinstance(errors, bool) or not isinstance(errors, int):
				raise AutomationStateError(self.state_path, "safety.consecutive_errors 必须是整数")
			if errors < 0:
				# 失败计数只递增、归零；负值（外部编辑 / 迁移）会让连续失败熔断
				# 需要更多次失败才打开，削弱安全闩锁，按状态损坏 fail-closed
				# （Issue #1242、#1330、#2173）。
				raise AutomationStateError(
					self.state_path,
					"safety.consecutive_errors 必须是非负整数；连续失败熔断按该计数判断，"
					"负值会让熔断不按预期打开。请先备份 state.json，再修正该字段或移除它",
				)
		stop = self.read_stop()
		if "open" in stop:
			state.setdefault("autonomy", {})["circuit_breaker"] = stop
		return state

	def write_state(self, state: dict[str, Any]) -> None:
		# Preserve legacy callers that set the breaker through write_state. A stale
		# snapshot lacking it can never remove the separate stop latch.
		with self._control_lock():
			_refuse_symlink_write(self.state_path, "state.json")
			autonomy = state.get("autonomy", {})
			if not isinstance(autonomy, dict):
				raise AutomationStateError(self.state_path, "autonomy 必须是 JSON object")
			breaker = autonomy.get("circuit_breaker", {})
			if not isinstance(breaker, dict):
				raise AutomationStateError(self.state_path, "autonomy.circuit_breaker 必须是 JSON object")
			if breaker and "open" not in breaker:
				# 不允许把「缺开关字段」的熔断快照写回磁盘：下次读取会按状态损坏
				# 拒绝，而不是让自动化在停止状态未知时继续（Issue #2397）。
				raise AutomationStateError(
					self.state_path,
					"autonomy.circuit_breaker 缺少 open 字段；停止状态未知，"
					"请先核对平台实际状态，再修正该字段或移除 circuit_breaker",
				)
			if "open" in breaker and not isinstance(breaker["open"], bool):
				raise AutomationStateError(self.state_path, "autonomy.circuit_breaker.open 必须是布尔值")
			stop = self.read_stop()
			if breaker.get("open") and not stop:
				self._set_stop(str(breaker.get("reason", "熔断已打开")))
				stop = self.read_stop()
			if "open" in stop:
				state.setdefault("autonomy", {})["circuit_breaker"] = stop
			self._write_json_atomic(self.state_path, state)

	def _write_json_atomic(self, path: Path, value: dict[str, Any]) -> None:
		fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=self.root)
		try:
			with os.fdopen(fd, "w", encoding="utf-8") as handle:
				try:
					json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
				except (ValueError, TypeError, RecursionError) as exc:
					# 深嵌套 / 循环引用的状态读得进来却写不回去：必须报本地状态
					# 不可写，不能兜成可重试的 NETWORK_ERROR（Issue #1929）。
					# 不可序列化对象 / 混合类型键（sort_keys）抛 TypeError，同属
					# 本地状态问题，不能裸穿成 INTERNAL_ERROR（Issue #2332）。
					raise AutomationStateError(
						path, f"状态文件无法序列化（{type(exc).__name__}: {exc}）"
					) from exc
				handle.flush()
				os.fsync(handle.fileno())
			os.replace(temporary, path)
		finally:
			Path(temporary).unlink(missing_ok=True)

	def append_event(self, event: AutomationEvent) -> None:
		self._append_jsonl("action-log.jsonl", asdict(event))

	def append_review(self, item: ReviewItem) -> None:
		self._append_jsonl("human-review-queue.jsonl", asdict(item))

	def append_pending(self, action: PendingAction) -> None:
		self._append_jsonl("pending-actions.jsonl", asdict(action))

	@property
	def reviews_path(self) -> Path:
		return self.root / "human-review-queue.jsonl"

	@property
	def pending_path(self) -> Path:
		return self.root / "pending-actions.jsonl"

	def read_reviews(self) -> list[ReviewItem]:
		path = self.reviews_path
		return [_review_from_row(row, path) for row in self.read_jsonl(path.name)]

	def read_pending(self) -> list[PendingAction]:
		path = self.pending_path
		return [_pending_from_row(row, path) for row in self.read_jsonl(path.name)]

	def write_reviews(self, items: list[ReviewItem]) -> None:
		self.write_jsonl("human-review-queue.jsonl", [asdict(item) for item in items])

	def write_pending(self, items: list[PendingAction]) -> None:
		self.write_jsonl("pending-actions.jsonl", [asdict(item) for item in items])

	def approve_review(self, review_id: str, reviewed_at: str) -> PendingAction | None:
		# 批准会追加待执行行、改写复核队列：与运行中的 cycle 串行化，避免刚入队
		# 的动作被整文件回写的旧快照覆盖丢失（Issue #1253）。
		with self.execution_lock():
			return self._approve_review_locked(review_id, reviewed_at)

	def _approve_review_locked(self, review_id: str, reviewed_at: str) -> PendingAction | None:
		reviews = self.read_reviews()
		updated_reviews: list[ReviewItem] = []
		pending: PendingAction | None = None
		for item in reviews:
			if item.id == review_id and item.status == "review":
				# 未知动作进不了待执行队列：在改写任何文件之前报出。
				queued_action(item, self.reviews_path)
				approved = ReviewItem(
					id=item.id,
					ts=item.ts,
					platform=item.platform,
					candidate_key=item.candidate_key,
					action=item.action,
					status="approved",
					confidence=item.confidence,
					reason=item.reason,
					message=item.message,
					reviewed_at=reviewed_at,
				)
				updated_reviews.append(approved)
				pending = PendingAction(
					id=item.id,
					ts=reviewed_at,
					platform=item.platform,
					candidate_key=item.candidate_key,
					action=item.action,
					status="pending",
					confidence=item.confidence,
					reason=item.reason,
					message=item.message,
					approved_review_id=item.id,
				)
			else:
				updated_reviews.append(item)
		if pending is None:
			return None
		# 先入队、再标记复核项：入队失败时复核项仍是 review，操作员可以直接重试；
		# 反过来先写 approved 会让动作永久丢失（Issue #1248）。上一次若在两步之间
		# 失败，pending 里已有同一个 approved_review_id，不重复入队，只补写复核队列。
		existing = next(
			(item for item in self.read_pending() if item.approved_review_id == review_id),
			None,
		)
		if existing is None:
			self.append_pending(pending)
		self.write_reviews(updated_reviews)
		return existing if existing is not None else pending

	def reject_review(
		self,
		review_id: str,
		reason: str,
		reviewed_at: str,
	) -> ReviewItem | None:
		# 拒绝也会改写复核队列：与运行中的 cycle 串行化（Issue #1253）。
		with self.execution_lock():
			return self._reject_review_locked(review_id, reason, reviewed_at)

	def _reject_review_locked(
		self,
		review_id: str,
		reason: str,
		reviewed_at: str,
	) -> ReviewItem | None:
		reviews = self.read_reviews()
		updated_reviews: list[ReviewItem] = []
		rejected: ReviewItem | None = None
		for item in reviews:
			if item.id == review_id and item.status == "review":
				# 拒绝会记录一条带动作的跳过事件：先确认动作可解析，再改写队列。
				queued_action(item, self.reviews_path)
				rejected = ReviewItem(
					id=item.id,
					ts=item.ts,
					platform=item.platform,
					candidate_key=item.candidate_key,
					action=item.action,
					status="rejected",
					confidence=item.confidence,
					reason=item.reason,
					message=item.message,
					reviewed_at=reviewed_at,
					rejection_reason=reason,
				)
				updated_reviews.append(rejected)
			else:
				updated_reviews.append(item)
		if rejected is None:
			return None
		# approve 的「先入队、再写复核队列」可能在中途失败，留下携带
		# approved_review_id 的 pending 行（Issue #1248）。人工拒绝后它不能再被
		# 下一轮派发：尚未派发的行就地移除；已在执行 / 结果未知的行拒绝本次
		# reject，先让操作者核对，绝不静默删除可能已生效的写操作（Issue #2124）。
		pending_rows = self.read_pending()
		linked = [row for row in pending_rows if row.approved_review_id == review_id]
		inflight = [row for row in linked if row.status in {"executing", "verification-required"}]
		if inflight:
			raise AutomationStateError(
				self.pending_path,
				f"复核项 {review_id!r} 对应的动作已派发（状态 {inflight[0].status}），"
				"结果未知；先核对平台与 action-log，再决定是否拒绝",
			)
		if any(row.status == "pending" for row in linked):
			# 先移除队列行再改写复核：反过来失败会让 rejected 的复核仍留下可派发
			# 的 pending 行。中断后复核仍是 review，可以再次 approve / reject。
			self.write_pending(
				[
					row
					for row in pending_rows
					if not (row.approved_review_id == review_id and row.status == "pending")
				]
			)
		self.write_reviews(updated_reviews)
		return rejected

	def read_jsonl(self, name: str) -> list[dict[str, Any]]:
		path = self.root / name
		rows: list[dict[str, Any]] = []
		try:
			mode = _path_mode(path)
		except (OSError, ValueError) as exc:
			raise AutomationStateError(path, f"队列文件无法读取路径状态（{exc}）") from exc
		if mode is None:
			# 只有「文件不存在」才是空队列。不能先用 path.exists() 预判：
			# 预判与读取之间文件可能被删除，那个 TOCTOU 窗口会让删除变成失败。
			return rows
		# FIFO / 设备直接 read_bytes() 会永久阻塞在打开阶段，agent run /
		# pending list / stats 都会挂死且没有任何信封（Issue #1728）。与
		# read_state() / read_stop() 同口径：悬空链接与非普通文件 fail-closed。
		# 指向普通文件的符号链接在 _path_mode 里已解析成目标类型，照常读取。
		if stat.S_ISLNK(mode):
			raise AutomationStateError(
				path,
				"队列文件无法读取：路径是悬空符号链接；请确认链接目标存在后重试，"
				"文件损坏时先备份再修正或移除",
			)
		if not stat.S_ISREG(mode):
			raise AutomationStateError(
				path,
				"队列文件无法读取：路径不是普通文件（目录 / FIFO / 设备）；"
				"请确认文件可读（权限正常、路径不是目录）后重试，"
				"文件损坏时先备份再修正或移除",
			)
		try:
			payload = path.read_bytes()
		except FileNotFoundError:
			# lstat 与读取之间文件被删除：仍然只是「队列为空」。
			return rows
		except OSError as exc:
			# 权限不足、路径被同名目录占用、磁盘错误都把队列读成空列表时，
			# `agent run` 会静默跳过已批准动作并照常报 OK（Issue #1406）。
			# 状态未知时 fail-closed，交由命令层输出 AUTOMATION_STATE_INVALID。
			raise AutomationStateError(
				path,
				f"队列文件无法读取（{exc}）；请确认文件可读（权限正常、路径不是目录）后重试，"
				"文件损坏时先备份再修正或移除",
			) from exc
		for raw_line in payload.splitlines():
			try:
				line = raw_line.decode("utf-8")
			except UnicodeDecodeError:
				# 整文件 read_text() 解码失败会把前面所有合法行一起丢掉：
				# 一个 GBK 字节就让 pending / stats 看到空队列（Issue #1337）。
				# 按行跳过坏字节行，与单行 JSON 损坏的容错口径一致。
				continue
			if not line.strip():
				continue
			try:
				parsed = json.loads(line)
			except (ValueError, RecursionError):
				# 单行损坏（手工编辑、外部写入、超长整数、深嵌套）不该让
				# stats / pending 整体失败。
				continue
			if isinstance(parsed, dict):
				rows.append(parsed)
		return rows

	def write_jsonl(self, name: str, rows: list[dict[str, Any]]) -> None:
		path = self.root / name
		_refuse_symlink_write(path, "队列文件")
		try:
			body = "".join(
				f"{json.dumps(row, ensure_ascii=False, sort_keys=True)}\n"
				for row in rows
			)
		except (ValueError, TypeError, RecursionError) as exc:
			# 与 _write_json_atomic 同口径：序列化失败是本地状态问题（Issue #1929、
			# #2332：不可序列化对象 / 混合类型键的 TypeError）。
			raise AutomationStateError(
				path, f"队列文件无法序列化（{type(exc).__name__}: {exc}）"
			) from exc
		# 读取路径会跳过坏行（Issue #1337），整文件回写不能因此把它们删掉：
		# 一行可能是一条已批准但因崩溃截断的动作记录，静默删除后既不会执行、
		# 也不会留下任何痕迹（Issue #2172）。坏行按原字节保留在文件末尾。
		preserved = self._preserved_jsonl_lines(path)
		fd, temporary = tempfile.mkstemp(prefix=f".{name}.", dir=self.root)
		try:
			with os.fdopen(fd, "wb") as handle:
				try:
					handle.write(body.encode("utf-8"))
				except UnicodeEncodeError as exc:
					# JSON 解码允许转义的孤立代理字符，UTF-8 写入会拒绝（Issue #2073）。
					raise AutomationStateError(path, f"队列文件无法编码为 UTF-8（{exc}）") from exc
				for raw_line in preserved:
					handle.write(raw_line)
					handle.write(b"\n")
				handle.flush()
				os.fsync(handle.fileno())
			os.replace(temporary, path)
		finally:
			Path(temporary).unlink(missing_ok=True)

	def _preserved_jsonl_lines(self, path: Path) -> list[bytes]:
		"""回写前读出读取路径会跳过的原始行，按原字节保留（Issue #2172）。

		调用方已通过 ``_refuse_symlink_write`` 排除符号链接与特殊文件；这里
		只需处理「文件不存在」与读取失败，读取失败按状态损坏 fail-closed。
		"""
		try:
			payload = path.read_bytes()
		except FileNotFoundError:
			return []
		except OSError as exc:
			raise AutomationStateError(
				path,
				f"队列文件无法读取（{exc}）；回写会丢失无法解析的行，已停止。"
				"请确认文件可读后重试，文件损坏时先备份再修正或移除",
			) from exc
		return _unparsed_jsonl_lines(payload)

	def append_interview_lead(
		self,
		candidate_key: str,
		interview_time: str,
		reason: str,
	) -> bool:
		"""Record one interview lead; returns False when it was already present.

		`candidate_key` 是候选人在平台上的昵称，属于外部可控输入：直接拼字符串
		会让含逗号 / 引号 / 换行的昵称破坏 CSV 行结构，以 `=` / `+` / `-` / `@`
		开头还会变成 Excel 公式。这里统一走 `csv` 模块与共享清洗规则，
		并按 `(candidate_key, interview_time)` 去重——同一候选人每轮自动化都会
		重新命中同一时间，重复行只会污染运维清单（Issue #889）。
		"""
		path = self.root / "interview-leads.csv"
		_refuse_symlink_write(path, "面试线索表")
		row = [sanitize_csv_cell(candidate_key), sanitize_csv_cell(interview_time), sanitize_csv_cell(reason)]
		try:
			mode = _path_mode(path)
		except (OSError, ValueError) as exc:
			# 路径状态读不出来时不能当成「文件不存在」：紧接着的表头写入会
			# 截断已有线索历史（Issue #1694）。
			raise AutomationStateError(path, f"无法读取面试线索表的路径状态（{exc}）") from exc
		if mode is not None and not stat.S_ISREG(mode):
			# 目录 / FIFO / 设备 / 悬空链接都不能按 CSV 追加：FIFO 会阻塞，
			# 其余路径写入会失败或丢掉链接目标之外的数据。
			raise AutomationStateError(path, "interview-leads.csv 不是普通文件，请先检查该路径后重试")
		if mode is None:
			try:
				_write_text_atomic(path, _INTERVIEW_LEAD_HEADER)
			except (OSError, UnicodeError) as exc:
				raise AutomationStateError(path, f"面试线索表无法写入（{exc}）") from exc
		else:
			try:
				with path.open("r", encoding="utf-8", newline="") as handle:
					first_line = handle.readline()
					# 只有首行确实是表头才跳过：0 字节文件（表头写入被中断的崩溃
					# 窗口）或外部创建的无表头文件，第一条数据行必须参与判重，
					# 否则同一线索会重复追加（Issue #2331）。
					has_header = first_line.rstrip("\r\n") == _INTERVIEW_LEAD_HEADER.rstrip("\n")
					tail: Iterator[list[str]]
					if has_header:
						tail = itertools.islice(csv.reader(handle), 0, None)
					else:
						# 无表头文件要整体交给同一个 reader：第一条记录可能带
						# 换行的引号字段，按物理行拆开会让它永远不参与判重，
						# 同一线索会被反复追加（Issue #2349）。
						handle.seek(0)
						tail = csv.reader(handle)
					if any(fields[:2] == row[:2] for fields in tail if len(fields) >= 2):
						return False
			except (UnicodeDecodeError, csv.Error):
				# 手工编辑或外部写入造成的坏文件不该让整轮自动化失败；无法判重时
				# 继续追加，最坏只是多一行可见的重复记录。
				pass
			except OSError as exc:
				# 读不出来就无法判重，也无法确认不会覆盖历史：与队列读取同口径
				# fail-closed，而不是让 PermissionError 冒成内部错误。
				raise AutomationStateError(path, f"面试线索表无法读取（{exc}）") from exc
		try:
			buffer = io.StringIO()
			csv.writer(buffer).writerow(row)
			record = buffer.getvalue().encode("utf-8")
			with path.open("a+b") as handle:
				handle.seek(0, os.SEEK_END)
				if handle.tell() == 0:
					# 空文件：补写表头，不能把第一条数据行当成表头（Issue #2331）。
					handle.write(_INTERVIEW_LEAD_HEADER.encode("utf-8"))
				else:
					handle.seek(-1, os.SEEK_END)
					if handle.read(1) != b"\n":
						# 半截表头 / 外部写入的无换行末行：先补换行，避免与新行粘连。
						handle.write(b"\n")
				handle.write(record)
		except (OSError, UnicodeError) as exc:
			# 只读文件 / 磁盘满 / 孤立代理字符：带路径报状态不可写，与队列写入口径
			# 一致（Issue #1933）。
			raise AutomationStateError(path, f"面试线索表无法写入（{exc}）") from exc
		return True

	def stats(self) -> dict[str, Any]:
		events = self.read_jsonl("action-log.jsonl")
		# 与 agent pending list / agent run 共用同一套行校验：统计页是熔断后的
		# 恢复入口，若用原始 JSONL 行计数，坏 confidence / status 会被当作
		# 正常待执行量，操作者据此判断「还有多少动作要核对」（Issue #1667）。
		reviews = [asdict(item) for item in self.read_reviews()]
		pending = [asdict(item) for item in self.read_pending()]
		state = self.read_state()
		today = datetime.now(timezone.utc).date().isoformat()
		recent_errors = [
			item
			for item in events[-20:]
			# 数组 / 对象的 `in` 集合判断会抛 TypeError，把恢复入口兜成
			# INTERNAL_ERROR：先做类型校验（Issue #1928）。
			if isinstance(item.get("status"), str)
			and item.get("status")
			in {"STOPPED_BY_SAFETY", "CIRCUIT_BREAKER_OPEN", "PLATFORM_VERIFICATION_REQUIRED"}
		]
		return {
			"events": len(events),
			"auto_executed": sum(
				1 for item in events if item.get("status") == "AUTO_EXECUTED"
			),
			"dry_run": sum(1 for item in events if item.get("status") == "DRY_RUN"),
			"today_executed": sum(
				1
				for item in events
				if str(item.get("ts", "")).startswith(today)
				and item.get("status") == "AUTO_EXECUTED"
			),
			"human_reviews": sum(1 for item in reviews if item.get("status") == "review"),
			"pending_actions": sum(1 for item in pending if item.get("status") == "pending"),
			"circuit_breakers": sum(
				1 for item in events if item.get("status") == "CIRCUIT_BREAKER_OPEN"
			),
			"circuit_breaker": state.get("autonomy", {}).get("circuit_breaker", {}),
			"recent_errors": recent_errors[-5:],
		}

	def _append_jsonl(self, name: str, row: dict[str, Any]) -> None:
		path = self.root / name
		_refuse_symlink_write(path, "队列文件")
		try:
			# 上一次写入的末行可能缺少换行。保留其分隔符，避免追加后两条记录
			# 粘成一行并在读取时一起丢失（Issue #2072）。
			record = (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
			with path.open("a+b") as handle:
				handle.seek(0, os.SEEK_END)
				if handle.tell() > 0:
					handle.seek(-1, os.SEEK_END)
					if handle.read(1) != b"\n":
						record = b"\n" + record
				handle.write(record)
				# 追加的是「已批准动作」等关键记录：与 _write_json_atomic 同口径
				# fsync，缩小崩溃 / 断电只落下半行的窗口（Issue #2172）。
				handle.flush()
				os.fsync(handle.fileno())
		except (OSError, ValueError, TypeError, RecursionError) as exc:
			# 只读文件 / 只读目录 / 磁盘满：动作已经发生，写不进日志同样必须
			# 带着路径与原因停下，不能兜成可重试的通用错误。深嵌套 / 循环引用 /
			# 不可序列化对象的序列化失败同属本地状态问题（Issue #1929、#2332）。
			raise AutomationStateError(
				path,
				f"队列文件无法写入（{exc}）；请确认文件可写（权限正常、路径不是目录）后重试",
			) from exc


def _row_confidence(row: dict[str, Any], path: Path) -> float:
	"""读取队列记录的 confidence；类型漂移按状态损坏上报，不带着未知分数执行动作。"""
	value = row.get("confidence", 0.0)
	try:
		confidence = float(value)
	except (TypeError, ValueError, OverflowError):
		confidence = math.nan
	if isinstance(value, bool) or not math.isfinite(confidence):
		raise AutomationStateError(path, f"记录 {str(row.get('id', ''))!r} 的 confidence 不是有限数字")
	return confidence


def queued_action(item: ReviewItem | PendingAction, path: Path) -> PlatformAction:
	"""把队列记录的 action 解析成平台动作；未知取值按状态损坏上报，不猜测要执行什么。"""
	try:
		return PlatformAction(item.action)
	except ValueError:
		raise AutomationStateError(path, f"记录 {item.id!r} 的 action {item.action!r} 不是已知动作") from None


def _row_text(row: dict[str, Any], key: str, path: Path, default: str = "") -> str:
	"""读取队列记录的文本字段；类型漂移按状态损坏上报，不把 str(None) 当话术执行。

	``message`` 会被真的发给候选人，``platform`` / ``candidate_key`` / ``reason``
	参与匹配与审计；静默 ``str()`` 会把 ``null`` / 数字 / 容器变成 `"None"` /
	`"123"` / `"{'text': 'hi'}"`（Issue #1867）。
	"""
	value = row.get(key, default)
	if not isinstance(value, str):
		raise AutomationStateError(
			path, f"记录 {str(row.get('id', ''))!r} 的 {key} 不是字符串"
		)
	return value


def _review_from_row(row: dict[str, Any], path: Path) -> ReviewItem:
	if "status" not in row:
		# 与 pending 队列同口径（Issue #2022）：缺字段的复核项不能默认成
		# 可等待 / 可批准的 review。
		raise AutomationStateError(path, f"记录 {str(row.get('id', ''))!r} 缺少 status 字段")
	status = row["status"]
	if not isinstance(status, str) or status not in _REVIEW_STATUSES:
		# 漂移值会让复核项永远不匹配 "review"，approve 报「不存在或已处理」，
		# stats 也不再计数：与 pending 队列同口径 fail-closed（Issue #1931）。
		raise AutomationStateError(
			path, f"记录 {str(row.get('id', ''))!r} 的 status {status!r} 不是已知状态"
		)
	action = str(row.get("action", ""))
	return ReviewItem(
		id=str(row.get("id", "")),
		ts=str(row.get("ts", "")),
		platform=_row_text(row, "platform", path),
		candidate_key=_row_text(row, "candidate_key", path),
		action=action,
		status=status,
		confidence=_row_confidence(row, path),
		reason=_row_text(row, "reason", path),
		message=_row_text(row, "message", path),
		reviewed_at=str(row.get("reviewed_at", "")),
		rejection_reason=str(row.get("rejection_reason", "")),
	)


#: 待执行队列的合法状态：pending 可执行；executing / verification-required 表示
#: 动作可能已派发，必须跳过且不重发；executed 是终态。
_PENDING_STATUSES = frozenset({"pending", "executing", "executed", "verification-required"})

#: 复核队列的合法状态：review 等待人工处理；approved / rejected 是终态。
_REVIEW_STATUSES = frozenset({"review", "approved", "rejected"})


def _pending_from_row(row: dict[str, Any], path: Path) -> PendingAction:
	if "status" not in row:
		# 缺字段不能默认成可执行的 pending：迁移 / 外部写入丢字段时该行实际
		# 可能已是 executing / executed，默认 pending 会重复派发可能已生效的
		# 写动作（Issue #2022）。
		raise AutomationStateError(path, f"记录 {str(row.get('id', ''))!r} 缺少 status 字段")
	status = row["status"]
	if not isinstance(status, str) or status not in _PENDING_STATUSES:
		# status 漂移（null / 数字 / 数组 / 对象 / 大小写不符 / 旧版 "dry-run"）会被
		# process_pending 静默 continue：已批准的动作永远不执行，agent run
		# 仍报 OK。数组 / 对象的 `in frozenset` 还会抛 TypeError 被兜成
		# INTERNAL_ERROR，所以先做类型校验再比对取值。
		# 与 confidence / action 同口径 fail-closed（Issue #1337 / #1410）。
		raise AutomationStateError(
			path, f"记录 {str(row.get('id', ''))!r} 的 status {status!r} 不是已知状态"
		)
	action = str(row.get("action", ""))
	return PendingAction(
		id=str(row.get("id", "")),
		ts=str(row.get("ts", "")),
		platform=_row_text(row, "platform", path),
		candidate_key=_row_text(row, "candidate_key", path),
		action=action,
		status=status,
		confidence=_row_confidence(row, path),
		reason=_row_text(row, "reason", path),
		message=_row_text(row, "message", path),
		approved_review_id=str(row.get("approved_review_id", "")),
		updated_at=str(row.get("updated_at", "")),
	)
