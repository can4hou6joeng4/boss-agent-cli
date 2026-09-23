"""Automation event helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from boss_agent_cli.automation.models import (
	AutomationEvent,
	EventStatus,
	PlatformAction,
)


def now_iso() -> str:
	return datetime.now(timezone.utc).isoformat()


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
