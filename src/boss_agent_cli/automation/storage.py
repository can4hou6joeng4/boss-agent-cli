"""File-backed automation state, queues, and event logs."""

from __future__ import annotations

import csv
import itertools
import json
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from boss_agent_cli.automation.models import AutomationEvent, PendingAction, ReviewItem
from boss_agent_cli.services.chat_utils import sanitize_csv_cell


class AutomationStateError(ValueError):
	"""`automation/state.json` 读不出来；调用方应输出信封并停止自动化。"""

	def __init__(self, path: Path, detail: str) -> None:
		super().__init__(f"{path} 无法解析（{detail}）")
		self.path = path
		self.detail = detail


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
		except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
			return {"open": True, "reason": f"stop.json 无法解析，已按熔断处理：{exc}", "corrupt": True}
		if not isinstance(loaded, dict):
			return {"open": True, "reason": "stop.json 顶层不是 JSON object，已按熔断处理", "corrupt": True}
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
	def _control_lock(self) -> Iterator[None]:
		"""Serialize tiny control updates across processes with a bounded wait."""
		connection = sqlite3.connect(self.root / ".control-lock.sqlite3", timeout=1.0)
		try:
			connection.execute("BEGIN IMMEDIATE")
			yield
			connection.commit()
		finally:
			connection.close()

	def read_state(self) -> dict[str, Any]:
		if not self.state_path.exists():
			state: dict[str, Any] = {"conversations": {}, "autonomy": {}, "safety": {}}
		else:
			try:
				loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
			except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
				raise AutomationStateError(self.state_path, str(exc)) from exc
			if not isinstance(loaded, dict):
				raise AutomationStateError(self.state_path, "顶层必须是 JSON object")
			state = cast("dict[str, Any]", loaded)
		stop = self.read_stop()
		if "open" in stop:
			state.setdefault("autonomy", {})["circuit_breaker"] = stop
		return state

	def write_state(self, state: dict[str, Any]) -> None:
		# Preserve legacy callers that set the breaker through write_state. A stale
		# snapshot lacking it can never remove the separate stop latch.
		with self._control_lock():
			breaker = state.get("autonomy", {}).get("circuit_breaker", {})
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

	def read_reviews(self) -> list[ReviewItem]:
		return [_review_from_row(row) for row in self.read_jsonl("human-review-queue.jsonl")]

	def read_pending(self) -> list[PendingAction]:
		return [_pending_from_row(row) for row in self.read_jsonl("pending-actions.jsonl")]

	def write_reviews(self, items: list[ReviewItem]) -> None:
		self.write_jsonl("human-review-queue.jsonl", [asdict(item) for item in items])

	def write_pending(self, items: list[PendingAction]) -> None:
		self.write_jsonl("pending-actions.jsonl", [asdict(item) for item in items])

	def approve_review(self, review_id: str, reviewed_at: str) -> PendingAction | None:
		reviews = self.read_reviews()
		updated_reviews: list[ReviewItem] = []
		pending: PendingAction | None = None
		for item in reviews:
			if item.id == review_id and item.status == "review":
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
		self.write_reviews(updated_reviews)
		self.append_pending(pending)
		return pending

	def reject_review(
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
			except json.JSONDecodeError:
				# 单行损坏（手工编辑、外部写入）不该让 stats / pending 整体失败。
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


def _review_from_row(row: dict[str, Any]) -> ReviewItem:
	return ReviewItem(
		id=str(row.get("id", "")),
		ts=str(row.get("ts", "")),
		platform=str(row.get("platform", "")),
		candidate_key=str(row.get("candidate_key", "")),
		action=str(row.get("action", "")),
		status=str(row.get("status", "review")),
		confidence=float(row.get("confidence", 0.0)),
		reason=str(row.get("reason", "")),
		message=str(row.get("message", "")),
		reviewed_at=str(row.get("reviewed_at", "")),
		rejection_reason=str(row.get("rejection_reason", "")),
	)


def _pending_from_row(row: dict[str, Any]) -> PendingAction:
	return PendingAction(
		id=str(row.get("id", "")),
		ts=str(row.get("ts", "")),
		platform=str(row.get("platform", "")),
		candidate_key=str(row.get("candidate_key", "")),
		action=str(row.get("action", "")),
		status=str(row.get("status", "pending")),
		confidence=float(row.get("confidence", 0.0)),
		reason=str(row.get("reason", "")),
		message=str(row.get("message", "")),
		approved_review_id=str(row.get("approved_review_id", "")),
		updated_at=str(row.get("updated_at", "")),
	)
