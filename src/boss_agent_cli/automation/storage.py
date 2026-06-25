"""File-backed automation state, queues, and event logs."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

	def read_state(self) -> dict[str, Any]:
		if not self.state_path.exists():
			return {"conversations": {}, "autonomy": {}, "safety": {}}
		return json.loads(self.state_path.read_text(encoding="utf-8"))

	def write_state(self, state: dict[str, Any]) -> None:
		self.state_path.write_text(
			json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
			encoding="utf-8",
		)

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
		path.write_text(body, encoding="utf-8")

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
			if item.get("status") in {"STOPPED_BY_SAFETY", "CIRCUIT_BREAKER_OPEN"}
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
