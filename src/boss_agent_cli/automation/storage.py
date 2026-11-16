"""File-backed automation state, queues, and event logs."""

from __future__ import annotations

import csv
import itertools
import json
import math
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from boss_agent_cli.automation.models import AutomationEvent, PendingAction, PlatformAction, ReviewItem
from boss_agent_cli.cache.store import is_cache_busy_error
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

	执行周期里队列写回与 checkpoint 是两次独立的本地写入：动作派发成功、队列
	已记为 executed 之后 checkpoint 才撞上控制锁时，平台侧可能已经生效。调用方
	必须按「结果可能已提交」的语义上报（禁止自动重发），而不是让操作者直接重试。
	"""


class AutomationRunBusy(RuntimeError):
	"""另一个 agent run 正在同一数据目录执行：本次调用没有发出任何请求。"""


class AutomationStore:
	"""Small JSONL-backed store for the recruiter automation engine."""

	def __init__(self, data_dir: Path) -> None:
		self.root = data_dir / "automation"
		self.root.mkdir(parents=True, exist_ok=True)
		(self.root / "archive").mkdir(exist_ok=True)

	@property
	def state_path(self) -> Path:
		return self.root / "state.json"

	@property
	def stop_path(self) -> Path:
		return self.root / "stop.json"

	def read_stop(self) -> dict[str, Any]:
		"""Read the independent stop latch; business snapshots cannot clear it.

		熔断开关读不出来时按「已熔断」处理：状态未知时宁可停下等人工核对，
		也不能让自动化继续向平台发送消息。
		"""
		if not self.stop_path.exists():
			return {}
		try:
			loaded = json.loads(self.stop_path.read_text(encoding="utf-8"))
		# ValueError 覆盖 JSONDecodeError / UnicodeDecodeError 与 3.11+ 的超长整数
		# 上限；RecursionError 覆盖深嵌套。熔断状态读不出来必须 fail-closed：
		# 抛出去会让调用方按「未知状态」继续，只有 corrupt 标记能通知人工核对。
		except (ValueError, OSError, RecursionError) as exc:
			return {"open": True, "reason": f"stop.json 无法解析，已按熔断处理：{exc}", "corrupt": True}
		if not isinstance(loaded, dict):
			return {"open": True, "reason": "stop.json 顶层不是 JSON object，已按熔断处理", "corrupt": True}
		if "open" in loaded and not isinstance(loaded["open"], bool):
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
		self._write_json_atomic(self.stop_path, {
			"open": True,
			"reason": reason,
			"opened_at": datetime.now(timezone.utc).isoformat(),
		})

	def resume(self) -> bool:
		"""Explicit operator recovery; retain every confirmed business checkpoint."""
		with self._control_lock():
			state = self.read_state()
			if not state.get("autonomy", {}).get("circuit_breaker", {}).get("open"):
				return False
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
			try:
				connection = sqlite3.connect(self.root / ".run-lock.sqlite3", timeout=0.5)
				connection.execute("BEGIN IMMEDIATE")
			except sqlite3.DatabaseError as exc:
				if isinstance(exc, sqlite3.OperationalError) and is_cache_busy_error(exc):
					raise AutomationRunBusy(
						"另一个 boss agent run 正在执行，本次没有发出任何请求"
					) from exc
				# 锁文件损坏或路径被同名目录占用是确定性本地状态问题：原样上抛
				# 会让命令丢掉整个信封，或在 agent run 里被兜成可重试的
				# NETWORK_ERROR（Issue #1304）。
				raise AutomationStateError(
					self.root / ".run-lock.sqlite3",
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
			try:
				connection = sqlite3.connect(self.root / ".control-lock.sqlite3", timeout=1.0)
				connection.execute("BEGIN IMMEDIATE")
			except sqlite3.DatabaseError as exc:
				# 锁冲突是 automation 自己的并发边界，不是缓存库问题：直接上抛会被
				# main 的兜底层报成 CACHE_BUSY 并指向 cache/boss_agent.db，而实际的
				# 锁文件是 automation/.control-lock.sqlite3（Issue #1251）。
				if isinstance(exc, sqlite3.OperationalError) and is_cache_busy_error(exc):
					raise AutomationControlBusy(
						"automation 控制锁正被其他进程占用，本次操作没有生效"
					) from exc
				# 锁文件损坏 / 路径被同名目录占用：确定性本地状态问题（Issue #1304）。
				raise AutomationStateError(
					self.root / ".control-lock.sqlite3",
					f"自动化控制锁无法打开（{exc}）",
				) from exc
			yield
			connection.commit()
		finally:
			if connection is not None:
				connection.close()

	def read_state(self) -> dict[str, Any]:
		if not self.state_path.exists():
			state: dict[str, Any] = {"conversations": {}, "autonomy": {}, "safety": {}}
		else:
			try:
				loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
			except (ValueError, OSError, RecursionError) as exc:
				raise AutomationStateError(self.state_path, str(exc)) from exc
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
		stop = self.read_stop()
		if "open" in stop:
			state.setdefault("autonomy", {})["circuit_breaker"] = stop
		return state

	def write_state(self, state: dict[str, Any]) -> None:
		# Preserve legacy callers that set the breaker through write_state. A stale
		# snapshot lacking it can never remove the separate stop latch.
		with self._control_lock():
			autonomy = state.get("autonomy", {})
			if not isinstance(autonomy, dict):
				raise AutomationStateError(self.state_path, "autonomy 必须是 JSON object")
			breaker = autonomy.get("circuit_breaker", {})
			if not isinstance(breaker, dict):
				raise AutomationStateError(self.state_path, "autonomy.circuit_breaker 必须是 JSON object")
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
				json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
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
		self.write_reviews(updated_reviews)
		return rejected

	def read_jsonl(self, name: str) -> list[dict[str, Any]]:
		path = self.root / name
		if not path.exists():
			return []
		rows: list[dict[str, Any]] = []
		try:
			lines = path.read_text(encoding="utf-8").splitlines()
		except (UnicodeDecodeError, OSError):
			return rows
		for line in lines:
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
		body = "".join(
			f"{json.dumps(row, ensure_ascii=False, sort_keys=True)}\n"
			for row in rows
		)
		fd, temporary = tempfile.mkstemp(prefix=f".{name}.", dir=self.root)
		try:
			with os.fdopen(fd, "w", encoding="utf-8") as handle:
				handle.write(body)
				handle.flush()
				os.fsync(handle.fileno())
			os.replace(temporary, path)
		finally:
			Path(temporary).unlink(missing_ok=True)

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
		row = [sanitize_csv_cell(candidate_key), sanitize_csv_cell(interview_time), sanitize_csv_cell(reason)]
		if path.exists():
			try:
				with path.open("r", encoding="utf-8", newline="") as handle:
					if any(existing[:2] == row[:2] for existing in itertools.islice(csv.reader(handle), 1, None)):
						return False
			except (UnicodeDecodeError, csv.Error):
				# 手工编辑或外部写入造成的坏文件不该让整轮自动化失败；无法判重时
				# 继续追加，最坏只是多一行可见的重复记录。
				pass
		else:
			path.write_text("candidate_key,interview_time,reason\n", encoding="utf-8")
		with path.open("a", encoding="utf-8", newline="") as handle:
			csv.writer(handle).writerow(row)
		return True

	def stats(self) -> dict[str, Any]:
		events = self.read_jsonl("action-log.jsonl")
		reviews = self.read_jsonl("human-review-queue.jsonl")
		pending = self.read_jsonl("pending-actions.jsonl")
		state = self.read_state()
		today = datetime.now(timezone.utc).date().isoformat()
		recent_errors = [
			item
			for item in events[-20:]
			if item.get("status")
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
		with (self.root / name).open("a", encoding="utf-8") as handle:
			handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
			handle.write("\n")


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


def _review_from_row(row: dict[str, Any], path: Path) -> ReviewItem:
	return ReviewItem(
		id=str(row.get("id", "")),
		ts=str(row.get("ts", "")),
		platform=str(row.get("platform", "")),
		candidate_key=str(row.get("candidate_key", "")),
		action=str(row.get("action", "")),
		status=str(row.get("status", "review")),
		confidence=_row_confidence(row, path),
		reason=str(row.get("reason", "")),
		message=str(row.get("message", "")),
		reviewed_at=str(row.get("reviewed_at", "")),
		rejection_reason=str(row.get("rejection_reason", "")),
	)


def _pending_from_row(row: dict[str, Any], path: Path) -> PendingAction:
	return PendingAction(
		id=str(row.get("id", "")),
		ts=str(row.get("ts", "")),
		platform=str(row.get("platform", "")),
		candidate_key=str(row.get("candidate_key", "")),
		action=str(row.get("action", "")),
		status=str(row.get("status", "pending")),
		confidence=_row_confidence(row, path),
		reason=str(row.get("reason", "")),
		message=str(row.get("message", "")),
		approved_review_id=str(row.get("approved_review_id", "")),
		updated_at=str(row.get("updated_at", "")),
	)
