"""File-backed automation state, queues, and event logs."""

from __future__ import annotations

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
		"""Read the independent stop latch; business snapshots cannot clear it."""
		if not self.stop_path.exists():
			return {}
		return cast("dict[str, Any]", json.loads(self.stop_path.read_text(encoding="utf-8")))

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
			state = cast("dict[str, Any]", json.loads(self.state_path.read_text(encoding="utf-8")))
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
				self._set_stop(str(breaker.get("reason", "circuit breaker is open")))
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
		for line in path.read_text(encoding="utf-8").splitlines():
			if line.strip():
				parsed = json.loads(line)
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
	) -> None:
		path = self.root / "interview-leads.csv"
		if not path.exists():
			path.write_text("candidate_key,interview_time,reason\n", encoding="utf-8")
		with path.open("a", encoding="utf-8") as handle:
			handle.write(f"{candidate_key},{interview_time},{reason.replace(',', '，')}\n")

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
