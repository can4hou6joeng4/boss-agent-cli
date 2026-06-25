"""Automation event helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha1
from typing import Any

from boss_agent_cli.automation.models import (
	AutomationEvent,
	Decision,
	EventStatus,
	PlatformAction,
)


def now_iso() -> str:
	return datetime.now(timezone.utc).isoformat()


def stable_action_id(
	platform: str,
	candidate_key: str,
	action: PlatformAction | str,
	ts: str,
) -> str:
	raw = f"{platform}|{candidate_key}|{action}|{ts}"
	return sha1(raw.encode("utf-8")).hexdigest()[:16]


def make_event(
	platform: str,
	candidate_key: str,
	action: PlatformAction,
	status: EventStatus,
	confidence: float,
	reason: str,
	result: dict[str, Any] | None = None,
) -> AutomationEvent:
	return AutomationEvent(
		ts=now_iso(),
		platform=platform,
		role="recruiter",
		candidate_key=candidate_key,
		action=action.value,
		status=status.value,
		confidence=round(confidence, 4),
		reason=reason,
		result=result or {},
	)


def decision_payload(decision: Decision) -> dict[str, Any]:
	return {
		"action": decision.action.value,
		"confidence": decision.confidence,
		"reason": decision.reason,
		"message": decision.message,
	}
